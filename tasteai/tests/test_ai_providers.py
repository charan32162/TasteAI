import json
import os
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from tasteai.ai_providers import (
    AIProviderError,
    HashEmbeddingProvider,
    GroundedAnswer,
    OpenAIEmbeddingProvider,
    OpenAIResponsesProvider,
    QueryIntent,
    configured_embedding_provider,
    configured_llm_provider,
)


class AIProviderTests(unittest.TestCase):
    def test_external_providers_are_disabled_by_default(self):
        with patch.dict(os.environ, {
            "TASTEAI_ALLOW_LLM": "false",
            "TASTEAI_ALLOW_EXTERNAL_EMBEDDINGS": "false",
            "OPENAI_API_KEY": "test-only-never-a-real-key",
        }):
            self.assertIsNone(configured_llm_provider())
            embedder = configured_embedding_provider()
            self.assertIsInstance(embedder, HashEmbeddingProvider)

    def test_structured_output_is_validated_and_request_is_non_stored(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={
                "output_text": json.dumps({
                    "dish_phrase": "chicken biryani",
                    "cuisine": "Indian",
                    "protein": "chicken",
                    "soft_preferences": ["spicy"],
                }),
            })

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = OpenAIResponsesProvider(
            api_key="test-only-never-a-real-key",
            model="test-model",
            timeout_seconds=2,
            max_retries=0,
            client=client,
        )
        result = provider.generate_structured(
            prompt_id="query_intent_v1",
            user_input="I want spicy chicken biryani",
            schema=QueryIntent,
            max_output_tokens=128,
        )
        payload = json.loads(requests[0].content)
        self.assertEqual(result.dish_phrase, "chicken biryani")
        self.assertFalse(payload["store"])
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertEqual(requests[0].url.host, "api.openai.com")
        self.assertNotIn("test-only-never-a-real-key", repr(payload))
        client.close()

    def test_grounded_answer_output_contains_only_typed_claim_selectors(self):
        requests = []
        client = httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(
            200, json={"output_text": json.dumps({"claims": [{"evidence_id": 7, "claim_type": "listed_price"}]})}
        )))
        provider = OpenAIResponsesProvider(
            api_key="test-only-never-a-real-key", model="test-model", timeout_seconds=2,
            max_retries=0, client=client,
        )
        try:
            result = provider.generate_structured(
                prompt_id="grounded_answer_v1", user_input='{"approved_evidence_data": []}',
                schema=GroundedAnswer, max_output_tokens=128,
            )
            self.assertEqual(result.claims[0].evidence_id, 7)
            schema = json.loads(requests[0].content)["text"]["format"]["schema"]
            self.assertIn("claims", schema["properties"])
            self.assertNotIn("answer", schema["properties"])
        finally:
            provider.close()
        with self.assertRaises(ValidationError):
            GroundedAnswer.model_validate({"claims": [{"evidence_id": 7, "claim_type": "spice_level"}]})

    def test_malformed_structured_output_is_rejected_without_echoing_output(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"output_text": '{"dish_phrase": 9}'})
        ))
        provider = OpenAIResponsesProvider(
            api_key="test-only-never-a-real-key", model="test-model", timeout_seconds=2,
            max_retries=0, client=client,
        )
        with self.assertRaisesRegex(AIProviderError, "failed validation") as caught:
            provider.generate_structured(
                prompt_id="query_intent_v1", user_input="test", schema=QueryIntent, max_output_tokens=128
            )
        self.assertNotIn('{"dish_phrase": 9}', str(caught.exception))
        client.close()

    def test_configured_output_limit_is_a_hard_request_cap(self):
        requests = []
        client = httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(
            200, json={"output_text": "ok"}
        )))
        provider = OpenAIResponsesProvider(
            api_key="test-only-never-a-real-key", model="test-model", timeout_seconds=2,
            max_retries=0, max_output_tokens=96, client=client,
        )
        try:
            provider.generate(prompt_id="query_intent_v1", user_input="hello", max_output_tokens=512)
            self.assertEqual(json.loads(requests[0].content)["max_output_tokens"], 96)
        finally:
            provider.close()

    def test_provider_errors_are_sanitized_and_do_not_retry_nontransient_failure(self):
        calls = []
        def handler(request):
            calls.append(1)
            return httpx.Response(401, text="SECRET PROVIDER BODY")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = OpenAIResponsesProvider(
            api_key="test-only-never-a-real-key", model="test-model", timeout_seconds=2,
            max_retries=2, client=client,
        )
        with self.assertRaises(AIProviderError) as caught:
            provider.generate(prompt_id="query_intent_v1", user_input="hello", max_output_tokens=128)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("SECRET PROVIDER BODY", str(caught.exception))
        client.close()

    def test_hash_embeddings_are_stable_normalized_and_bounded(self):
        provider = HashEmbeddingProvider()
        first = provider.embed_text("Chicken biryani under 300")
        second = provider.embed_text("Chicken biryani under 300")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 256)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0)
        with self.assertRaises(AIProviderError):
            provider.embed_documents(["x"] * 65)

    def test_openai_embedding_adapter_sorts_and_validates_batch_vectors(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ],
        })))
        provider = OpenAIEmbeddingProvider(api_key="test-only-never-a-real-key", model="test-model", client=client)
        self.assertEqual(provider.embed_documents(["a", "b"]), [[1.0, 0.0], [0.0, 1.0]])
        client.close()


if __name__ == "__main__":
    unittest.main()
