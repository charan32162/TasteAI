"""Add manual proposal creator provenance and allow zero-valued menu prices.

Revision ID: d2ab61cafe15
Revises: bc81a431dfee
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2ab61cafe15"
down_revision: Union[str, None] = "bc81a431dfee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    proposal_columns = {column["name"] for column in inspector.get_columns("menu_item_proposals")}
    proposal_checks = {item["name"] for item in inspector.get_check_constraints("menu_item_proposals")}
    proposal_fks = {item.get("name") for item in inspector.get_foreign_keys("menu_item_proposals")}
    with op.batch_alter_table("menu_item_proposals") as batch_op:
        if "ck_menu_item_proposal_price_positive" in proposal_checks:
            batch_op.drop_constraint("ck_menu_item_proposal_price_positive", type_="check")
        if "ck_menu_item_proposal_corrected_price_positive" in proposal_checks:
            batch_op.drop_constraint("ck_menu_item_proposal_corrected_price_positive", type_="check")
        if "ck_menu_item_proposal_price_nonnegative" not in proposal_checks:
            batch_op.create_check_constraint(
                "ck_menu_item_proposal_price_nonnegative", "proposed_price IS NULL OR proposed_price >= 0"
            )
        if "ck_menu_item_proposal_corrected_price_nonnegative" not in proposal_checks:
            batch_op.create_check_constraint(
                "ck_menu_item_proposal_corrected_price_nonnegative", "corrected_price IS NULL OR corrected_price >= 0"
            )
        if "created_by" not in proposal_columns:
            batch_op.add_column(sa.Column("created_by", sa.Integer(), nullable=True))
        if "fk_menu_item_proposals_created_by_users" not in proposal_fks:
            batch_op.create_foreign_key(
                "fk_menu_item_proposals_created_by_users", "users", ["created_by"], ["id"], ondelete="SET NULL"
            )
    proposal_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("menu_item_proposals")}
    if "ix_menu_item_proposals_created_by" not in proposal_indexes:
        op.create_index("ix_menu_item_proposals_created_by", "menu_item_proposals", ["created_by"], unique=False)

    evidence_checks = {item["name"] for item in sa.inspect(bind).get_check_constraints("menu_item_evidence")}
    with op.batch_alter_table("menu_item_evidence") as batch_op:
        for old_name, new_name, expression in (
            ("ck_menu_item_price_positive", "ck_menu_item_price_nonnegative", "price_amount IS NULL OR price_amount >= 0"),
            ("ck_menu_item_price_min_positive", "ck_menu_item_price_min_nonnegative", "price_min IS NULL OR price_min >= 0"),
            ("ck_menu_item_price_max_positive", "ck_menu_item_price_max_nonnegative", "price_max IS NULL OR price_max >= 0"),
        ):
            if old_name in evidence_checks:
                batch_op.drop_constraint(old_name, type_="check")
            if new_name not in evidence_checks:
                batch_op.create_check_constraint(new_name, expression)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    proposal_checks = {item["name"] for item in inspector.get_check_constraints("menu_item_proposals")}
    proposal_fks = {item.get("name") for item in inspector.get_foreign_keys("menu_item_proposals")}
    proposal_columns = {column["name"] for column in inspector.get_columns("menu_item_proposals")}
    proposal_indexes = {item["name"] for item in inspector.get_indexes("menu_item_proposals")}
    if "ix_menu_item_proposals_created_by" in proposal_indexes:
        op.drop_index("ix_menu_item_proposals_created_by", table_name="menu_item_proposals")
    with op.batch_alter_table("menu_item_proposals") as batch_op:
        if "fk_menu_item_proposals_created_by_users" in proposal_fks:
            batch_op.drop_constraint("fk_menu_item_proposals_created_by_users", type_="foreignkey")
        if "ck_menu_item_proposal_price_nonnegative" in proposal_checks:
            batch_op.drop_constraint("ck_menu_item_proposal_price_nonnegative", type_="check")
        if "ck_menu_item_proposal_corrected_price_nonnegative" in proposal_checks:
            batch_op.drop_constraint("ck_menu_item_proposal_corrected_price_nonnegative", type_="check")
        if "ck_menu_item_proposal_price_positive" not in proposal_checks:
            batch_op.create_check_constraint(
                "ck_menu_item_proposal_price_positive", "proposed_price IS NULL OR proposed_price > 0"
            )
        if "ck_menu_item_proposal_corrected_price_positive" not in proposal_checks:
            batch_op.create_check_constraint(
                "ck_menu_item_proposal_corrected_price_positive", "corrected_price IS NULL OR corrected_price > 0"
            )
        if "created_by" in proposal_columns:
            batch_op.drop_column("created_by")

    evidence_checks = {item["name"] for item in sa.inspect(bind).get_check_constraints("menu_item_evidence")}
    with op.batch_alter_table("menu_item_evidence") as batch_op:
        for new_name, old_name, expression in (
            ("ck_menu_item_price_nonnegative", "ck_menu_item_price_positive", "price_amount IS NULL OR price_amount > 0"),
            ("ck_menu_item_price_min_nonnegative", "ck_menu_item_price_min_positive", "price_min IS NULL OR price_min > 0"),
            ("ck_menu_item_price_max_nonnegative", "ck_menu_item_price_max_positive", "price_max IS NULL OR price_max > 0"),
        ):
            if new_name in evidence_checks:
                batch_op.drop_constraint(new_name, type_="check")
            if old_name not in evidence_checks:
                batch_op.create_check_constraint(old_name, expression)
