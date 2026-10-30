"""Provider-neutral, provenance-preserving menu evidence storage and queries.

This module never retrieves network resources. Provider adapters may only parse
content that has already been supplied through an authorized ingestion path.
"""

import ipaddress
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    MenuDocument,
    MenuItemEvidence,
    MenuSource,
    RestaurantIdentity,
    RestaurantIdentityLink,
    utc_now,
)
from .structured_data import StructuredMenuParseError, parse_schema_menu


DEFAULT_MENU_FRESHNESS_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_MENU_FRESHNESS_TTL_SECONDS = 365 * 24 * 60 * 60
MAX_MENU_ITEMS_PER_DOCUMENT = 500
MAX_MENU_EVIDENCE_RESULTS = 100
MENU_SOURCE_TYPES = {
    "restaurant_api",
    "authorized_website",
    "restaurant_document",
    "schema_org_jsonld",
    "licensed_provider",
    "local_fixture",
    "user_captured_menu_photo",
}


class IdentityMatchState(StrEnum):
    EXACT_MATCH = "exact_match"
    HIGH_CONFIDENCE_MATCH = "high_confidence_match"
    POSSIBLE_MATCH = "possible_match"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


class EvidenceState(StrEnum):
    VERIFIED = "verified"
    SUPPORTED = "supported"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"
    STALE = "stale"


class AvailabilityState(StrEnum):
    MENU_LISTED = "menu_listed"
    AVAILABILITY_SUPPORTED = "availability_supported"
    AVAILABILITY_UNKNOWN = "availability_unknown"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    STALE = "stale"


class MenuEvidenceError(ValueError):
    """A menu source or evidence record violates the ingestion contract."""


@dataclass(frozen=True)
class RestaurantIdentityCandidate:
    name: str
    address: str | None = None
    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    website_url: str | None = None
    provider_name: str | None = None
    provider_place_id: str | None = None


@dataclass(frozen=True)
class IdentityResolution:
    state: IdentityMatchState
    restaurant_identity_id: str | None
    candidate_identity_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MenuItemDraft:
    name: str
    description: str | None = None
    category: str | None = None
    cuisine: str | None = None
    food_type: str | None = None
    dietary_attributes: tuple[str, ...] = ()
    price_amount: Decimal | None = None
    price_currency: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    variant: str | None = None
    availability_state: AvailabilityState = AvailabilityState.MENU_LISTED
    availability_evidence: str | None = None
    evidence_state: EvidenceState = EvidenceState.SUPPORTED
    confidence: str = "high"
    confidence_basis: str = "explicit_source_field_extraction"


@dataclass(frozen=True)
class MenuEvidenceBatch:
    source_id: str
    provider_name: str
    source_type: str
    source_url: str | None
    captured_at: datetime | None
    content_type: str
    content_sha256: str | None
    parser_version: str | None
    items: tuple[MenuItemDraft, ...]
    document_id: str | None = None


class MenuSourceProvider(Protocol):
    provider_name: str
    source_type: str

    def parse_document(
        self,
        content: str,
        *,
        source_id: str,
        source_url: str,
        captured_at: datetime | None = None,
        content_type: str = "text/html",
    ) -> MenuEvidenceBatch: ...


class SchemaOrgMenuSourceProvider:
    """Adapter for caller-supplied HTML; deliberately has no network client."""

    provider_name = "schema_org_jsonld"
    source_type = "schema_org_jsonld"

    def parse_document(
        self,
        content: str,
        *,
        source_id: str,
        source_url: str,
        captured_at: datetime | None = None,
        content_type: str = "text/html",
    ) -> MenuEvidenceBatch:
        if not isinstance(content, str):
            raise MenuEvidenceError("Schema.org menu content must be text.")
        if not isinstance(content_type, str):
            raise MenuEvidenceError("Schema.org menu content type is invalid.")
        if content_type.split(";", 1)[0].strip().lower() not in {
            "text/html",
            "application/xhtml+xml",
        }:
            raise MenuEvidenceError("Schema.org menu content must be HTML.")
        try:
            parsed = parse_schema_menu(
                content,
                source_url=source_url,
                captured_at=captured_at,
            )
        except StructuredMenuParseError as error:
            raise MenuEvidenceError(str(error)) from error

        drafts: list[MenuItemDraft] = []
        for item in parsed.items:
            prices = item.prices or (None,)
            for price in prices:
                drafts.append(
                    MenuItemDraft(
                        name=item.name,
                        description=item.description,
                        category=item.category,
                        cuisine=item.cuisine,
                        food_type=item.food_type,
                        dietary_attributes=item.dietary_attributes,
                        price_amount=price.amount if price else None,
                        price_currency=price.currency if price else None,
                        price_min=price.minimum if price else None,
                        price_max=price.maximum if price else None,
                        variant=price.variant if price else None,
                    )
                )
        if len(drafts) > MAX_MENU_ITEMS_PER_DOCUMENT:
            raise MenuEvidenceError("Menu document contains too many extracted items.")
        return MenuEvidenceBatch(
            source_id=source_id,
            provider_name=self.provider_name,
            source_type=self.source_type,
            source_url=parsed.source_url,
            captured_at=parsed.captured_at,
            content_type=content_type.split(";", 1)[0].strip().lower(),
            content_sha256=parsed.content_sha256,
            parser_version=parsed.parser_version,
            items=tuple(drafts),
        )


def default_freshness_ttl_seconds() -> int:
    raw = os.getenv(
        "TASTEAI_MENU_EVIDENCE_TTL_SECONDS",
        str(DEFAULT_MENU_FRESHNESS_TTL_SECONDS),
    )
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise MenuEvidenceError("Menu evidence freshness TTL must be an integer.") from error
    if not 60 <= value <= MAX_MENU_FRESHNESS_TTL_SECONDS:
        raise MenuEvidenceError("Menu evidence freshness TTL must be between 60 seconds and one year.")
    return value


def normalize_dish_name(value: str) -> str:
    normalized = _normalized_words(value)
    tokens = ["biryani" if token == "biriyani" else token for token in normalized.split()]
    return " ".join(tokens)


def normalize_restaurant_name(value: str) -> str:
    return _normalized_words(value.replace("&", " and "))


def _normalized_words(value: str) -> str:
    if not isinstance(value, str):
        raise MenuEvidenceError("Names must be text.")
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    normalized = []
    for character in decomposed:
        if unicodedata.combining(character):
            continue
        normalized.append(character if character.isalnum() else " ")
    return " ".join("".join(normalized).split())


def _normalized_address(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalized_words(value)
    return normalized or None


def _normalized_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(character for character in value if character.isdigit())
    return digits or None


def _website_domain(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MenuEvidenceError("Website URL is invalid.")
    try:
        parsed = urlparse(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise MenuEvidenceError("Website URL is invalid.") from error
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        raise MenuEvidenceError("Website URL must use HTTPS on the standard port.")
    _validate_public_hostname(host)
    return host.lower().rstrip(".").removeprefix("www.")


def _validate_public_hostname(host: str) -> None:
    normalized_host = host.lower().rstrip(".")
    if normalized_host == "localhost" or normalized_host.endswith((".localhost", ".local", ".internal")):
        raise MenuEvidenceError("Source URL must use a public hostname.")
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        return
    if not address.is_global:
        raise MenuEvidenceError("Source URL must use a public hostname.")


def validate_provenance_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise MenuEvidenceError("Source URL is invalid.")
    try:
        parsed = urlparse(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise MenuEvidenceError("Source URL is invalid.") from error
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise MenuEvidenceError("Source URL must use HTTPS without credentials, query data, or fragments.")
    _validate_public_hostname(host)
    return value


def create_restaurant_identity(
    db: Session,
    candidate: RestaurantIdentityCandidate,
) -> RestaurantIdentity:
    name, normalized_name, website_domain = _validate_identity_candidate(candidate)
    identity = RestaurantIdentity(
        id=str(uuid4()),
        name=name,
        normalized_name=normalized_name[:200],
        address=" ".join(candidate.address.split())[:500] if candidate.address else None,
        normalized_address=_normalized_address(candidate.address),
        phone=" ".join(candidate.phone.split())[:80] if candidate.phone else None,
        normalized_phone=_normalized_phone(candidate.phone),
        latitude=candidate.latitude,
        longitude=candidate.longitude,
        website_domain=website_domain,
    )
    db.add(identity)
    db.flush()
    if candidate.provider_name and candidate.provider_place_id:
        db.add(
            RestaurantIdentityLink(
                restaurant_identity_id=identity.id,
                provider_name=candidate.provider_name,
                provider_place_id=candidate.provider_place_id,
            )
        )
    db.flush()
    return identity


def _validate_identity_candidate(
    candidate: RestaurantIdentityCandidate,
) -> tuple[str, str, str | None]:
    if not isinstance(candidate, RestaurantIdentityCandidate):
        raise MenuEvidenceError("Restaurant identity candidate is invalid.")
    name = " ".join(candidate.name.split()) if isinstance(candidate.name, str) else ""
    normalized_name = normalize_restaurant_name(name)
    if not name or len(name) > 160 or not normalized_name or len(normalized_name) > 200:
        raise MenuEvidenceError("Restaurant name must contain 1–160 characters.")
    for value, label, limit in (
        (candidate.address, "Restaurant address", 500),
        (candidate.phone, "Restaurant phone", 80),
    ):
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise MenuEvidenceError(f"{label} is too long or invalid.")
    if (
        (candidate.latitude is None) != (candidate.longitude is None)
        or (candidate.latitude is not None and (
            isinstance(candidate.latitude, bool)
            or isinstance(candidate.longitude, bool)
            or not isinstance(candidate.latitude, (int, float))
            or not isinstance(candidate.longitude, (int, float))
            or not math.isfinite(candidate.latitude)
            or not math.isfinite(candidate.longitude)
            or not -90 <= candidate.latitude <= 90
            or not -180 <= candidate.longitude <= 180
        ))
    ):
        raise MenuEvidenceError("Restaurant coordinates must be a valid latitude/longitude pair.")
    website_domain = _website_domain(candidate.website_url)
    if candidate.provider_name and candidate.provider_place_id:
        _validate_identifier(candidate.provider_name, "Provider name", 100)
        _validate_identifier(candidate.provider_place_id, "Provider place ID", 255)
    elif candidate.provider_name or candidate.provider_place_id:
        raise MenuEvidenceError("Provider name and place ID must be supplied together.")
    return name, normalized_name, website_domain


def resolve_restaurant_identity(
    db: Session,
    candidate: RestaurantIdentityCandidate,
) -> IdentityResolution:
    _, normalized_name, _ = _validate_identity_candidate(candidate)
    if not normalized_name:
        return IdentityResolution(IdentityMatchState.UNRESOLVED, None)
    if candidate.provider_name and candidate.provider_place_id:
        link = db.scalar(
            select(RestaurantIdentityLink).where(
                RestaurantIdentityLink.provider_name == candidate.provider_name,
                RestaurantIdentityLink.provider_place_id == candidate.provider_place_id,
            )
        )
        if link is not None:
            identity = db.get(RestaurantIdentity, link.restaurant_identity_id)
            if identity is None or _identity_conflicts(candidate, identity):
                return IdentityResolution(IdentityMatchState.CONFLICT, None, (link.restaurant_identity_id,))
            return IdentityResolution(
                IdentityMatchState.EXACT_MATCH,
                link.restaurant_identity_id,
                (link.restaurant_identity_id,),
            )

    identities = list(
        db.scalars(
            select(RestaurantIdentity).where(
                RestaurantIdentity.normalized_name == normalized_name
            )
        )
    )
    if not identities:
        return IdentityResolution(IdentityMatchState.UNRESOLVED, None)
    conflicts = [identity for identity in identities if _identity_conflicts(candidate, identity)]
    eligible = [identity for identity in identities if identity not in conflicts]
    exact = [identity for identity in eligible if _identity_exact_match(candidate, identity)]
    high = [identity for identity in eligible if _identity_high_confidence(candidate, identity)]
    if len(exact) == 1:
        identity = exact[0]
        return IdentityResolution(
            IdentityMatchState.EXACT_MATCH, identity.id, (identity.id,)
        )
    if len(exact) > 1 or len(high) > 1:
        ids = tuple(identity.id for identity in exact or high)
        return IdentityResolution(IdentityMatchState.CONFLICT, None, ids)
    if len(high) == 1:
        identity = high[0]
        return IdentityResolution(
            IdentityMatchState.HIGH_CONFIDENCE_MATCH, identity.id, (identity.id,)
        )
    if eligible:
        return IdentityResolution(
            IdentityMatchState.POSSIBLE_MATCH,
            None,
            tuple(identity.id for identity in eligible),
        )
    return IdentityResolution(
        IdentityMatchState.CONFLICT,
        None,
        tuple(identity.id for identity in conflicts),
    )


def _identity_conflicts(
    candidate: RestaurantIdentityCandidate,
    identity: RestaurantIdentity,
) -> bool:
    if normalize_restaurant_name(candidate.name) != identity.normalized_name:
        return True
    candidate_phone = _normalized_phone(candidate.phone)
    candidate_address = _normalized_address(candidate.address)
    candidate_domain = _website_domain(candidate.website_url) if candidate.website_url else None
    if candidate_phone and identity.normalized_phone and candidate_phone != identity.normalized_phone:
        return True
    if candidate_domain and identity.website_domain and candidate_domain != identity.website_domain:
        return True
    distance = _distance_meters(
        candidate.latitude,
        candidate.longitude,
        identity.latitude,
        identity.longitude,
    )
    if distance is not None and distance > 500:
        return True
    if candidate_address and identity.normalized_address and candidate_address != identity.normalized_address:
        if distance is None or distance > 500:
            return True
    return False


def _identity_exact_match(
    candidate: RestaurantIdentityCandidate,
    identity: RestaurantIdentity,
) -> bool:
    name_matches = normalize_restaurant_name(candidate.name) == identity.normalized_name
    address_matches = (
        _normalized_address(candidate.address) is not None
        and _normalized_address(candidate.address) == identity.normalized_address
    )
    phone_matches = (
        _normalized_phone(candidate.phone) is not None
        and _normalized_phone(candidate.phone) == identity.normalized_phone
    )
    return name_matches and (address_matches or phone_matches)


def _identity_high_confidence(
    candidate: RestaurantIdentityCandidate,
    identity: RestaurantIdentity,
) -> bool:
    name_matches = normalize_restaurant_name(candidate.name) == identity.normalized_name
    candidate_domain = _website_domain(candidate.website_url) if candidate.website_url else None
    distance = _distance_meters(
        candidate.latitude,
        candidate.longitude,
        identity.latitude,
        identity.longitude,
    )
    return bool(
        name_matches
        and candidate_domain
        and candidate_domain == identity.website_domain
        and distance is not None
        and distance <= 100
    )


def _distance_meters(
    first_latitude: float | None,
    first_longitude: float | None,
    second_latitude: float | None,
    second_longitude: float | None,
) -> float | None:
    if None in (first_latitude, first_longitude, second_latitude, second_longitude):
        return None
    from math import asin, cos, radians, sin, sqrt

    lat1, lat2 = radians(first_latitude), radians(second_latitude)
    delta_lat = lat2 - lat1
    delta_lon = radians(second_longitude - first_longitude)
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 6_371_008.8 * 2 * asin(sqrt(min(1.0, max(0.0, value))))


def register_menu_source(
    db: Session,
    *,
    restaurant_identity_id: str,
    source_type: str,
    provider_name: str,
    source_identifier: str | None = None,
    source_url: str | None = None,
    attribution: str | None = None,
    freshness_ttl_seconds: int | None = None,
) -> MenuSource:
    if db.get(RestaurantIdentity, restaurant_identity_id) is None:
        raise MenuEvidenceError("Restaurant identity does not exist.")
    if not isinstance(source_type, str) or source_type not in MENU_SOURCE_TYPES:
        raise MenuEvidenceError("Menu source type is not supported.")
    _validate_identifier(provider_name, "Provider name", 100)
    if source_identifier is not None:
        _validate_identifier(source_identifier, "Source identifier", 255)
    if attribution is not None and (
        not isinstance(attribution, str) or len(attribution) > 255
    ):
        raise MenuEvidenceError("Source attribution is too long or invalid.")
    source_url = validate_provenance_url(source_url)
    ttl = (
        default_freshness_ttl_seconds()
        if freshness_ttl_seconds is None
        else freshness_ttl_seconds
    )
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 60 <= ttl <= MAX_MENU_FRESHNESS_TTL_SECONDS:
        raise MenuEvidenceError("Menu evidence freshness TTL must be between 60 seconds and one year.")
    source = MenuSource(
        id=str(uuid4()),
        restaurant_identity_id=restaurant_identity_id,
        source_type=source_type,
        provider_name=provider_name,
        source_identifier=source_identifier,
        source_url=source_url,
        attribution=" ".join(attribution.split()) if attribution else None,
        authorization_status="pending",
        freshness_ttl_seconds=ttl,
    )
    db.add(source)
    db.flush()
    return source


def approve_menu_source(
    db: Session,
    source_id: str,
    *,
    approved_by: str,
    authorization_reference: str,
) -> MenuSource:
    source = db.get(MenuSource, source_id)
    if source is None:
        raise MenuEvidenceError("Menu source does not exist.")
    _validate_identifier(approved_by, "Approver", 120)
    _validate_identifier(authorization_reference, "Authorization reference", 255)
    if source.authorization_status == "revoked":
        raise MenuEvidenceError("A revoked menu source cannot be re-approved.")
    source.authorization_status = "approved"
    source.authorized_by = approved_by.strip()
    source.authorization_reference = authorization_reference.strip()
    db.flush()
    return source


def revoke_menu_source(db: Session, source_id: str) -> MenuSource:
    source = db.get(MenuSource, source_id)
    if source is None:
        raise MenuEvidenceError("Menu source does not exist.")
    source.authorization_status = "revoked"
    db.flush()
    return source


def ingest_menu_batch(db: Session, batch: MenuEvidenceBatch) -> MenuDocument:
    if not isinstance(batch, MenuEvidenceBatch):
        raise MenuEvidenceError("Menu evidence batch is invalid.")
    if not isinstance(batch.items, (tuple, list)):
        raise MenuEvidenceError("Menu evidence items must be a sequence.")
    source = db.get(MenuSource, batch.source_id)
    if source is None or source.authorization_status != "approved":
        raise MenuEvidenceError("Menu source must be approved before evidence can be stored.")
    if batch.provider_name != source.provider_name or batch.source_type != source.source_type:
        raise MenuEvidenceError("Menu evidence provider does not match its registered source.")
    source_url = validate_provenance_url(batch.source_url)
    if source.source_url != source_url:
        raise MenuEvidenceError("Menu evidence URL does not match the approved source.")
    if not batch.content_type or len(batch.content_type) > 100:
        raise MenuEvidenceError("Menu content type is invalid.")
    if batch.captured_at is None and batch.document_id is None:
        raise MenuEvidenceError("Capture time must include a timezone.")
    captured_at = _aware_utc(batch.captured_at, "Capture time") if batch.captured_at is not None else None
    if captured_at is not None and captured_at > utc_now() + timedelta(minutes=5):
        raise MenuEvidenceError("Capture time cannot be in the future.")
    if batch.content_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", batch.content_sha256):
        raise MenuEvidenceError("Menu content hash is invalid.")
    if len(batch.items) > MAX_MENU_ITEMS_PER_DOCUMENT:
        raise MenuEvidenceError("Menu document contains too many items.")

    if batch.document_id:
        document = db.get(MenuDocument, batch.document_id)
        document_capture = document.captured_at if document is not None else None
        if document_capture is not None:
            document_capture = (
                document_capture.replace(tzinfo=timezone.utc)
                if document_capture.tzinfo is None or document_capture.utcoffset() is None
                else document_capture.astimezone(timezone.utc)
            )
        if (
            document is None
            or document.menu_source_id != source.id
            or document.content_type != batch.content_type
            or document.content_sha256 != batch.content_sha256
            or (captured_at is not None and document_capture != captured_at)
        ):
            raise MenuEvidenceError("Menu evidence does not match the registered photo document.")
        if batch.parser_version:
            document.parser_version = batch.parser_version
    else:
        document = MenuDocument(
            id=str(uuid4()),
            menu_source_id=source.id,
            captured_at=captured_at,
            content_sha256=batch.content_sha256,
            parser_version=batch.parser_version,
            content_type=batch.content_type,
        )
        db.add(document)
        db.flush()
    for item in batch.items:
        _validate_menu_item(item)
        normalized_name = normalize_dish_name(item.name)
        normalized_variant = normalize_dish_name(item.variant or "")
        db.add(
            MenuItemEvidence(
                menu_document_id=document.id,
                name=" ".join(item.name.split()),
                normalized_name=normalized_name,
                description=" ".join(item.description.split()) if item.description else None,
                category=item.category,
                cuisine=item.cuisine,
                food_type=item.food_type,
                dietary_attributes=list(item.dietary_attributes),
                price_state="known" if any(
                    value is not None
                    for value in (item.price_amount, item.price_min, item.price_max)
                ) else "unknown",
                price_amount=item.price_amount,
                price_currency=item.price_currency,
                price_min=item.price_min,
                price_max=item.price_max,
                variant=" ".join(item.variant.split()) if item.variant else None,
                normalized_variant=normalized_variant,
                availability_state=item.availability_state.value,
                availability_evidence=item.availability_evidence,
                evidence_state=item.evidence_state.value,
                confidence=item.confidence,
                confidence_basis=item.confidence_basis,
                source_metadata={},
            )
        )
    db.flush()
    return document


def verify_menu_document(
    db: Session,
    document_id: str,
    *,
    verified_by: str,
) -> MenuDocument:
    document = db.get(MenuDocument, document_id)
    if document is None:
        raise MenuEvidenceError("Menu document does not exist.")
    source = db.get(MenuSource, document.menu_source_id)
    if source is None or source.authorization_status != "approved":
        raise MenuEvidenceError("Only an approved menu source can be verified.")
    _validate_identifier(verified_by, "Verifier", 120)
    now = utc_now()
    document.last_verified_at = now
    document.verified_by = verified_by.strip()
    for evidence in db.scalars(
        select(MenuItemEvidence).where(MenuItemEvidence.menu_document_id == document.id)
    ):
        evidence.evidence_state = EvidenceState.VERIFIED.value
        evidence.confidence = "high"
        evidence.confidence_basis = f"document_review:{verified_by.strip()}"[:120]
    db.flush()
    return document


def restaurant_menu_evidence(
    db: Session,
    restaurant_identity_id: str,
    *,
    limit: int = MAX_MENU_EVIDENCE_RESULTS,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_MENU_EVIDENCE_RESULTS:
        raise MenuEvidenceError(f"Result limit must be between 1 and {MAX_MENU_EVIDENCE_RESULTS}.")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
        raise MenuEvidenceError("Result offset must be between 0 and 100000.")
    identity = db.get(RestaurantIdentity, restaurant_identity_id)
    if identity is None:
        return None
    rows = _approved_evidence_rows(db, restaurant_identity_id)
    effective_now = _aware_utc(now or utc_now(), "Current time")
    conflicts = _conflicting_prices(rows, effective_now)
    items = []
    for evidence, document, source in rows[offset:offset + limit]:
        stale = _is_stale(document, source, effective_now)
        conflict_key = (source.restaurant_identity_id, evidence.normalized_name, evidence.normalized_variant)
        evidence_state = (
            EvidenceState.STALE.value if stale
            else EvidenceState.CONTRADICTED.value if conflict_key in conflicts
            else evidence.evidence_state
        )
        availability_state = (
            AvailabilityState.STALE.value if stale
            else evidence.availability_state
        )
        items.append(
            {
                "evidence_id": evidence.id,
                "name": evidence.name,
                "normalized_name": evidence.normalized_name,
                "description": evidence.description,
                "category": evidence.category,
                "cuisine": evidence.cuisine,
                "food_type": evidence.food_type,
                "dietary_attributes": evidence.dietary_attributes,
                "price": {
                    "state": evidence.price_state,
                    "amount": _decimal_string(evidence.price_amount),
                    "currency": evidence.price_currency,
                    "minimum": _decimal_string(evidence.price_min),
                    "maximum": _decimal_string(evidence.price_max),
                    "variant": evidence.variant,
                },
                "availability": {
                    "state": availability_state,
                    "evidence": evidence.availability_evidence,
                    "available_now": (
                        None if evidence_state == EvidenceState.CONTRADICTED.value
                        else False if availability_state == AvailabilityState.TEMPORARILY_UNAVAILABLE.value
                        else True if (
                            availability_state == AvailabilityState.AVAILABILITY_SUPPORTED.value
                            and evidence_state != EvidenceState.CONTRADICTED.value
                        )
                        else None
                    ),
                },
                "evidence_state": evidence_state,
                "confidence": {
                    "level": evidence.confidence,
                    "basis": evidence.confidence_basis,
                    "scope": "extraction_or_document_review_not_source_authority",
                },
                "freshness": {
                    "state": "stale" if stale else "fresh",
                    "captured_at": document.captured_at,
                    "last_verified_at": document.last_verified_at,
                    "expires_at": _freshness_reference(document) + timedelta(
                        seconds=source.freshness_ttl_seconds
                    ),
                    "ttl_seconds": source.freshness_ttl_seconds,
                },
                "provenance": {
                    "source_id": source.id,
                    "source_type": source.source_type,
                    "provider_name": source.provider_name,
                    "source_identifier": source.source_identifier,
                    "source_url": source.source_url,
                    "attribution": source.attribution,
                    "authorization_status": source.authorization_status,
                    "captured_at": document.captured_at,
                    "content_sha256": document.content_sha256,
                    "parser_version": document.parser_version,
                    "content_type": document.content_type,
                },
            }
        )
    return {
        "restaurant": {
            "id": identity.id,
            "name": identity.name,
            "address": identity.address,
            "website_domain": identity.website_domain,
        },
        "items": items,
        "truncated": offset + limit < len(rows),
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "source_count": len({source.id for _, _, source in rows}),
    }


def restaurants_with_dish_evidence(
    db: Session,
    dish_name: str,
    *,
    now: datetime | None = None,
    include_stale: bool = False,
    include_temporarily_unavailable: bool = False,
    include_conflicted: bool = False,
) -> list[dict[str, Any]]:
    if not all(
        isinstance(value, bool)
        for value in (include_stale, include_temporarily_unavailable, include_conflicted)
    ):
        raise MenuEvidenceError("Evidence inclusion policies must be booleans.")
    normalized_name = normalize_dish_name(dish_name)
    if not normalized_name:
        return []
    effective_now = _aware_utc(now or utc_now(), "Current time")
    rows = _approved_evidence_rows(db, normalized_name=normalized_name)
    conflicts = _conflicting_prices(rows, effective_now)
    matches = []
    for evidence, document, source in rows:
        stale = _is_stale(document, source, effective_now)
        if stale and not include_stale:
            continue
        if evidence.evidence_state not in {
            EvidenceState.SUPPORTED.value,
            EvidenceState.VERIFIED.value,
        }:
            continue
        if (
            evidence.availability_state == AvailabilityState.TEMPORARILY_UNAVAILABLE.value
            and not include_temporarily_unavailable
        ):
            continue
        conflict_key = (source.restaurant_identity_id, evidence.normalized_name, evidence.normalized_variant)
        if conflict_key in conflicts and not include_conflicted:
            continue
        identity = db.get(RestaurantIdentity, source.restaurant_identity_id)
        if identity is None:
            continue
        availability_state = evidence.availability_state
        matches.append(
            {
                "restaurant_identity_id": source.restaurant_identity_id,
                "restaurant_name": identity.name,
                "restaurant_address": identity.address,
                "identity_match_state": "linked_canonical_identity",
                "identity_confidence": "canonical_identity_record_linked",
                "evidence_id": evidence.id,
                "dish_name": evidence.name,
                "normalized_dish_name": evidence.normalized_name,
                "description": evidence.description,
                "category": evidence.category,
                "cuisine": evidence.cuisine,
                "food_type": evidence.food_type,
                "dietary_attributes": evidence.dietary_attributes,
                "variant": evidence.variant,
                "price": {
                    "state": evidence.price_state,
                    "amount": _decimal_string(evidence.price_amount),
                    "currency": evidence.price_currency,
                    "minimum": _decimal_string(evidence.price_min),
                    "maximum": _decimal_string(evidence.price_max),
                    "variant": evidence.variant,
                },
                "availability_state": availability_state,
                "available_now": (
                    None if stale
                    else False if availability_state == AvailabilityState.TEMPORARILY_UNAVAILABLE.value
                    else True if availability_state == AvailabilityState.AVAILABILITY_SUPPORTED.value
                    else None
                ),
                "evidence_state": evidence.evidence_state,
                "confidence": {
                    "level": evidence.confidence,
                    "basis": evidence.confidence_basis,
                    "scope": "extraction_or_document_review_not_source_authority",
                },
                "freshness": {
                    "state": "stale" if stale else "fresh",
                    "captured_at": document.captured_at,
                    "last_verified_at": document.last_verified_at,
                    "expires_at": _freshness_reference(document) + timedelta(
                        seconds=source.freshness_ttl_seconds
                    ),
                    "ttl_seconds": source.freshness_ttl_seconds,
                },
                "source_id": source.id,
                "source_type": source.source_type,
                "provider_name": source.provider_name,
                "source_identifier": source.source_identifier,
                "source_url": source.source_url,
                "attribution": source.attribution,
                "captured_at": document.captured_at,
            }
        )
    return matches


def _approved_evidence_rows(
    db: Session,
    restaurant_identity_id: str | None = None,
    *,
    normalized_name: str | None = None,
) -> list[tuple[MenuItemEvidence, MenuDocument, MenuSource]]:
    statement = (
        select(MenuItemEvidence, MenuDocument, MenuSource)
        .join(MenuDocument, MenuDocument.id == MenuItemEvidence.menu_document_id)
        .join(MenuSource, MenuSource.id == MenuDocument.menu_source_id)
        .where(MenuSource.authorization_status == "approved")
        .order_by(MenuDocument.captured_at.desc(), MenuItemEvidence.id)
    )
    if restaurant_identity_id is not None:
        statement = statement.where(MenuSource.restaurant_identity_id == restaurant_identity_id)
    if normalized_name is not None:
        statement = statement.where(MenuItemEvidence.normalized_name == normalized_name)
    return list(db.execute(statement))


def _conflicting_prices(
    rows: list[tuple[MenuItemEvidence, MenuDocument, MenuSource]],
    now: datetime,
) -> set[tuple[str, str, str]]:
    prices: dict[tuple[str, str, str], set[tuple[str | None, str, str, str]]] = {}
    for evidence, document, source in rows:
        key = (
            source.restaurant_identity_id,
            evidence.normalized_name,
            evidence.normalized_variant,
        )
        if _is_stale(document, source, now):
            continue
        if evidence.price_state == "known":
            prices.setdefault(key, set()).add(
                (
                    evidence.price_currency,
                    _decimal_string(evidence.price_amount) or "",
                    _decimal_string(evidence.price_min) or "",
                    _decimal_string(evidence.price_max) or "",
                )
            )
    conflicts = {key for key, values in prices.items() if len(values) > 1}
    availability: dict[tuple[str, str, str], set[str]] = {}
    for evidence, document, source in rows:
        key = (
            source.restaurant_identity_id,
            evidence.normalized_name,
            evidence.normalized_variant,
        )
        if _is_stale(document, source, now):
            continue
        if evidence.availability_state in {
            AvailabilityState.AVAILABILITY_SUPPORTED.value,
            AvailabilityState.TEMPORARILY_UNAVAILABLE.value,
        }:
            availability.setdefault(key, set()).add(evidence.availability_state)
    conflicts.update(
        key for key, states in availability.items()
        if AvailabilityState.AVAILABILITY_SUPPORTED.value in states
        and AvailabilityState.TEMPORARILY_UNAVAILABLE.value in states
    )
    return conflicts


def _is_stale(document: MenuDocument, source: MenuSource, now: datetime) -> bool:
    reference = _freshness_reference(document)
    if reference is None:
        return True
    return now >= reference + timedelta(seconds=source.freshness_ttl_seconds)


def _freshness_reference(document: MenuDocument) -> datetime | None:
    stored = document.last_verified_at or document.captured_at
    if stored is None:
        return None
    if stored.tzinfo is None or stored.utcoffset() is None:
        stored = stored.replace(tzinfo=timezone.utc)
    return stored.astimezone(timezone.utc)


def _aware_utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MenuEvidenceError(f"{field} must include a timezone.")
    return value.astimezone(timezone.utc)


def _decimal_string(value: Any) -> str | None:
    if value is None:
        return None
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal_value, "f")


def _validate_identifier(value: Any, label: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise MenuEvidenceError(f"{label} must contain 1–{limit} characters.")


def _validate_menu_item(item: MenuItemDraft) -> None:
    if not isinstance(item, MenuItemDraft):
        raise MenuEvidenceError("Menu item evidence is invalid.")
    if not isinstance(item.availability_state, AvailabilityState):
        raise MenuEvidenceError("Availability state is invalid.")
    if not isinstance(item.evidence_state, EvidenceState):
        raise MenuEvidenceError("Evidence state is invalid.")
    _validate_identifier(item.name, "Menu item name", 160)
    normalized_name = normalize_dish_name(item.name)
    if not normalized_name or len(normalized_name) > 200:
        raise MenuEvidenceError("Menu item name must contain letters or numbers.")
    if item.description is not None and (
        not isinstance(item.description, str) or len(item.description) > 500
    ):
        raise MenuEvidenceError("Menu description is too long.")
    if item.category is not None and (
        not isinstance(item.category, str) or len(item.category) > 120
    ):
        raise MenuEvidenceError("Menu category is too long.")
    if item.cuisine is not None and (
        not isinstance(item.cuisine, str) or len(item.cuisine) > 120
    ):
        raise MenuEvidenceError("Cuisine value is too long.")
    if item.food_type is not None and (
        not isinstance(item.food_type, str) or len(item.food_type) > 120
    ):
        raise MenuEvidenceError("Food type is too long.")
    if item.variant is not None:
        _validate_identifier(item.variant, "Menu variant", 80)
    if item.availability_state in {
        AvailabilityState.AVAILABILITY_SUPPORTED,
        AvailabilityState.TEMPORARILY_UNAVAILABLE,
    }:
        _validate_identifier(item.availability_evidence, "Availability evidence", 240)
    if item.availability_state == AvailabilityState.AVAILABILITY_UNKNOWN and item.availability_evidence:
        raise MenuEvidenceError("Unknown availability cannot include supporting evidence.")
    if (
        item.availability_state not in {
            AvailabilityState.AVAILABILITY_SUPPORTED,
            AvailabilityState.TEMPORARILY_UNAVAILABLE,
        }
        and item.availability_evidence
    ):
        raise MenuEvidenceError("Availability evidence requires an explicit availability state.")
    if item.evidence_state not in {
        EvidenceState.SUPPORTED,
        EvidenceState.UNKNOWN,
    }:
        raise MenuEvidenceError("Provider imports may only create supported or unknown evidence.")
    if not isinstance(item.confidence, str) or item.confidence not in {"high", "medium", "low", "unknown"}:
        raise MenuEvidenceError("Confidence level is invalid.")
    _validate_identifier(item.confidence_basis, "Confidence basis", 120)
    if not isinstance(item.dietary_attributes, (tuple, list)) or len(item.dietary_attributes) > 20:
        raise MenuEvidenceError("Too many dietary attributes.")
    for attribute in item.dietary_attributes:
        _validate_identifier(attribute, "Dietary attribute", 80)
    values = (item.price_amount, item.price_min, item.price_max)
    if any(value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0 or value > Decimal("1000000")) for value in values):
        raise MenuEvidenceError("Menu prices must be finite non-negative amounts no greater than 1000000.")
    if any(
        value is not None and max(0, -value.as_tuple().exponent) > 2
        for value in values
    ):
        raise MenuEvidenceError("Menu prices may have at most two decimal places.")
    if item.price_min is not None and item.price_max is not None and item.price_min > item.price_max:
        raise MenuEvidenceError("Minimum menu price cannot exceed maximum price.")
    if item.price_currency is not None and (
        not isinstance(item.price_currency, str)
        or not re.fullmatch(r"[A-Z]{3}", item.price_currency)
    ):
        raise MenuEvidenceError("Currency must be an explicit three-letter code.")
    if (
        item.price_amount is None
        and item.price_min is None
        and item.price_max is None
        and item.price_currency is not None
    ):
        raise MenuEvidenceError("Currency without an explicit price is not supported.")
