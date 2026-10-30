"""Deterministic, evidence-grounded search over normalized menu records."""

import json
import math
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .engine import parse_intent
from .menu_evidence import normalize_dish_name, restaurants_with_dish_evidence
from .models import CanonicalDish, DishAlias, TasteProfile


DEFAULT_EVIDENCE_RANKING_WEIGHTS = {
    "dish_match": 0.25,
    "evidence_confidence": 0.25,
    "freshness": 0.20,
    "price_fit": 0.15,
    "taste_match": 0.10,
    "availability_confidence": 0.05,
}
CONFIDENCE_SCORES = {"high": 100.0, "medium": 70.0, "low": 40.0, "unknown": 0.0}
INGREDIENT_EVIDENCE_FIELDS = ("dish_name", "description", "category", "food_type")


def evidence_ranking_weights() -> dict[str, float]:
    key = "TASTEAI_EVIDENCE_RANKING_WEIGHTS"
    raw = os.getenv(key)
    if not raw:
        return DEFAULT_EVIDENCE_RANKING_WEIGHTS.copy()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or set(parsed) - set(DEFAULT_EVIDENCE_RANKING_WEIGHTS):
            raise ValueError
        values = {
            name: float(parsed.get(name, default))
            for name, default in DEFAULT_EVIDENCE_RANKING_WEIGHTS.items()
        }
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{key} must be a JSON object of non-negative numbers") from error
    total = sum(values.values())
    if total <= 0:
        raise RuntimeError(f"At least one {key} weight must be positive")
    return {name: value / total for name, value in values.items()}


def evidence_stale_policy() -> str:
    policy = os.getenv("TASTEAI_EVIDENCE_STALE_POLICY", "exclude").strip().lower()
    if policy not in {"exclude", "downgrade"}:
        raise RuntimeError("TASTEAI_EVIDENCE_STALE_POLICY must be 'exclude' or 'downgrade'")
    return policy


def extract_requested_dish(query: str, intent: dict[str, Any] | None = None) -> str:
    if not isinstance(query, str):
        return ""
    cleaned = query.casefold()
    cleaned = re.sub(
        r"\b(?:under|below|up to|upto|within|maximum|max|no more than|not more than|"
        r"budget(?:\s+of)?|around|about|roughly)\s*(?:₹\s*|rs\.?\s*)?\d{1,5}\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"(?:₹\s*|rs\.?\s*)\d{1,5}\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(?:no|without|avoid|never)\s+(?:any\s+)?[a-z][a-z -]{0,40}?"
        r"\b(?:in|on|from)\b\s+",
        " ",
        cleaned,
    )
    for term in (intent or {}).get("avoid", []):
        escaped = re.escape(str(term))
        cleaned = re.sub(
            rf"\b(?:not|no|avoid|without|never)\s+(?:any\s+)?{escaped}\b",
            " ",
            cleaned,
        )
        cleaned = re.sub(rf"\b{escaped}\b", " ", cleaned) if not cleaned.strip() else cleaned
    cleaned = re.sub(
        r"\b(?:i\s+want|i\s+would\s+like|i'd\s+like|give\s+me|show\s+me|"
        r"find\s+me|looking\s+for|search\s+for|recommend|suggest|craving|"
        r"please|something|some|food|dish|a|an|the|near\s+me)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:spicy|extra spicy|very spicy|hot|mild|crispy|crunchy|crisp|"
        r"sweet|sour|tangy|smoky|rich|creamy|oily|healthy|light|filling|"
        r"hearty|affordable|cheap|budget friendly|vegetarian|veggie|vegan|"
        r"plant based|veg)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:not|no|avoid|without|never|any|under|below|up to|within|"
        r"for|my|me|today|now|i|want|would|like)\b",
        " ",
        cleaned,
    )
    return " ".join(cleaned.split())


def search_menu_evidence(
    db: Session,
    query: str,
    *,
    profile: TasteProfile | None = None,
    budget_max: int | None = None,
    limit: int = 10,
    restaurant_identity_ids: set[str] | None = None,
    mode: str = "auto",
    known_dish_names: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError("Evidence search query must be text up to 500 characters.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("Evidence search limit must be between 1 and 50.")

    intent = parse_intent(query, budget_max)
    if mode != "auto":
        if mode not in {"usual", "different", "surprise"}:
            raise ValueError("Evidence recommendation mode is invalid.")
        intent["recommendation_mode"] = mode
    dish_query = extract_requested_dish(query, intent)
    policy = evidence_stale_policy()
    weights = evidence_ranking_weights()
    excluded: dict[str, int] = {}

    def exclude(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    profile_dietary = {
        str(item).strip().casefold()
        for item in (profile.dietary_restrictions if profile else [])
    }
    unsupported_profile_dietary = sorted(
        profile_dietary - {"vegetarian", "vegan", "plant-based"}
    )
    if intent["unsupported_constraints"] or intent["contradictions"] or unsupported_profile_dietary:
        reasons = [
            *intent["unsupported_constraints"],
            *intent["contradictions"],
            *unsupported_profile_dietary,
        ]
        return _empty_result(
            query,
            dish_query,
            intent,
            policy,
            weights,
            "unsupported_or_contradictory_constraint",
            f"Menu evidence cannot verify this request safely: {', '.join(sorted(set(reasons)))}.",
        )
    if not dish_query:
        return _empty_result(
            query,
            dish_query,
            intent,
            policy,
            weights,
            "dish_name_required",
            "Enter a dish name so TasteAI can search for exact menu evidence.",
        )

    lookup_names = _canonical_lookup_names(db, dish_query)
    rows = []
    seen_evidence_ids: set[int] = set()
    for lookup_name in lookup_names:
        for row in restaurants_with_dish_evidence(
            db,
            lookup_name,
            now=now,
            include_stale=True,
            include_temporarily_unavailable=True,
            include_conflicted=True,
        ):
            if row["evidence_id"] not in seen_evidence_ids:
                seen_evidence_ids.add(row["evidence_id"])
                rows.append(row)
    if restaurant_identity_ids is not None:
        rows = [
            row for row in rows
            if row["restaurant_identity_id"] in restaurant_identity_ids
        ]
    if not rows:
        return _empty_result(
            query,
            dish_query,
            intent,
            policy,
            weights,
            "no_evidence",
            f"No approved menu evidence was found for {dish_query}.",
        )

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (
            row["restaurant_identity_id"],
            row["normalized_dish_name"],
            normalize_dish_name(row["variant"] or ""),
        )
        grouped.setdefault(key, []).append(row)

    ranked: list[dict[str, Any]] = []
    for group in grouped.values():
        fresh_rows = [row for row in group if row["freshness"]["state"] == "fresh"]
        selected_rows = fresh_rows or group
        if not fresh_rows and policy == "exclude":
            exclude("stale_evidence")
            continue
        if _has_conflicting_claims(selected_rows):
            exclude("conflicting_evidence")
            continue

        representative = max(
            selected_rows,
            key=lambda row: (
                row["evidence_state"] == "verified",
                CONFIDENCE_SCORES.get(row["confidence"]["level"], 0.0),
                _timestamp(row["captured_at"]),
                -int(row["evidence_id"]),
            ),
        )
        price_rows = [
            row for row in selected_rows
            if row["price"]["state"] == "known"
            and (
                not fresh_rows
                or row["freshness"]["state"] == "fresh"
            )
        ]
        price_source = max(
            price_rows,
            key=lambda row: (
                row["evidence_state"] == "verified",
                CONFIDENCE_SCORES.get(row["confidence"]["level"], 0.0),
                _timestamp(row["captured_at"]),
                -int(row["evidence_id"]),
            ),
            default=representative,
        )
        candidate = _merge_candidate_evidence(selected_rows, representative, price_source)
        if candidate["availability"]["state"] == "temporarily_unavailable":
            exclude("temporarily_unavailable")
            continue
        hard_reason = _hard_constraint_failure(candidate, intent, profile_dietary)
        if hard_reason:
            exclude(hard_reason)
            continue
        if intent["budget_max"] is not None:
            if candidate["freshness"]["state"] == "stale":
                exclude("stale_price")
                continue
            price_status = _within_budget(candidate["price"], intent["budget_max"])
            if price_status != "eligible":
                exclude(price_status)
                continue
        if _has_unverified_ingredient_exclusions(intent, profile, candidate):
            exclude("ingredient_absence_unverified")
            continue

        scores = _candidate_scores(candidate, intent, profile, weights)
        denominator = sum(weights[key] for key in scores)
        score = sum(scores[key] * weights[key] for key in scores) / denominator if denominator else 0.0
        candidate["ranking"] = {
            "score": round(score, 2),
            "is_estimate": True,
            "components": {
                key: {
                    "score": round(value, 2),
                    "configured_weight": weights[key],
                }
                for key, value in scores.items()
            },
        }
        normalized_candidate_name = candidate["dish"]["normalized_name"]
        known = normalized_candidate_name in (known_dish_names or set())
        if intent["recommendation_mode"] == "usual":
            candidate["ranking"]["score"] = round(
                candidate["ranking"]["score"] + (8.0 if known else 0.0), 2
            )
        elif intent["recommendation_mode"] in {"different", "surprise"}:
            candidate["ranking"]["score"] = round(
                candidate["ranking"]["score"] + (8.0 if not known else -8.0), 2
            )
        candidate["personalization"] = {
            "mode": intent["recommendation_mode"],
            "known_from_history": known,
        }
        candidate["why"] = _grounded_explanation(
            candidate,
            dish_query,
            intent,
        )
        candidate["explanation"] = {
            "summary": candidate["why"][0],
            "facts": candidate["why"],
            "ranking_trace": {
                "mode": intent["recommendation_mode"],
                "components": candidate["ranking"]["components"],
                "hard_constraints_applied": True,
                "evidence_policy": policy,
            },
            "citations": [
                {
                    "evidence_id": source["evidence_id"],
                    "source_id": source["source_id"],
                    "source_type": source["source_type"],
                    "captured_at": source["captured_at"],
                }
                for source in candidate["provenance"]["sources"]
            ],
        }
        ranked.append(candidate)

    ranked.sort(
        key=lambda candidate: (
            -candidate["ranking"]["score"],
            candidate["freshness"]["state"] != "fresh",
            candidate["evidence_state"] != "verified",
            -CONFIDENCE_SCORES.get(candidate["confidence"]["level"], 0.0),
            candidate["restaurant"]["name"].casefold(),
            (candidate["price"]["variant"] or "").casefold(),
            candidate["evidence_id"],
        )
    )
    results = ranked[:limit]
    if results:
        message = None
        status = "results"
    elif excluded.get("ingredient_absence_unverified"):
        message = "The menu evidence does not verify the requested ingredient exclusions, so no dishes are presented as safe matches."
        status = "insufficient_evidence"
    elif excluded.get("conflicting_evidence"):
        message = "Conflicting fresh menu claims were found; the affected dish variants are not presented."
        status = "no_constraint_match"
    elif excluded.get("temporarily_unavailable"):
        message = "The source explicitly marks the matching menu item temporarily unavailable."
        status = "no_constraint_match"
    elif intent["budget_max"] is not None and excluded:
        message = f"No fresh menu evidence with an explicit INR price within ₹{intent['budget_max']} was found."
        status = "no_constraint_match"
    elif excluded.get("stale_evidence"):
        message = "Only stale menu evidence was found, and the configured policy excludes it."
        status = "no_constraint_match"
    elif excluded:
        message = "Menu evidence was found, but none met the requested evidence and dietary constraints."
        status = "no_constraint_match"
    else:
        message = f"No approved menu evidence was found for {dish_query}."
        status = "no_evidence"
    return {
        "mode": "approved_menu_evidence",
        "status": status,
        "query": query,
        "dish_query": dish_query,
        "intent": intent,
        "freshness_policy": policy,
        "ranking_weights": weights,
        "results": results,
        "result_count": len(results),
        "excluded_candidate_counts": excluded,
        "message": message,
    }


def _canonical_lookup_names(db: Session, requested_name: str) -> tuple[str, ...]:
    """Resolve only approved canonical aliases; punctuation normalization remains the fallback."""
    normalized = normalize_dish_name(requested_name)
    names = [normalized]
    alias = db.scalar(
        select(DishAlias).where(
            DishAlias.normalized_alias == normalized,
            DishAlias.review_status == "approved",
        )
    )
    if alias is None:
        return tuple(names)
    canonical = db.get(CanonicalDish, alias.canonical_dish_id)
    if canonical is not None and canonical.status == "active":
        names.append(canonical.normalized_name)
    return tuple(dict.fromkeys(names))


def _empty_result(
    query: str,
    dish_query: str,
    intent: dict[str, Any],
    policy: str,
    weights: dict[str, float],
    reason: str,
    message: str,
) -> dict[str, Any]:
    return {
        "mode": "approved_menu_evidence",
        "status": reason,
        "query": query,
        "dish_query": dish_query,
        "intent": intent,
        "freshness_policy": policy,
        "ranking_weights": weights,
        "results": [],
        "result_count": 0,
        "excluded_candidate_counts": {},
        "message": message,
    }


def _has_conflicting_claims(rows: list[dict[str, Any]]) -> bool:
    prices = {
        (
            row["price"]["currency"],
            row["price"]["amount"],
            row["price"]["minimum"],
            row["price"]["maximum"],
        )
        for row in rows
        if row["price"]["state"] == "known"
    }
    if len(prices) > 1:
        return True
    availability = {
        row["availability_state"]
        for row in rows
        if row["availability_state"] in {
            "availability_supported",
            "temporarily_unavailable",
        }
    }
    return len(availability) > 1


def _merge_candidate_evidence(
    rows: list[dict[str, Any]],
    representative: dict[str, Any],
    price_source: dict[str, Any],
) -> dict[str, Any]:
    common_cuisines = {row["cuisine"] for row in rows}
    common_food_types = {row["food_type"] for row in rows}
    common_dietary = set(rows[0]["dietary_attributes"] or [])
    for row in rows[1:]:
        common_dietary.intersection_update(row["dietary_attributes"] or [])
    chosen_price = dict(price_source["price"])
    chosen_price["source_id"] = price_source["source_id"]
    chosen_price["evidence_id"] = price_source["evidence_id"]
    availability_rows = [
        row for row in rows
        if row["availability_state"] in {
            "availability_supported",
            "temporarily_unavailable",
        }
    ]
    availability_source = max(
        availability_rows,
        key=lambda row: (
            row["evidence_state"] == "verified",
            CONFIDENCE_SCORES.get(row["confidence"]["level"], 0.0),
            _timestamp(row["captured_at"]),
            -int(row["evidence_id"]),
        ),
        default=representative,
    )
    all_sources = sorted(
        (
            {
                "source_id": row["source_id"],
                "source_type": row["source_type"],
                "provider_name": row["provider_name"],
                "source_identifier": row["source_identifier"],
                "source_url": row["source_url"],
                "attribution": row["attribution"],
                "evidence_id": row["evidence_id"],
                "evidence_state": row["evidence_state"],
                "confidence": row["confidence"],
                "captured_at": row["captured_at"],
            }
            for row in rows
        ),
        key=lambda source: (
            source["evidence_state"] != "verified",
            source["source_type"],
            source["source_id"],
        ),
    )
    return {
        "evidence_id": representative["evidence_id"],
        "restaurant": {
            "identity_id": representative["restaurant_identity_id"],
            "name": representative["restaurant_name"],
            "address": representative["restaurant_address"],
            "identity_match_state": representative["identity_match_state"],
            "identity_confidence": representative["identity_confidence"],
        },
        "dish": {
            "name": representative["dish_name"],
            "normalized_name": representative["normalized_dish_name"],
            "match": "exact_normalized_name",
            "description": representative["description"],
            "category": representative["category"],
            "cuisine": next(iter(common_cuisines)) if len(common_cuisines) == 1 else None,
            "food_type": next(iter(common_food_types)) if len(common_food_types) == 1 else None,
            "dietary_attributes": sorted(common_dietary),
            "variant": chosen_price["variant"],
        },
        "price": chosen_price,
        "availability": {
            "state": availability_source["availability_state"],
            "available_now": availability_source["available_now"]
            if availability_source["freshness"]["state"] == "fresh"
            else None,
            "evidence": availability_source.get("availability_evidence"),
        },
        "evidence_state": representative["evidence_state"],
        "confidence": representative["confidence"],
        "freshness": representative["freshness"],
        "provenance": {
            "authorization_status": "approved",
            "source_count": len({source["source_id"] for source in all_sources}),
            "evidence_count": len(all_sources),
            "sources": all_sources,
            "price_source_id": price_source["source_id"],
            "price_source_type": price_source["source_type"],
            "price_captured_at": price_source["captured_at"],
        },
    }


def _hard_constraint_failure(
    candidate: dict[str, Any],
    intent: dict[str, Any],
    profile_dietary: set[str],
) -> str | None:
    if intent["vegetarian"] or profile_dietary & {"vegetarian", "vegan", "plant-based"}:
        if not _explicitly_vegetarian(candidate):
            return "dietary_status_unknown"
    if intent["vegan"] or profile_dietary & {"vegan", "plant-based"}:
        if "vegan" not in candidate["dish"]["dietary_attributes"] and (
            (candidate["dish"]["food_type"] or "").strip().casefold() != "vegan"
        ):
            return "dietary_status_unknown"
    excluded_proteins = set(intent.get("exclude_proteins", []))
    if excluded_proteins:
        text = _evidence_text(candidate)
        if any(_contains_term(text, protein) for protein in excluded_proteins):
            return "explicit_protein_conflict"
        return "protein_absence_unverified"
    return None


def _explicitly_vegetarian(candidate: dict[str, Any]) -> bool:
    attributes = set(candidate["dish"]["dietary_attributes"])
    food_type = (candidate["dish"]["food_type"] or "").strip().casefold().replace("-", " ")
    if "vegetarian" in attributes or "vegan" in attributes:
        return True
    if food_type in {"vegetarian", "vegan", "veg"}:
        return True
    return False


def _has_unverified_ingredient_exclusions(
    intent: dict[str, Any],
    profile: TasteProfile | None,
    _candidate: dict[str, Any],
) -> bool:
    exclusions = [*intent.get("avoid", [])]
    if profile:
        exclusions.extend(profile.disliked_ingredients or [])
    return bool(exclusions)


def _evidence_text(candidate: dict[str, Any]) -> str:
    dish = candidate["dish"]
    return " ".join(
        str(dish.get(field) or "")
        for field in (*INGREDIENT_EVIDENCE_FIELDS, "cuisine")
    ) + " " + " ".join(dish.get("dietary_attributes") or [])


def _contains_term(text: str, term: str) -> bool:
    normalized_text = normalize_dish_name(text)
    normalized_term = normalize_dish_name(term)
    return bool(
        normalized_term
        and re.search(
            rf"(?<![\w]){re.escape(normalized_term)}(?![\w])",
            normalized_text,
            flags=re.UNICODE,
        )
    )


def _within_budget(price: dict[str, Any], budget: int) -> str:
    if price["state"] != "known":
        return "price_unknown"
    if price["currency"] != "INR":
        return "price_currency_unknown"
    ceiling_text = price["amount"] or price["maximum"]
    if ceiling_text is None:
        return "price_range_unknown"
    return "eligible" if Decimal(ceiling_text) <= Decimal(budget) else "price_over_budget"


def _candidate_scores(
    candidate: dict[str, Any],
    intent: dict[str, Any],
    profile: TasteProfile | None,
    weights: dict[str, float],
) -> dict[str, float]:
    scores = {
        "dish_match": 100.0,
        "evidence_confidence": (
            100.0 if candidate["evidence_state"] == "verified"
            else CONFIDENCE_SCORES.get(candidate["confidence"]["level"], 0.0)
        ),
        "freshness": 100.0 if candidate["freshness"]["state"] == "fresh" else 5.0,
    }
    price = candidate["price"]
    price_amount = price["amount"] or price["maximum"]
    if price["state"] == "known" and price["currency"] == "INR" and price_amount is not None:
        amount = float(Decimal(price_amount))
        budget = intent.get("budget_max")
        target = budget if budget is not None else intent.get("budget_target")
        if target:
            scores["price_fit"] = max(0.0, min(100.0, 100.0 * (1 - amount / float(target))))
    if profile and profile.preferred_cuisines and candidate["dish"]["cuisine"]:
        scores["taste_match"] = (
            100.0
            if candidate["dish"]["cuisine"].casefold()
            in {str(cuisine).casefold() for cuisine in profile.preferred_cuisines}
            else 0.0
        )
    if candidate["availability"]["state"] == "availability_supported":
        scores["availability_confidence"] = 100.0
    return {key: value for key, value in scores.items() if weights.get(key, 0) > 0}


def _grounded_explanation(
    candidate: dict[str, Any],
    requested_dish: str,
    intent: dict[str, Any],
) -> list[str]:
    explanation = [
        f"The stored menu item name exactly matches {requested_dish}.",
        (
            f"The evidence is marked {candidate['evidence_state']} with "
            f"{candidate['confidence']['level']} extraction/document-review confidence."
        ),
        (
            f"The linked restaurant identity is {candidate['restaurant']['identity_match_state']}; "
            "this is a stored identity link, not an independent identity audit."
        ),
    ]
    price = candidate["price"]
    if price["state"] == "known":
        if price["amount"] is not None and price["currency"]:
            explanation.append(
                f"The menu evidence lists {price['currency']} {price['amount']} "
                f"for the {price['variant'] or 'unnamed'} option."
            )
        elif price["minimum"] is not None or price["maximum"] is not None:
            explanation.append(
                f"The menu evidence lists a price range of {price['currency'] or 'unknown currency'} "
                f"{price['minimum'] or 'unknown'}–{price['maximum'] or 'unknown'}."
            )
        else:
            explanation.append("A price amount is recorded, but its currency is unknown.")
    else:
        explanation.append("The menu evidence does not include a price.")
    if intent.get("budget_max") is not None:
        explanation.append(f"The explicit INR price is within your ₹{intent['budget_max']} budget.")
    if candidate["freshness"]["state"] == "stale":
        explanation.append("This menu evidence is stale and is strongly downgraded; it is not current confirmation.")
    else:
        explanation.append("The captured menu evidence is within its configured freshness window.")
    if candidate["availability"]["available_now"] is True:
        explanation.append("The source explicitly supports availability; the claim is time-bound to the captured evidence.")
    elif candidate["availability"]["available_now"] is False:
        explanation.append("The source explicitly marks this item temporarily unavailable.")
    else:
        explanation.append("The menu listing does not establish whether this item is available right now.")
    return explanation


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()
