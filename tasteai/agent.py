"""Small bounded state machine for the local food-answer workflow.

The state machine is deterministic. It invokes only injected application
services; it has no shell, filesystem, network, SQL-write, or arbitrary-tool API.
"""

from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4


MAX_AGENT_STEPS = 4
MAX_AGENT_TOOL_CALLS = 2
AGENT_TOOL_CONTRACTS = (
    {"name": "understand_query", "input": "text", "output": "validated_intent", "side_effects": "none"},
    {"name": "retrieve_approved_evidence", "input": "text", "output": "approved_evidence", "side_effects": "read_only"},
)


@dataclass
class AgentState:
    user_id: int | None
    query: str
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    intent: dict[str, Any] | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    restaurant_candidates: list[dict[str, Any]] = field(default_factory=list)
    dish_candidates: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    retrieved_context: list[dict[str, Any]] = field(default_factory=list)
    ranking: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    final_response: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    tool_calls: int = 0


class AgentLimitError(RuntimeError):
    pass


def run_bounded_food_flow(
    state: AgentState,
    *,
    understand: Callable[[str], dict[str, Any]],
    retrieve: Callable[[str], list[dict[str, Any]]],
    rank: Callable[[AgentState], dict[str, Any]],
    explain: Callable[[AgentState], dict[str, Any]],
) -> AgentState:
    """Execute a fixed four-step flow with at most two retrieval/tool calls."""
    if not isinstance(state.query, str) or len(state.query) > 500:
        raise ValueError("Agent query must contain at most 500 characters.")
    if MAX_AGENT_STEPS != 4 or MAX_AGENT_TOOL_CALLS != 2:
        raise AgentLimitError("Agent safety limits are invalid.")
    state.intent = understand(state.query)
    state.steps.append("understand_query")
    state.retrieved_context = retrieve(state.query)
    state.tool_calls += 1
    state.steps.append("retrieve_approved_evidence")
    state.ranking = rank(state)
    state.steps.append("deterministic_rank")
    state.explanation = explain(state)
    state.steps.append("grounded_explanation")
    if len(state.steps) > MAX_AGENT_STEPS or state.tool_calls > MAX_AGENT_TOOL_CALLS:
        raise AgentLimitError("Agent execution exceeded its configured limit.")
    state.final_response = {
        "trace_id": state.trace_id,
        "step_count": len(state.steps),
        "tool_call_count": state.tool_calls,
        "steps": list(state.steps),
        "explanation": state.explanation,
        "authority": {
            "intent": "deterministic_parser_with_optional_llm_hints",
            "retrieval": "approved_evidence_only",
            "ranking": "deterministic_ranker",
            "response": "grounded_renderer",
        },
        "tool_contracts": list(AGENT_TOOL_CONTRACTS),
        "autonomy": "bounded_fixed_flow",
    }
    return state
