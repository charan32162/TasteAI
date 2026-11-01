"""Conservative canonical dish and alias management.

Names are normalized for exact lookup only. Similarity never creates a merge.
Suggested aliases remain pending until a reviewer explicitly approves them.
"""

from datetime import timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .menu_evidence import normalize_dish_name
from .models import (
    CanonicalDish,
    DishAlias,
    DishEvidenceLink,
    MenuDocument,
    MenuItemEvidence,
    MenuSource,
    utc_now,
)


class CanonicalDishError(ValueError):
    """A canonical dish operation violated its validation or review gate."""


def create_canonical_dish(
    db: Session,
    *,
    canonical_name: str,
    cuisine: str | None = None,
    dietary_type: str | None = None,
    description: str | None = None,
) -> CanonicalDish:
    name = _clean_text(canonical_name, "Canonical dish name", 160)
    normalized = normalize_dish_name(name)
    if not normalized:
        raise CanonicalDishError("Canonical dish name must contain letters or numbers.")
    if cuisine is not None:
        cuisine = _clean_text(cuisine, "Cuisine", 80)
    if dietary_type is not None:
        dietary_type = _clean_text(dietary_type, "Dietary type", 40).casefold()
    if description is not None:
        description = _clean_text(description, "Description", 500)
    existing = db.scalar(select(CanonicalDish).where(CanonicalDish.normalized_name == normalized))
    if existing is not None:
        raise CanonicalDishError("A canonical dish with this normalized name already exists.")
    dish = CanonicalDish(
        id=str(uuid4()),
        canonical_name=name,
        normalized_name=normalized,
        cuisine=cuisine,
        dietary_type=dietary_type,
        description=description,
        status="active",
    )
    db.add(dish)
    db.flush()
    return dish


def propose_dish_alias(
    db: Session,
    *,
    canonical_dish_id: str,
    alias: str,
    source: str,
    confidence: float,
) -> DishAlias:
    dish = db.get(CanonicalDish, canonical_dish_id)
    if dish is None or dish.status != "active":
        raise CanonicalDishError("Active canonical dish was not found.")
    clean_alias = _clean_text(alias, "Dish alias", 160)
    normalized_alias = normalize_dish_name(clean_alias)
    if not normalized_alias or normalized_alias == dish.normalized_name:
        raise CanonicalDishError("Alias must differ from the canonical name after normalization.")
    if source not in {"manual_review", "approved_source", "llm_suggestion"}:
        raise CanonicalDishError("Alias source is unsupported.")
    if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not 0 <= confidence <= 1:
        raise CanonicalDishError("Alias confidence must be between 0 and 1.")
    if db.scalar(select(CanonicalDish.id).where(CanonicalDish.normalized_name == normalized_alias)):
        raise CanonicalDishError("Alias already belongs to a canonical name; no merge was made.")
    if db.scalar(select(DishAlias.id).where(DishAlias.normalized_alias == normalized_alias)):
        raise CanonicalDishError("Alias already exists; no merge was made.")
    proposal = DishAlias(
        canonical_dish_id=dish.id,
        alias=clean_alias,
        normalized_alias=normalized_alias,
        source=source,
        confidence=float(confidence),
        review_status="pending",
    )
    db.add(proposal)
    db.flush()
    return proposal


def review_dish_alias(db: Session, alias_id: int, *, reviewer_id: int, approve: bool) -> DishAlias:
    alias = db.get(DishAlias, alias_id)
    if alias is None:
        raise CanonicalDishError("Dish alias proposal was not found.")
    if alias.review_status != "pending":
        raise CanonicalDishError("Only pending alias proposals can be reviewed.")
    if isinstance(reviewer_id, bool) or reviewer_id <= 0:
        raise CanonicalDishError("Reviewer ID is invalid.")
    alias.review_status = "approved" if approve else "rejected"
    alias.reviewed_by = reviewer_id
    alias.reviewed_at = utc_now()
    db.flush()
    return alias


def resolve_canonical_dish(db: Session, name: str) -> CanonicalDish | None:
    normalized = normalize_dish_name(name)
    if not normalized:
        return None
    direct = db.scalar(select(CanonicalDish).where(
        CanonicalDish.normalized_name == normalized,
        CanonicalDish.status == "active",
    ))
    if direct is not None:
        return direct
    return db.scalar(
        select(CanonicalDish)
        .join(DishAlias, DishAlias.canonical_dish_id == CanonicalDish.id)
        .where(
            DishAlias.normalized_alias == normalized,
            DishAlias.review_status == "approved",
            CanonicalDish.status == "active",
        )
    )


def link_verified_evidence(
    db: Session,
    *,
    canonical_dish_id: str,
    evidence_id: int,
    linked_by: str,
    confidence: float = 1.0,
) -> DishEvidenceLink:
    dish = db.get(CanonicalDish, canonical_dish_id)
    evidence = db.get(MenuItemEvidence, evidence_id)
    if dish is None or dish.status != "active":
        raise CanonicalDishError("Active canonical dish was not found.")
    if evidence is None or evidence.evidence_state not in {"verified", "supported"}:
        raise CanonicalDishError("Only verified or supported menu evidence can be linked.")
    document = db.get(MenuDocument, evidence.menu_document_id)
    source = db.get(MenuSource, document.menu_source_id) if document else None
    if source is None or source.authorization_status != "approved":
        raise CanonicalDishError("Evidence source is not approved.")
    normalized_by_link = _clean_text(linked_by, "Link provenance", 120)
    if isinstance(confidence, bool) or not isinstance(confidence, (float, int)) or not 0 <= confidence <= 1:
        raise CanonicalDishError("Link confidence must be between 0 and 1.")
    existing = db.scalar(select(DishEvidenceLink).where(DishEvidenceLink.evidence_id == evidence.id))
    if existing is not None:
        if existing.canonical_dish_id != dish.id:
            raise CanonicalDishError("Evidence is already linked to another canonical dish.")
        return existing
    link = DishEvidenceLink(
        canonical_dish_id=dish.id,
        evidence_id=evidence.id,
        confidence=float(confidence),
        linked_by=normalized_by_link,
    )
    db.add(link)
    db.flush()
    return link


def _clean_text(value: str, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise CanonicalDishError(f"{label} must be text.")
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > maximum:
        raise CanonicalDishError(f"{label} must contain 1–{maximum} characters.")
    return cleaned
