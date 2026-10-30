"""Read-only restaurant discovery and approved menu-evidence integration."""

import math
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from .evidence_discovery import search_menu_evidence
from .location_providers import (
    GeoPoint,
    ResolvedLocation,
    RestaurantDiscoveryProvider,
    RestaurantPlace,
)
from .menu_evidence import (
    RestaurantIdentityCandidate,
    resolve_restaurant_identity,
)
from .models import TasteProfile
from .schemas import RestaurantDiscoveryRequest


def discover_restaurants(
    db: Session,
    provider: RestaurantDiscoveryProvider,
    request: RestaurantDiscoveryRequest,
    *,
    profile: TasteProfile | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    location, places, request_sent = _discover_places(provider, request)
    center = _location_center(location)
    excluded_outside_radius_count = 0
    excluded_missing_coordinates_count = 0
    if center is None:
        places = []
    else:
        places, excluded_outside_radius_count, excluded_missing_coordinates_count = (
            filter_places_by_radius(places, center, request.radius_meters)
        )
    places = [
        place for place in places
        if request.cuisine is None or _cuisine_matches(place, request.cuisine)
    ][:request.limit]

    identity_ids: set[str] = set()
    result_by_identity: dict[str, dict[str, Any]] = {}
    results = []
    for place in places:
        resolution = resolve_restaurant_identity(db, _identity_candidate(place, provider.name))
        canonical_id = resolution.restaurant_identity_id
        if canonical_id:
            identity_ids.add(canonical_id)
        candidate_result = _restaurant_result(
            place,
            provider.name,
            resolution.state.value,
            canonical_id,
            list(resolution.candidate_identity_ids),
        )
        if canonical_id and canonical_id in result_by_identity:
            existing = result_by_identity[canonical_id]
            existing["provider_ids"].append(place.provider_id)
            existing["provider_records"].append(candidate_result)
            continue
        candidate_result["provider_ids"] = [place.provider_id]
        candidate_result["provider_records"] = [candidate_result.copy()]
        results.append(candidate_result)
        if canonical_id:
            result_by_identity[canonical_id] = candidate_result

    evidence_search = None
    evidence_by_identity: dict[str, list[dict[str, Any]]] = {}
    if request.food_query:
        evidence_search = search_menu_evidence(
            db,
            request.food_query,
            profile=profile,
            budget_max=request.budget_max,
            limit=50,
            restaurant_identity_ids=identity_ids,
            now=now,
        )
        for candidate in evidence_search["results"]:
            evidence_by_identity.setdefault(
                candidate["restaurant"]["identity_id"], []
            ).append(candidate)

    for result in results:
        identity_id = result["identity_resolution"]["canonical_identity_id"]
        matched = evidence_by_identity.get(identity_id, []) if identity_id else []
        if not request.food_query:
            result["dish_evidence"] = {
                "status": "not_requested",
                "message": None,
                "results": [],
            }
        elif matched:
            result["dish_evidence"] = {
                "status": "evidence_found",
                "message": (
                    "The requested dish appears in approved menu evidence. "
                    "This does not establish current availability."
                ),
                "results": matched,
            }
        elif not identity_id:
            result["dish_evidence"] = {
                "status": "identity_unresolved",
                "message": "Restaurant discovered, but requested dish could not be verified because its identity is unresolved.",
                "results": [],
            }
        else:
            result["dish_evidence"] = {
                "status": "not_verified",
                "message": "Restaurant discovered, but requested dish could not be verified.",
                "results": [],
            }

    if not provider.enabled:
        status = "provider_disabled"
        message = provider.reason or "Restaurant discovery provider is disabled."
    elif (
        request.location_query
        and location["resolution_status"] == "unresolved"
        and not results
    ):
        status = "location_unresolved"
        message = "The requested location could not be resolved; no restaurant-area search was performed."
    elif not results:
        status = "no_candidates"
        message = "No restaurant candidates were returned within the requested search area."
    else:
        status = "candidates_found"
        message = None

    return {
        "provider": provider.name,
        "status": status,
        "message": message,
        "location": location,
        "search": {
            "requested_radius_meters": request.radius_meters,
            "cuisine_filter": request.cuisine,
            "limit": request.limit,
            "fallback_used": False,
            "expanded_radius_meters": None,
            "excluded_outside_radius_count": excluded_outside_radius_count,
            "excluded_missing_coordinates_count": excluded_missing_coordinates_count,
        },
        "request_sent": request_sent,
        "location_stored": False,
        "results": results,
        "dish_evidence_search": evidence_search,
        "recommendations": (
            evidence_search["results"] if evidence_search else []
        ),
    }


def _discover_places(
    provider: RestaurantDiscoveryProvider,
    request: RestaurantDiscoveryRequest,
) -> tuple[dict[str, Any], list[RestaurantPlace], bool]:
    if request.location_query:
        query = request.location_query
        if not provider.enabled:
            return _unresolved_location(query, provider.name), [], False
        resolver = getattr(provider, "resolve_location", None)
        if callable(resolver):
            resolved = resolver(query)
            if resolved is None:
                return _unresolved_location(query, provider.name), [], True
            point = GeoPoint(resolved.latitude, resolved.longitude)
            places = provider.nearby_restaurants(
                point,
                request.radius_meters,
                request.limit,
            )
            return _location_payload(resolved), places, True
        return _unresolved_location(query, provider.name), [], False

    point = GeoPoint(request.latitude, request.longitude)
    location = {
        "query": None,
        "normalized_query": None,
        "latitude": point.latitude,
        "longitude": point.longitude,
        "display_name": None,
        "source": "user_supplied_coordinates",
        "confidence": None,
        "confidence_basis": "coordinate accuracy is not known",
        "resolution_status": "coordinates_supplied",
    }
    if not provider.enabled:
        return location, [], False
    return (
        location,
        provider.nearby_restaurants(point, request.radius_meters, request.limit),
        True,
    )


def _location_center(location: dict[str, Any]) -> GeoPoint | None:
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if (
        isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not isinstance(latitude, (int, float))
        or not isinstance(longitude, (int, float))
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None
    return GeoPoint(float(latitude), float(longitude))


def filter_places_by_radius(
    places: list[RestaurantPlace],
    center: GeoPoint,
    radius_meters: int,
) -> tuple[list[RestaurantPlace], int, int]:
    inside: list[tuple[float, RestaurantPlace]] = []
    excluded_outside = 0
    excluded_missing_coordinates = 0
    for place in places:
        latitude = place.latitude
        longitude = place.longitude
        if (
            latitude is None
            or longitude is None
            or isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or not isinstance(longitude, (int, float))
            or not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            excluded_missing_coordinates += 1
            continue
        distance_meters = _distance_meters(
            center,
            GeoPoint(float(latitude), float(longitude)),
        )
        # Absorb sub-micrometer floating-point noise so an exact boundary
        # point is treated as inside the requested inclusive radius.
        if distance_meters - radius_meters > 1e-6:
            excluded_outside += 1
            continue
        inside.append((
            distance_meters,
            replace(place, distance_km=round(distance_meters / 1000, 2)),
        ))
    inside.sort(key=lambda item: item[0])
    return [place for _, place in inside], excluded_outside, excluded_missing_coordinates


def _distance_meters(first: GeoPoint, second: GeoPoint) -> float:
    earth_radius_meters = 6_371_008.8
    latitude_one = math.radians(first.latitude)
    latitude_two = math.radians(second.latitude)
    delta_latitude = latitude_two - latitude_one
    delta_longitude = math.radians(second.longitude - first.longitude)
    value = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(latitude_one)
        * math.cos(latitude_two)
        * math.sin(delta_longitude / 2) ** 2
    )
    value = max(0.0, min(1.0, value))
    return earth_radius_meters * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _unresolved_location(
    query: str,
    provider_name: str,
    resolution_status: str = "unresolved",
) -> dict[str, Any]:
    normalized = " ".join(query.casefold().split())
    return {
        "query": query,
        "normalized_query": normalized,
        "latitude": None,
        "longitude": None,
        "display_name": None,
        "source": provider_name,
        "confidence": None,
        "confidence_basis": None,
        "resolution_status": resolution_status,
    }


def _location_payload(location: ResolvedLocation) -> dict[str, Any]:
    return {
        "query": location.query,
        "normalized_query": " ".join(location.query.casefold().split()),
        "latitude": location.latitude,
        "longitude": location.longitude,
        "display_name": location.display_name,
        "source": location.source,
        "confidence": location.confidence,
        "confidence_basis": location.confidence_basis,
        "resolution_status": "resolved",
    }


def _identity_candidate(place: RestaurantPlace, provider_name: str) -> RestaurantIdentityCandidate:
    return RestaurantIdentityCandidate(
        name=place.name,
        address=place.address,
        phone=place.phone,
        latitude=place.latitude,
        longitude=place.longitude,
        website_url=place.website_url,
        provider_name=provider_name,
        provider_place_id=place.provider_id,
    )


def _restaurant_result(
    place: RestaurantPlace,
    provider_name: str,
    identity_state: str,
    canonical_identity_id: str | None,
    candidate_identity_ids: list[str],
) -> dict[str, Any]:
    return {
        "provider": provider_name,
        "provider_id": place.provider_id,
        "name": place.name,
        "address": place.address,
        "city": place.city,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "distance_km": place.distance_km,
        "phone": place.phone,
        "website": place.website_url,
        "cuisine": place.cuisine,
        "opening_hours": list(place.opening_hours),
        "takeaway": place.takeaway,
        "delivery": place.delivery,
        "dietary_tags": list(place.dietary_tags),
        "source": {
            "label": place.source_label,
            "url": place.maps_url,
            "attribution": place.attribution or place.source_label,
            "attribution_url": place.attribution_url,
            "license": "Open Database License (ODbL)" if provider_name == "openstreetmap" else None,
        },
        "identity_resolution": {
            "state": identity_state,
            "status": (
                "resolved" if canonical_identity_id else "needs_review"
            ),
            "canonical_identity_id": canonical_identity_id,
            "candidate_identity_ids": candidate_identity_ids,
        },
        "dish_evidence": None,
    }


def _cuisine_matches(place: RestaurantPlace, requested: str) -> bool:
    if not place.cuisine:
        return False
    wanted = " ".join(requested.casefold().split())
    cuisines = {
        " ".join(value.strip().casefold().split())
        for value in place.cuisine.replace(";", ",").split(",")
        if value.strip()
    }
    return wanted in cuisines
