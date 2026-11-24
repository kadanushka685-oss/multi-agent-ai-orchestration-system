"""The seven dashboard pages. Each function takes the shared Services object and renders one page."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from agents import build_agents, self_test
from config.settings import (
    AGENT_NAMES,
    HARD_MAX_RETRIES,
    MAX_RUN_SECONDS,
    MAX_REVISIONS,
    MAX_STEPS,
    MAX_TASK_CHARS,
    MIN_TASK_CHARS,
    SAMPLE_TASKS_PATH,
)
from orchestration.engine import Orchestrator
from orchestration.models import FaultInjection
from orchestration.state import TERMINAL_STATUSES
from reports.csv_report import build_csv
from reports.pdf_report import build_pdf
from reports.report_builder import build_report
from services.llm_client import check_connection
from services.security import InputError
from ui import charts
from ui.labels import short_time as _short_time, task_labels as _task_labels

STATUS_OPTIONS = ["completed", "completed_with_warnings", "failed", "running", "awaiting_approval", "planning"]


# ---------------------------------------------------------------- helpers

def load_sample_tasks() -> list[dict]:
    return json.loads(SAMPLE_TASKS_PATH.read_text(encoding="utf-8"))


def _task_row(task: dict) -> dict:
    verdict = (task.get("context_json") or {}).get("verification", {}) or {}
    return {
        "created": _short_time(task["created_at"]),
        "status": task["status"],
        "mode": task["mode"],
        "duration_s": task["duration_seconds"],
        "retries": task["total_retries"],
        "verdict": verdict.get("verdict", "-"),
        "task": task["task_text"][:90],
    }


def _plan_rows(run) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "order": index + 1,
            "step": record.id,
            "agent": record.step.agent,
            "title": record.step.title,
            "depends_on": ", ".join(record.step.depends_on) or "-",
            "status": record.status,
            "attempts": record.attempts,
            "duration_s": record.duration_seconds,
            "skip_reason": record.skip_reason,
            "errors": " | ".join(record.errors),
        }
        for index, record in enumerate(run.steps)
    ])


def report_from_db(svc, task_id: str) -> dict | None:
    task = svc.db.get_task(task_id)
    if task is None:
        return None
    return build_report(task, svc.db.get_plan_steps(task_id), svc.db.get_outputs(task_id), svc.settings.secrets)


# ---------------------------------------------------------------- page 1: overview

def page_overview(svc) -> None:
    st.title("Overview Dashboard")
    st.caption("Live figures from the orchestration database.")
    metrics = svc.db.dashboard_metrics()
    cols = st.columns(6)
    cols[0].metric("Total tasks", metrics["total"])
    cols[1].metric("Completed tasks", metrics["completed"])
    cols[2].metric("Failed tasks", metrics["failed"])
    cols[3].metric("Active agents", f"{metrics['active_agents']} of {len(AGENT_NAMES)}")
    cols[4].metric(
        "Average execution time",
        "n/a" if metrics["avg_duration"] is None else f"{metrics['avg_duration']:.2f} s",
    )
    cols[5].metric(
        "Agent success rate",
        "n/a" if metrics["success_rate"] is None else f"{metrics['success_rate']:.0%}",
    )

    left, right = st.columns(2)
    left.plotly_chart(charts.status_chart(svc.db.status_counts()), use_container_width=True)
    right.plotly_chart(charts.agent_success_chart(svc.db.agent_success_rates()), use_container_width=True)
    st.plotly_chart(charts.daily_chart(svc.db.daily_task_counts()), use_container_width=True)

    st.subheader("Recent tasks")
    recent = svc.db.list_tasks(limit=10)
    if not recent:
        st.info("No tasks yet. Open the AI Task Workspace to run a sample task.")
        return
    st.dataframe(pd.DataFrame([_task_row(task) for task in recent]), hide_index=True)


# ---------------------------------------------------------------- page 2: workspace

def start_run(svc, task_text: str, selected: list[str], mode: str, retries: int,
              fault_agent: str | None, fail_times: int) -> None:
    try:
        fault = FaultInjection(agent=fault_agent, fail_times=fail_times) if fault_agent else None
        orchestrator = Orchestrator(svc.db, svc.store, svc.settings, llm=svc.llm)
        run = orchestrator.create_run(
            task_text,
            selected_agents=selected,
            mode=mode,
            max_retries=retries,
            fault_injection=fault,
        )
    except (InputError, ValueError) as exc:
        st.error(str(exc))
        return
    st.session_state["run"] = run
    if mode == "automatic" and run.status == "running":
        progress = st.progress(0.0, text="Starting the orchestrator...")
        total = max(1, len(run.steps))

        def on_step(record, state) -> None:
            done = sum(1 for item in state.steps if item.status in ("completed", "failed", "skipped"))
            progress.progress(min(done / total, 1.0), text=f"{record.id} {record.status}: {record.step.title}")

        orchestrator.run_to_completion(run, on_step=on_step)
        progress.progress(1.0, text=f"Finished with status: {run.status}")


def render_run(svc, run) -> None:
    orchestrator = Orchestrator(svc.db, svc.store, svc.settings, llm=svc.llm)
    st.divider()
    st.subheader(f"Run {run.task_id}")
    cols = st.columns(4)
    cols[0].metric("Status", run.status)
    cols[1].metric("Steps", len(run.steps))
    cols[2].metric("Retries", run.total_retries())
    cols[3].metric("Revisions", run.revisions)
    if run.injection_flags:
        st.warning(
            "Security notice: this task contains instruction-like phrases ("
            + ", ".join(run.injection_flags)
            + "). They were treated as data only."
        )
    if run.error:
        st.error(run.error)

    if run.status == "awaiting_approval":
        st.info("Manual mode: review the plan below, then approve it to begin.")
        if st.button("Approve plan", type="primary"):
            orchestrator.approve(run)
    elif run.mode == "manual" and not run.is_terminal:
        left, right = st.columns(2)
        if left.button("Run next step", type="primary"):
            orchestrator.next_step(run)
        if right.button("Run all remaining steps"):
            orchestrator.run_to_completion(run)

    st.markdown("#### Execution plan")
    st.dataframe(_plan_rows(run), hide_index=True)

    st.markdown("#### Agent outputs")
    shown = False
    for record in run.steps:
        if record.output is None:
            continue
        shown = True
        with st.expander(f"{record.id} · {record.output.agent} · {record.status}"):
            st.write(record.output.summary)
            for warning in record.output.warnings:
                st.warning(warning)
            st.json(record.output.data)
    if not shown:
        st.caption("No agent has produced output yet.")

    if run.is_terminal and run.context.final_response:
        st.markdown("#### Final response")
        st.markdown(run.context.final_response)
        report = report_from_db(svc, run.task_id)
        if report is not None:
            left, right = st.columns(2)
            left.download_button(
                "Download CSV report", data=build_csv(report),
                file_name=f"report_{run.task_id}.csv", mime="text/csv",
            )
            right.download_button(
                "Download PDF report", data=build_pdf(report),
                file_name=f"report_{run.task_id}.pdf", mime="application/pdf",
            )


def page_workspace(svc) -> None:
    st.title("AI Task Workspace")
    st.caption(
        "Describe a task. The orchestrator plans it, delegates each step to a specialised agent, "
        "and verifies the result before showing it."
    )
    if svc.config_error:
        st.warning(
            f"The .env settings contain a problem, so default demo settings are in use: {svc.config_error}"
        )
    if svc.llm_error:
        st.error(
            f"Live mode is not available: {svc.llm_error} Demo mode needs no key: set LLM_PROVIDER=mock in .env."
        )

    samples = load_sample_tasks()
    titles = ["(write your own task)"] + [sample["title"] for sample in samples]
    choice = st.selectbox("Sample tasks", titles, key="sample_choice")
    if st.session_state.get("applied_sample") != choice:
        st.session_state["applied_sample"] = choice
        st.session_state["task_text"] = next((s["task"] for s in samples if s["title"] == choice), "")
    task_text = st.text_area(
        "Task",
        key="task_text",
        height=110,
        max_chars=MAX_TASK_CHARS,
        help=f"Between {MIN_TASK_CHARS} and {MAX_TASK_CHARS} characters.",
    )

    left, middle, right = st.columns(3)
    selected = left.multiselect(
        "Agents to use",
        list(AGENT_NAMES),
        default=list(AGENT_NAMES),
        help="The Writing Agent is required, because it produces the final response.",
    )
    mode = middle.radio(
        "Execution mode",
        ["automatic", "manual"],
        horizontal=True,
        help="Automatic runs every step. Manual waits for approval, then runs one step at a time.",
    )
    retries = right.slider("Retries per step", 0, HARD_MAX_RETRIES, 2, help=f"Hard limit: {HARD_MAX_RETRIES}.")

    with st.expander("Demo options: simulate a transient failure"):
        inject = st.checkbox("Inject a failure into one agent")
        fault_agent = st.selectbox("Agent to fail", list(AGENT_NAMES), disabled=not inject)
        fail_times = st.slider("Failures to inject", 1, 3, 1, disabled=not inject)

    if st.button("Start orchestration", type="primary", disabled=bool(svc.llm_error)):
        start_run(
            svc, task_text, selected, mode, retries,
            fault_agent if inject else None, fail_times,
        )

    run = st.session_state.get("run")
    if run is not None:
        render_run(svc, run)


# ---------------------------------------------------------------- page 3: agent management

def page_agents(svc) -> None:
    st.title("Agent Management")
    st.caption(
        "Each agent has one responsibility. Disabled agents are left out of new plans. "
        "The Writing Agent is required, so it cannot be disabled."
    )
    flags = svc.db.get_agent_enabled()
    agents = build_agents(svc.store, None)
    stats = {row["agent"]: row for row in svc.db.agent_success_rates()}

    for name in AGENT_NAMES:
        agent = agents[name]
        with st.container(border=True):
            header, toggle = st.columns([4, 1])
            header.markdown(f"**{agent.display_name}**  ·  `{name}`")
            header.write(agent.role)
            row = stats.get(name)
            if row and row["rate"] is not None:
                header.caption(f"Steps attempted: {row['attempted']}  ·  success rate: {row['rate']:.0%}")
            else:
                header.caption("No steps run yet.")
            enabled = toggle.toggle(
                "Enabled",
                value=flags.get(name, True),
                key=f"agent_enabled_{name}",
                disabled=(name == "writing"),
            )
            if enabled != flags.get(name, True):
                svc.db.set_agent_enabled(name, enabled)
                flags[name] = enabled
            with st.expander("Responsibilities"):
                for item in agent.responsibilities:
                    st.markdown(f"- {item}")

    st.subheader("Self-test")
    st.caption(
        "Runs one agent on a fixed, deterministic context built from the sample documents, "
        "then shows the real output. The self-test never calls a language model."
    )
    choice = st.selectbox("Agent to test", list(AGENT_NAMES), key="selftest_agent")
    if st.button("Run self-test"):
        output = self_test(choice, svc.store)
        st.success(output.summary)
        st.json(output.model_dump())


# ---------------------------------------------------------------- page 4: execution monitor

def page_monitor(svc) -> None:
    st.title("Execution Monitor")
    st.caption("Step-by-step view of a stored run: agent, status, timing, attempts, errors and the execution log.")
    tasks = svc.db.list_tasks(limit=100)
    if not tasks:
        st.info("No runs yet. Start one in the AI Task Workspace.")
        return
    labels = _task_labels(tasks)
    task = svc.db.get_task(labels[st.selectbox("Run", list(labels), key="monitor_run")])

    cols = st.columns(4)
    cols[0].metric("Status", task["status"])
    cols[1].metric("Mode", task["mode"])
    cols[2].metric("Duration", "n/a" if task["duration_seconds"] is None else f"{task['duration_seconds']:.2f} s")
    cols[3].metric("Retries", task["total_retries"])
    if task["error"]:
        st.error(task["error"])

    steps = svc.db.get_plan_steps(task["id"])
    st.markdown("#### Steps")
    if steps:
        st.dataframe(pd.DataFrame([
            {
                "order": index + 1,
                "step": step["step_id"],
                "agent": step["agent"],
                "title": step["title"],
                "status": step["status"],
                "success": "yes" if step["status"] == "completed" else ("no" if step["status"] == "failed" else "-"),
                "attempts": step["attempts"],
                "started": _short_time(step["started_at"]),
                "completed": _short_time(step["completed_at"]),
                "duration_s": step["duration_seconds"],
                "skip_reason": step["skip_reason"],
                "errors": " | ".join(step["errors"]),
            }
            for index, step in enumerate(steps)
        ]), hide_index=True)
        timed = [step for step in steps if step["duration_seconds"] is not None]
        if timed:
            st.plotly_chart(charts.duration_chart(timed), use_container_width=True)
    else:
        st.caption("This run has no plan steps (planning failed).")

    st.markdown("#### Execution log")
    logs = svc.db.get_logs(task["id"])
    if logs:
        st.dataframe(
            pd.DataFrame(logs)[["timestamp", "level", "event", "agent", "message"]],
            hide_index=True,
        )
    else:
        st.caption("No log entries.")

    with st.expander("Agent outputs"):
        outputs = svc.db.get_outputs(task["id"])
        if not outputs:
            st.caption("No outputs stored.")
        for output in outputs:
            st.markdown(f"**{output['step_id']} · {output['agent']}**: {output['summary']}")
            for warning in output["warnings"]:
                st.warning(warning)


# ---------------------------------------------------------------- page 5: reports

def page_reports(svc) -> None:
    st.title("Reports")
    st.caption("Export a finished run as CSV or PDF. Both files are built from the same report structure.")
    tasks = svc.db.list_tasks(statuses=sorted(TERMINAL_STATUSES), limit=200)
    if not tasks:
        st.info("No finished runs yet. Complete a run in the AI Task Workspace first.")
        return
    labels = _task_labels(tasks)
    task_id = labels[st.selectbox("Finished run", list(labels), key="report_run")]
    report = report_from_db(svc, task_id)

    cols = st.columns(4)
    cols[0].metric("Status", report["status"])
    cols[1].metric("Verification verdict", report["verification"]["verdict"])
    cols[2].metric("Duration", "n/a" if report["duration_seconds"] is None else f"{report['duration_seconds']:.2f} s")
    cols[3].metric("Retries", report["total_retries"])

    st.markdown("#### Original task")
    st.write(report["original_task"])
    st.markdown("#### Execution plan")
    if report["plan"]:
        st.dataframe(pd.DataFrame(report["plan"])[["step_id", "agent", "title", "status", "attempts"]], hide_index=True)
    st.markdown("#### Agents involved")
    if report["agents_involved"]:
        st.dataframe(pd.DataFrame(report["agents_involved"]), hide_index=True)
    verification = report["verification"]
    st.markdown("#### Verification results")
    st.write(
        f"Verified: {verification['verified']} · Unverified: {verification['unverified']} · "
        f"Labelled inferences: {verification['inferences']} · Unsupported: {verification['unsupported']}"
    )
    for issue in verification["issues"]:
        st.write(f"- **{issue['severity']}** ({issue['kind']}): {issue['message']}")
    st.markdown("#### Final response")
    st.markdown(report["final_response"] or "_No final response was produced._")

    left, right = st.columns(2)
    left.download_button(
        "Download CSV report", data=build_csv(report),
        file_name=f"report_{task_id}.csv", mime="text/csv",
    )
    right.download_button(
        "Download PDF report", data=build_pdf(report),
        file_name=f"report_{task_id}.pdf", mime="application/pdf",
    )


# ---------------------------------------------------------------- page 6: execution history

def page_history(svc) -> None:
    st.title("Execution History")
    st.caption("Search, filter, open and delete stored runs.")
    today = date.today()

    first = st.columns([3, 2, 2])
    search = first[0].text_input("Search task text")
    statuses = first[1].multiselect("Status", STATUS_OPTIONS)
    agent = first[2].selectbox("Agent used", ["Any"] + list(AGENT_NAMES))
    second = st.columns(2)
    start_day = second[0].date_input("Created from", value=today - timedelta(days=30))
    end_day = second[1].date_input("Created to", value=today)

    rows = svc.db.list_tasks(
        search=search.strip() or None,
        statuses=statuses or None,
        agent=None if agent == "Any" else agent,
        start=start_day.isoformat(),
        end=(end_day + timedelta(days=1)).isoformat(),
        limit=500,
    )
    st.markdown(f"**{len(rows)}** matching run(s)")
    if not rows:
        st.info("No runs match these filters.")
        return

    st.dataframe(pd.DataFrame([_task_row(task) for task in rows]), hide_index=True)
    labels = _task_labels(rows)
    task = svc.db.get_task(labels[st.selectbox("Open a run", list(labels), key="history_run")])

    with st.expander("Plan and outputs", expanded=True):
        steps = svc.db.get_plan_steps(task["id"])
        if steps:
            st.dataframe(pd.DataFrame([
                {"step": s["step_id"], "agent": s["agent"], "status": s["status"],
                 "attempts": s["attempts"], "duration_s": s["duration_seconds"], "errors": " | ".join(s["errors"])}
                for s in steps
            ]), hide_index=True)
        st.write(task["final_response"] or "No final response stored.")

    report = report_from_db(svc, task["id"])
    left, right = st.columns(2)
    left.download_button(
        "Download CSV report", data=build_csv(report),
        file_name=f"report_{task['id']}.csv", mime="text/csv",
    )
    right.download_button(
        "Download PDF report", data=build_pdf(report),
        file_name=f"report_{task['id']}.pdf", mime="application/pdf",
    )

    with st.expander("Delete this run"):
        confirmed = st.checkbox(
            "I understand this permanently deletes the run, its plan, outputs and logs.",
            key="history_delete_confirm",
        )
        if st.button("Delete run", disabled=not confirmed):
            svc.db.delete_task(task["id"])
            st.success("Run deleted.")


# ---------------------------------------------------------------- page 7: settings

def page_settings(svc) -> None:
    st.title("Settings")
    st.caption("Configuration comes from environment variables or a local .env file. Secret values are never shown.")
    if svc.settings.is_mock:
        st.success("Demo mode is active. Every agent runs its deterministic workflow over the sample documents. No API key is needed.")
    else:
        st.info(f"Live mode uses the {svc.settings.provider} provider.")
    if svc.config_error:
        st.error(f"Invalid configuration (demo defaults are in use): {svc.config_error}")
    if svc.llm_error:
        st.error(svc.llm_error)
    st.json(svc.settings.describe())
    if not svc.settings.is_mock and svc.llm is not None:
        if st.button("Test the LLM connection"):
            ok, message = check_connection(svc.llm)
            if ok:
                st.success(message)
            else:
                st.error(message)

    st.subheader("Execution limits")
    st.table(pd.DataFrame([
        {"limit": "Maximum steps per plan", "value": MAX_STEPS},
        {"limit": "Maximum execution time per run (seconds)", "value": MAX_RUN_SECONDS},
        {"limit": "Maximum verification revisions per run", "value": MAX_REVISIONS},
        {"limit": "Hard ceiling for retries per step", "value": HARD_MAX_RETRIES},
        {"limit": "Maximum task length (characters)", "value": MAX_TASK_CHARS},
    ]))

    st.subheader("Knowledge base")
    st.write(
        f"{len(svc.store)} statements from {len(svc.store.documents)} sample documents: "
        + ", ".join(svc.store.documents)
    )

    st.subheader("Maintenance")
    st.caption(f"Database file: {svc.settings.db_path}")
    confirmed = st.checkbox("I understand this deletes every stored run, plan, output and log.")
    if st.button("Delete all runs", disabled=not confirmed):
        removed = svc.db.delete_all_tasks()
        st.success(f"Deleted {removed} run(s).")
