"""Add update timestamps to restaurant identities.

Revision ID: 7f4e2a1c9b30
Revises: 91da4ef027ca
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f4e2a1c9b30"
down_revision: Union[str, None] = "91da4ef027ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("restaurant_identities") as batch_op:
        batch_op.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("restaurant_identities") as batch_op:
        batch_op.drop_column("updated_at")
