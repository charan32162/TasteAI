import unittest
from datetime import datetime, timedelta, timezone

from tasteai.engine import (
    active_memories,
    analyze_review_excerpts,
    capture_memory,
    group_recommendations,
    parse_intent,
    rank_dishes,
    update_profile_from_feedback,
    update_profile_from_rating,
    taste_transfer_comparison,
)
from tasteai.models import Dish, FoodEvent, FoodMemory, Restaurant, TasteProfile


def dish(dish_id="sample", name="Crispy Chickpea", price=120, vegetarian=True, attributes=None, ingredients=None):
    return Dish(
        id=dish_id,
        restaurant_id="sample-kitchen",
        name=name,
        description="A sample menu item.",
        cuisine="Indian",
        price_inr=price,
        vegetarian=vegetarian,
        ingredients=ingredients or ["chickpeas", "chilli"],
        attributes=attributes or {"spice": 7, "sweet": 2, "sour": 3, "salty": 5, "richness": 3, "oiliness": 2, "smokiness": 1, "crunch": 9, "adventure": 5},
        source_label="fictional demo",
        restaurant=Restaurant(id="sample-kitchen", name="Sample Kitchen", city="Demo City", source_label="fictional demo", is_fictional_demo=True),
    )


def profile():
    return TasteProfile(
        user_id=1,
        features={"spice": 5, "sweet": 5, "sour": 5, "salty": 5, "richness": 5, "oiliness": 5, "smokiness": 5, "crunch": 5, "adventure": 4},
        preferred_cuisines=[],
        liked_ingredients=[],
        disliked_ingredients=[],
        dietary_restrictions=[],
    )


class EngineTests(unittest.TestCase):
    def test_recommendation_modes_are_explicit_and_deterministic(self):
        self.assertEqual(parse_intent("Show me something like what I usually enjoy")["recommendation_mode"], "usual")
        self.assertEqual(parse_intent("Give me something different")["recommendation_mode"], "different")
        self.assertEqual(parse_intent("Surprise me")["recommendation_mode"], "surprise")

    def test_feedback_learning_is_bounded_and_provenanced(self):
        taste_profile = profile()
        self.assertEqual(update_profile_from_feedback(taste_profile, "too_spicy"), "spice")
        self.assertEqual(taste_profile.features["spice"], 4)
        self.assertEqual(taste_profile.feature_provenance["spice"], "learned")
        for _ in range(20):
            update_profile_from_feedback(taste_profile, "too_spicy")
        self.assertEqual(taste_profile.features["spice"], 0)

    def test_structured_feedback_preserves_explicit_features(self):
        taste_profile = profile()
        taste_profile.feature_provenance = {"spice": "explicit"}
        self.assertIsNone(update_profile_from_feedback(taste_profile, "too_spicy"))
        self.assertEqual(taste_profile.features["spice"], 5)
        self.assertEqual(taste_profile.feature_provenance["spice"], "explicit")

    def test_parses_food_intent_budget_and_exclusions(self):
        intent = parse_intent("I want chicken but not biryani, spicy, under ₹300")
        self.assertEqual(intent["protein"], "chicken")
        self.assertEqual(intent["spice_level"], "high")
        self.assertEqual(intent["budget_max"], 300)
        self.assertIn("biryani", intent["avoid"])

    def test_parses_low_spice_as_low(self):
        self.assertEqual(parse_intent("something mild, not spicy")["spice_level"], "low")

    def test_parses_guest_discovery_requests_and_hard_protein_exclusions(self):
        intents = {
            "spicy crispy": parse_intent("I want something spicy and crispy under ₹300"),
            "vegetarian": parse_intent("I want vegetarian food under ₹200"),
            "chicken": parse_intent("I want chicken and something spicy"),
            "no chicken": parse_intent("I don't want chicken"),
            "mild": parse_intent("I want something mild"),
            "crunchy": parse_intent("I want something crispy"),
            "adventurous": parse_intent("I want something adventurous"),
            "filling": parse_intent("I want something filling under ₹300"),
            "around": parse_intent("Give me vegetarian food around ₹200"),
        }
        self.assertEqual(intents["spicy crispy"]["spice_level"], "high")
        self.assertTrue(intents["spicy crispy"]["crunchy"])
        self.assertEqual(intents["spicy crispy"]["budget_max"], 300)
        self.assertTrue(intents["vegetarian"]["vegetarian"])
        self.assertEqual(intents["vegetarian"]["budget_max"], 200)
        self.assertEqual(intents["chicken"]["protein"], "chicken")
        self.assertEqual(intents["chicken"]["spice_level"], "high")
        self.assertIsNone(intents["no chicken"]["protein"])
        self.assertIn("chicken", intents["no chicken"]["exclude_proteins"])
        self.assertEqual(intents["mild"]["spice_level"], "low")
        self.assertTrue(intents["crunchy"]["crunchy"])
        self.assertEqual(intents["adventurous"]["adventure_level"], "high")
        self.assertTrue(intents["filling"]["filling"])
        self.assertEqual(intents["filling"]["budget_max"], 300)
        self.assertIsNone(intents["around"]["budget_max"])
        self.assertEqual(intents["around"]["budget_target"], 200)

    def test_unsupported_allergy_intent_is_explicit(self):
        intent = parse_intent("I am allergic to peanuts")
        self.assertTrue(intent["unsupported_constraints"])

    def test_protein_request_only_ranks_matching_dishes(self):
        results = rank_dishes(
            [dish("chicken", "Chilli Chicken Bowl", vegetarian=False), dish("veg", "Crispy Chickpea")],
            profile(), [], [], parse_intent("chicken but not biryani"), limit=10,
        )
        self.assertEqual([item["dish_id"] for item in results], ["chicken"])

    def test_hard_diet_and_budget_filters_precede_ranking(self):
        results = rank_dishes(
            [dish(price=180), dish("meat", "Chicken Bowl", 150, False)],
            profile(), [], [], parse_intent("veg under ₹200"), limit=10,
        )
        self.assertEqual([item["dish_id"] for item in results], ["sample"])
        self.assertTrue(all(item["price_inr"] <= 200 and item["vegetarian"] for item in results))

    def test_around_price_is_a_soft_target_not_a_hard_ceiling(self):
        near_target = dish("near", "Near target", price=180)
        above_target = dish("above", "Above target", price=290)
        results = rank_dishes(
            [above_target, near_target],
            profile(),
            [],
            [],
            parse_intent("around ₹200"),
        )
        self.assertEqual(results[0]["dish_id"], "near")
        self.assertEqual({item["dish_id"] for item in results}, {"near", "above"})

    def test_explicit_protein_exclusion_and_unsupported_diet_fail_closed(self):
        chicken = dish("chicken", "Chilli Chicken Bowl", vegetarian=False, ingredients=["chicken", "chilli"])
        veg = dish("veg", "Chickpea Plate", ingredients=["chickpeas"])
        results = rank_dishes([chicken, veg], profile(), [], [], parse_intent("I don't want chicken"))
        self.assertEqual([item["dish_id"] for item in results], ["veg"])
        blocked = rank_dishes(
            [veg], profile(), [], [], parse_intent("I have a peanut allergy"),
        )
        self.assertEqual(blocked, [])

    def test_multiple_requested_attributes_outscore_partial_match(self):
        complete = dish(
            "complete",
            "Spicy Crispy Bowl",
            price=260,
            attributes={"spice": 8, "crunch": 9, "texture": 9, "portion_size": 7, "oiliness": 4, "smokiness": 5},
        )
        partial = dish(
            "partial",
            "Spicy Soft Bowl",
            price=280,
            attributes={"spice": 8, "crunch": 1, "texture": 1, "portion_size": 7, "oiliness": 4, "smokiness": 5},
        )
        results = rank_dishes(
            [partial, complete], profile(), [], [],
            parse_intent("I want something spicy and crispy under ₹300"),
        )
        self.assertEqual(results[0]["dish_id"], "complete")
        self.assertTrue(all(item["price_inr"] <= 300 for item in results))
        self.assertTrue(any("crunch score is 9/10" in reason for reason in results[0]["why"]))
        self.assertTrue(any("within your ₹300 budget" in reason for reason in results[0]["why"]))

    def test_mild_filling_and_adventurous_requests_affect_ordering(self):
        mild = dish("mild", "Mild Filling Bowl", attributes={"spice": 2, "crunch": 2, "texture": 2, "portion_size": 9, "oiliness": 3, "adventure": 2})
        hot = dish("hot", "Hot Light Snack", attributes={"spice": 9, "crunch": 2, "texture": 2, "portion_size": 2, "oiliness": 3, "adventure": 9})
        mild_results = rank_dishes([hot, mild], profile(), [], [], parse_intent("something mild"))
        filling_results = rank_dishes([hot, mild], profile(), [], [], parse_intent("something filling"))
        adventure_results = rank_dishes([mild, hot], profile(), [], [], parse_intent("something adventurous"), exploration=0)
        self.assertEqual(mild_results[0]["dish_id"], "mild")
        self.assertEqual(filling_results[0]["dish_id"], "mild")
        self.assertEqual(adventure_results[0]["dish_id"], "hot")
        self.assertTrue(any("portion-size score is 9/10" in reason for reason in filling_results[0]["why"]))

    def test_explicit_memory_excludes_and_recent_rejection_is_downgraded(self):
        favorite = dish("favorite", "Chickpea Plate")
        rejected = dish("rejected", "Smoky Tofu Plate", ingredients=["tofu"])
        events = [FoodEvent(user_id=1, dish_id="rejected", kind="reject", created_at=datetime.now(timezone.utc))]
        results = rank_dishes(
            [favorite, rejected], profile(), events,
            [FoodMemory(user_id=1, kind="preference", subject="paneer", detail="Never paneer", permanence="explicit")],
            parse_intent(""), limit=10,
        )
        self.assertEqual(results[0]["dish_id"], "favorite")
        self.assertLess(results[1]["components"]["history"], 20)
        blocked = rank_dishes(
            [dish(name="Paneer bowl", ingredients=["paneer"])], profile(), [],
            [FoodMemory(user_id=1, kind="preference", subject="paneer", detail="Never paneer", permanence="explicit")],
            parse_intent(""), limit=10,
        )
        self.assertEqual(blocked, [])

    def test_rating_updates_inferred_features_without_creating_hard_exclusions(self):
        taste = profile()
        update_profile_from_rating(taste, dish(attributes={"spice": 9, "sweet": 2, "sour": 3, "salty": 5, "richness": 3, "oiliness": 2, "smokiness": 1, "crunch": 9, "adventure": 5}), 5)
        self.assertEqual(taste.features["spice"], 6)
        self.assertEqual(taste.feature_provenance["spice"], "inferred")
        self.assertIn("Indian", taste.preferred_cuisines)
        self.assertIn("chickpeas", taste.liked_ingredients)
        self.assertEqual(taste.disliked_ingredients, [])

    def test_rating_learning_preserves_explicit_taste_features(self):
        taste = profile()
        taste.feature_provenance = {"spice": "explicit"}
        update_profile_from_rating(taste, dish(), 5)
        self.assertEqual(taste.features["spice"], 5)
        self.assertEqual(taste.feature_provenance["spice"], "explicit")

    def test_legacy_non_default_taste_values_remain_transparent_ranking_signals(self):
        taste = profile()
        taste.features["spice"] = 9
        taste.feature_provenance = {"spice": "legacy"}
        result = rank_dishes([dish()], taste, [], [], parse_intent(""), limit=1)[0]
        self.assertIsNotNone(result["components"]["taste"])
        self.assertTrue(any("legacy preference" in reason for reason in result["why"]))

    def test_price_sensitivity_changes_price_match(self):
        cheap = dish("cheap", "Low price", price=100)
        expensive = dish("expensive", "Higher price", price=350)
        budget_conscious = profile()
        budget_conscious.features["price_sensitivity"] = 10
        premium = profile()
        premium.features["price_sensitivity"] = 0
        budget_scores = {
            item["dish_id"]: item["components"]["price"]
            for item in rank_dishes([cheap, expensive], budget_conscious, [], [], parse_intent(""))
        }
        premium_scores = {
            item["dish_id"]: item["components"]["price"]
            for item in rank_dishes([cheap, expensive], premium, [], [], parse_intent(""))
        }
        self.assertGreater(budget_scores["cheap"], budget_scores["expensive"])
        self.assertGreater(premium_scores["expensive"], premium_scores["cheap"])

    def test_memory_expiry_and_extraction(self):
        self.assertEqual(capture_memory("Never recommend paneer"), ("paneer", "Explicitly asked to avoid paneer."))
        self.assertEqual(capture_memory("Avoid spicy food today"), ("spicy", "Asked to avoid spicy food temporarily."))
        temporary = FoodMemory(user_id=1, kind="preference", subject="spicy", detail="Skip spice today", permanence="temporary", expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        expired = FoodMemory(user_id=1, kind="preference", subject="hot", detail="Old", permanence="temporary", expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        self.assertEqual(active_memories([temporary, expired]), [temporary])

    def test_review_analysis_returns_only_matching_caller_evidence(self):
        result = analyze_review_excerpts(["It was crispy but a little oily.", "The portion was generous."])
        self.assertEqual(result["review_count"], 2)
        self.assertEqual(result["aspects"]["texture"]["evidence"], ["It was crispy but a little oily."])
        self.assertNotIn("service", result["aspects"])
        self.assertEqual(analyze_review_excerpts(["No aspect words here."])["aspects"], {})

    def test_each_result_has_transparent_components_and_grounded_explanation(self):
        result = rank_dishes([dish()], profile(), [], [], parse_intent("crispy"), limit=1)[0]
        self.assertEqual(set(result["components"]), {"taste", "intent", "history", "price", "novelty"})
        self.assertTrue(result["why"])
        self.assertEqual(result["confidence_label"], "heuristic estimate")
        self.assertEqual(result["source_label"], "fictional demo")

    def test_missing_catalog_attributes_are_not_invented_as_scores_or_evidence(self):
        sparse_dish = dish(
            "sparse",
            "Sparse catalog item",
            attributes={"protein": "chicken"},
            ingredients=["chicken"],
        )
        result = rank_dishes(
            [sparse_dish],
            profile(),
            [],
            [],
            parse_intent("spicy crispy filling healthy adventurous"),
        )[0]
        self.assertIsNone(result["components"]["taste"])
        self.assertIsNone(result["components"]["intent"])
        self.assertFalse(any("demo spice score" in reason for reason in result["why"]))
        self.assertFalse(any("crunch score" in reason for reason in result["why"]))
        self.assertFalse(any("portion-size score" in reason for reason in result["why"]))
        self.assertFalse(any("oiliness score" in reason for reason in result["why"]))
        self.assertFalse(any("adventure score" in reason for reason in result["why"]))
        self.assertFalse(any("Taste DNA is close" in reason for reason in result["why"]))

    def test_group_recommendations_require_an_eligible_dish_for_every_attendee(self):
        chicken = dish("chicken", "Chilli Chicken Bowl", vegetarian=False, ingredients=["chicken", "chilli"])
        paneer = dish("paneer", "Herb Paneer Plate", ingredients=["paneer", "yogurt"])
        result = group_recommendations(
            [chicken, paneer],
            [
                {"name": "Alex", "query": "chicken", "dietary_restrictions": [], "ingredient_exclusions": [], "budget_max": None, "use_my_taste_profile": False},
                {"name": "Sam", "query": "vegetarian", "dietary_restrictions": ["vegetarian"], "ingredient_exclusions": [], "budget_max": None, "use_my_taste_profile": False},
            ],
            profile(),
            [],
        )
        self.assertFalse(result["no_compatible_options"])
        self.assertEqual(len(result["recommendations"]), 1)
        self.assertEqual(
            {item["dish_id"] for item in result["recommendations"][0]["menu_options"]},
            {"chicken", "paneer"},
        )

    def test_group_recommendations_fail_closed_for_unverifiable_dietary_needs(self):
        result = group_recommendations(
            [dish()],
            [{"name": "Alex", "query": "", "dietary_restrictions": ["peanut allergy"], "ingredient_exclusions": [], "budget_max": None, "use_my_taste_profile": False},
             {"name": "Sam", "query": "", "dietary_restrictions": [], "ingredient_exclusions": [], "budget_max": None, "use_my_taste_profile": False}],
            profile(),
            [],
        )
        self.assertTrue(result["no_compatible_options"])
        self.assertEqual(result["unsupported_restrictions"], {"Alex": ["peanut allergy"]})

    def test_group_recommendations_respect_vegan_ingredients(self):
        paneer = dish("paneer", "Paneer Plate", ingredients=["paneer", "yogurt"])
        egg = dish("egg", "Egg Plate", ingredients=["egg"])
        vegan = dish("vegan", "Chickpea Plate", price=120, ingredients=["chickpeas"])
        participants = [
            {"name": "Alex", "query": "", "dietary_restrictions": ["vegan"], "ingredient_exclusions": [], "budget_max": None, "use_my_taste_profile": False},
            {"name": "Sam", "query": "", "dietary_restrictions": [], "ingredient_exclusions": [], "budget_max": 150, "use_my_taste_profile": False},
        ]
        result = group_recommendations([paneer, egg, vegan], participants, profile(), [])
        self.assertFalse(result["no_compatible_options"])
        self.assertEqual(
            {item["dish_id"] for item in result["recommendations"][0]["menu_options"]},
            {"vegan"},
        )
        participants[1]["budget_max"] = 100
        self.assertTrue(group_recommendations([paneer, egg, vegan], participants, profile(), [])["no_compatible_options"])

    def test_taste_transfer_compares_only_features_shared_by_both_people(self):
        taste = profile()
        result = taste_transfer_comparison(
            dish(attributes={"spice": 8}),
            taste,
            {"spice": 9, "crunch": 5},
            5,
        )
        self.assertEqual(result["friend_rating"], 5)
        self.assertEqual(result["similarity_percent"], 80)
        self.assertEqual([item["feature"] for item in result["shared_features"]], ["crunch"])
        self.assertEqual([item["feature"] for item in result["different_features"]], ["spice"])
        self.assertIn("not a calibrated prediction", result["confidence_label"])


if __name__ == "__main__":
    unittest.main()
