"""Add provenance for Taste DNA feature values.

Revision ID: 39a6c0d911fe
Revises: 21fa1dde6851
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "39a6c0d911fe"
down_revision: Union[str, None] = "21fa1dde6851"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "taste_profiles",
        sa.Column(
            "feature_provenance",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("taste_profiles", "feature_provenance")
