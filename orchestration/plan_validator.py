"""Structural checks on a plan before it is accepted for execution."""
from __future__ import annotations

from orchestration.models import PlanStep


def validate_plan(steps: list[PlanStep], enabled_agents: set[str], max_steps: int) -> list[str]:
    """Return a list of problems. An empty list means the plan may run."""
    errors: list[str] = []
    if not steps:
        errors.append("The plan has no steps.")
    if len(steps) > max_steps:
        errors.append(f"The plan has {len(steps)} steps; the limit is {max_steps}.")
    ids = [step.id for step in steps]
    if len(set(ids)) != len(ids):
        errors.append("Step ids must be unique.")
    earlier: set[str] = set()
    for step in steps:
        if step.agent not in enabled_agents:
            errors.append(f"{step.id} is assigned to the disabled or unselected agent '{step.agent}'.")
        for dependency in step.depends_on:
            if dependency == step.id:
                errors.append(f"{step.id} depends on itself.")
            elif dependency not in earlier:
                errors.append(f"{step.id} depends on {dependency}, which must appear earlier in the plan.")
        earlier.add(step.id)
    if not any(step.agent == "writing" for step in steps):
        errors.append("The plan must include a writing step to produce a final response.")
    return errors
