"""Small bounded state machine for the local food-answer workflow.

The state machine is deterministic. It invokes only injected application
services; it has no shell, filesystem, network, SQL-write, or arbitrary-tool API.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
import json
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_AGENT_STEPS = 4
MAX_AGENT_TOOL_CALLS = 2
MAX_TOOL_RESULT_ITEMS = 25
MAX_TOOL_RESULT_CHARS = 12000
AGENT_TOOL_CONTRACTS = (
    {"name": "understand_query", "input": "text", "output": "validated_intent", "side_effects": "none"},
    {"name": "retrieve_approved_evidence", "input": "text", "output": "approved_evidence", "side_effects": "read_only"},
)


class ToolQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)


class ToolResultOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_TOOL_RESULT_ITEMS)


class ToolExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]
    authorization: str = "agent_read"
    timeout_seconds: float = 1.0


class ToolRegistry:
    """Allowlisted, read-only internal tools; never executes arbitrary code supplied by callers."""

    def __init__(self, *, max_calls: int = MAX_AGENT_TOOL_CALLS, overall_timeout_seconds: float = 4.0):
        if max_calls < 1 or overall_timeout_seconds <= 0:
            raise ValueError("Tool limits must be positive.")
        self.max_calls = max_calls
        self.overall_timeout_seconds = overall_timeout_seconds
        self._specs: dict[str, ToolSpec] = {}
        self._calls = 0
        self._started = monotonic()
        self.audit: list[dict[str, Any]] = []
        self._seen: set[tuple[str, str]] = set()

    @property
    def calls(self) -> int:
        return self._calls

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or spec.name in self._specs or spec.authorization != "agent_read":
            raise ValueError("Only unique read-only agent tools may be registered.")
        if not 0.05 <= spec.timeout_seconds <= 5:
            raise ValueError("Tool timeout is outside the safe range.")
        self._specs[spec.name] = spec

    def execute(self, name: str, payload: dict[str, Any]) -> BaseModel:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolExecutionError("Unknown agent tool.")
        if self._calls >= self.max_calls:
            self._record(name, "limit_exceeded")
            raise ToolExecutionError("Agent tool call limit reached.")
        if monotonic() - self._started > self.overall_timeout_seconds:
            self._record(name, "overall_timeout")
            raise ToolExecutionError("Agent tool time budget exceeded.")
        try:
            request = spec.input_model.model_validate(payload)
        except ValidationError as error:
            self._record(name, "invalid_input")
            raise ToolExecutionError("Agent tool input is invalid.") from error
        fingerprint = (name, json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))
        if fingerprint in self._seen:
            self._record(name, "duplicate_call")
            raise ToolExecutionError("Repeated agent tool call blocked.")
        self._seen.add(fingerprint)
        self._calls += 1
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(spec.handler, request)
                result = future.result(timeout=min(spec.timeout_seconds, self.overall_timeout_seconds))
            validated = spec.output_model.model_validate(result)
            if len(validated.model_dump_json()) > MAX_TOOL_RESULT_CHARS:
                raise ToolExecutionError("Agent tool result is too large.")
            self._record(name, "ok")
            return validated
        except FutureTimeoutError as error:
            self._record(name, "timeout")
            raise ToolExecutionError("Agent tool timed out.") from error
        except ToolExecutionError:
            self._record(name, "rejected_result")
            raise
        except (ValidationError, TypeError, ValueError, RuntimeError) as error:
            self._record(name, "failed")
            raise ToolExecutionError("Agent tool failed safely.") from error

    def _record(self, name: str, status: str) -> None:
        self.audit.append({
            "tool_call_id": f"tool-{len(self.audit) + 1}",
            "tool": name,
            "status": status,
            "call_count": self._calls,
        })


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
    audit: list[dict[str, Any]] = field(default_factory=list)
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
    registry: ToolRegistry | None = None,
) -> AgentState:
    """Execute a fixed four-step flow with at most two retrieval/tool calls."""
    if not isinstance(state.query, str) or len(state.query) > 500:
        raise ValueError("Agent query must contain at most 500 characters.")
    if MAX_AGENT_STEPS != 4 or MAX_AGENT_TOOL_CALLS != 2:
        raise AgentLimitError("Agent safety limits are invalid.")
    if registry is None:
        state.intent = understand(state.query)
    else:
        result = registry.execute("understand_query", {"query": state.query})
        state.intent = result.items[0] if result.items else {}
    state.steps.append("understand_query")
    state.audit.append({"step": "understand_query", "status": "ok", "tool_call": False})
    if registry is None:
        state.retrieved_context = retrieve(state.query)
        state.tool_calls += 1
    else:
        result = registry.execute("retrieve_approved_evidence", {"query": state.query})
        state.retrieved_context = result.items
        state.tool_calls = registry.calls
    state.steps.append("retrieve_approved_evidence")
    state.audit.append({"step": "retrieve_approved_evidence", "status": "ok", "tool_call": True, "read_only": True})
    state.ranking = rank(state)
    state.steps.append("deterministic_rank")
    state.audit.append({"step": "deterministic_rank", "status": "ok", "tool_call": False})
    state.explanation = explain(state)
    state.steps.append("grounded_explanation")
    state.audit.append({"step": "grounded_explanation", "status": "ok", "tool_call": False})
    if len(state.steps) > MAX_AGENT_STEPS or state.tool_calls > MAX_AGENT_TOOL_CALLS:
        raise AgentLimitError("Agent execution exceeded its configured limit.")
    state.final_response = {
        "trace_id": state.trace_id,
        "agent_run_id": state.trace_id,
        "step_count": len(state.steps),
        "tool_call_count": state.tool_calls,
        "steps": list(state.steps),
        "audit": list(state.audit),
        "limits": {"max_steps": MAX_AGENT_STEPS, "max_tool_calls": MAX_AGENT_TOOL_CALLS},
        "explanation": state.explanation,
        "authority": {
            "intent": "deterministic_parser_with_optional_llm_hints",
            "retrieval": "approved_evidence_only",
            "ranking": "deterministic_ranker",
            "response": "grounded_renderer",
        },
        "tool_contracts": list(AGENT_TOOL_CONTRACTS),
        "tool_audit": list(registry.audit) if registry is not None else [],
        "autonomy": "bounded_fixed_flow",
    }
    return state
