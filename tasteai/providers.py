import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DemoRestaurant:
    id: str
    name: str
    city: str


@dataclass(frozen=True)
class DemoDish:
    id: str
    restaurant_id: str
    name: str
    description: str
    cuisine: str
    price_inr: int
    vegetarian: bool
    ingredients: tuple[str, ...]
    attributes: dict[str, object]


@dataclass(frozen=True)
class MenuEvidence:
    status: str
    source_label: str | None
    items: tuple[DemoDish, ...]


class RestaurantDataProvider(Protocol):
    source_label: str

    def restaurants(self) -> list[DemoRestaurant]: ...

    def dishes(self) -> list[DemoDish]: ...


class MenuProvider(Protocol):
    def get_menu(self, restaurant_id: str) -> MenuEvidence: ...


class DemoMenuProvider:
    """A small synthetic catalog, clearly labeled and not presented as real data."""

    source_label = "fictional demo"

    def __init__(self, catalog: "FictionalDemoProvider"):
        self._catalog = catalog

    def get_menu(self, restaurant_id: str) -> MenuEvidence:
        known_restaurants = {restaurant.id for restaurant in self._catalog.restaurants()}
        if restaurant_id not in known_restaurants:
            return MenuEvidence(status="unknown", source_label=None, items=())
        items = tuple(
            dish for dish in self._catalog.dishes()
            if dish.restaurant_id == restaurant_id
        )
        return MenuEvidence(
            status="demo",
            source_label=self.source_label,
            items=items,
        )


class UnknownMenuProvider:
    """No menu source is configured for an external restaurant."""

    def get_menu(self, restaurant_id: str) -> MenuEvidence:
        return MenuEvidence(status="unknown", source_label=None, items=())


class FictionalDemoProvider:
    """Synthetic restaurant and dish catalog used by the local recommendation engine."""

    source_label = "fictional demo"

    def restaurants(self) -> list[DemoRestaurant]:
        return [
            DemoRestaurant("demo-kitchen-01", "Demo Kitchen 01", "Demo City"),
            DemoRestaurant("demo-kitchen-02", "Demo Kitchen 02", "Demo City"),
            DemoRestaurant("demo-kitchen-03", "Demo Kitchen 03", "Demo City"),
        ]

    def dishes(self) -> list[DemoDish]:
        return list(_indian_demo_dishes())


_DATASET_FIELDS = {
    "id", "name", "description", "cuisine", "price_inr", "diet",
    "ingredient_tags", "spice_level", "taste_tags", "region", "category",
    "meal_type",
}
_SPICE_SCORES = {"mild": 2, "medium": 5, "spicy": 7, "very_spicy": 9}


@lru_cache(maxsize=1)
def _indian_demo_dishes() -> tuple[DemoDish, ...]:
    path = Path(__file__).with_name("data") / "indian_demo_dishes.json"
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("The bundled fictional Indian demo dataset is unavailable or invalid.") from error
    if not isinstance(dataset, dict):
        raise RuntimeError("The bundled fictional Indian demo dataset has an invalid envelope.")
    rows = dataset.get("dishes")
    if (
        dataset.get("dataset_type") != "synthetic_demo"
        or dataset.get("dish_count") != 1000
        or not isinstance(rows, list)
        or len(rows) != 1000
    ):
        raise RuntimeError("The bundled fictional Indian demo dataset failed its count or provenance check.")

    dishes = []
    seen_ids = set()
    seen_names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _DATASET_FIELDS:
            raise RuntimeError("A fictional Indian demo dish has missing or unexpected fields.")
        name = row["name"]
        dish_id = row["id"]
        if not isinstance(name, str) or not name.strip() or not isinstance(dish_id, str) or not dish_id:
            raise RuntimeError("A fictional Indian demo dish has an invalid name or ID.")
        if dish_id in seen_ids or name.casefold().strip() in seen_names:
            raise RuntimeError("The fictional Indian demo dataset contains duplicate dish IDs or names.")
        if (
            any(
                not isinstance(row[key], str) or not row[key].strip()
                for key in ("description", "cuisine", "diet", "spice_level", "region", "category", "meal_type")
            )
            or not isinstance(row["price_inr"], int)
            or isinstance(row["price_inr"], bool)
            or row["price_inr"] < 0
            or not isinstance(row["ingredient_tags"], list)
            or not row["ingredient_tags"]
            or any(not isinstance(item, str) or not item.strip() for item in row["ingredient_tags"])
            or row["diet"] not in {"vegan", "vegetarian", "egg", "non_vegetarian"}
            or row["spice_level"] not in _SPICE_SCORES
            or not isinstance(row["taste_tags"], list)
            or not row["taste_tags"]
            or any(not isinstance(item, str) or not item.strip() for item in row["taste_tags"])
        ):
            raise RuntimeError("A fictional Indian demo dish contains invalid metadata.")
        seen_ids.add(dish_id)
        seen_names.add(name.casefold().strip())
        # The source contains no restaurant facts; use only the existing fictional demo venue.
        dishes.append(DemoDish(
            id=dish_id,
            restaurant_id="demo-kitchen-01",
            name=name,
            description=row["description"],
            cuisine=row["cuisine"],
            price_inr=row["price_inr"],
            vegetarian=row["diet"] in {"vegan", "vegetarian"},
            ingredients=tuple(row["ingredient_tags"]),
            attributes={
                "spice": _SPICE_SCORES[row["spice_level"]],
                "dataset_metadata": row.copy(),
            },
        ))
    return tuple(dishes)
