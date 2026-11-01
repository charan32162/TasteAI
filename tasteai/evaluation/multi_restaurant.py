"""Reusable synthetic multi-restaurant evidence fixture for offline evaluation."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from ..evidence_discovery import search_menu_evidence
from ..menu_evidence import (
    AvailabilityState,
    EvidenceState,
    MenuEvidenceBatch,
    MenuItemDraft,
    RestaurantIdentityCandidate,
    approve_menu_source,
    create_restaurant_identity,
    ingest_menu_batch,
    register_menu_source,
    revoke_menu_source,
    verify_menu_document,
)
from ..models import CanonicalDish, DishAlias, DishEvidenceLink, MenuItemEvidence, User


@dataclass(frozen=True)
class MultiRestaurantFixture:
    restaurant_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    canonical_dish_id: str
    alias: str
    stale_source_id: str
    revoked_source_id: str


def build_multi_restaurant_fixture(
    db: Session,
    *,
    now: datetime | None = None,
) -> MultiRestaurantFixture:
    """Seed synthetic records only; no provider, network, or real restaurant data is used."""
    now = now or datetime.now(timezone.utc)
    canonical = CanonicalDish(
        id=str(uuid4()),
        canonical_name="Chicken Biryani",
        normalized_name="chicken biryani",
        cuisine="Indian",
        dietary_type="non-vegetarian",
        description="Synthetic canonical dish for offline evaluation.",
    )
    db.add(canonical)
    db.flush()
    reviewer = User(
        name="Synthetic Reviewer",
        email=f"synthetic-reviewer-{uuid4()}@example.test",
        password_hash="synthetic-only",
    )
    db.add(reviewer)
    db.flush()
    alias = DishAlias(
        canonical_dish_id=canonical.id,
        alias="Murgh Biryani",
        normalized_alias="murgh biryani",
        source="synthetic_fixture",
        confidence=1.0,
        review_status="approved",
        reviewed_by=reviewer.id,
        reviewed_at=now,
    )
    db.add(alias)
    db.flush()

    identities = []
    for name in ("Curry House", "Coastal Kitchen", "Green Table"):
        identity = create_restaurant_identity(
            db,
            RestaurantIdentityCandidate(
                name,
                address=f"{name}, Synthetic City",
                provider_name="synthetic_fixture",
                provider_place_id=f"synthetic-{name.casefold().replace(' ', '-')}",
            ),
        )
        identities.append(identity)
    db.flush()

    def source(identity_id: str, identifier: str, ttl: int = 86400):
        record = register_menu_source(
            db,
            restaurant_identity_id=identity_id,
            source_type="local_fixture",
            provider_name="synthetic_fixture",
            source_identifier=identifier,
            source_url=f"https://synthetic.example/{identifier}",
            attribution="Synthetic offline fixture",
            freshness_ttl_seconds=ttl,
        )
        approve_menu_source(
            db,
            record.id,
            approved_by="synthetic-reviewer",
            authorization_reference="synthetic-evaluation",
        )
        return record

    fresh_sources = [source(identity.id, f"fresh-{index}") for index, identity in enumerate(identities)]
    stale = source(identities[0].id, "stale", ttl=60)
    revoked = source(identities[1].id, "revoked")
    revoke_menu_source(db, revoked.id)

    def ingest(record, items, captured_at):
        document = ingest_menu_batch(
            db,
            MenuEvidenceBatch(
                source_id=record.id,
                provider_name=record.provider_name,
                source_type=record.source_type,
                source_url=record.source_url,
                captured_at=captured_at,
                content_type="application/json",
                content_sha256=None,
                parser_version="synthetic-fixture-v1",
                items=tuple(items),
            ),
        )
        return document

    first_document = ingest(
        fresh_sources[0],
        [
            MenuItemDraft(
                name="Chicken Biryani",
                description="Spicy chicken and rice.",
                cuisine="Indian",
                food_type="chicken",
                price_amount=Decimal("260"),
                price_currency="INR",
                confidence="high",
            ),
            MenuItemDraft(
                name="Paneer Tikka",
                cuisine="Indian",
                food_type="vegetarian",
                dietary_attributes=("vegetarian",),
                price_amount=Decimal("220"),
                price_currency="INR",
            ),
        ],
        now - timedelta(minutes=5),
    )
    verify_menu_document(db, first_document.id, verified_by="synthetic-reviewer")
    ingest(
        fresh_sources[1],
        [
            MenuItemDraft(
                name="Murgh Biryani",
                description="Alias-labelled chicken rice dish.",
                cuisine="Indian",
                food_type="chicken",
                price_amount=Decimal("290"),
                price_currency="INR",
                confidence="medium",
            ),
            MenuItemDraft(
                name="Fish Curry",
                cuisine="Coastal",
                food_type="fish",
                price_amount=Decimal("240"),
                price_currency="INR",
            ),
        ],
        now - timedelta(minutes=4),
    )
    ingest(
        fresh_sources[2],
        [
            MenuItemDraft(
                name="Chicken Biryani",
                cuisine="Indian",
                food_type="chicken",
                price_amount=Decimal("310"),
                price_currency="INR",
            ),
            MenuItemDraft(
                name="Seasonal Bowl",
                cuisine="Fusion",
                food_type="unknown",
                evidence_state=EvidenceState.UNKNOWN,
                confidence="unknown",
            ),
            MenuItemDraft(
                name="Unavailable Biryani",
                cuisine="Indian",
                food_type="chicken",
                price_amount=Decimal("250"),
                price_currency="INR",
                availability_state=AvailabilityState.TEMPORARILY_UNAVAILABLE,
                availability_evidence="Synthetic closure notice",
            ),
        ],
        now - timedelta(minutes=3),
    )
    ingest(
        stale,
        [
            MenuItemDraft(
                name="Chicken Biryani",
                cuisine="Indian",
                food_type="chicken",
                price_amount=Decimal("180"),
                price_currency="INR",
            ),
        ],
        now - timedelta(days=2),
    )
    db.flush()
    for evidence in db.query(MenuItemEvidence).all():
        if evidence.normalized_name in {"chicken biryani", "murgh biryani"}:
            db.add(DishEvidenceLink(
                canonical_dish_id=canonical.id,
                evidence_id=evidence.id,
                confidence=1.0,
                linked_by="synthetic-fixture",
            ))
    db.commit()
    return MultiRestaurantFixture(
        restaurant_ids=tuple(identity.id for identity in identities),
        source_ids=tuple(record.id for record in fresh_sources),
        canonical_dish_id=canonical.id,
        alias=alias.alias,
        stale_source_id=stale.id,
        revoked_source_id=revoked.id,
    )


def evaluate_multi_restaurant_fixture(db: Session, fixture: MultiRestaurantFixture, *, now: datetime) -> dict:
    """Return deterministic checks suitable for the strict offline evaluator."""
    alias_result = search_menu_evidence(db, "Murgh Biryani under ₹300", budget_max=300, now=now)
    chicken_result = search_menu_evidence(db, "Chicken Biryani under ₹300", budget_max=300, now=now)
    veg_result = search_menu_evidence(db, "Chicken Biryani vegetarian", budget_max=300, now=now)
    return {
        "alias_matches": alias_result["result_count"] == 2,
        "budget_excludes_over_limit": all(
            result["price"]["amount"] is None or Decimal(result["price"]["amount"]) <= 300
            for result in chicken_result["results"]
        ),
        "wrong_diet_fails_closed": veg_result["result_count"] == 0,
        "stale_and_revoked_not_returned": all(
            result["restaurant"]["identity_id"] != fixture.restaurant_ids[0]
            or result["freshness"]["state"] == "fresh"
            for result in chicken_result["results"]
        ),
        "all_results_are_cited": all(
            result["explanation"]["citations"] for result in chicken_result["results"]
        ),
    }
