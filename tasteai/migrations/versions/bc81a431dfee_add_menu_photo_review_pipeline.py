"""Add private menu photo metadata and human-reviewed extraction proposals.

Revision ID: bc81a431dfee
Revises: a418e1bd3107
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "bc81a431dfee"
down_revision: Union[str, None] = "a418e1bd3107"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"]: column for column in inspector.get_columns("menu_documents")}
    existing_checks = {item["name"] for item in inspector.get_check_constraints("menu_documents")}
    existing_fks = {item.get("name") for item in inspector.get_foreign_keys("menu_documents")}
    existing_uniques = {item.get("name") for item in inspector.get_unique_constraints("menu_documents")}
    with op.batch_alter_table("menu_documents") as batch_op:
        if not existing_columns["captured_at"]["nullable"]:
            batch_op.alter_column("captured_at", existing_type=sa.DateTime(timezone=True), nullable=True)
        for column in (
            sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("uploaded_by", sa.Integer(), nullable=True),
            sa.Column("storage_key", sa.String(length=80), nullable=True),
            sa.Column("content_length_bytes", sa.Integer(), nullable=True),
            sa.Column("image_width", sa.Integer(), nullable=True),
            sa.Column("image_height", sa.Integer(), nullable=True),
            sa.Column("extraction_status", sa.String(length=24), nullable=False, server_default="not_started"),
        ):
            if column.name not in existing_columns:
                batch_op.add_column(column)
        if "ck_menu_document_extraction_status" not in existing_checks:
            batch_op.create_check_constraint(
                "ck_menu_document_extraction_status",
                "extraction_status IN ('not_started', 'not_configured', 'proposed', 'failed')",
            )
        if "fk_menu_documents_uploaded_by_users" not in existing_fks:
            batch_op.create_foreign_key(
                "fk_menu_documents_uploaded_by_users", "users", ["uploaded_by"], ["id"], ondelete="SET NULL"
            )
        if "uq_menu_documents_storage_key" not in existing_uniques:
            batch_op.create_unique_constraint("uq_menu_documents_storage_key", ["storage_key"])
    if "uploaded_at" not in existing_columns:
        op.execute("UPDATE menu_documents SET uploaded_at = created_at WHERE uploaded_at IS NULL")
    current_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("menu_documents")}
    if "uploaded_at" in current_columns:
        with op.batch_alter_table("menu_documents") as batch_op:
            batch_op.alter_column("uploaded_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    current_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("menu_documents")}
    if "ix_menu_documents_uploaded_by" not in current_indexes:
        op.create_index("ix_menu_documents_uploaded_by", "menu_documents", ["uploaded_by"], unique=False)

    if "menu_item_proposals" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
        "menu_item_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("item_name", sa.String(length=160), nullable=False),
        sa.Column("variant", sa.String(length=80), nullable=True),
        sa.Column("proposed_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("proposed_availability", sa.String(length=32), nullable=False, server_default="menu_listed"),
        sa.Column("extraction_confidence", sa.Float(), nullable=True),
        sa.Column("confidence_basis", sa.String(length=120), nullable=False),
        sa.Column("extraction_provider", sa.String(length=100), nullable=False),
        sa.Column("extraction_provider_version", sa.String(length=80), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_notes", sa.String(length=1000), nullable=True),
        sa.Column("corrected_item_name", sa.String(length=160), nullable=True),
        sa.Column("corrected_variant", sa.String(length=80), nullable=True),
        sa.Column("corrected_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("corrected_currency", sa.String(length=3), nullable=True),
        sa.Column("corrected_availability", sa.String(length=32), nullable=True),
        sa.Column("committed_evidence_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("review_status IN ('pending', 'accepted', 'rejected')", name="ck_menu_item_proposal_review_status"),
        sa.CheckConstraint("review_status = 'pending' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name="ck_menu_item_proposal_review_audit"),
        sa.CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_menu_item_proposal_confidence",
        ),
        sa.CheckConstraint("proposed_price IS NULL OR proposed_price > 0", name="ck_menu_item_proposal_price_positive"),
        sa.CheckConstraint("corrected_price IS NULL OR corrected_price > 0", name="ck_menu_item_proposal_corrected_price_positive"),
        sa.ForeignKeyConstraint(["document_id"], ["menu_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["committed_evidence_id"], ["menu_item_evidence.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("committed_evidence_id", name="uq_menu_item_proposals_committed_evidence_id"),
        )
    proposal_indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("menu_item_proposals")}
    if "ix_menu_item_proposals_document_id" not in proposal_indexes:
        op.create_index("ix_menu_item_proposals_document_id", "menu_item_proposals", ["document_id"], unique=False)
    if "ix_menu_item_proposals_reviewed_by" not in proposal_indexes:
        op.create_index("ix_menu_item_proposals_reviewed_by", "menu_item_proposals", ["reviewed_by"], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "menu_item_proposals" in tables:
        indexes = {item["name"] for item in inspector.get_indexes("menu_item_proposals")}
        if "ix_menu_item_proposals_reviewed_by" in indexes:
            op.drop_index("ix_menu_item_proposals_reviewed_by", table_name="menu_item_proposals")
        if "ix_menu_item_proposals_document_id" in indexes:
            op.drop_index("ix_menu_item_proposals_document_id", table_name="menu_item_proposals")
        op.drop_table("menu_item_proposals")
    if "menu_documents" not in tables:
        return
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("menu_documents")}
    captured_nullable = next(
        (item["nullable"] for item in inspector.get_columns("menu_documents") if item["name"] == "captured_at"),
        False,
    )
    checks = {item["name"] for item in inspector.get_check_constraints("menu_documents")}
    fks = {item.get("name") for item in inspector.get_foreign_keys("menu_documents")}
    uniques = {item.get("name") for item in inspector.get_unique_constraints("menu_documents")}
    indexes = {item["name"] for item in inspector.get_indexes("menu_documents")}
    if "ix_menu_documents_uploaded_by" in indexes:
        op.drop_index("ix_menu_documents_uploaded_by", table_name="menu_documents")
    with op.batch_alter_table("menu_documents") as batch_op:
        if "uq_menu_documents_storage_key" in uniques:
            batch_op.drop_constraint("uq_menu_documents_storage_key", type_="unique")
        if "fk_menu_documents_uploaded_by_users" in fks:
            batch_op.drop_constraint("fk_menu_documents_uploaded_by_users", type_="foreignkey")
        if "ck_menu_document_extraction_status" in checks:
            batch_op.drop_constraint("ck_menu_document_extraction_status", type_="check")
        for column in ("extraction_status", "image_height", "image_width", "content_length_bytes", "storage_key", "uploaded_by", "uploaded_at"):
            if column in columns:
                batch_op.drop_column(column)
        if "captured_at" in columns and captured_nullable:
            batch_op.alter_column("captured_at", existing_type=sa.DateTime(timezone=True), nullable=False)
