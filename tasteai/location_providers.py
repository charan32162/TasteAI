import math
import os
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Protocol
from urllib.parse import quote, urlparse

import httpx


PLACES_BASE_URL = "https://places.googleapis.com/v1"
PLACE_SEARCH_FIELDS = (
    "places.id,places.displayName,places.formattedAddress,places.location,places.googleMapsUri"
)
PLACE_LOCATION_FIELDS = "places.displayName,places.location,places.types"
PLACE_DETAIL_FIELDS = "id,displayName,formattedAddress,location,googleMapsUri,primaryType,types,rating,userRatingCount,priceLevel"
PLACE_REVIEW_FIELDS = "googleMapsUri,reviews"
PLACE_PHOTO_FIELDS = "photos"
MAX_PLACE_RESULTS = 10
GOOGLE_LOCATION_TYPES = frozenset({
    "administrative_area_level_1",
    "administrative_area_level_2",
    "administrative_area_level_3",
    "administrative_area_level_4",
    "administrative_area_level_5",
    "colloquial_area",
    "locality",
    "neighborhood",
    "postal_town",
    "sublocality",
    "sublocality_level_1",
    "sublocality_level_2",
    "sublocality_level_3",
    "sublocality_level_4",
    "sublocality_level_5",
})


class RestaurantProviderError(Exception):
    """A safe, user-presentable provider failure without upstream response content."""

    def __init__(self, message: str, category: str = "provider_error"):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class ResolvedLocation:
    query: str
    latitude: float
    longitude: float
    display_name: str | None
    source: str
    confidence: float | None = None
    confidence_basis: str | None = None


@dataclass(frozen=True)
class RestaurantPlace:
    provider_id: str
    name: str
    address: str | None
    latitude: float | None
    longitude: float | None
    rating: float | None
    rating_count: int | None
    price_level: str | None
    maps_url: str | None
    website_url: str | None
    phone: str | None = None
    opening_hours: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    distance_km: float | None = None
    source_label: str = "Google Maps"
    city: str | None = None
    cuisine: str | None = None
    takeaway: bool | None = None
    delivery: bool | None = None
    dietary_tags: tuple[str, ...] = ()
    attribution: str | None = None
    attribution_url: str | None = None
    maps_url_label: str | None = None


@dataclass(frozen=True)
class ReviewEvidence:
    text: str
    original_text: str | None
    rating: float | None
    author_name: str | None
    author_url: str | None
    author_photo_url: str | None
    relative_time: str | None
    source_url: str | None
    source_label: str = "Google Maps"


@dataclass(frozen=True)
class PhotoEvidence:
    provider_name: str
    author_attributions: tuple[tuple[str, str | None, str | None], ...]


class RestaurantDiscoveryProvider(Protocol):
    name: str
    enabled: bool
    reason: str | None

    def search_restaurants(self, query: str, limit: int = MAX_PLACE_RESULTS) -> list[RestaurantPlace]: ...

    def nearby_restaurants(
        self, point: GeoPoint, radius_meters: int, limit: int = MAX_PLACE_RESULTS
    ) -> list[RestaurantPlace]: ...


class LocationResolver(Protocol):
    def resolve_location(self, query: str) -> ResolvedLocation | None: ...


class RestaurantProvider(RestaurantDiscoveryProvider, Protocol):
    def get_restaurant(self, place_id: str) -> RestaurantPlace | None: ...

    def get_restaurant_details(self, place_id: str) -> RestaurantPlace | None: ...

    def get_reviews(self, place_id: str) -> list[ReviewEvidence]: ...

    def get_photos(self, place_id: str) -> list[PhotoEvidence]: ...


RestaurantSearchProvider = RestaurantProvider


class DemoRestaurantSearchProvider:
    name = "fictional_demo"
    enabled = False

    def __init__(self, reason: str):
        self.reason = reason

    def search_restaurants(self, query: str, limit: int = MAX_PLACE_RESULTS) -> list[RestaurantPlace]:
        return []

    def nearby_restaurants(
        self, point: GeoPoint, radius_meters: int, limit: int = MAX_PLACE_RESULTS
    ) -> list[RestaurantPlace]:
        return []

    def get_restaurant(self, place_id: str) -> RestaurantPlace | None:
        return None

    def get_restaurant_details(self, place_id: str) -> RestaurantPlace | None:
        return None

    def get_reviews(self, place_id: str) -> list[ReviewEvidence]:
        return []

    def get_photos(self, place_id: str) -> list[PhotoEvidence]:
        return []


class GooglePlacesProvider:
    name = "google_places"
    enabled = True
    reason = None
    timeout_seconds = 5.0

    def __init__(
        self,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self._api_key = api_key
        self._transport = transport

    def search_restaurants(self, query: str, limit: int = MAX_PLACE_RESULTS) -> list[RestaurantPlace]:
        limit = self._limit(limit)
        payload = self._request(
            "POST",
            f"{PLACES_BASE_URL}/places:searchText",
            PLACE_SEARCH_FIELDS,
            json={
                "textQuery": query,
                "includedType": "restaurant",
                "strictTypeFiltering": True,
                "pageSize": limit,
            },
        )
        return self._places(payload)[:limit]

    def search_text(self, query: str) -> list[RestaurantPlace]:
        return self.search_restaurants(query)

    def resolve_location(self, query: str) -> ResolvedLocation | None:
        normalized_query = " ".join(query.split()) if isinstance(query, str) else ""
        if not normalized_query or len(normalized_query) > 150:
            raise ValueError("Provide an area between 1 and 150 characters.")
        payload = self._request(
            "POST",
            f"{PLACES_BASE_URL}/places:searchText",
            PLACE_LOCATION_FIELDS,
            json={"textQuery": normalized_query, "pageSize": 5},
        )
        values = payload.get("places", [])
        if not isinstance(values, list):
            raise RestaurantProviderError(
                "The restaurant provider returned an invalid location response.",
                "malformed_response",
            )
        for item in values:
            if not isinstance(item, dict):
                continue
            types = item.get("types")
            location = item.get("location")
            if not isinstance(types, list) or not isinstance(location, dict):
                continue
            location_type = next(
                (
                    value for value in types
                    if isinstance(value, str) and value in GOOGLE_LOCATION_TYPES
                ),
                None,
            )
            latitude = self._coordinate(location.get("latitude"), -90, 90)
            longitude = self._coordinate(location.get("longitude"), -180, 180)
            if location_type is None or latitude is None or longitude is None:
                continue
            display_name = item.get("displayName")
            display_name = display_name if isinstance(display_name, dict) else {}
            return ResolvedLocation(
                query=normalized_query,
                latitude=latitude,
                longitude=longitude,
                display_name=self._string(display_name.get("text")),
                source="Google Places",
                confidence=None,
                confidence_basis=(
                    f"Google Places returned type {location_type}; this point is not an area boundary"
                ),
            )
        return None

    def nearby_restaurants(
        self, point: GeoPoint, radius_meters: int, limit: int = MAX_PLACE_RESULTS
    ) -> list[RestaurantPlace]:
        limit = self._limit(limit)
        payload = self._request(
            "POST",
            f"{PLACES_BASE_URL}/places:searchNearby",
            PLACE_SEARCH_FIELDS,
            json={
                "includedTypes": ["restaurant"],
                "maxResultCount": limit,
                "rankPreference": "DISTANCE",
                "locationRestriction": {
                    "circle": {
                        "center": {
                            "latitude": point.latitude,
                            "longitude": point.longitude,
                        },
                        "radius": radius_meters,
                    }
                },
            },
        )
        return [
            RestaurantPlace(
                **{
                    **place.__dict__,
                    "distance_km": self._distance_km(
                        point, GeoPoint(place.latitude, place.longitude)
                    ) if place.latitude is not None and place.longitude is not None else None,
                }
            )
            for place in self._places(payload)[:limit]
        ]

    def search_nearby(self, point: GeoPoint, radius_meters: int) -> list[RestaurantPlace]:
        return self.nearby_restaurants(point, radius_meters)

    def get_restaurant(self, place_id: str) -> RestaurantPlace | None:
        return self.get_restaurant_details(place_id)

    def get_restaurant_details(self, place_id: str) -> RestaurantPlace | None:
        payload = self._place_details(place_id, PLACE_DETAIL_FIELDS)
        places = self._places({"places": [payload]})
        if not places:
            return None
        place = places[0]
        types = payload.get("types")
        primary_type = payload.get("primaryType")
        normalized_types = tuple(
            value for value in types
            if isinstance(value, str) and value
        ) if isinstance(types, list) else ()
        if isinstance(primary_type, str) and primary_type and primary_type not in normalized_types:
            normalized_types = (primary_type, *normalized_types)
        return RestaurantPlace(**{**place.__dict__, "types": normalized_types})

    def get_reviews(self, place_id: str) -> list[ReviewEvidence]:
        payload = self._place_details(place_id, PLACE_REVIEW_FIELDS)
        maps_url = self._safe_maps_url(payload.get("googleMapsUri"))
        reviews = payload.get("reviews", [])
        if not isinstance(reviews, list):
            raise RestaurantProviderError(
                "The restaurant provider returned an invalid response.",
                "malformed_response",
            )
        result = []
        for review in reviews:
            if not isinstance(review, dict):
                continue
            text_value = review.get("text")
            text = text_value.get("text") if isinstance(text_value, dict) else None
            if not isinstance(text, str) or not text.strip():
                continue
            original_value = review.get("originalText")
            original_text = original_value.get("text") if isinstance(original_value, dict) else None
            author = review.get("authorAttribution")
            author = author if isinstance(author, dict) else {}
            result.append(ReviewEvidence(
                text=text,
                original_text=self._string(original_text),
                rating=self._rating(review.get("rating")),
                author_name=self._string(author.get("displayName")),
                author_url=self._safe_url(author.get("uri")),
                author_photo_url=self._safe_url(author.get("photoUri")),
                relative_time=self._string(review.get("relativePublishTimeDescription")),
                source_url=maps_url,
            ))
        return result

    def get_photos(self, place_id: str) -> list[PhotoEvidence]:
        payload = self._place_details(place_id, PLACE_PHOTO_FIELDS)
        photos = payload.get("photos", [])
        if not isinstance(photos, list):
            raise RestaurantProviderError(
                "The restaurant provider returned an invalid response.",
                "malformed_response",
            )
        result = []
        for photo in photos:
            if not isinstance(photo, dict) or not isinstance(photo.get("name"), str):
                continue
            attributions = photo.get("authorAttributions", [])
            if not isinstance(attributions, list):
                attributions = []
            result.append(PhotoEvidence(
                provider_name=photo["name"],
                author_attributions=tuple(
                    (
                        name,
                        self._safe_url(item.get("uri")),
                        self._safe_url(item.get("photoUri")),
                    ) for item in attributions
                    if isinstance(item, dict)
                    if (name := self._string(item.get("displayName")))
                ),
            ))
        return result

    def _place_details(self, place_id: str, field_mask: str) -> dict:
        safe_id = quote(place_id, safe="")
        return self._request(
            "GET",
            f"{PLACES_BASE_URL}/places/{safe_id}",
            field_mask,
        )

    def _request(self, method: str, url: str, field_mask: str, **kwargs) -> dict:
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self.timeout_seconds,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method,
                    url,
                    headers={
                        "X-Goog-Api-Key": self._api_key,
                        "X-Goog-FieldMask": field_mask,
                    },
                    **kwargs,
                )
        except httpx.TimeoutException as error:
            raise RestaurantProviderError(
                "Restaurant lookup timed out. Please try again.", "timeout"
            ) from error
        except httpx.RequestError as error:
            raise RestaurantProviderError(
                "Restaurant lookup is temporarily unavailable.", "unavailable"
            ) from error
        if response.status_code == 429:
            raise RestaurantProviderError(
                "Restaurant lookup has reached a provider quota limit. Please try later.",
                "quota",
            )
        if response.status_code >= 500:
            raise RestaurantProviderError(
                "Restaurant lookup is temporarily unavailable.", "unavailable"
            )
        if not response.is_success:
            category = "configuration" if response.status_code in {401, 403} else "provider_error"
            message = (
                "The restaurant provider credentials or permissions need attention."
                if category == "configuration"
                else "The restaurant provider rejected this lookup."
            )
            try:
                error_status = response.json().get("error", {}).get("status")
            except (ValueError, AttributeError):
                error_status = None
            if error_status in {"RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED"}:
                category = "quota"
                message = "Restaurant lookup has reached a provider quota limit. Please try later."
            raise RestaurantProviderError(message, category)
        try:
            payload = response.json()
        except ValueError as error:
            raise RestaurantProviderError(
                "The restaurant provider returned an invalid response.",
                "malformed_response",
            ) from error
        if not isinstance(payload, dict):
            raise RestaurantProviderError(
                "The restaurant provider returned an invalid response.",
                "malformed_response",
            )
        return payload

    def _places(self, payload: dict) -> list[RestaurantPlace]:
        values = payload.get("places", [])
        if not isinstance(values, list):
            raise RestaurantProviderError(
                "The restaurant provider returned an invalid response.",
                "malformed_response",
            )
        places = []
        for item in values:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"].strip()
            ):
                continue
            display_name = item.get("displayName")
            location = item.get("location")
            display_name = display_name if isinstance(display_name, dict) else {}
            location = location if isinstance(location, dict) else {}
            name = self._string(display_name.get("text"))
            if not name:
                continue
            places.append(RestaurantPlace(
                provider_id=item["id"],
                name=name,
                address=self._string(item.get("formattedAddress")),
                latitude=self._coordinate(location.get("latitude"), -90, 90),
                longitude=self._coordinate(location.get("longitude"), -180, 180),
                rating=self._rating(item.get("rating")),
                rating_count=self._nonnegative_int(item.get("userRatingCount")),
                price_level=self._string(item.get("priceLevel")),
                maps_url=self._safe_maps_url(item.get("googleMapsUri")),
                website_url=self._safe_url(item.get("websiteUri")),
                types=tuple(
                    value for value in item.get("types", [])
                    if isinstance(value, str) and value
                ) if isinstance(item.get("types"), list) else (),
            ))
        unique_places: dict[str, RestaurantPlace] = {}
        for place in places:
            unique_places.setdefault(place.provider_id, place)
        return list(unique_places.values())

    @staticmethod
    def _limit(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Result limit must be an integer.")
        if not 1 <= value <= MAX_PLACE_RESULTS:
            raise ValueError(f"Result limit must be between 1 and {MAX_PLACE_RESULTS}.")
        return value

    @staticmethod
    def _string(value) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _coordinate(value, minimum: float, maximum: float) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) and minimum <= value <= maximum else None

    @staticmethod
    def _rating(value) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) and 0 <= value <= 5 else None

    @staticmethod
    def _nonnegative_int(value) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    @classmethod
    def _safe_url(cls, value) -> str | None:
        url = cls._string(value)
        if not url:
            return None
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        return url

    @classmethod
    def _safe_maps_url(cls, value) -> str | None:
        url = cls._safe_url(value)
        if not url:
            return None
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        try:
            if parsed.port not in (None, 443):
                return None
        except ValueError:
            return None
        if host == "maps.app.goo.gl":
            return url
        if host in {"google.com", "www.google.com", "maps.google.com"} and (
            parsed.path.startswith("/maps") or (host == "maps.google.com" and parsed.path == "/")
        ):
            return url
        return None

    @staticmethod
    def _distance_km(first: GeoPoint, second: GeoPoint) -> float:
        radius_km = 6371.0088
        lat1, lat2 = math.radians(first.latitude), math.radians(second.latitude)
        delta_lat = lat2 - lat1
        delta_lon = math.radians(second.longitude - first.longitude)
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        value = max(0.0, min(1.0, value))
        return round(radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)), 2)


def configured_restaurant_provider() -> RestaurantSearchProvider:
    requested = os.getenv(
        "TASTEAI_RESTAURANT_PROVIDER",
        os.getenv("RESTAURANT_PROVIDER", "demo"),
    ).strip().lower()
    if requested not in {"demo", "google", "osm"}:
        raise RuntimeError("TASTEAI_RESTAURANT_PROVIDER must be 'demo', 'google', or 'osm'.")
    if requested == "osm":
        user_agent = os.getenv("TASTEAI_OSM_USER_AGENT", "").strip()
        if not user_agent:
            return DemoRestaurantSearchProvider(
                "OpenStreetMap search requires a descriptive TASTEAI_OSM_USER_AGENT."
            )
        from .osm_provider import OpenStreetMapProvider

        try:
            return OpenStreetMapProvider(
                user_agent=user_agent,
                nominatim_url=os.getenv(
                    "TASTEAI_OSM_NOMINATIM_URL",
                    "https://nominatim.openstreetmap.org/search",
                ),
                overpass_url=os.getenv(
                    "TASTEAI_OSM_OVERPASS_URL",
                    "https://overpass-api.de/api/interpreter",
                ),
            )
        except ValueError:
            return DemoRestaurantSearchProvider(
                "OpenStreetMap endpoint configuration is invalid."
            )
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    terms_url = _public_https_url(os.getenv("TASTEAI_TERMS_URL", ""))
    privacy_url = _public_https_url(os.getenv("TASTEAI_PRIVACY_URL", ""))
    paid_calls_enabled = os.getenv("TASTEAI_ALLOW_PAID_PLACES_LOOKUPS", "").strip().lower() == "true"
    if requested == "google" and paid_calls_enabled and api_key and terms_url and privacy_url:
        return GooglePlacesProvider(api_key)
    if requested != "google":
        reason = "Location search is disabled in fictional demo mode."
    elif not api_key:
        reason = "Google Places is selected but GOOGLE_PLACES_API_KEY is not configured."
    elif not paid_calls_enabled:
        reason = "Google Places lookups are disabled unless paid API usage is explicitly enabled."
    else:
        reason = "Google Places requires publicly accessible HTTPS Terms of Use and Privacy Policy URLs."
    return DemoRestaurantSearchProvider(reason)


def _public_https_url(value: str) -> str | None:
    normalized = value.strip()
    try:
        parsed = urlparse(normalized)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return normalized


def restaurant_provider_status() -> dict[str, str | bool | None]:
    selected = os.getenv(
        "TASTEAI_RESTAURANT_PROVIDER",
        os.getenv("RESTAURANT_PROVIDER", "demo"),
    ).strip().lower()
    provider = configured_restaurant_provider()
    return {
        "provider": provider.name,
        "selected_provider": selected,
        "places_enabled": provider.enabled,
        "reason": provider.reason,
        "terms_url": _public_https_url(os.getenv("TASTEAI_TERMS_URL", "")),
        "privacy_url": _public_https_url(os.getenv("TASTEAI_PRIVACY_URL", "")),
    }


class PlaceLookupRateLimit:
    """Small process-local guard for explicit Places requests in local deployments."""

    def __init__(
        self,
        requests_per_hour: int = 60,
        client_interval_seconds: float = 2.0,
        clock=time.monotonic,
    ):
        self.requests_per_hour = requests_per_hour
        self.client_interval_seconds = client_interval_seconds
        self.clock = clock
        self._requests: deque[float] = deque()
        self._last_client_request: dict[str, float] = {}
        self._lock = Lock()

    def check(self, client_key: str) -> int | None:
        now = self.clock()
        with self._lock:
            while self._requests and now - self._requests[0] >= 3600:
                self._requests.popleft()
            active_keys = {
                key for key, timestamp in self._last_client_request.items()
                if now - timestamp < 3600
            }
            self._last_client_request = {
                key: timestamp for key, timestamp in self._last_client_request.items()
                if key in active_keys
            }
            previous = self._last_client_request.get(client_key)
            if previous is not None and now - previous < self.client_interval_seconds:
                return max(1, math.ceil(self.client_interval_seconds - (now - previous)))
            if len(self._requests) >= self.requests_per_hour:
                return max(1, math.ceil(3600 - (now - self._requests[0])))
            self._requests.append(now)
            self._last_client_request[client_key] = now
            return None

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._last_client_request.clear()
