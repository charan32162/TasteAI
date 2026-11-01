from dataclasses import dataclass
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
    attributes: dict[str, int | str]


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
        return [
            DemoDish("demo-chilli-chicken", "demo-kitchen-01", "Chilli Chicken Bowl", "A fictional demo bowl with a bold chilli profile.", "Indo-Chinese", 260, False, ("chicken", "chilli", "pepper"), {"spice": 8, "sweet": 2, "sour": 3, "salty": 6, "richness": 6, "oiliness": 5, "smokiness": 2, "crunch": 7, "texture": 7, "portion_size": 7, "adventure": 6, "protein": "chicken"}),
            DemoDish("demo-herb-paneer", "demo-kitchen-01", "Herb Paneer Plate", "A fictional demo paneer plate with herbs and a mild finish.", "North Indian", 240, True, ("paneer", "herbs", "yogurt"), {"spice": 3, "sweet": 1, "sour": 4, "salty": 5, "richness": 6, "oiliness": 3, "smokiness": 1, "crunch": 2, "texture": 5, "portion_size": 6, "adventure": 3, "protein": "paneer"}),
            DemoDish("demo-crispy-chickpeas", "demo-kitchen-02", "Crispy Chickpea Snack", "A fictional demo snack with toasted chickpeas and warm spice.", "Indian", 120, True, ("chickpeas", "cumin", "chilli"), {"spice": 6, "sweet": 1, "sour": 2, "salty": 6, "richness": 3, "oiliness": 2, "smokiness": 2, "crunch": 9, "texture": 9, "portion_size": 3, "adventure": 5, "protein": "chickpeas"}),
            DemoDish("demo-mushroom-rice", "demo-kitchen-02", "Mushroom Rice Bowl", "A fictional demo rice bowl with mushrooms and gentle seasoning.", "Fusion", 210, True, ("mushroom", "rice", "ginger"), {"spice": 2, "sweet": 1, "sour": 2, "salty": 4, "richness": 5, "oiliness": 2, "smokiness": 1, "crunch": 1, "texture": 4, "portion_size": 6, "adventure": 4, "protein": "mushroom"}),
            DemoDish("demo-smoky-chicken", "demo-kitchen-03", "Smoky Chicken Wrap", "A fictional demo wrap with a smoky chicken filling.", "Fusion", 290, False, ("chicken", "pepper", "flatbread"), {"spice": 5, "sweet": 1, "sour": 2, "salty": 5, "richness": 6, "oiliness": 4, "smokiness": 9, "crunch": 3, "texture": 6, "portion_size": 7, "adventure": 7, "protein": "chicken"}),
            DemoDish("demo-lentil-meal", "demo-kitchen-03", "Lentil Garden Meal", "A fictional demo meal with lentils, vegetables, and rice.", "Indian", 180, True, ("lentils", "vegetables", "rice"), {"spice": 3, "sweet": 2, "sour": 2, "salty": 4, "richness": 3, "oiliness": 1, "smokiness": 1, "crunch": 2, "texture": 3, "portion_size": 8, "adventure": 2, "protein": "lentils"}),
            DemoDish("demo-spicy-paneer", "demo-kitchen-01", "Spicy Paneer Bowl", "A fictional demo paneer bowl with chilli and pepper.", "North Indian", 280, True, ("paneer", "chilli", "pepper", "yogurt"), {"spice": 7, "sweet": 1, "sour": 3, "salty": 6, "richness": 5, "oiliness": 4, "smokiness": 1, "crunch": 3, "texture": 5, "portion_size": 7, "adventure": 5, "protein": "paneer"}),
        ]
