"""Add conservative canonical dishes and provenance-aware retrieval chunks."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "82c9384c5a10"
down_revision: Union[str, None] = "d2ab61cafe15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canonical_dishes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("canonical_name", sa.String(160), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("cuisine", sa.String(80)),
        sa.Column("dietary_type", sa.String(40)),
        sa.Column("description", sa.String(500)),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_canonical_dish_status"),
        sa.UniqueConstraint("normalized_name", name="uq_canonical_dish_normalized_name"),
    )
    op.create_index("ix_canonical_dishes_normalized_name", "canonical_dishes", ["normalized_name"])

    op.create_table(
        "dish_aliases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_dish_id", sa.String(36), sa.ForeignKey("canonical_dishes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(160), nullable=False),
        sa.Column("normalized_alias", sa.String(200), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_dish_alias_confidence"),
        sa.CheckConstraint("review_status IN ('pending', 'approved', 'rejected')", name="ck_dish_alias_review_status"),
        sa.CheckConstraint("review_status = 'pending' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name="ck_dish_alias_review_audit"),
        sa.UniqueConstraint("normalized_alias", name="uq_dish_alias_normalized_alias"),
    )
    op.create_index("ix_dish_aliases_canonical_dish_id", "dish_aliases", ["canonical_dish_id"])

    op.create_table(
        "dish_evidence_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_dish_id", sa.String(36), sa.ForeignKey("canonical_dishes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("menu_item_evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("linked_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_dish_evidence_link_confidence"),
        sa.UniqueConstraint("evidence_id", name="uq_dish_evidence_link_evidence"),
    )
    op.create_index("ix_dish_evidence_links_canonical_dish_id", "dish_evidence_links", ["canonical_dish_id"])
    op.create_index("ix_dish_evidence_links_evidence_id", "dish_evidence_links", ["evidence_id"])

    op.create_table(
        "evidence_chunks",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("menu_item_evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_dish_id", sa.String(36), sa.ForeignKey("canonical_dishes.id", ondelete="SET NULL")),
        sa.Column("restaurant_identity_id", sa.String(36), sa.ForeignKey("restaurant_identities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("menu_source_id", sa.String(36), sa.ForeignKey("menu_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("menu_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("evidence_state", sa.String(20), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(120), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="ck_evidence_chunk_index"),
        sa.CheckConstraint("embedding_dimension > 0", name="ck_evidence_chunk_embedding_dimension"),
        sa.UniqueConstraint("evidence_id", "chunk_index", name="uq_evidence_chunk_position"),
    )
    for column in ("evidence_id", "canonical_dish_id", "restaurant_identity_id", "menu_source_id", "document_id", "content_hash"):
        op.create_index(f"ix_evidence_chunks_{column}", "evidence_chunks", [column])


def downgrade() -> None:
    for column in ("evidence_id", "canonical_dish_id", "restaurant_identity_id", "menu_source_id", "document_id", "content_hash"):
        op.drop_index(f"ix_evidence_chunks_{column}", table_name="evidence_chunks")
    op.drop_table("evidence_chunks")
    op.drop_index("ix_dish_evidence_links_evidence_id", table_name="dish_evidence_links")
    op.drop_index("ix_dish_evidence_links_canonical_dish_id", table_name="dish_evidence_links")
    op.drop_table("dish_evidence_links")
    op.drop_index("ix_dish_aliases_canonical_dish_id", table_name="dish_aliases")
    op.drop_table("dish_aliases")
    op.drop_index("ix_canonical_dishes_normalized_name", table_name="canonical_dishes")
    op.drop_table("canonical_dishes")
