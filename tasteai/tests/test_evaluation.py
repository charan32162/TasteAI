import unittest

from tasteai.evaluation.run import _top_attributes_check, evaluate, evaluate_grounded_claim_cases, evaluate_rag_retrieval_cases


class EvaluationTests(unittest.TestCase):
    def test_missing_requested_catalog_attributes_do_not_pass(self):
        self.assertFalse(_top_attributes_check({"spice_min": 6}, [{"attributes": {}}]))

    def test_synthetic_discovery_corpus_meets_regression_thresholds(self):
        result = evaluate()
        self.assertEqual(result["case_count"], 110)
        self.assertEqual(result["failures"], [])
        for metric in (
            "intent_expectation_accuracy",
            "hard_constraint_satisfaction",
            "top_match_attribute_rate",
            "explanation_grounding_rate",
        ):
            self.assertEqual(result["metrics"][metric], 1.0)

    def test_synthetic_grounded_claim_cases_reject_unknown_and_injected_claims(self):
        result = evaluate_grounded_claim_cases()
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["claim_status_accuracy"], 1.0)
        self.assertEqual(result["supported_claim_citation_coverage"], 1.0)
        self.assertEqual(result["unsupported_claim_output_rate"], 0.0)

    def test_synthetic_rag_retrieval_cases_preserve_hard_filters(self):
        result = evaluate_rag_retrieval_cases()
        self.assertEqual(result["checks_passed"], result["checks_total"])
        self.assertEqual(result["failures"], [])


if __name__ == "__main__":
    unittest.main()
