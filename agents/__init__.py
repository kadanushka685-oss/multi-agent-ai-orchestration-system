"""Specialised agents and the registry the orchestrator uses to create them."""
from __future__ import annotations

from agents.analysis_agent import AnalysisAgent
from agents.planning_agent import PlanningAgent
from agents.research_agent import ResearchAgent
from agents.verification_agent import VerificationAgent
from agents.writing_agent import WritingAgent
from config.settings import AGENT_NAMES
from orchestration.context import TaskContext
from orchestration.models import AgentOutput, PlanStep

SELF_TEST_TASK = "Compare battery electric vehicle adoption in Europe and Asia and write a brief."


def build_agents(store, llm=None) -> dict:
    """One instance of every agent, keyed by name."""
    return {
        "planning": PlanningAgent(store, llm),
        "research": ResearchAgent(store, llm),
        "analysis": AnalysisAgent(llm),
        "writing": WritingAgent(llm),
        "verification": VerificationAgent(),
    }


def prepare_demo_context(store) -> TaskContext:
    """Run research, analysis and writing once so that later agents have real inputs to check."""
    context = TaskContext(task=SELF_TEST_TASK)
    agents = build_agents(store)
    steps = [
        PlanStep(id="S1", agent="research", title="Self-test research", instruction="Collect sources.", focus="battery"),
        PlanStep(id="S2", agent="analysis", title="Self-test analysis", instruction="Analyse.", depends_on=["S1"]),
        PlanStep(id="S3", agent="writing", title="Self-test writing", instruction="Write.", depends_on=["S2"]),
    ]
    for step in steps:
        agents[step.agent].handle(step, context, [])
    return context


def self_test(agent_name: str, store) -> AgentOutput:
    """Run one agent on a prepared, deterministic context and return its output."""
    if agent_name not in AGENT_NAMES:
        raise KeyError(agent_name)
    context = prepare_demo_context(store)
    agent = build_agents(store)[agent_name]
    step = PlanStep(id="S1", agent=agent_name, title=f"Self-test {agent.display_name}", instruction="Self-test.")
    return agent.handle(step, context, [])
