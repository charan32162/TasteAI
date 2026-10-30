import json
import unittest
from unittest.mock import patch

import httpx

from tasteai.location_providers import (
    DemoRestaurantSearchProvider,
    GeoPoint,
    GooglePlacesProvider,
    PLACE_LOCATION_FIELDS,
    PLACE_SEARCH_FIELDS,
    PlaceLookupRateLimit,
    RestaurantProviderError,
    configured_restaurant_provider,
    restaurant_provider_status,
)
from tasteai.providers import DemoMenuProvider, FictionalDemoProvider, UnknownMenuProvider


class GooglePlacesProviderTests(unittest.TestCase):
    def test_location_resolution_uses_bounded_text_search_and_only_area_results(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={"places": [
                    {
                        "displayName": {"text": "Chintalapudi"},
                        "location": {"latitude": 16.12, "longitude": 81.13},
                        "types": ["locality", "political"],
                    },
                    {
                        "displayName": {"text": "Example Restaurant"},
                        "location": {"latitude": 16.12, "longitude": 81.13},
                        "types": ["restaurant"],
                    },
                ]},
                request=request,
            )

        provider = GooglePlacesProvider("unit-test-key", httpx.MockTransport(respond))
        resolved = provider.resolve_location(" Chintalapudi ")

        self.assertEqual(resolved.display_name, "Chintalapudi")
        self.assertEqual((resolved.latitude, resolved.longitude), (16.12, 81.13))
        self.assertEqual(resolved.source, "Google Places")
        self.assertEqual(requests[0].url.path, "/v1/places:searchText")
        self.assertEqual(requests[0].headers["X-Goog-FieldMask"], PLACE_LOCATION_FIELDS)
        self.assertEqual(json.loads(requests[0].content), {"textQuery": "Chintalapudi", "pageSize": 5})

    def test_nearby_search_uses_server_key_field_mask_and_transient_place_data(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "places": [{
                        "id": "place-123",
                        "displayName": {"text": "Sample Place"},
                        "formattedAddress": "1 Sample Road",
                        "location": {"latitude": 12.0, "longitude": 77.0},
                        "rating": 4.5,
                        "userRatingCount": 21,
                        "priceLevel": "PRICE_LEVEL_MODERATE",
                        "googleMapsUri": "https://maps.google.com/?cid=123",
                        "websiteUri": "https://example.test",
                    }]
                },
                request=request,
            )

        provider = GooglePlacesProvider(
            "unit-test-key",
            transport=httpx.MockTransport(respond),
        )
        places = provider.search_nearby(GeoPoint(12.0, 77.0), 1500)

        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].provider_id, "place-123")
        self.assertEqual(places[0].distance_km, 0)
        self.assertEqual(places[0].source_label, "Google Maps")
        self.assertEqual(requests[0].headers["X-Goog-Api-Key"], "unit-test-key")
        self.assertEqual(requests[0].headers["X-Goog-FieldMask"], PLACE_SEARCH_FIELDS)
        self.assertNotIn("rating", requests[0].headers["X-Goog-FieldMask"])
        self.assertNotIn("priceLevel", requests[0].headers["X-Goog-FieldMask"])
        self.assertNotIn("reviews", requests[0].headers["X-Goog-FieldMask"])
        self.assertEqual(json.loads(requests[0].content)["maxResultCount"], 10)
        self.assertEqual(requests[0].url.query, b"")
        self.assertEqual(json.loads(requests[0].content)["locationRestriction"]["circle"]["radius"], 1500)

    def test_review_results_keep_author_and_source_attribution(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "googleMapsUri": "https://maps.google.com/?cid=123",
                    "reviews": [{
                        "rating": 4,
                        "text": {"text": "The sample dish was crisp."},
                        "originalText": {"text": "The original sample dish was crisp."},
                        "relativePublishTimeDescription": "2 months ago",
                        "authorAttribution": {
                            "displayName": "Reviewer",
                            "uri": "https://example.test/reviewer",
                            "photoUri": "https://example.test/reviewer.jpg",
                        },
                    }],
                },
                request=request,
            )

        provider = GooglePlacesProvider("unit-test-key", httpx.MockTransport(respond))
        reviews = provider.get_reviews("place-123")

        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].text, "The sample dish was crisp.")
        self.assertEqual(reviews[0].original_text, "The original sample dish was crisp.")
        self.assertEqual(reviews[0].author_name, "Reviewer")
        self.assertEqual(reviews[0].author_photo_url, "https://example.test/reviewer.jpg")
        self.assertEqual(reviews[0].source_url, "https://maps.google.com/?cid=123")
        self.assertEqual(reviews[0].source_label, "Google Maps")
        self.assertEqual(requests[0].headers["X-Goog-FieldMask"], "googleMapsUri,reviews")

    def test_photo_metadata_preserves_available_author_attribution_links(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "photos": [{
                        "name": "places/place-123/photos/photo-1",
                        "authorAttributions": [{
                            "displayName": "Photographer",
                            "uri": "https://example.test/photographer",
                            "photoUri": "https://example.test/photographer.jpg",
                        }],
                    }]
                },
                request=request,
            )

        provider = GooglePlacesProvider("unit-test-key", httpx.MockTransport(respond))
        photos = provider.get_photos("place-123")
        self.assertEqual(
            photos[0].author_attributions,
            (("Photographer", "https://example.test/photographer", "https://example.test/photographer.jpg"),),
        )
        self.assertEqual(requests[0].headers["X-Goog-FieldMask"], "photos")

    def test_unsafe_provider_urls_are_omitted(self):
        def respond(request):
            return httpx.Response(
                200,
                json={
                    "places": [{
                        "id": "place-unsafe",
                        "displayName": {"text": "Sample Place"},
                        "googleMapsUri": "javascript:alert(1)",
                        "websiteUri": "http://example.test",
                    }]
                },
                request=request,
            )

        place = GooglePlacesProvider(
            "unit-test-key",
            httpx.MockTransport(respond),
        ).search_text("restaurants in Sample City")[0]
        self.assertIsNone(place.maps_url)
        self.assertIsNone(place.website_url)

    def test_maps_links_are_restricted_to_google_maps_hosts(self):
        for url in (
            "https://attacker.example/maps?id=123",
            "https://user@maps.google.com/maps?id=123",
            "https://maps.google.com:444/maps?id=123",
        ):
            with self.subTest(url=url):
                def respond(request):
                    return httpx.Response(
                        200,
                        json={
                            "places": [{
                                "id": "place-unsafe-map",
                                "displayName": {"text": "Sample Place"},
                                "googleMapsUri": url,
                            }]
                        },
                        request=request,
                    )

                place = GooglePlacesProvider(
                    "unit-test-key",
                    httpx.MockTransport(respond),
                ).search_restaurants("restaurants in Sample City")[0]
                self.assertIsNone(place.maps_url)

    def test_explicit_demo_and_unknown_menu_providers_preserve_unknown_state(self):
        demo = DemoMenuProvider(FictionalDemoProvider()).get_menu("demo-kitchen-01")
        self.assertEqual(demo.status, "demo")
        self.assertEqual(demo.source_label, "fictional demo")
        self.assertTrue(demo.items)

        unknown = UnknownMenuProvider().get_menu("real-place-123")
        self.assertEqual(unknown.status, "unknown")
        self.assertIsNone(unknown.source_label)
        self.assertEqual(unknown.items, ())

    def test_demo_restaurant_provider_never_invents_places(self):
        demo = DemoRestaurantSearchProvider("disabled")
        self.assertFalse(demo.enabled)
        self.assertEqual(demo.search_restaurants("restaurants in Demo City"), [])
        self.assertEqual(demo.nearby_restaurants(GeoPoint(0, 0), 1000), [])
        self.assertIsNone(demo.get_restaurant_details("anything"))

    def test_provider_errors_do_not_expose_upstream_body_or_secret(self):
        def respond(request):
            return httpx.Response(
                403,
                text="sensitive provider diagnostic",
                request=request,
            )

        provider = GooglePlacesProvider(
            "unit-test-key",
            httpx.MockTransport(respond),
        )
        with self.assertRaises(RestaurantProviderError) as raised:
            provider.search_text("restaurants in Sample City")
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("unit-test-key", str(raised.exception))
        self.assertEqual(raised.exception.category, "configuration")

    def test_quota_and_malformed_responses_have_safe_error_categories(self):
        for response, expected_category in (
            (httpx.Response(429, text="private quota detail"), "quota"),
            (httpx.Response(200, json={"places": "not-a-list"}), "malformed_response"),
        ):
            def respond(request, response=response):
                return httpx.Response(
                    response.status_code,
                    text=response.text,
                    json=response.json() if response.status_code == 200 else None,
                    request=request,
                )

            with self.subTest(category=expected_category):
                provider = GooglePlacesProvider(
                    "unit-test-key",
                    httpx.MockTransport(respond),
                )
                with self.assertRaises(RestaurantProviderError) as raised:
                    provider.search_restaurants("restaurants in Demo City", limit=1)
                self.assertEqual(raised.exception.category, expected_category)
                self.assertNotIn("private quota detail", str(raised.exception))

    def test_provider_timeout_is_sanitized_and_categorized(self):
        def timeout(request):
            raise httpx.ReadTimeout("sensitive request details", request=request)

        provider = GooglePlacesProvider(
            "unit-test-key",
            httpx.MockTransport(timeout),
        )
        with self.assertRaises(RestaurantProviderError) as raised:
            provider.search_restaurants("restaurants in Demo City")
        self.assertEqual(raised.exception.category, "timeout")
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("unit-test-key", str(raised.exception))

    def test_place_details_use_a_separate_minimal_field_mask(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "id": "place-123",
                    "displayName": {"text": "Sample Place"},
                    "formattedAddress": "1 Sample Road",
                    "location": {"latitude": 12.0, "longitude": 77.0},
                    "primaryType": "restaurant",
                    "types": ["restaurant", "food"],
                    "rating": 4.6,
                    "userRatingCount": 321,
                    "priceLevel": "PRICE_LEVEL_MODERATE",
                },
                request=request,
            )

        provider = GooglePlacesProvider("unit-test-key", httpx.MockTransport(respond))
        place = provider.get_restaurant_details("place-123")
        self.assertEqual(place.name, "Sample Place")
        self.assertEqual(place.types, ("restaurant", "food"))
        self.assertEqual(place.rating, 4.6)
        self.assertEqual(place.rating_count, 321)
        self.assertEqual(place.price_level, "PRICE_LEVEL_MODERATE")
        self.assertIn("rating", requests[0].headers["X-Goog-FieldMask"])
        self.assertIn("userRatingCount", requests[0].headers["X-Goog-FieldMask"])
        self.assertIn("priceLevel", requests[0].headers["X-Goog-FieldMask"])
        self.assertNotIn("reviews", requests[0].headers["X-Goog-FieldMask"])
        self.assertEqual(requests[0].url.path, "/v1/places/place-123")

    def test_result_limit_is_bounded(self):
        provider = GooglePlacesProvider(
            "unit-test-key",
            httpx.MockTransport(lambda request: httpx.Response(200, json={"places": []}, request=request)),
        )
        with self.assertRaises(ValueError):
            provider.search_restaurants("restaurants in Demo City", limit=11)

    def test_search_deduplicates_provider_places_and_keeps_missing_signals_unknown(self):
        def respond(request):
            item = {
                "id": "place-123",
                "displayName": {"text": "Sample Place"},
            }
            return httpx.Response(200, json={"places": [item, item]}, request=request)

        places = GooglePlacesProvider(
            "unit-test-key",
            httpx.MockTransport(respond),
        ).search_restaurants("restaurants in Sample City")
        self.assertEqual(len(places), 1)
        self.assertIsNone(places[0].address)
        self.assertIsNone(places[0].latitude)
        self.assertIsNone(places[0].rating)
        self.assertIsNone(places[0].price_level)

    def test_lookup_rate_limit_enforces_per_client_and_global_caps(self):
        now = [100.0]
        limiter = PlaceLookupRateLimit(
            requests_per_hour=2,
            client_interval_seconds=2,
            clock=lambda: now[0],
        )
        self.assertIsNone(limiter.check("client-a"))
        self.assertEqual(limiter.check("client-a"), 2)
        now[0] += 2
        self.assertIsNone(limiter.check("client-a"))
        self.assertEqual(limiter.check("client-b"), 3598)
        now[0] += 3600
        self.assertIsNone(limiter.check("client-b"))

    def test_provider_selection_is_explicit_and_credentials_are_not_reported(self):
        with patch.dict("os.environ", {}, clear=True):
            status = restaurant_provider_status()
            self.assertEqual(status["provider"], "fictional_demo")
            self.assertFalse(status["places_enabled"])

        with patch.dict(
            "os.environ",
            {
                "TASTEAI_RESTAURANT_PROVIDER": "google",
                "GOOGLE_PLACES_API_KEY": "unit-test-key",
                "TASTEAI_ALLOW_PAID_PLACES_LOOKUPS": "true",
                "TASTEAI_TERMS_URL": "https://example.test/terms",
                "TASTEAI_PRIVACY_URL": "https://example.test/privacy",
            },
            clear=True,
        ):
            status = restaurant_provider_status()
            provider = configured_restaurant_provider()
            self.assertEqual(status["provider"], "google_places")
            self.assertTrue(status["places_enabled"])
            self.assertNotIn("unit-test-key", str(status))
            self.assertEqual(provider.name, "google_places")

    def test_unprefixed_provider_environment_setting_is_supported(self):
        with patch.dict("os.environ", {"RESTAURANT_PROVIDER": "google"}, clear=True):
            status = restaurant_provider_status()
        self.assertEqual(status["selected_provider"], "google")
        self.assertFalse(status["places_enabled"])
        self.assertIn("GOOGLE_PLACES_API_KEY", status["reason"])

    def test_openstreetmap_requires_a_descriptive_user_agent_and_is_optional(self):
        with patch.dict(
            "os.environ",
            {"TASTEAI_RESTAURANT_PROVIDER": "osm"},
            clear=True,
        ):
            status = restaurant_provider_status()
        self.assertEqual(status["selected_provider"], "osm")
        self.assertFalse(status["places_enabled"])
        self.assertIn("TASTEAI_OSM_USER_AGENT", status["reason"])

        with patch.dict(
            "os.environ",
            {
                "TASTEAI_RESTAURANT_PROVIDER": "osm",
                "TASTEAI_OSM_USER_AGENT": "TasteAI test client (contact: test@example.invalid)",
            },
            clear=True,
        ):
            status = restaurant_provider_status()
            provider = configured_restaurant_provider()
        self.assertEqual(status["provider"], "openstreetmap")
        self.assertTrue(status["places_enabled"])
        self.assertEqual(provider.name, "openstreetmap")

    def test_openstreetmap_invalid_custom_endpoint_fails_closed(self):
        with patch.dict(
            "os.environ",
            {
                "TASTEAI_RESTAURANT_PROVIDER": "osm",
                "TASTEAI_OSM_USER_AGENT": "TasteAI test client (contact: test@example.invalid)",
                "TASTEAI_OSM_OVERPASS_URL": "http://127.0.0.1:8080/interpreter",
            },
            clear=True,
        ):
            status = restaurant_provider_status()
        self.assertFalse(status["places_enabled"])
        self.assertIn("endpoint configuration is invalid", status["reason"])
    def test_google_provider_requires_terms_and_privacy_urls(self):
        with patch.dict(
            "os.environ",
            {
                "TASTEAI_RESTAURANT_PROVIDER": "google",
                "GOOGLE_PLACES_API_KEY": "unit-test-key",
                "TASTEAI_ALLOW_PAID_PLACES_LOOKUPS": "true",
            },
            clear=True,
        ):
            status = restaurant_provider_status()
            self.assertFalse(status["places_enabled"])
            self.assertIn("Terms of Use and Privacy Policy", status["reason"])

    def test_paid_provider_calls_require_explicit_opt_in(self):
        with patch.dict(
            "os.environ",
            {
                "TASTEAI_RESTAURANT_PROVIDER": "google",
                "GOOGLE_PLACES_API_KEY": "unit-test-key",
                "TASTEAI_TERMS_URL": "https://example.test/terms",
                "TASTEAI_PRIVACY_URL": "https://example.test/privacy",
            },
            clear=True,
        ):
            status = restaurant_provider_status()
            self.assertFalse(status["places_enabled"])
            self.assertIn("explicitly enabled", status["reason"])


if __name__ == "__main__":
    unittest.main()
