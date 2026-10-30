import unittest

from tasteai.agent import (
    AgentState,
    MAX_AGENT_STEPS,
    MAX_AGENT_TOOL_CALLS,
    run_bounded_food_flow,
)


class BoundedAgentTests(unittest.TestCase):
    def test_fixed_food_flow_runs_in_order_with_deterministic_ranker(self):
        calls = []
        state = run_bounded_food_flow(
            AgentState(user_id=None, query="spicy chicken"),
            understand=lambda query: calls.append(("understand", query)) or {"query": query},
            retrieve=lambda query: calls.append(("retrieve", query)) or [{"evidence_id": 4}],
            rank=lambda current: calls.append(("rank", current.retrieved_context)) or {"items": ["ranked"]},
            explain=lambda current: calls.append(("explain", current.ranking)) or {"grounded": True},
        )
        self.assertEqual([name for name, _ in calls], ["understand", "retrieve", "rank", "explain"])
        self.assertEqual(len(state.steps), MAX_AGENT_STEPS)
        self.assertEqual(state.tool_calls, 1)
        self.assertLessEqual(state.tool_calls, MAX_AGENT_TOOL_CALLS)
        self.assertEqual(state.final_response["autonomy"], "bounded_fixed_flow")
        self.assertEqual(state.final_response["authority"]["ranking"], "deterministic_ranker")
        self.assertEqual(state.final_response["tool_contracts"][1]["side_effects"], "read_only")
        self.assertEqual(state.final_response["step_count"], MAX_AGENT_STEPS)
        self.assertTrue(state.explanation["grounded"])

    def test_agent_rejects_unbounded_user_query_before_running_callbacks(self):
        called = []
        with self.assertRaisesRegex(ValueError, "at most 500"):
            run_bounded_food_flow(
                AgentState(user_id=None, query="x" * 501),
                understand=lambda query: called.append("understand") or {},
                retrieve=lambda query: called.append("retrieve") or [],
                rank=lambda state: called.append("rank") or {},
                explain=lambda state: called.append("explain") or {},
            )
        self.assertEqual(called, [])

    def test_agent_returns_empty_evidence_for_honest_insufficient_flow(self):
        state = run_bounded_food_flow(
            AgentState(user_id=1, query="dish"),
            understand=lambda _query: {},
            retrieve=lambda _query: [],
            rank=lambda _state: {"recommendations": []},
            explain=lambda state: {"insufficient_evidence": not state.retrieved_context},
        )
        self.assertTrue(state.final_response["explanation"]["insufficient_evidence"])
        self.assertEqual(state.restaurant_candidates, [])


if __name__ == "__main__":
    unittest.main()
