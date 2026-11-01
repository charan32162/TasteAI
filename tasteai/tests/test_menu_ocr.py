import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

import httpx

from tasteai.menu_photos import (
    MenuPhotoError,
    NoMenuPhotoExtractor,
    OpenAIMenuPhotoExtractor,
    configured_menu_photo_extractor,
)


class MenuOCRTests(unittest.TestCase):
    def test_external_ocr_is_closed_by_default_and_explicitly_configured(self):
        with patch.dict(os.environ, {"TASTEAI_ALLOW_EXTERNAL_OCR": "false", "OPENAI_API_KEY": "test-only-key"}):
            self.assertIsInstance(configured_menu_photo_extractor(), NoMenuPhotoExtractor)
        with patch.dict(os.environ, {
            "TASTEAI_ALLOW_EXTERNAL_OCR": "true", "TASTEAI_OCR_PROVIDER": "openai",
            "TASTEAI_OCR_MODEL": "test-model", "OPENAI_API_KEY": "test-only-key",
        }):
            provider = configured_menu_photo_extractor()
            self.assertIsInstance(provider, OpenAIMenuPhotoExtractor)
            provider.close()

    def test_vision_adapter_uses_strict_structured_output_and_returns_unverified_candidates(self):
        captured = []
        def handler(request):
            captured.append(request)
            return httpx.Response(200, json={"output_text": json.dumps({"items": [{
                "item_name": "Chicken Dum Biryani", "variant": "Full", "price": "320.00",
                "currency": "INR", "menu_section": "Rice", "description": "Serves two",
                "confidence": 0.93, "page_number": 1, "bounding_box": [0.1, 0.2, 0.8, 0.5],
            }]})})
        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = OpenAIMenuPhotoExtractor(api_key="test-only-key", model="test-model", client=client)
        try:
            items = provider.extract(b"mock image bytes", "image/png")
        finally:
            provider.close()
        payload = json.loads(captured[0].content)
        item = items[0]
        self.assertEqual(item.item_name, "Chicken Dum Biryani")
        self.assertEqual(item.price, Decimal("320.00"))
        self.assertEqual(item.currency, "INR")
        self.assertEqual(item.menu_section, "Rice")
        self.assertEqual(item.page_number, 1)
        self.assertEqual(item.bounding_box, (0.1, 0.2, 0.8, 0.5))
        self.assertEqual(item.availability, "menu_listed")
        self.assertIn("pending_human_review", item.confidence_basis)
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertEqual(payload["input"][0]["content"][1]["type"], "input_image")
        self.assertEqual(payload["text"]["format"]["schema"]["required"], ["items"])

    def test_adapter_rejects_invalid_image_type_and_invalid_bounding_box(self):
        provider = OpenAIMenuPhotoExtractor(
            api_key="test-only-key", model="test-model", client=httpx.Client(transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"output_text": json.dumps({"items": [{
                    "item_name": "Dish", "variant": None, "price": None, "currency": None,
                    "menu_section": None, "description": None, "confidence": None,
                    "page_number": 1, "bounding_box": [0.8, 0.2, 0.1, 0.5],
                }]})})
            )),
        )
        try:
            with self.assertRaises(MenuPhotoError):
                provider.extract(b"image", "text/plain")
            with self.assertRaises(MenuPhotoError):
                provider.extract(b"image", "image/png")
        finally:
            provider.close()

    def test_provider_failure_does_not_echo_upstream_body_or_key(self):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda _request: httpx.Response(401, text="test-only-key provider secret")
        ))
        provider = OpenAIMenuPhotoExtractor(api_key="test-only-key", model="test-model", client=client)
        try:
            with self.assertRaises(MenuPhotoError) as caught:
                provider.extract(b"mock image", "image/png")
        finally:
            provider.close()
        self.assertNotIn("test-only-key", str(caught.exception))
        self.assertNotIn("provider secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
