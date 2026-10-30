"""Preserve pre-provenance Taste DNA values as legacy preferences.

Revision ID: 5cb087842f31
Revises: 39a6c0d911fe
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5cb087842f31"
down_revision: Union[str, None] = "39a6c0d911fe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_FEATURES = {
    "spice": 4,
    "sweet": 5,
    "sour": 5,
    "salty": 5,
    "richness": 5,
    "oiliness": 5,
    "smokiness": 5,
    "crunch": 5,
    "texture": 5,
    "portion_size": 5,
    "price_sensitivity": 6,
    "adventure": 4,
}


def upgrade() -> None:
    profiles = sa.table(
        "taste_profiles",
        sa.column("user_id", sa.Integer()),
        sa.column("features", sa.JSON()),
        sa.column("feature_provenance", sa.JSON()),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(
        profiles.c.user_id,
        profiles.c.features,
        profiles.c.feature_provenance,
    ))
    for user_id, features, existing_provenance in rows:
        if not isinstance(features, dict):
            continue
        provenance = dict(existing_provenance or {})
        for feature, value in features.items():
            if feature in DEFAULT_FEATURES and value != DEFAULT_FEATURES[feature]:
                provenance.setdefault(feature, "legacy")
        if provenance != (existing_provenance or {}):
            connection.execute(
                profiles.update()
                .where(profiles.c.user_id == user_id)
                .values(feature_provenance=provenance)
            )


def downgrade() -> None:
    profiles = sa.table(
        "taste_profiles",
        sa.column("user_id", sa.Integer()),
        sa.column("features", sa.JSON()),
        sa.column("feature_provenance", sa.JSON()),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(
        profiles.c.user_id,
        profiles.c.features,
        profiles.c.feature_provenance,
    ))
    for user_id, features, existing_provenance in rows:
        provenance = dict(existing_provenance or {})
        features = features if isinstance(features, dict) else {}
        for feature, source in list(provenance.items()):
            if source == "legacy" and features.get(feature) != DEFAULT_FEATURES.get(feature):
                del provenance[feature]
        if provenance != (existing_provenance or {}):
            connection.execute(
                profiles.update()
                .where(profiles.c.user_id == user_id)
                .values(feature_provenance=provenance)
            )
