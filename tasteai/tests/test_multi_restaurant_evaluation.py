import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tasteai.database import Base
from tasteai.evaluation.multi_restaurant import (
    build_multi_restaurant_fixture,
    evaluate_multi_restaurant_fixture,
)


class MultiRestaurantEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        Base.metadata.drop_all(bind=self.engine)
        Base.metadata.create_all(bind=self.engine)

    def test_synthetic_fixture_covers_aliases_and_evidence_policy(self):
        with self.Session() as db:
            now = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
            fixture = build_multi_restaurant_fixture(db, now=now)
            checks = evaluate_multi_restaurant_fixture(db, fixture, now=now)
            self.assertEqual(checks, {
                "alias_matches": True,
                "budget_excludes_over_limit": True,
                "wrong_diet_fails_closed": True,
                "stale_and_revoked_not_returned": True,
                "all_results_are_cited": True,
            })

    def test_pending_or_unapproved_source_never_becomes_evidence(self):
        with self.Session() as db:
            fixture = build_multi_restaurant_fixture(
                db,
                now=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
            )
            source_ids = set(fixture.source_ids)
            self.assertEqual(len(source_ids), 3)
            self.assertNotIn(fixture.revoked_source_id, source_ids)


if __name__ == "__main__":
    unittest.main()
