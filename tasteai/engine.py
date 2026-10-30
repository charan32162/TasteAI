import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import Dish, FoodEvent, FoodMemory, TasteProfile

DEFAULT_WEIGHTS = {"taste": 0.28, "intent": 0.42, "history": 0.13, "price": 0.10, "novelty": 0.07}
DEFAULT_INTENT_WEIGHTS = {
    "protein": 1.0,
    "spice": 2.0,
    "crunch": 2.0,
    "filling": 2.0,
    "healthy": 1.5,
    "adventure": 1.0,
    "cuisine": 1.0,
    "liked_ingredient": 1.0,
    "sweet": 1.0,
    "sour": 1.0,
    "richness": 1.0,
    "smokiness": 1.0,
}
FEATURES = ("spice", "sweet", "sour", "salty", "richness", "oiliness", "smokiness", "crunch", "texture", "portion_size")
PROTEINS = (
    "chicken", "fish", "egg", "paneer", "tofu", "mutton", "beef", "pork",
    "shrimp", "prawns", "seafood", "chickpeas", "lentils", "mushrooms",
)
SUPPORTED_DIETARY_TERMS = ("vegetarian", "veggie", "veg", "vegan", "plant-based")
UNSUPPORTED_DIETARY_PATTERNS = (
    r"\b(?:allerg(?:y|ic)|celiac|coeliac)\b",
    r"\b(?:gluten|dairy|lactose|nut|peanut|tree nut|sesame|shellfish|soy|soya|egg)\s*[- ]?free\b",
    r"\b(?:halal|kosher)\b",
)
MEAT_PROTEINS = ("chicken", "fish", "mutton", "beef", "pork", "shrimp", "prawns", "seafood")


def _normalized_weights(environment_key: str, defaults: dict[str, float]) -> dict[str, float]:
    raw = os.getenv(environment_key)
    if not raw:
        return defaults.copy()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or set(parsed) - set(defaults):
            raise ValueError
        values = {key: float(parsed.get(key, default)) for key, default in defaults.items()}
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{environment_key} must be a JSON object of non-negative numbers") from error
    total = sum(values.values())
    if total <= 0:
        raise RuntimeError(f"At least one {environment_key} weight must be positive")
    return {key: value / total for key, value in values.items()}


def ranking_weights() -> dict[str, float]:
    return _normalized_weights("TASTEAI_RANKING_WEIGHTS", DEFAULT_WEIGHTS)


def intent_weights() -> dict[str, float]:
    return _normalized_weights("TASTEAI_INTENT_WEIGHTS", DEFAULT_INTENT_WEIGHTS)


def parse_intent(query: str, budget_max: int | None = None) -> dict[str, Any]:
    text = query.lower()
    budget = budget_max
    budget_target = None
    if budget is None:
        hard_match = re.search(
            r"\b(?:under|below|up to|upto|within|max(?:imum)?|no more than|not more than|"
            r"budget(?:\s+of)?|for)\s*(?:₹\s*|rs\.?\s*)?(\d{1,5})\b",
            text,
        )
        target_match = re.search(r"\b(?:around|about|roughly)\s+₹?\s*(\d{1,5})\b", text)
        currency_match = re.search(r"(?:₹\s*|rs\.?\s*)(\d{1,5})\b", text)
        if hard_match:
            budget = int(hard_match.group(1))
        elif target_match:
            budget_target = int(target_match.group(1))
        elif currency_match:
            budget = int(currency_match.group(1))
    elif budget is not None:
        budget = int(budget)
    protein_aliases = {
        "chicken": ("chicken", "chickens"), "fish": ("fish",),
        "egg": ("egg", "eggs"), "paneer": ("paneer",), "tofu": ("tofu",),
        "mutton": ("mutton",), "beef": ("beef",), "pork": ("pork",),
        "shrimp": ("shrimp", "shrimps"), "prawns": ("prawn", "prawns"),
        "seafood": ("seafood",), "chickpeas": ("chickpea", "chickpeas"),
        "lentils": ("lentil", "lentils"), "mushrooms": ("mushroom", "mushrooms"),
    }
    alias_to_protein = {
        alias: protein for protein, aliases in protein_aliases.items() for alias in aliases
    }
    protein_terms = "|".join(
        re.escape(alias) for alias in sorted(alias_to_protein, key=len, reverse=True)
    )
    protein_pattern = re.compile(rf"\b(?:{protein_terms})\b")
    negator_pattern = re.compile(
        r"\b(?:i\s+)?(?:do not|don't|dont|not|no|avoid|without|never)\s+"
        r"(?:(?:want|like|eat|have)\s+)?(?:any\s+)?"
    )
    negative_spans = []
    excluded_proteins_set = set()
    for marker in negator_pattern.finditer(text):
        remainder = text[marker.end():]
        boundary = re.search(r"[,.;!?]|\bbut\b", remainder)
        end = marker.end() + (boundary.start() if boundary else len(remainder))
        negative_spans.append((marker.start(), end))
        excluded_proteins_set.update(
            alias_to_protein[match.group(0)] for match in protein_pattern.finditer(text[marker.end():end])
        )
    for match in re.finditer(rf"\b({protein_terms})\s+nahi\s+chahiye\b", text):
        negative_spans.append((match.start(), match.end()))
        excluded_proteins_set.add(alias_to_protein[match.group(1)])
    negative_text = text
    for start, end in reversed(negative_spans):
        negative_text = negative_text[:start] + " " + negative_text[end:]
    excluded_proteins = sorted(excluded_proteins_set)
    protein = next(
        (
            alias_to_protein[alias]
            for alias in sorted(alias_to_protein, key=len, reverse=True)
            if re.search(rf"\b{re.escape(alias)}\b", negative_text)
        ),
        None,
    )
    no_meat = bool(re.search(
        r"\b(?:no|without|avoid|don't want|do not want)\b[^,.;!?]{0,40}\bmeat\b",
        text,
    ))
    if no_meat:
        excluded_proteins = sorted(set(excluded_proteins) | set(MEAT_PROTEINS))
    avoid: list[str] = []
    avoid_pattern = re.compile(
        r"\b(?:i\s+)?(?:do\s+not|don't|dont|not|no|avoid|without|never)\s+"
        r"(?:want\s+|like\s+|eat\s+|have\s+)?(?:any\s+)?(?:the\s+)?([a-z][a-z -]{1,24})"
        r"|\b(?:bored of|tired of|sick of|done with)\s+([a-z][a-z -]{1,24})"
    )
    excluded_phrases = {
        "spicy", "spice", "hot", "mild", "crispy", "crunchy", "filling",
        "something", "food", "anything", "too spicy", "oily", "oil", "sweet",
        "sour", "smoky", "smoke", "rich", "creamy", "cream", "light", "teekha", "halka",
        "healthy", "flavorful", "flavourful", "menu", "menu details",
    }
    for match in avoid_pattern.finditer(text):
        raw_phrase = match.group(1) or match.group(2) or ""
        phrase = re.split(r"[,.;!?]|\b(?:under|below|up to|around|with|and|or|but|for|less|too)\b", raw_phrase)[0].strip()
        if phrase and phrase not in excluded_phrases and phrase not in excluded_proteins:
            avoid.append(phrase)
    spice_high = bool(re.search(r"\b(?:spicy|extra spicy|very spicy|hot|chilli|chili|teekha)\b", text))
    spice_low = bool(re.search(r"\b(?:mild|less spicy|not spicy|not too spicy|nothing too spicy|no spice|avoid spicy|avoid spice)\b", text))
    spice = "low" if spice_low else "high" if spice_high else None
    attribute_targets: dict[str, str] = {}
    attribute_patterns = {
        "sweet": (r"\b(?:sweet|sugary)\b", r"\b(?:not sweet|less sweet|not too sweet|no sweetness|nothing too sweet|too sweet)\b"),
        "sour": (r"\b(?:sour|tangy)\b", r"\b(?:not sour|less sour|not too sour)\b"),
        "smokiness": (r"\b(?:smoky|smokiness)\b", r"\b(?:not smoky|less smoky)\b"),
        "richness": (r"\b(?:rich|creamy|creaminess)\b", r"\b(?:not rich|less rich|not creamy)\b"),
        "oiliness": (r"\b(?:oily|oiliness)\b", r"\b(?:not oily|oily nahi|less oily|not too oily|low oil|light|halka|healthy)\b"),
    }
    for attribute, (high_pattern, low_pattern) in attribute_patterns.items():
        if re.search(low_pattern, text):
            attribute_targets[attribute] = "low"
        elif re.search(high_pattern, text):
            attribute_targets[attribute] = "high"
    if spice:
        attribute_targets["spice"] = spice
    if re.search(r"\b(?:crispy|crunchy|crisp)\b", text):
        attribute_targets["crunch"] = "high"
    if re.search(r"\b(?:filling|hearty|substantial|big portion|hungry|pet bharne wala)\b", text):
        attribute_targets["portion_size"] = "high"
    unsupported = sorted({
        match.group(0).strip()
        for pattern in UNSUPPORTED_DIETARY_PATTERNS
        for match in re.finditer(pattern, text)
    })
    high_protein_unsupported = bool(re.search(r"\b(?:high[- ]protein|protein[- ]rich)\b", text))
    if high_protein_unsupported:
        unsupported.append("high protein")
    vegan = bool(re.search(r"\b(?:vegan|plant-based)\b", text))
    vegetarian = vegan or bool(re.search(r"\b(?:vegetarian|veggie|veg)\b", text))
    filling = "portion_size" in attribute_targets
    adventure = bool(re.search(r"\b(?:adventurous|surprise|different|new|never tried|outside my usual|outside my usual choices)\b", text))
    if re.search(r"\b(?:usual|usually|normally|what i like|what i enjoy)\b", text):
        recommendation_mode = "usual"
    elif re.search(r"\b(?:different|something new|outside my usual)\b", text):
        recommendation_mode = "different"
    elif re.search(r"\b(?:surprise|never tried|adventurous)\b", text):
        recommendation_mode = "surprise"
    else:
        recommendation_mode = "auto"
    affordable = bool(re.search(r"\b(?:cheap|cheaper|affordable|budget[- ]friendly|low[- ]cost)\b", text))
    value_requested = bool(re.search(r"\b(?:good value|value for money|best value|great value)\b", text))
    unavailable_signals = []
    if value_requested:
        unavailable_signals.append("value for money (no quality or verified price-comparison data)")
    if re.search(r"\b(?:flavorful|flavourful|full of flavor|full of flavour)\b", text):
        unavailable_signals.append("flavor intensity")
    if re.search(r"\b(?:soggy|sogginess)\b", text):
        unavailable_signals.append("sogginess")
    if high_protein_unsupported:
        unavailable_signals.append("protein quantity")
    positive_protein = protein
    contradictions = []
    if spice_high and spice_low:
        contradictions.append("spice")
    if vegan and positive_protein in MEAT_PROTEINS:
        contradictions.append("protein")
    if vegetarian and positive_protein in MEAT_PROTEINS:
        contradictions.append("protein")
    if positive_protein and positive_protein in excluded_proteins:
        contradictions.append("protein")
    if any(re.search(rf"\b{re.escape(item)}\b", negative_text) for item in excluded_proteins):
        contradictions.append("protein")
    restaurant_discovery = bool(re.search(r"\b(?:restaurant|restaurants|place to eat|places to eat)\b", text))
    location_requested = bool(re.search(r"\b(?:near me|nearby|around here|close to me|in my area)\b", text))
    understood = bool(
        protein or excluded_proteins or spice or attribute_targets or vegetarian or budget is not None
        or budget_target is not None or avoid or adventure or affordable or value_requested
        or unsupported or location_requested
    )
    ambiguous = not understood and bool(text.strip())
    return {
        "query": query.strip(),
        "protein": protein,
        "exclude_proteins": excluded_proteins,
        "spice_level": spice,
        "crunchy": bool(re.search(r"\b(?:crispy|crunchy)\b", text)),
        "filling": filling,
        "healthy": bool(re.search(r"\b(?:healthy|light|low oil)\b", text)),
        "vegetarian": vegetarian,
        "vegan": vegan,
        "budget_max": budget,
        "budget_target": budget_target,
        "avoid": sorted(set(avoid)),
        "attribute_targets": attribute_targets,
        "unsupported_constraints": sorted(set(unsupported)),
        "unavailable_signals": unavailable_signals,
        "contradictions": sorted(set(contradictions)),
        "affordable": affordable,
        "value_requested": value_requested,
        "adventure_level": "high" if adventure else "medium",
        "recommendation_mode": recommendation_mode,
        "ambiguous": ambiguous,
        "restaurant_discovery": restaurant_discovery,
        "location_requested": location_requested,
    }


def active_memories(memories: list[FoodMemory]) -> list[FoodMemory]:
    now = datetime.now(timezone.utc)
    return [memory for memory in memories if memory.expires_at is None or memory.expires_at.replace(tzinfo=timezone.utc) > now]


def capture_memory(text: str) -> tuple[str, str] | None:
    normalized = text.lower()
    temporary = re.search(r"\b(?:avoid|no)\s+(?:any\s+)?(?:spicy|spice|hot)(?:\s+food)?\s+(?:today|for now)\b", normalized)
    if temporary:
        return "spicy", "Asked to avoid spicy food temporarily."
    excluded = re.search(r"\b(?:never recommend|never suggest|do not recommend|don't recommend|avoid)\s+(?:me\s+)?([a-z][a-z -]{1,30})", normalized)
    if excluded:
        subject = excluded.group(1).strip(" .,!").split()[0]
        return subject, f"Explicitly asked to avoid {subject}."
    oily = re.search(r"\b([a-z][a-z ]{1,30})\s+(?:was|is)\s+too oily\b", normalized)
    if oily:
        subject = oily.group(1).strip().split()[-1]
        return subject, f"Previously said {subject} was too oily."
    return None


def _numeric_attribute(dish: Dish, feature: str) -> float | None:
    value = dish.attributes.get(feature)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 10 else None


def update_profile_from_rating(profile: TasteProfile, dish: Dish, rating: int) -> None:
    if rating not in range(1, 6):
        return
    features = dict(profile.features)
    provenance = dict(profile.feature_provenance or {})
    direction = 1 if rating >= 4 else -1 if rating <= 2 else 0
    if direction:
        for feature in FEATURES:
            if provenance.get(feature) == "explicit":
                continue
            observed = _numeric_attribute(dish, feature)
            if observed is None:
                continue
            previous = int(features.get(feature, 5))
            delta = (observed - previous) * direction
            step = round(delta * 0.25)
            if delta and step == 0:
                step = 1 if delta > 0 else -1
            features[feature] = max(0, min(10, previous + step))
            if delta:
                provenance[feature] = "inferred"
    profile.features = features
    profile.feature_provenance = provenance
    if rating >= 4 and dish.cuisine not in profile.preferred_cuisines:
        profile.preferred_cuisines = [*profile.preferred_cuisines, dish.cuisine][:20]
    if rating >= 4:
        liked = {item.lower() for item in profile.liked_ingredients}
        profile.liked_ingredients = list(profile.liked_ingredients) + [item for item in dish.ingredients if item.lower() not in liked][:3]


def update_profile_from_feedback(profile: TasteProfile, action: str) -> str | None:
    adjustments = {
        "too_expensive": ("price_sensitivity", 1),
        "too_spicy": ("spice", -1),
        "not_spicy_enough": ("spice", 1),
        "too_oily": ("oiliness", -1),
    }
    change = adjustments.get(action)
    if change is None:
        return None
    feature, delta = change
    features = dict(profile.features)
    provenance = dict(profile.feature_provenance or {})
    if provenance.get(feature) == "explicit":
        return None
    previous = int(features.get(feature, 5))
    updated = max(0, min(10, previous + delta))
    features[feature] = updated
    if updated != previous:
        provenance[feature] = "learned"
    profile.features = features
    profile.feature_provenance = provenance
    return feature if updated != previous else None


def _matches_term(dish: Dish, term: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", term.lower()).strip()
    if not normalized:
        return False
    haystack = " ".join((dish.name, dish.description, dish.cuisine, *dish.ingredients)).lower()
    normalized_haystack = re.sub(r"[^a-z0-9]+", " ", haystack)
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", normalized_haystack))


def _dish_proteins(dish: Dish) -> set[str]:
    metadata_protein = dish.attributes.get("protein")
    proteins = {str(metadata_protein).lower()} if metadata_protein else set()
    for protein in PROTEINS:
        if _matches_term(dish, protein):
            proteins.add(protein)
    if "prawns" in proteins:
        proteins.add("shrimp")
    if "shrimp" in proteins:
        proteins.add("prawns")
    return proteins


def _score_intent(dish: Dish, intent: dict[str, Any], profile: TasteProfile) -> float | None:
    weights = intent_weights()
    signal_scores: dict[str, float] = {}
    if intent.get("protein"):
        signal_scores["protein"] = 100.0
    attribute_weights = {
        "spice": "spice",
        "crunch": "crunch",
        "portion_size": "filling",
        "oiliness": "healthy",
        "sweet": "sweet",
        "sour": "sour",
        "richness": "richness",
        "smokiness": "smokiness",
    }
    for feature, direction in intent.get("attribute_targets", {}).items():
        value = _numeric_attribute(dish, feature)
        if feature == "crunch" and value is None:
            value = _numeric_attribute(dish, "texture")
        if value is None:
            continue
        normalized = max(0.0, min(10.0, value))
        if direction == "high":
            score = min(100.0, normalized * (100.0 / 6.0))
        else:
            score = max(0.0, 100.0 - normalized * 25.0)
        signal_scores[attribute_weights.get(feature, feature)] = score
    if intent.get("adventure_level") == "high":
        adventure = _numeric_attribute(dish, "adventure")
        if adventure is not None:
            signal_scores["adventure"] = max(0.0, min(10.0, adventure)) * 10.0
    if profile.preferred_cuisines:
        signal_scores["cuisine"] = 100.0 if dish.cuisine in profile.preferred_cuisines else 0.0
    if profile.liked_ingredients:
        signal_scores["liked_ingredient"] = 100.0 if any(
            _matches_term(dish, item) for item in profile.liked_ingredients
        ) else 0.0
    if not signal_scores:
        return None
    denominator = sum(weights[key] for key in signal_scores)
    if denominator <= 0:
        return None
    return sum(signal * weights[key] for key, signal in signal_scores.items()) / denominator


def _intent_explanations(dish: Dish, intent: dict[str, Any]) -> list[str]:
    explanations = []
    if intent.get("protein"):
        explanations.append(f"Includes {intent['protein']} as requested.")
    spice = _numeric_attribute(dish, "spice")
    if intent.get("spice_level") and spice is not None:
        level = "high" if intent["spice_level"] == "high" else "low"
        explanations.append(f"You asked for {level}-spice food; its demo spice score is {spice:g}/10.")
    if intent.get("crunchy"):
        crunch = _numeric_attribute(dish, "crunch")
        attribute_label = "crunch"
        if crunch is None:
            crunch = _numeric_attribute(dish, "texture")
            attribute_label = "texture"
        if crunch is not None:
            explanations.append(f"You asked for crispy food; its demo {attribute_label} score is {crunch:g}/10.")
    portion = _numeric_attribute(dish, "portion_size")
    if intent.get("filling") and portion is not None:
        explanations.append(f"You asked for something filling; its demo portion-size score is {portion:g}/10.")
    attribute_labels = {
        "oiliness": "oiliness", "sweet": "sweetness", "sour": "sourness",
        "richness": "richness", "smokiness": "smokiness",
    }
    for feature, target in intent.get("attribute_targets", {}).items():
        if feature not in attribute_labels:
            continue
        value = _numeric_attribute(dish, feature)
        if value is not None:
            direction = "lower" if target == "low" else "higher"
            explanations.append(
                f"You asked for {direction} {attribute_labels[feature]}; "
                f"its demo score is {value:g}/10."
            )
    if intent.get("vegetarian"):
        explanations.append("Matches the catalog's vegetarian label.")
    if intent.get("vegan"):
        explanations.append("No listed animal-derived ingredient appears in this fictional demo dish.")
    if intent.get("budget_max") is not None:
        if dish.price_inr is not None:
            explanations.append(f"Listed at ₹{dish.price_inr}, within your ₹{intent['budget_max']} budget.")
    elif intent.get("budget_target") is not None:
        if dish.price_inr is not None:
            explanations.append(f"Listed at ₹{dish.price_inr}; your soft price target was around ₹{intent['budget_target']}.")
    adventure = _numeric_attribute(dish, "adventure")
    if intent.get("adventure_level") == "high" and adventure is not None:
        explanations.append(f"Its demo adventure score is {adventure:g}/10.")
    return explanations


def rank_dishes(
    dishes: list[Dish],
    profile: TasteProfile,
    events: list[FoodEvent],
    memories: list[FoodMemory],
    intent: dict[str, Any],
    exploration: float = 0.3,
    limit: int = 10,
) -> list[dict[str, Any]]:
    weights = ranking_weights()
    known = {event.dish_id for event in events if event.kind in {"tried", "like", "dislike"}}
    mode = intent.get("recommendation_mode", "auto")
    now = datetime.now(timezone.utc)
    rejected_recent = {
        event.dish_id for event in events
        if event.kind == "reject" and event.created_at.replace(tzinfo=timezone.utc) >= now - timedelta(days=14)
    }
    ratings: dict[str, list[int]] = {}
    for event in events:
        if event.rating is not None:
            ratings.setdefault(event.dish_id, []).append(event.rating)
    live_memories = active_memories(memories)
    excluded = [*intent.get("avoid", []), *profile.disliked_ingredients]
    excluded.extend(memory.subject for memory in live_memories if memory.permanence == "explicit")
    dietary_terms = {item.strip().lower() for item in profile.dietary_restrictions}
    profile_unsupported = [
        term for term in dietary_terms
        if term not in {"vegetarian", "vegan", "plant-based"}
    ]
    temporary_spice_avoid = any(memory.subject.lower() == "spicy" and memory.permanence == "temporary" for memory in live_memories)
    budget = intent.get("budget_max")
    if intent.get("unsupported_constraints") or intent.get("contradictions") or profile_unsupported:
        return []
    result = []
    for dish in dishes:
        proteins = _dish_proteins(dish)
        if (intent.get("vegetarian") or dietary_terms & {"vegetarian", "vegan", "plant-based"}) and not dish.vegetarian:
            continue
        if intent.get("vegan") or dietary_terms & {"vegan", "plant-based"}:
            animal_ingredients = {
                "beef", "butter", "cheese", "chicken", "cream", "curd", "egg",
                "fish", "gelatin", "ghee", "honey", "lard", "meat", "milk",
                "mutton", "paneer", "pork", "seafood", "shrimp", "whey", "yogurt",
            }
            if any(_matches_term(dish, ingredient) for ingredient in animal_ingredients):
                continue
        if intent.get("protein") and intent["protein"] not in proteins:
            continue
        if proteins & set(intent.get("exclude_proteins", [])):
            continue
        if any(_matches_term(dish, term) for term in excluded):
            continue
        price = getattr(dish, "price_inr", None)
        if budget is not None and (price is None or price > budget):
            continue
        known_features = [
            (feature, _numeric_attribute(dish, feature))
            for feature in FEATURES
            if (profile.feature_provenance or {}).get(feature) in {"explicit", "inferred", "legacy"}
        ]
        known_features = [
            (feature, value) for feature, value in known_features if value is not None
        ]
        taste = (
            max(0.0, 100.0 - sum(
                abs(float(profile.features.get(feature, 5)) - value)
                for feature, value in known_features
            ) * 12.5 / len(known_features))
            if known_features else None
        )
        intent_score = _score_intent(dish, intent, profile)
        dish_ratings = ratings.get(dish.id, [])
        history_score = sum(dish_ratings) * 20 / len(dish_ratings) if dish_ratings else None
        if dish.id in rejected_recent:
            history_score = min(history_score if history_score is not None else 50.0, 8.0)
        if price is None:
            price_score = None
        elif budget is None:
            target_price = intent.get("budget_target")
            if target_price is None and intent.get("affordable"):
                available_prices = [candidate.price_inr for candidate in dishes if candidate.price_inr is not None]
                target_price = min(available_prices) if available_prices else None
            if target_price is None:
                target_price = 350 - 25 * int(profile.features.get("price_sensitivity", 5))
            price_score = max(0.0, 100.0 - abs(price - target_price) * 0.35)
        else:
            price_score = max(0.0, 100.0 - (price / budget) * 35.0)
        novelty_score = 18.0 if dish.id in known else 100.0
        if mode == "usual":
            novelty_score = 100.0 if dish.id in known else 62.0
            if dish_ratings:
                history_score = max(history_score or 0.0, 70.0)
        elif mode in {"different", "surprise"}:
            novelty_score = 0.0 if dish.id in known else 100.0
            if dish.id in known:
                history_score = min(history_score if history_score is not None else 50.0, 20.0)
        if dish.id in rejected_recent:
            novelty_score = min(novelty_score, 0.0)
        blended_weights = dict(weights)
        has_explicit_intent = any((
            intent.get("protein"), intent.get("spice_level"), intent.get("crunchy"),
            intent.get("filling"), intent.get("healthy"), intent.get("adventure_level") == "high",
            intent.get("attribute_targets"),
        ))
        shift = max(0.0, min(1.0, exploration)) * (0.02 if has_explicit_intent else 0.06)
        blended_weights["novelty"] += shift
        blended_weights["taste"] -= shift
        if taste is not None and temporary_spice_avoid and dish.attributes.get("spice", 0) >= 7:
            taste *= 0.55
        inferred_penalty = 0.0
        for memory in live_memories:
            if memory.permanence != "inferred":
                continue
            if _matches_term(dish, memory.subject):
                inferred_penalty = max(inferred_penalty, 24.0)
            if memory.subject.lower() == "oily" and dish.attributes.get("oiliness", 0) >= 7:
                inferred_penalty = max(inferred_penalty, 18.0)
        if taste is not None:
            taste = max(0.0, taste - inferred_penalty)
        components = {
            "taste": round(taste) if taste is not None else None,
            "intent": round(intent_score) if intent_score is not None else None,
            "history": round(history_score) if history_score is not None else None,
            "price": round(price_score) if price_score is not None else None,
            "novelty": round(novelty_score),
        }
        available_weight = sum(
            blended_weights[key] for key in components if components[key] is not None
        )
        match = round(sum(
            components[key] * blended_weights[key]
            for key in components if components[key] is not None
        ) / available_weight) if available_weight else 0
        explanations = _intent_explanations(dish, intent)
        if mode == "usual" and dish.id in known:
            explanations.append("This is close to foods you have previously tried or liked.")
        elif mode in {"different", "surprise"} and dish.id not in known:
            explanations.append("This keeps your hard constraints while exploring beyond your recent choices.")
        if dish_ratings:
            explanations.append(f"Your previous rating for this dish averaged {sum(dish_ratings) / len(dish_ratings):.1f}/5.")
        if profile.preferred_cuisines and dish.cuisine in profile.preferred_cuisines:
            explanations.append(f"{dish.cuisine} is in your preferred cuisines.")
        matched_likes = [item for item in profile.liked_ingredients if _matches_term(dish, item)]
        if matched_likes:
            explanations.append(f"Contains an ingredient you like: {matched_likes[0]}.")
        closest_features = sorted(
            (feature for feature, _ in known_features),
            key=lambda feature: abs(
                float(profile.features.get(feature, 5)) - float(dish.attributes[feature])
            ),
        )[:2]
        explanations.extend(
            f"Your Taste DNA is close on {feature.replace('_', ' ')} "
            f"({profile.features.get(feature, 5)}/10 vs demo {dish.attributes[feature]}/10; "
            f"{(profile.feature_provenance or {}).get(feature)} preference)."
            for feature in closest_features
        )
        if not explanations:
            explanations.append("Ranked from available menu data; unverified attributes were not scored.")
        explanation = {
            "summary": explanations[0] if explanations else "Ranked from available menu data.",
            "facts": explanations,
            "ranking_trace": {
                "mode": mode,
                "components": components,
                "weights": blended_weights,
                "hard_constraints_applied": True,
            },
            "citations": [{
                "source_label": dish.source_label,
                "restaurant": dish.restaurant.name,
                "dish_id": dish.id,
            }],
        }
        result.append({
            "dish_id": dish.id, "name": dish.name, "description": dish.description,
            "cuisine": dish.cuisine, "price_inr": dish.price_inr, "vegetarian": dish.vegetarian,
            "ingredients": dish.ingredients, "attributes": dish.attributes,
            "restaurant": dish.restaurant.name, "source_label": dish.source_label,
            "match_percent": match, "components": components, "why": explanations,
            "explanation": explanation,
            "confidence_label": "heuristic estimate",
        })
    return sorted(result, key=lambda item: (-item["match_percent"], item["name"]))[:limit]


def group_recommendations(
    dishes: list[Dish],
    participants: list[dict[str, Any]],
    current_profile: TasteProfile,
    current_memories: list[FoodMemory],
) -> dict[str, Any]:
    supported_restrictions = {"vegetarian", "vegan", "plant-based"}
    unsupported = {}
    for person in participants:
        restrictions = {item.lower() for item in person["dietary_restrictions"]}
        if person["use_my_taste_profile"]:
            restrictions.update(item.lower() for item in current_profile.dietary_restrictions)
        unsupported[person["name"]] = sorted(restrictions - supported_restrictions)
    unsupported = {name: restrictions for name, restrictions in unsupported.items() if restrictions}
    if unsupported:
        return {
            "recommendations": [],
            "unsupported_restrictions": unsupported,
            "no_compatible_options": True,
        }

    by_restaurant: dict[str, list[Dish]] = {}
    for dish in dishes:
        by_restaurant.setdefault(dish.restaurant_id, []).append(dish)
    recommendations = []
    for restaurant_dishes in by_restaurant.values():
        member_picks = []
        member_scores = []
        for person in participants:
            uses_profile = person["use_my_taste_profile"]
            profile = current_profile if uses_profile else TasteProfile(
                user_id=current_profile.user_id,
                features={feature: 5 for feature in FEATURES},
                preferred_cuisines=[],
                liked_ingredients=[],
                disliked_ingredients=[],
                dietary_restrictions=[],
            )
            restrictions = {item.lower() for item in person["dietary_restrictions"]}
            if uses_profile:
                restrictions.update(item.lower() for item in profile.dietary_restrictions)
            intent = parse_intent(person["query"], person["budget_max"])
            intent["vegetarian"] = intent["vegetarian"] or bool(restrictions & {"vegetarian", "vegan", "plant-based"})
            intent["avoid"] = sorted(set(intent["avoid"]) | {
                item.lower() for item in person["ingredient_exclusions"]
            })
            if uses_profile:
                intent["avoid"] = sorted(set(intent["avoid"]) | {
                    item.lower() for item in profile.disliked_ingredients
                })
            ranked = rank_dishes(
                restaurant_dishes,
                profile,
                [],
                current_memories if uses_profile else [],
                intent,
                exploration=0.3,
                limit=len(restaurant_dishes),
            )
            if "vegan" in restrictions or "plant-based" in restrictions:
                animal_ingredients = {
                    "beef", "butter", "cheese", "chicken", "cream", "curd", "egg",
                    "fish", "gelatin", "ghee", "honey", "lard", "meat", "milk",
                    "mutton", "paneer", "pork", "seafood", "shrimp", "whey", "yogurt",
                }
                ranked = [
                    item for item in ranked
                    if not animal_ingredients.intersection(
                        ingredient.lower() for ingredient in item["ingredients"]
                    )
                ]
            if not ranked:
                member_picks = []
                break
            pick = ranked[0]
            member_picks.append({
                "attendee": person["name"],
                "dish_id": pick["dish_id"],
                "dish_name": pick["name"],
                "price_inr": pick["price_inr"],
                "match_percent": pick["match_percent"],
                "vegetarian": pick["vegetarian"],
                "source_label": pick["source_label"],
            })
            member_scores.append(pick["match_percent"])
        if len(member_picks) != len(participants):
            continue
        average_score = sum(member_scores) / len(member_scores)
        balanced_score = round(average_score * 0.7 + min(member_scores) * 0.3)
        restaurant = restaurant_dishes[0].restaurant
        recommendations.append({
            "restaurant_id": restaurant.id,
            "restaurant": restaurant.name,
            "source_label": restaurant.source_label,
            "match_percent": balanced_score,
            "confidence_label": "heuristic group estimate",
            "attendee_count": len(participants),
            "menu_options": member_picks,
        })
    recommendations.sort(key=lambda item: (-item["match_percent"], item["restaurant"]))
    return {
        "recommendations": recommendations[:8],
        "unsupported_restrictions": {},
        "no_compatible_options": not recommendations,
    }


def taste_transfer_comparison(
    dish: Dish,
    profile: TasteProfile,
    friend_features: dict[str, int],
    friend_rating: int,
) -> dict[str, Any]:
    shared = [feature for feature in FEATURES if feature in friend_features]
    if not shared:
        return {
            "dish_id": dish.id,
            "dish_name": dish.name,
            "friend_rating": friend_rating,
            "similarity_percent": None,
            "estimated_you_match_percent": None,
            "shared_features": [],
            "different_features": [],
            "confidence_label": "insufficient shared taste features",
        }
    differences = {
        feature: abs(int(profile.features.get(feature, 5)) - friend_features[feature])
        for feature in shared
    }
    similarity = round(100 - sum(differences.values()) * 10 / len(differences))
    dish_distances = [
        abs(int(profile.features.get(feature, 5)) - int(dish.attributes.get(feature, 5)))
        for feature in FEATURES
    ]
    estimated_match = round(100 - sum(dish_distances) * 10 / len(dish_distances))
    feature_details = [
        {
            "feature": feature,
            "your_level": int(profile.features.get(feature, 5)),
            "friend_level": friend_features[feature],
            "difference": differences[feature],
        }
        for feature in shared
    ]
    return {
        "dish_id": dish.id,
        "dish_name": dish.name,
        "friend_rating": friend_rating,
        "similarity_percent": max(0, min(100, similarity)),
        "estimated_you_match_percent": max(0, min(100, estimated_match)),
        "shared_features": [item for item in feature_details if item["difference"] <= 2],
        "different_features": [item for item in feature_details if item["difference"] > 2],
        "confidence_label": "heuristic estimate, not a calibrated prediction",
    }


ASPECT_TERMS = {
    "taste": ("flavor", "flavour", "tasty", "bland", "delicious"),
    "spice": ("spicy", "mild", "chilli", "chili", "peppery"),
    "freshness": ("fresh", "stale"),
    "quantity": ("portion", "quantity", "generous", "small serving"),
    "oiliness": ("oily", "greasy", "too much oil"),
    "texture": ("crispy", "crunchy", "soggy", "tender"),
    "value": ("value", "overpriced", "worth the price"),
}


def analyze_review_excerpts(excerpts: list[str]) -> dict[str, Any]:
    aspects = {}
    for aspect, terms in ASPECT_TERMS.items():
        evidence = [excerpt for excerpt in excerpts if any(term in excerpt.lower() for term in terms)]
        if evidence:
            aspects[aspect] = {"mentions": len(evidence), "evidence": evidence}
    return {"source": "caller-supplied excerpts", "review_count": len(excerpts), "aspects": aspects}
