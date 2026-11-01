import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tasteai.canonical_dishes import (
    CanonicalDishError,
    create_canonical_dish,
    link_verified_evidence,
    propose_dish_alias,
    resolve_canonical_dish,
    review_dish_alias,
)
from tasteai.database import Base
from tasteai.menu_evidence import RestaurantIdentityCandidate, approve_menu_source, create_restaurant_identity, normalize_dish_name, register_menu_source
from tasteai.models import DishAlias, MenuDocument, MenuItemEvidence, User


ENGINE = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Session = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)


class CanonicalDishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=ENGINE)

    @classmethod
    def tearDownClass(cls):
        ENGINE.dispose()

    def setUp(self):
        Base.metadata.drop_all(bind=ENGINE)
        Base.metadata.create_all(bind=ENGINE)
        self.db = Session()
        self.user = User(name="Reviewer", email="reviewer@example.test", password_hash="test")
        self.db.add(self.user)
        self.identity = create_restaurant_identity(self.db, RestaurantIdentityCandidate(name="Test Hotel", address="1 Test Road"))
        self.source = register_menu_source(
            self.db,
            restaurant_identity_id=self.identity.id,
            source_type="user_captured_menu_photo",
            provider_name="user_captured_menu_photo",
            source_identifier=self.identity.id,
            attribution="User captured menu photo",
        )
        approve_menu_source(
            self.db,
            self.source.id,
            approved_by="reviewer-test",
            authorization_reference="test-photo-review",
        )
        captured = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.document = MenuDocument(
            id="document-test",
            menu_source_id=self.source.id,
            captured_at=captured,
            content_type="image/png",
            content_sha256="a" * 64,
        )
        self.db.add(self.document)
        self.db.flush()
        self.evidence = MenuItemEvidence(
            menu_document_id=self.document.id,
            name="Chicken Dum Biryani",
            normalized_name=normalize_dish_name("Chicken Dum Biryani"),
            price_state="known",
            price_amount=Decimal("260"),
            price_currency="INR",
            availability_state="menu_listed",
            evidence_state="verified",
            confidence="high",
            confidence_basis="human_menu_photo_review",
            source_metadata={},
            dietary_attributes=[],
            normalized_variant="",
        )
        self.db.add(self.evidence)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_deterministic_spelling_normalization_does_not_collapse_variants(self):
        canonical = create_canonical_dish(self.db, canonical_name="Chicken Dum Biryani")
        self.db.commit()
        self.assertEqual(resolve_canonical_dish(self.db, "Chicken Dum Biriyani").id, canonical.id)
        for distinct_name in (
            "Chicken Biryani", "Chicken Fry Biryani", "Chicken Mandi Biryani",
            "Special Chicken Biryani", "Chicken Dum Biryani Full",
        ):
            self.assertIsNone(resolve_canonical_dish(self.db, distinct_name), distinct_name)

    def test_llm_alias_suggestion_is_pending_until_review(self):
        canonical = create_canonical_dish(self.db, canonical_name="Chicken Dum Biryani")
        suggestion = propose_dish_alias(
            self.db,
            canonical_dish_id=canonical.id,
            alias="Dum Chicken Rice",
            source="llm_suggestion",
            confidence=0.76,
        )
        self.db.commit()
        self.assertEqual(suggestion.review_status, "pending")
        self.assertIsNone(resolve_canonical_dish(self.db, "Dum Chicken Rice"))
        approved = review_dish_alias(self.db, suggestion.id, reviewer_id=self.user.id, approve=True)
        self.db.commit()
        self.assertEqual(approved.review_status, "approved")
        self.assertEqual(approved.reviewed_by, self.user.id)
        self.assertEqual(resolve_canonical_dish(self.db, "Dum Chicken Rice").id, canonical.id)

    def test_alias_conflicts_are_rejected_instead_of_merging(self):
        first = create_canonical_dish(self.db, canonical_name="Chicken Biryani")
        create_canonical_dish(self.db, canonical_name="Chicken Fry Biryani")
        with self.assertRaisesRegex(CanonicalDishError, "belongs to a canonical name"):
            propose_dish_alias(
                self.db, canonical_dish_id=first.id, alias="Chicken Fry Biriyani",
                source="manual_review", confidence=1.0,
            )

    def test_only_approved_evidence_can_link_and_link_is_idempotent(self):
        canonical = create_canonical_dish(self.db, canonical_name="Chicken Dum Biryani")
        first = link_verified_evidence(
            self.db, canonical_dish_id=canonical.id, evidence_id=self.evidence.id,
            linked_by="reviewer:test", confidence=1.0,
        )
        again = link_verified_evidence(
            self.db, canonical_dish_id=canonical.id, evidence_id=self.evidence.id,
            linked_by="reviewer:test", confidence=1.0,
        )
        self.db.commit()
        self.assertEqual(first.id, again.id)
        self.assertEqual(first.evidence_id, self.evidence.id)

    def test_evidence_cannot_be_linked_to_two_canonical_dishes(self):
        first = create_canonical_dish(self.db, canonical_name="Chicken Dum Biryani")
        second = create_canonical_dish(self.db, canonical_name="Chicken Biryani")
        link_verified_evidence(
            self.db, canonical_dish_id=first.id, evidence_id=self.evidence.id,
            linked_by="reviewer:test",
        )
        with self.assertRaisesRegex(CanonicalDishError, "already linked"):
            link_verified_evidence(
                self.db, canonical_dish_id=second.id, evidence_id=self.evidence.id,
                linked_by="reviewer:test",
            )

    def test_invalid_name_or_confidence_rejected(self):
        with self.assertRaises(CanonicalDishError):
            create_canonical_dish(self.db, canonical_name="  ")
        canonical = create_canonical_dish(self.db, canonical_name="Chicken Dum Biryani")
        with self.assertRaises(CanonicalDishError):
            propose_dish_alias(
                self.db, canonical_dish_id=canonical.id, alias="Other Biryani",
                source="manual_review", confidence=1.1,
            )


if __name__ == "__main__":
    unittest.main()
