import ipaddress
import json
import math
import re
import time
from threading import Lock
from typing import Callable, Any
from urllib.parse import urlparse

import httpx

from .location_providers import (
    MAX_PLACE_RESULTS,
    GeoPoint,
    PhotoEvidence,
    ResolvedLocation,
    RestaurantPlace,
    RestaurantProviderError,
    ReviewEvidence,
)


NOMINATIM_DEFAULT_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_DEFAULT_URL = "https://overpass-api.de/api/interpreter"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"
MAX_OSM_RESPONSE_BYTES = 1_000_000
DEFAULT_SEARCH_RADIUS_METERS = 5000
REQUEST_TIMEOUT_SECONDS = 5.0
_osm_pacing_lock = Lock()
_last_osm_request_at = 0.0


def _pace_public_osm_request() -> None:
    global _last_osm_request_at
    with _osm_pacing_lock:
        now = time.monotonic()
        delay = max(0.0, 1.0 - (now - _last_osm_request_at))
        if delay:
            time.sleep(delay)
        _last_osm_request_at = time.monotonic()


class OpenStreetMapProvider:
    """Explicit-action OSM place lookup using Nominatim and Overpass."""

    name = "openstreetmap"
    enabled = True
    reason = None

    def __init__(
        self,
        *,
        user_agent: str,
        nominatim_url: str = NOMINATIM_DEFAULT_URL,
        overpass_url: str = OVERPASS_DEFAULT_URL,
        transport: httpx.BaseTransport | None = None,
        request_pacer: Callable[[], None] = _pace_public_osm_request,
    ):
        self._user_agent = self._validate_user_agent(user_agent)
        self._nominatim_url = self._validate_endpoint(nominatim_url)
        self._overpass_url = self._validate_endpoint(overpass_url)
        self._transport = transport
        self._request_pacer = request_pacer

    def search_restaurants(
        self,
        query: str,
        limit: int = MAX_PLACE_RESULTS,
    ) -> list[RestaurantPlace]:
        limit = self._limit(limit)
        match = re.fullmatch(r"\s*restaurants\s+in\s+(.+?)\s*", query, flags=re.IGNORECASE)
        area = match.group(1) if match else query.strip()
        location = self.resolve_location(area)
        if location is None:
            return []
        return self.nearby_restaurants(
            GeoPoint(location.latitude, location.longitude),
            DEFAULT_SEARCH_RADIUS_METERS,
            limit,
        )

    def resolve_location(self, query: str) -> ResolvedLocation | None:
        normalized_query = " ".join(query.split()) if isinstance(query, str) else ""
        if not normalized_query or len(normalized_query) > 150:
            raise ValueError("Provide an area between 1 and 150 characters.")
        geocoded = self._request_json(
            "GET",
            self._nominatim_url,
            params={"q": normalized_query, "format": "jsonv2", "limit": "1"},
        )
        if not isinstance(geocoded, list):
            raise RestaurantProviderError(
                "OpenStreetMap returned an invalid area-search response.",
                "malformed_response",
            )
        if not geocoded:
            return None
        point = self._point_from_geocode(geocoded[0])
        if point is None:
            raise RestaurantProviderError(
                "OpenStreetMap returned an invalid area location.",
                "malformed_response",
            )
        result = geocoded[0]
        importance = self._coordinate(result.get("importance"), 0, 1)
        return ResolvedLocation(
            query=normalized_query,
            latitude=point.latitude,
            longitude=point.longitude,
            display_name=self._bounded_string(result.get("display_name"), 500),
            source="OpenStreetMap Nominatim",
            confidence=importance,
            confidence_basis=(
                "provider-reported importance; not a calibrated accuracy probability"
                if importance is not None
                else None
            ),
        )

    def nearby_restaurants(
        self,
        point: GeoPoint,
        radius_meters: int,
        limit: int = MAX_PLACE_RESULTS,
    ) -> list[RestaurantPlace]:
        limit = self._limit(limit)
        self._validate_point(point)
        if (
            isinstance(radius_meters, bool)
            or not isinstance(radius_meters, int)
            or not 100 <= radius_meters <= 50_000
        ):
            raise ValueError("Search radius must be between 100 and 50000 meters.")
        query = self._nearby_query(point, radius_meters, limit)
        payload = self._request_json(
            "POST",
            self._overpass_url,
            data={"data": query},
        )
        places = self._places(payload)
        return [
            RestaurantPlace(
                **{
                    **place.__dict__,
                    "distance_km": self._distance_km(
                        point, GeoPoint(place.latitude, place.longitude)
                    ) if place.latitude is not None and place.longitude is not None else None,
                }
            )
            for place in places[:limit]
        ]

    def get_restaurant(self, place_id: str) -> RestaurantPlace | None:
        return self.get_restaurant_details(place_id)

    def get_restaurant_details(self, place_id: str) -> RestaurantPlace | None:
        match = re.fullmatch(r"osm_(node|way|relation)_(\d{1,20})", place_id)
        if not match:
            return None
        element_type, element_id = match.groups()
        if int(element_id) <= 0:
            return None
        query = (
            "[out:json][timeout:5];"
            f"{element_type}({element_id});"
            "out center tags;"
        )
        payload = self._request_json(
            "POST",
            self._overpass_url,
            data={"data": query},
        )
        places = self._places(payload)
        return places[0] if places else None

    def get_reviews(self, place_id: str) -> list[ReviewEvidence]:
        return []

    def get_photos(self, place_id: str) -> list[PhotoEvidence]:
        return []

    def _request_json(self, method: str, url: str, **kwargs) -> Any:
        self._request_pacer()
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=False,
                headers={"User-Agent": self._user_agent},
            ) as client:
                with client.stream(method, url, **kwargs) as response:
                    if response.status_code == 429:
                        raise RestaurantProviderError(
                            "OpenStreetMap lookup is rate limited. Please try again later.",
                            "quota",
                        )
                    if response.status_code >= 500:
                        raise RestaurantProviderError(
                            "OpenStreetMap lookup is temporarily unavailable.",
                            "unavailable",
                        )
                    if not response.is_success:
                        raise RestaurantProviderError(
                            "OpenStreetMap rejected this lookup.",
                            "provider_error",
                        )
                    content_type = response.headers.get("content-type", "").lower()
                    if "json" not in content_type:
                        raise RestaurantProviderError(
                            "OpenStreetMap returned an invalid response type.",
                            "malformed_response",
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if len(body) + len(chunk) > MAX_OSM_RESPONSE_BYTES:
                            raise RestaurantProviderError(
                                "OpenStreetMap returned a response that exceeds the size limit.",
                                "response_too_large",
                            )
                        body.extend(chunk)
        except RestaurantProviderError:
            raise
        except httpx.TimeoutException as error:
            raise RestaurantProviderError(
                "OpenStreetMap lookup timed out. Please try again.",
                "timeout",
            ) from error
        except httpx.RequestError as error:
            raise RestaurantProviderError(
                "OpenStreetMap lookup is temporarily unavailable.",
                "unavailable",
            ) from error
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise RestaurantProviderError(
                "OpenStreetMap returned an invalid response.",
                "malformed_response",
            ) from error
        if not isinstance(payload, (dict, list)):
            raise RestaurantProviderError(
                "OpenStreetMap returned an invalid response.",
                "malformed_response",
            )
        return payload

    def _places(self, payload: Any) -> list[RestaurantPlace]:
        if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
            raise RestaurantProviderError(
                "OpenStreetMap returned an invalid place response.",
                "malformed_response",
            )
        places: list[RestaurantPlace] = []
        seen: set[str] = set()
        for element in payload["elements"]:
            place = self._place(element)
            if place is not None and place.provider_id not in seen:
                seen.add(place.provider_id)
                places.append(place)
        return places

    def _place(self, element: Any) -> RestaurantPlace | None:
        if not isinstance(element, dict):
            return None
        element_type = element.get("type")
        element_id = element.get("id")
        if (
            element_type not in {"node", "way", "relation"}
            or isinstance(element_id, bool)
            or not isinstance(element_id, int)
            or element_id <= 0
        ):
            return None
        tags = element.get("tags")
        if not isinstance(tags, dict) or tags.get("amenity") not in {"restaurant", "fast_food"}:
            return None
        name = self._bounded_string(tags.get("name"), MAX_NAME_LENGTH)
        if not name:
            return None
        center = element.get("center") if isinstance(element.get("center"), dict) else {}
        location = element if element_type == "node" else center
        latitude = self._coordinate(location.get("lat"), -90, 90)
        longitude = self._coordinate(location.get("lon"), -180, 180)
        address = self._address(tags)
        cuisine = self._bounded_string(tags.get("cuisine"), 200)
        opening_hours = self._bounded_string(tags.get("opening_hours"), 500)
        phone = self._bounded_string(tags.get("phone") or tags.get("contact:phone"), 80)
        website = tags.get("website") or tags.get("contact:website")
        amenity = tags["amenity"]
        provider_id = f"osm_{element_type}_{element_id}"
        return RestaurantPlace(
            provider_id=provider_id,
            name=name,
            address=address,
            latitude=latitude,
            longitude=longitude,
            rating=None,
            rating_count=None,
            price_level=None,
            maps_url=f"https://www.openstreetmap.org/{element_type}/{element_id}",
            website_url=self._safe_website_url(website),
            phone=phone,
            opening_hours=(opening_hours,) if opening_hours else (),
            types=tuple(value for value in (amenity, cuisine) if value),
            source_label="OpenStreetMap",
            city=self._bounded_string(tags.get("addr:city"), 120),
            cuisine=cuisine,
            takeaway=self._yes_no(tags.get("takeaway")),
            delivery=self._yes_no(tags.get("delivery")),
            dietary_tags=self._dietary_tags(tags),
            attribution=OSM_ATTRIBUTION,
            attribution_url=OSM_ATTRIBUTION_URL,
            maps_url_label="Open in OpenStreetMap",
        )

    @staticmethod
    def _point_from_geocode(value: Any) -> GeoPoint | None:
        if not isinstance(value, dict):
            return None
        latitude = OpenStreetMapProvider._coordinate(value.get("lat"), -90, 90)
        longitude = OpenStreetMapProvider._coordinate(value.get("lon"), -180, 180)
        if latitude is None or longitude is None:
            return None
        return GeoPoint(latitude, longitude)

    @staticmethod
    def _nearby_query(point: GeoPoint, radius_meters: int, limit: int) -> str:
        latitude = f"{point.latitude:.6f}"
        longitude = f"{point.longitude:.6f}"
        return (
            "[out:json][timeout:5];("
            f'node["amenity"~"^(restaurant|fast_food)$"](around:{radius_meters},{latitude},{longitude});'
            f'way["amenity"~"^(restaurant|fast_food)$"](around:{radius_meters},{latitude},{longitude});'
            f'relation["amenity"~"^(restaurant|fast_food)$"](around:{radius_meters},{latitude},{longitude});'
            f");out center tags {limit};"
        )

    @staticmethod
    def _validate_point(point: GeoPoint) -> None:
        if (
            not isinstance(point, GeoPoint)
            or OpenStreetMapProvider._coordinate(point.latitude, -90, 90) is None
            or OpenStreetMapProvider._coordinate(point.longitude, -180, 180) is None
        ):
            raise ValueError("Location must contain valid latitude and longitude.")

    @staticmethod
    def _validate_user_agent(value: str) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        if (
            not 10 <= len(normalized) <= 200
            or "\r" in normalized
            or "\n" in normalized
        ):
            raise ValueError("A descriptive OpenStreetMap User-Agent is required.")
        return normalized

    @staticmethod
    def _validate_endpoint(value: str) -> str:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError("OpenStreetMap endpoint configuration is invalid.")
        try:
            parsed = urlparse(value)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise ValueError("OpenStreetMap endpoint configuration is invalid.") from error
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or port not in (None, 443)
        ):
            raise ValueError("OpenStreetMap endpoint configuration is invalid.")
        host = hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError("OpenStreetMap endpoint configuration is invalid.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError("OpenStreetMap endpoint configuration is invalid.")
        return value

    @staticmethod
    def _limit(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Result limit must be an integer.")
        if not 1 <= value <= MAX_PLACE_RESULTS:
            raise ValueError(f"Result limit must be between 1 and {MAX_PLACE_RESULTS}.")
        return value

    @staticmethod
    def _coordinate(value: Any, minimum: float, maximum: float) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            coordinate = float(value)
        except ValueError:
            return None
        return coordinate if math.isfinite(coordinate) and minimum <= coordinate <= maximum else None

    @staticmethod
    def _bounded_string(value: Any, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = " ".join(value.split())
        return normalized if normalized and len(normalized) <= limit else None

    @classmethod
    def _address(cls, tags: dict[str, Any]) -> str | None:
        full = cls._bounded_string(tags.get("addr:full"), 500)
        if full:
            return full
        street = " ".join(
            value for value in (
                cls._bounded_string(tags.get("addr:housenumber"), 40),
                cls._bounded_string(tags.get("addr:street"), 160),
            ) if value
        )
        locality = ", ".join(
            value for value in (
                cls._bounded_string(tags.get("addr:suburb"), 120),
                cls._bounded_string(tags.get("addr:city"), 120),
                cls._bounded_string(tags.get("addr:postcode"), 24),
            ) if value
        )
        address = ", ".join(value for value in (street, locality) if value)
        return address[:500] if address else None

    @staticmethod
    def _yes_no(value: Any) -> bool | None:
        normalized = value.strip().lower() if isinstance(value, str) else ""
        if normalized in {"yes", "only"}:
            return True
        if normalized == "no":
            return False
        return None

    @classmethod
    def _dietary_tags(cls, tags: dict[str, Any]) -> tuple[str, ...]:
        recognized = {"vegetarian", "vegan", "gluten_free", "lactose_free", "halal", "kosher"}
        values = []
        for key in sorted(tags):
            if not isinstance(key, str) or not key.startswith("diet:"):
                continue
            diet = key.removeprefix("diet:")
            value = cls._bounded_string(tags.get(key), 40)
            if diet in recognized and value:
                values.append(f"diet:{diet}={value}")
        return tuple(values)

    @classmethod
    def _safe_website_url(cls, value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 2048:
            return None
        try:
            parsed = urlparse(value.strip())
            hostname = parsed.hostname
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username
            or parsed.password
            or port not in (None, 443)
        ):
            return None
        host = hostname.lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            return None
        return value.strip()

    @staticmethod
    def _distance_km(first: GeoPoint, second: GeoPoint) -> float:
        radius_km = 6371.0088
        lat1, lat2 = math.radians(first.latitude), math.radians(second.latitude)
        delta_lat = lat2 - lat1
        delta_lon = math.radians(second.longitude - first.longitude)
        haversine = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        haversine = max(0.0, min(1.0, haversine))
        return round(
            radius_km * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine)),
            2,
        )


MAX_NAME_LENGTH = 160
