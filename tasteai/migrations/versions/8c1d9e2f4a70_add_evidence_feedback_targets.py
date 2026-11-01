"""Allow feedback to target approved menu evidence."""

from alembic import op
import sqlalchemy as sa


revision = "8c1d9e2f4a70"
down_revision = "7f4e2a1c9b30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("food_events") as batch:
        batch.alter_column("dish_id", existing_type=sa.String(length=80), nullable=True)
        batch.add_column(
            sa.Column(
                "evidence_id",
                sa.Integer(),
                sa.ForeignKey("menu_item_evidence.id", ondelete="SET NULL", name="fk_food_events_evidence_id"),
                nullable=True,
            )
        )
        batch.create_index("ix_food_events_evidence_id", ["evidence_id"])


def downgrade() -> None:
    with op.batch_alter_table("food_events") as batch:
        batch.drop_index("ix_food_events_evidence_id")
        batch.drop_column("evidence_id")
        batch.alter_column("dish_id", existing_type=sa.String(length=80), nullable=False)
