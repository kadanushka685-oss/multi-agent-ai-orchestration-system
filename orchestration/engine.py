"""Central orchestrator: the only component that plans, delegates, routes messages and decides.

Responsibilities:
  * validate the task and the selected agents, then create the run;
  * ask the Planning Agent for a plan and validate it before any step runs;
  * execute steps in dependency order, one agent per step;
  * deliver each step's inputs as messages (task, handoff and revision messages);
  * retry transient failures up to a hard cap, and record every attempt;
  * send rejected statements back to the Writing Agent once, then stop;
  * skip steps whose upstream work failed, and aggregate the final response;
  * persist every change so the monitor, history and reports always match the run.
"""
from __future__ import annotations

import time
import uuid
from typing import Callable, Optional

from pydantic import ValidationError

from agents import build_agents
from agents.planning_agent import build_default_plan
from config.settings import AGENT_NAMES, MAX_REVISIONS, Settings
from orchestration.context import TaskContext
from orchestration.logger import ExecutionLogger
from orchestration.models import AgentMessage, AgentOutput, FaultInjection, Plan, RunOptions
from orchestration.plan_validator import validate_plan
from orchestration.retry import AgentExecutionError, RetryableError, run_with_retries
from orchestration.state import RESOLVED_STATUSES, RunState, StepRecord, now_iso
from services.security import InputError, detect_injection, redact, validate_task_text

NOT_RUN_BANNER = "> Verification: NOT RUN. Every statement in this report is unverified."


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


class Orchestrator:
    def __init__(
        self,
        db,
        store,
        settings: Settings,
        llm=None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.db = db
        self.store = store
        self.settings = settings
        self.sleep = sleep
        self.clock = clock
        self.max_steps = settings.max_steps
        self.agents = build_agents(store, llm)

    # ------------------------------------------------------------------ public API

    def enabled_agents(self) -> list[str]:
        flags = self.db.get_agent_enabled()
        return [name for name in AGENT_NAMES if flags.get(name, True)]

    def create_run(
        self,
        task_text: str,
        selected_agents: Optional[list[str]] = None,
        mode: str = "automatic",
        max_retries: Optional[int] = None,
        fault_injection: Optional[FaultInjection] = None,
    ) -> RunState:
        """Validate the request, plan the work and persist the run. Nothing executes here."""
        text = redact(validate_task_text(task_text), self.settings.secrets)
        enabled = set(self.enabled_agents())
        requested = list(dict.fromkeys(selected_agents or AGENT_NAMES))
        unknown = [name for name in requested if name not in AGENT_NAMES]
        if unknown:
            raise InputError(f"Unknown agent(s): {', '.join(unknown)}.")
        selected = [name for name in requested if name in enabled]
        if "writing" not in selected:
            raise InputError("The Writing Agent must be selected and enabled, because it produces the final response.")
        try:
            options = RunOptions(
                mode=mode,
                max_retries=self.settings.max_retries if max_retries is None else max_retries,
                fault_injection=fault_injection,
            )
        except ValidationError as exc:
            raise InputError(f"Invalid run options: {exc}") from exc

        run = RunState(
            task_id=uuid.uuid4().hex[:12],
            task_text=text,
            options=options,
            selected_agents=selected,
            context=TaskContext(task=text),
            created_at=now_iso(),
            fault_remaining=fault_injection.fail_times if fault_injection else 0,
        )
        log = self._logger(run)
        self.db.create_task(
            task_id=run.task_id,
            created_at=run.created_at,
            task_text=text,
            mode=options.mode,
            status=run.status,
            selected_agents=selected,
            fault_injection=fault_injection.model_dump() if fault_injection else None,
        )
        log.log("INFO", "task_received", f"Task received in {options.mode} mode with agents: {', '.join(selected)}.")

        flags = detect_injection(text)
        run.injection_flags = flags
        if flags:
            note = f"Instruction-like phrases were detected ({', '.join(flags)}). The text is treated as data only."
            run.context.warnings.append(note)
            log.log("WARNING", "injection_detected", note)

        outcome = run_with_retries(
            lambda attempt: self._plan_once(run, selected),
            options.max_retries,
            self.settings.retry_backoff_seconds,
            self.sleep,
            on_retry=self._retry_logger(log, "PLAN", "planning"),
        )
        if not outcome.ok:
            run.status = "failed"
            run.error = redact(outcome.errors[-1], self.settings.secrets)
            run.completed_at = now_iso()
            log.log("ERROR", "plan_failed", f"Planning failed after {outcome.attempts} attempt(s). {run.error}", "planning")
            self._persist(run)
            return run

        plan: Plan = outcome.value
        run.plan = plan
        run.steps = [StepRecord(step=item) for item in plan.steps]
        run.status = "awaiting_approval" if options.mode == "manual" else "running"
        summary = f"Plan with {len(plan.steps)} step(s) created by the {plan.planner} planner."
        run.context.outputs["PLAN"] = summary
        self.db.add_output(
            run.task_id,
            step_id="PLAN",
            agent="planning",
            created_at=now_iso(),
            summary=summary,
            data={"assumptions": plan.assumptions, "step_ids": [item.id for item in plan.steps]},
            warnings=[],
        )
        self.db.update_task(run.task_id, plan_json=plan.model_dump())
        self.db.replace_plan_steps(run.task_id, [self._step_row(i, record) for i, record in enumerate(run.steps)])
        log.log("INFO", "plan_created", summary, "planning")
        self._persist(run)
        return run

    def approve(self, run: RunState) -> None:
        if run.status == "awaiting_approval":
            run.status = "running"
            self._logger(run).log("INFO", "plan_approved", "The user approved the plan. Execution can begin.")
            self._persist(run)

    def next_step(self, run: RunState) -> Optional[StepRecord]:
        """Execute the next ready step. Returns None when nothing is left to run."""
        if run.status != "running":
            return None
        if run.started_at is None:
            run.started_at = now_iso()
            run.clock_start = self.clock()
        if self.clock() - run.clock_start > self.settings.max_run_seconds:
            self._abort(run, "The run exceeded the execution time limit.")
            return None
        self._mark_unreachable(run)
        ready = next((r for r in run.steps if r.status == "pending" and self._deps_resolved(run, r)), None)
        if ready is None:
            self.finish(run)
            return None
        if run.executions >= self.max_steps:
            self._abort(run, "The run reached the execution step limit.")
            return None
        missing = [dep for dep in ready.step.depends_on if run.step(dep).status != "completed"]
        if missing:
            note = f"{ready.id} proceeded without the output of {', '.join(missing)}."
            run.context.warnings.append(note)
            self._logger(run).log("WARNING", "partial_inputs", note, ready.step.agent)
        self._execute(run, ready)
        self._persist(run)
        return ready

    def run_to_completion(self, run: RunState, on_step: Optional[Callable[[StepRecord, RunState], None]] = None) -> RunState:
        while True:
            record = self.next_step(run)
            if record is None:
                break
            if on_step is not None:
                on_step(record, run)
        return run

    # ------------------------------------------------------------------ internals

    def _logger(self, run: RunState) -> ExecutionLogger:
        return ExecutionLogger(self.db, run.task_id, self.settings.secrets)

    def _retry_logger(self, log: ExecutionLogger, step_id: str, agent: str):
        def on_retry(attempt: int, exc: BaseException) -> None:
            log.log("WARNING", "retry", f"{step_id} attempt {attempt} failed ({_short(exc)}). Retrying.", agent)

        return on_retry

    def _plan_once(self, run: RunState, selected: list[str]) -> Plan:
        if "planning" in selected:
            plan = self.agents["planning"].build_plan(run.task_text, selected)
        else:
            plan = build_default_plan(run.task_text, selected, self.store)
        errors = validate_plan(plan.steps, set(selected), self.max_steps)
        if errors:
            raise AgentExecutionError("Plan rejected: " + "; ".join(errors))
        return plan

    @staticmethod
    def _deps_resolved(run: RunState, record: StepRecord) -> bool:
        return all(run.step(dep).status in RESOLVED_STATUSES for dep in record.step.depends_on)

    def _mark_unreachable(self, run: RunState) -> None:
        """Skip any pending step whose upstream steps are all resolved but none completed."""
        log = self._logger(run)
        changed = True
        while changed:
            changed = False
            for record in run.steps:
                if record.status != "pending" or not record.step.depends_on:
                    continue
                deps = [run.step(dep) for dep in record.step.depends_on]
                if all(d.status in RESOLVED_STATUSES for d in deps) and not any(d.status == "completed" for d in deps):
                    record.status = "skipped"
                    record.skip_reason = "No upstream step completed (" + ", ".join(
                        f"{d.id}: {d.status}" for d in deps
                    ) + ")."
                    log.log("WARNING", "step_skipped", f"{record.id} skipped. {record.skip_reason}", record.step.agent)
                    changed = True

    def _execute(self, run: RunState, record: StepRecord) -> None:
        agent = self.agents[record.step.agent]
        log = self._logger(run)
        run.executions += 1
        run.bus.send(AgentMessage(
            sender="orchestrator",
            recipient=agent.name,
            message_type="task",
            step_id=record.id,
            payload={"title": record.step.title, "instruction": record.step.instruction, "focus": record.step.focus},
            created_at=now_iso(),
        ))
        inbox = run.bus.take_for(agent.name, record.id)
        record.status = "running"
        record.started_at = now_iso()
        started = self.clock()
        self._persist_step(run, record)
        log.log("INFO", "step_started", f"{record.id} started: {record.step.title}", agent.name)
        fault = run.options.fault_injection

        def attempt(number: int) -> AgentOutput:
            if fault is not None and fault.agent == agent.name and run.fault_remaining > 0:
                run.fault_remaining -= 1
                raise RetryableError(f"Injected transient failure in the {agent.display_name} (demo mode).")
            return agent.handle(record.step, run.context, inbox)

        outcome = run_with_retries(
            attempt,
            run.options.max_retries,
            self.settings.retry_backoff_seconds,
            self.sleep,
            on_retry=self._retry_logger(log, record.id, agent.name),
        )
        record.attempts = outcome.attempts
        record.errors = [redact(error, self.settings.secrets) for error in outcome.errors]
        record.completed_at = now_iso()
        record.duration_seconds = round(self.clock() - started, 4)

        if not outcome.ok:
            record.status = "failed"
            log.log("ERROR", "step_failed", f"{record.id} failed after {outcome.attempts} attempt(s). {record.errors[-1]}", agent.name)
            self._persist_step(run, record)
            return

        output: AgentOutput = outcome.value
        record.output = output
        record.status = "completed"
        run.context.outputs[record.id] = output.summary
        run.context.warnings.extend(output.warnings)
        self.db.add_output(
            run.task_id,
            step_id=record.id,
            agent=agent.name,
            created_at=now_iso(),
            summary=output.summary,
            data=output.data,
            warnings=output.warnings,
        )
        log.log("INFO", "step_completed", f"{record.id} completed in {record.duration_seconds:.2f}s. {output.summary}", agent.name)
        for warning in output.warnings:
            log.log("WARNING", "agent_warning", warning, agent.name)
        self._handoff(run, record, output)
        self._persist_step(run, record)
        if agent.name == "verification":
            self._after_verification(run, record)

    def _handoff(self, run: RunState, record: StepRecord, output: AgentOutput) -> None:
        """Route the finished output to every dependent step as a message."""
        log = self._logger(run)
        for other in run.steps:
            if other.status != "pending" or record.id not in other.step.depends_on:
                continue
            if other.step.agent == record.step.agent:
                continue
            run.bus.send(AgentMessage(
                sender=record.step.agent,
                recipient=other.step.agent,
                message_type="handoff",
                step_id=other.id,
                payload={"from_step": record.id, "summary": output.summary, "warnings": output.warnings},
                created_at=now_iso(),
            ))
            log.log(
                "INFO", "handoff",
                f"{record.step.agent} handed the output of {record.id} to {other.step.agent} for {other.id}.",
                record.step.agent,
            )

    def _after_verification(self, run: RunState, record: StepRecord) -> None:
        """If verification rejected statements, send them back to the Writing Agent once."""
        unsupported = list((run.context.verification or {}).get("unsupported_lines") or [])
        if not unsupported or run.revisions >= MAX_REVISIONS:
            return
        writer = next((r for r in run.steps if r.step.agent == "writing"), None)
        if writer is None:
            return
        run.revisions += 1
        run.context.feedback = unsupported
        run.bus.send(AgentMessage(
            sender="verification",
            recipient="writing",
            message_type="revision",
            step_id=writer.id,
            payload={"rejected_statements": unsupported},
            created_at=now_iso(),
        ))
        writer.status = "pending"
        writer.attempts = 0
        record.status = "pending"
        record.attempts = 0
        self._logger(run).log(
            "WARNING", "revision",
            f"Verification rejected {len(unsupported)} statement(s). The Writing Agent will revise "
            f"(round {run.revisions} of {MAX_REVISIONS}).",
            "verification",
        )
        self._persist_step(run, writer)
        self._persist_step(run, record)

    def finish(self, run: RunState) -> None:
        """Decide the final status and the final response once no step can run."""
        if run.is_terminal:
            return
        log = self._logger(run)
        writer = next((r for r in run.steps if r.step.agent == "writing"), None)
        verifier = next((r for r in run.steps if r.step.agent == "verification"), None)
        failed = [r for r in run.steps if r.status == "failed"]
        ctx = run.context

        if writer is None or writer.status != "completed":
            run.status = "failed"
            reason = writer.errors[-1] if writer and writer.errors else "The Writing Agent did not produce a report."
            run.error = redact(reason, self.settings.secrets)
        else:
            verified_step = verifier is not None and verifier.status == "completed"
            if not verified_step:
                ctx.final_response = NOT_RUN_BANNER + "\n\n" + ctx.draft
            verdict = (ctx.verification or {}).get("verdict")
            clean = (
                verified_step
                and verdict == "passed"
                and not failed
                and not any(r.status == "skipped" for r in run.steps)
            )
            run.status = "completed" if clean else "completed_with_warnings"
            if failed:
                run.error = redact(failed[0].errors[-1] if failed[0].errors else "A step failed.", self.settings.secrets)

        run.completed_at = now_iso()
        run.duration_seconds = round(self.clock() - run.clock_start, 4) if run.started_at else None
        level = {"completed": "INFO", "completed_with_warnings": "WARNING"}.get(run.status, "ERROR")
        log.log(level, "task_finished", f"Task finished with status '{run.status}'.")
        self._persist(run)

    def _abort(self, run: RunState, reason: str) -> None:
        for record in run.steps:
            if record.status == "pending":
                record.status = "skipped"
                record.skip_reason = f"Stopped: {reason}"
        run.status = "failed"
        run.error = reason
        run.completed_at = now_iso()
        run.duration_seconds = round(self.clock() - run.clock_start, 4)
        self._logger(run).log("ERROR", "run_stopped", reason)
        self._persist(run)

    def _persist(self, run: RunState) -> None:
        verdict = (run.context.verification or {}).get("verdict")
        self.db.update_task(
            run.task_id,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            duration_seconds=run.duration_seconds,
            final_response=run.context.final_response or None,
            verification_verdict=verdict,
            total_retries=run.total_retries(),
            revisions=run.revisions,
            error=run.error or None,
            context_json=run.context.to_dict(),
        )
        for record in run.steps:
            self._persist_step(run, record)

    def _persist_step(self, run: RunState, record: StepRecord) -> None:
        self.db.update_plan_step(
            run.task_id,
            record.id,
            status=record.status,
            attempts=record.attempts,
            errors=record.errors,
            started_at=record.started_at,
            completed_at=record.completed_at,
            duration_seconds=record.duration_seconds,
            skip_reason=record.skip_reason,
        )

    @staticmethod
    def _step_row(position: int, record: StepRecord) -> dict:
        return {
            "step_id": record.id,
            "position": position,
            "agent": record.step.agent,
            "title": record.step.title,
            "instruction": record.step.instruction,
            "depends_on": list(record.step.depends_on),
            "status": record.status,
            "attempts": 0,
            "errors": [],
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "skip_reason": "",
        }
