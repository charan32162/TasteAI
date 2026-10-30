import os
import unittest

from tasteai.location_providers import configured_restaurant_provider


def live_test_enabled() -> bool:
    return (
        os.getenv("GOOGLE_PLACES_LIVE_TEST", "").strip().lower() == "true"
        and os.getenv("TASTEAI_ALLOW_PAID_PLACES_LOOKUPS", "").strip().lower() == "true"
        and os.getenv("TASTEAI_RESTAURANT_PROVIDER", os.getenv("RESTAURANT_PROVIDER", "demo")).strip().lower() == "google"
        and configured_restaurant_provider().enabled
    )


@unittest.skipUnless(
    live_test_enabled(),
    "Set the live-test flag, Google provider, paid-use opt-in, API key, and HTTPS policy URLs to enable this billable smoke test.",
)
class LiveGooglePlacesSmokeTest(unittest.TestCase):
    def test_one_minimal_text_search_normalizes_a_place(self):
        provider = configured_restaurant_provider()
        places = provider.search_restaurants("restaurant in Vijayawada", limit=1)

        self.assertLessEqual(len(places), 1)
        if places:
            self.assertTrue(places[0].provider_id)
            self.assertTrue(places[0].name)
            self.assertEqual(places[0].source_label, "Google Maps")


if __name__ == "__main__":
    unittest.main()
