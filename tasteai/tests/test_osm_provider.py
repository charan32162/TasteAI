import unittest
from urllib.parse import parse_qs

import httpx

from tasteai.location_providers import GeoPoint, RestaurantProviderError
from tasteai.osm_provider import OpenStreetMapProvider


USER_AGENT = "TasteAI test client (contact: test@example.invalid)"


class OpenStreetMapProviderTests(unittest.TestCase):
    def test_location_resolution_normalizes_query_and_preserves_provider_metadata(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json=[{
                    "lat": "17.0",
                    "lon": "81.0",
                    "display_name": "Chintalapudi, Eluru district",
                    "importance": 0.42,
                }],
                request=request,
            )

        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            nominatim_url="https://nominatim.example/search",
            transport=httpx.MockTransport(respond),
            request_pacer=lambda: None,
        )
        location = provider.resolve_location("  Chintalapudi   ")

        self.assertEqual(location.query, "Chintalapudi")
        self.assertEqual(location.latitude, 17.0)
        self.assertEqual(location.longitude, 81.0)
        self.assertEqual(location.display_name, "Chintalapudi, Eluru district")
        self.assertEqual(location.source, "OpenStreetMap Nominatim")
        self.assertEqual(location.confidence, 0.42)
        self.assertIn("not a calibrated", location.confidence_basis)
        self.assertEqual(requests[0].url.params["q"], "Chintalapudi")
        self.assertEqual(requests[0].url.params["limit"], "1")

    def test_location_resolution_empty_and_malformed_results(self):
        empty = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            nominatim_url="https://nominatim.example/search",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=[], request=request)
            ),
            request_pacer=lambda: None,
        )
        self.assertIsNone(empty.resolve_location("Unknown place"))
        self.assertEqual(empty.search_restaurants("Unknown place"), [])

        malformed = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            nominatim_url="https://nominatim.example/search",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=[{"lat": "bad"}], request=request)
            ),
            request_pacer=lambda: None,
        )
        with self.assertRaises(RestaurantProviderError) as raised:
            malformed.resolve_location("Unknown place")
        self.assertEqual(raised.exception.category, "malformed_response")

    def test_provider_http_error_and_timeout_are_sanitized(self):
        scenarios = (
            (
                lambda request: httpx.Response(503, text="secret upstream text", request=request),
                "unavailable",
            ),
            (
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("private timeout detail")),
                "timeout",
            ),
        )
        for handler, category in scenarios:
            with self.subTest(category=category):
                provider = OpenStreetMapProvider(
                    user_agent=USER_AGENT,
                    overpass_url="https://overpass.example/api/interpreter",
                    transport=httpx.MockTransport(handler),
                    request_pacer=lambda: None,
                )
                with self.assertRaises(RestaurantProviderError) as raised:
                    provider.nearby_restaurants(GeoPoint(0, 0), 1000)
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn("secret", str(raised.exception))
                self.assertNotIn("private", str(raised.exception))

    def test_area_search_uses_nominatim_then_bounded_overpass_and_preserves_attribution(self):
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.host == "nominatim.example":
                return httpx.Response(
                    200,
                    json=[{"lat": "12.9716", "lon": "77.5946"}],
                    request=request,
                )
            query = parse_qs(request.content.decode())["data"][0]
            self.assertIn("around:5000,12.971600,77.594600", query)
            self.assertIn("out center tags 2;", query)
            return httpx.Response(
                200,
                json={
                    "elements": [
                        {
                            "type": "node",
                            "id": 101,
                            "lat": 12.9717,
                            "lon": 77.5947,
                            "tags": {
                                "amenity": "restaurant",
                                "name": "Open Food House",
                                "cuisine": "indian",
                                "addr:street": "Sample Road",
                                "addr:city": "Bengaluru",
                                "opening_hours": "Mo-Su 10:00-22:00",
                                "phone": "+91 5550000000",
                                "website": "https://restaurant.example",
                                "takeaway": "yes",
                                "delivery": "no",
                                "diet:vegetarian": "yes",
                            },
                        },
                        {
                            "type": "way",
                            "id": 202,
                            "center": {"lat": 12.972, "lon": 77.595},
                            "tags": {
                                "amenity": "fast_food",
                                "name": "Quick Food",
                                "website": "https://127.0.0.1/menu",
                            },
                        },
                    ]
                },
                request=request,
            )

        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            nominatim_url="https://nominatim.example/search",
            overpass_url="https://overpass.example/api/interpreter",
            transport=httpx.MockTransport(respond),
            request_pacer=lambda: None,
        )
        places = provider.search_restaurants("restaurants in Bengaluru", limit=2)

        self.assertEqual(len(places), 2)
        place = places[0]
        self.assertEqual(place.provider_id, "osm_node_101")
        self.assertEqual(place.name, "Open Food House")
        self.assertEqual(place.address, "Sample Road, Bengaluru")
        self.assertEqual(place.city, "Bengaluru")
        self.assertEqual(place.cuisine, "indian")
        self.assertEqual(place.opening_hours, ("Mo-Su 10:00-22:00",))
        self.assertEqual(place.phone, "+91 5550000000")
        self.assertEqual(place.takeaway, True)
        self.assertEqual(place.delivery, False)
        self.assertEqual(place.dietary_tags, ("diet:vegetarian=yes",))
        self.assertEqual(place.source_label, "OpenStreetMap")
        self.assertEqual(place.attribution, "© OpenStreetMap contributors")
        self.assertEqual(
            place.attribution_url,
            "https://www.openstreetmap.org/copyright",
        )
        self.assertEqual(
            place.maps_url,
            "https://www.openstreetmap.org/node/101",
        )
        self.assertEqual(place.maps_url_label, "Open in OpenStreetMap")
        self.assertIsNone(place.rating)
        self.assertIsNone(place.price_level)
        self.assertIsNone(places[1].website_url)
        self.assertEqual(requests[0].url.params["q"], "Bengaluru")
        self.assertEqual(requests[0].url.params["limit"], "1")
        self.assertTrue(all(r.headers["user-agent"] == USER_AGENT for r in requests))
        self.assertEqual(len(requests), 2)

    def test_nearby_search_sends_only_coordinates_and_radius_to_overpass(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "elements": [{
                        "type": "way",
                        "id": 303,
                        "center": {"lat": 12.0, "lon": 77.0},
                        "tags": {"amenity": "restaurant", "name": "Nearby Place"},
                    }]
                },
                request=request,
            )

        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            nominatim_url="https://nominatim.example/search",
            overpass_url="https://overpass.example/api/interpreter",
            transport=httpx.MockTransport(respond),
            request_pacer=lambda: None,
        )
        places = provider.nearby_restaurants(GeoPoint(12.0, 77.0), 1500, 1)

        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].distance_km, 0)
        query = parse_qs(requests[0].content.decode())["data"][0]
        self.assertIn("around:1500,12.000000,77.000000", query)
        self.assertEqual(requests[0].url.query, b"")

    def test_details_accept_only_provider_scoped_identifiers(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={"elements": [{
                    "type": "relation",
                    "id": 456,
                    "center": {"lat": 1, "lon": 2},
                    "tags": {"amenity": "restaurant", "name": "Detail Place"},
                }]},
                request=request,
            )

        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            overpass_url="https://overpass.example/api/interpreter",
            transport=httpx.MockTransport(respond),
            request_pacer=lambda: None,
        )
        self.assertIsNone(provider.get_restaurant_details("other_456"))
        place = provider.get_restaurant_details("osm_relation_456")
        self.assertEqual(place.name, "Detail Place")
        self.assertIn("relation(456)", parse_qs(requests[0].content.decode())["data"][0])
        self.assertEqual(provider.get_reviews("osm_relation_456"), [])
        self.assertEqual(provider.get_photos("osm_relation_456"), [])

    def test_provider_errors_are_bounded_and_sanitized(self):
        scenarios = (
            (
                lambda request: httpx.Response(302, headers={"Location": "https://evil.example"}, request=request),
                "provider_error",
            ),
            (
                lambda request: httpx.Response(200, content=b"not-json", headers={"Content-Type": "application/json"}, request=request),
                "malformed_response",
            ),
        )
        for handler, category in scenarios:
            with self.subTest(category=category):
                provider = OpenStreetMapProvider(
                    user_agent=USER_AGENT,
                    overpass_url="https://overpass.example/api/interpreter",
                    transport=httpx.MockTransport(handler),
                    request_pacer=lambda: None,
                )
                with self.assertRaises(RestaurantProviderError) as raised:
                    provider.nearby_restaurants(GeoPoint(0, 0), 1000)
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn("evil.example", str(raised.exception))

        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            overpass_url="https://overpass.example/api/interpreter",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=b'{"data":"' + b"x" * 1_000_000 + b'"}',
                    headers={"Content-Type": "application/json"},
                    request=request,
                )
            ),
            request_pacer=lambda: None,
        )
        with self.assertRaises(RestaurantProviderError) as raised:
            provider.nearby_restaurants(GeoPoint(0, 0), 1000)
        self.assertEqual(raised.exception.category, "response_too_large")

    def test_request_parameters_are_validated_and_endpoint_configuration_is_safe(self):
        for kwargs in (
            {"user_agent": "short"},
            {"user_agent": "Valid User-Agent\r\nInjected: header"},
            {"user_agent": USER_AGENT, "nominatim_url": "http://nominatim.example/search"},
            {"user_agent": USER_AGENT, "overpass_url": "https://127.0.0.1/interpreter"},
            {"user_agent": USER_AGENT, "overpass_url": "https://user@overpass.example/api"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    OpenStreetMapProvider(**kwargs)

        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            overpass_url="https://overpass.example/api/interpreter",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"elements": []}, request=request)),
            request_pacer=lambda: None,
        )
        for radius in (True, 99, 50_001):
            with self.subTest(radius=radius):
                with self.assertRaises(ValueError):
                    provider.nearby_restaurants(GeoPoint(0, 0), radius)
        with self.assertRaises(ValueError):
            provider.nearby_restaurants(GeoPoint(100, 0), 1000)
        with self.assertRaises(ValueError):
            provider.search_restaurants(" " * 151)

    def test_malformed_osm_elements_and_duplicates_are_not_promoted(self):
        elements = [
            {"type": "node", "id": 1, "lat": 0, "lon": 0, "tags": {"amenity": "restaurant", "name": "Valid"}},
            {"type": "node", "id": 1, "lat": 0, "lon": 0, "tags": {"amenity": "restaurant", "name": "Duplicate"}},
            {"type": "node", "id": 2, "lat": 0, "lon": 0, "tags": {"amenity": "restaurant"}},
            {"type": "node", "id": 3, "lat": 0, "lon": 0, "tags": {"amenity": "cafe", "name": "Not selected"}},
            {"type": "node", "id": True, "lat": 0, "lon": 0, "tags": {"amenity": "restaurant", "name": "Bad id"}},
        ]
        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            overpass_url="https://overpass.example/api/interpreter",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"elements": elements}, request=request)
            ),
            request_pacer=lambda: None,
        )
        places = provider.nearby_restaurants(GeoPoint(0, 0), 1000)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].name, "Valid")

    def test_missing_coordinates_and_cuisine_remain_unknown(self):
        provider = OpenStreetMapProvider(
            user_agent=USER_AGENT,
            overpass_url="https://overpass.example/api/interpreter",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"elements": [{
                        "type": "way",
                        "id": 55,
                        "tags": {"amenity": "restaurant", "name": "Metadata Sparse"},
                    }]},
                    request=request,
                )
            ),
            request_pacer=lambda: None,
        )

        place = provider.nearby_restaurants(GeoPoint(0, 0), 1000)[0]

        self.assertEqual(place.name, "Metadata Sparse")
        self.assertIsNone(place.latitude)
        self.assertIsNone(place.longitude)
        self.assertIsNone(place.cuisine)
        self.assertEqual(place.dietary_tags, ())


if __name__ == "__main__":
    unittest.main()
