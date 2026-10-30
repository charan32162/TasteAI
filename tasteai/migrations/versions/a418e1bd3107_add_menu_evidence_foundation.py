"""Add source provenance, restaurant identities, and normalized menu evidence.

Revision ID: a418e1bd3107
Revises: 5cb087842f31
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a418e1bd3107"
down_revision: Union[str, None] = "5cb087842f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "restaurant_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("normalized_address", sa.String(length=500), nullable=True),
        sa.Column("phone", sa.String(length=80), nullable=True),
        sa.Column("normalized_phone", sa.String(length=32), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("website_domain", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_restaurant_identity_coordinate_pair",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_restaurant_identity_latitude",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_restaurant_identity_longitude",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_restaurant_identities_normalized_name"),
        "restaurant_identities",
        ["normalized_name"],
        unique=False,
    )
    op.create_table(
        "restaurant_identity_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("restaurant_identity_id", sa.String(length=36), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("provider_place_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["restaurant_identity_id"],
            ["restaurant_identities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_name",
            "provider_place_id",
            name="uq_identity_provider_place",
        ),
    )
    op.create_index(
        op.f("ix_restaurant_identity_links_restaurant_identity_id"),
        "restaurant_identity_links",
        ["restaurant_identity_id"],
        unique=False,
    )
    op.create_table(
        "menu_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("restaurant_identity_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("source_identifier", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("attribution", sa.String(length=255), nullable=True),
        sa.Column("authorization_status", sa.String(length=20), nullable=False),
        sa.Column("authorization_reference", sa.String(length=255), nullable=True),
        sa.Column("authorized_by", sa.String(length=120), nullable=True),
        sa.Column("freshness_ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "authorization_status IN ('pending', 'approved', 'revoked')",
            name="ck_menu_source_authorization_status",
        ),
        sa.CheckConstraint(
            "authorization_status != 'approved' OR "
            "(authorized_by IS NOT NULL AND authorization_reference IS NOT NULL)",
            name="ck_menu_source_approval_reference",
        ),
        sa.CheckConstraint(
            "freshness_ttl_seconds >= 60 AND freshness_ttl_seconds <= 31536000",
            name="ck_menu_source_freshness_ttl",
        ),
        sa.ForeignKeyConstraint(
            ["restaurant_identity_id"],
            ["restaurant_identities.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_name",
            "source_identifier",
            name="uq_menu_source_identifier",
        ),
    )
    op.create_index(
        op.f("ix_menu_sources_restaurant_identity_id"),
        "menu_sources",
        ["restaurant_identity_id"],
        unique=False,
    )
    op.create_table(
        "menu_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("menu_source_id", sa.String(length=36), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by", sa.String(length=120), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=80), nullable=True),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR length(content_sha256) = 64",
            name="ck_menu_document_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["menu_source_id"],
            ["menu_sources.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_menu_documents_menu_source_id"),
        "menu_documents",
        ["menu_source_id"],
        unique=False,
    )
    op.create_table(
        "menu_item_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("menu_document_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("cuisine", sa.String(length=120), nullable=True),
        sa.Column("food_type", sa.String(length=120), nullable=True),
        sa.Column("dietary_attributes", sa.JSON(), nullable=False),
        sa.Column("price_state", sa.String(length=16), nullable=False),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_currency", sa.String(length=3), nullable=True),
        sa.Column("price_min", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_max", sa.Numeric(12, 2), nullable=True),
        sa.Column("variant", sa.String(length=80), nullable=True),
        sa.Column("normalized_variant", sa.String(length=100), nullable=False),
        sa.Column("availability_state", sa.String(length=32), nullable=False),
        sa.Column("availability_evidence", sa.String(length=240), nullable=True),
        sa.Column("evidence_state", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.String(length=16), nullable=False),
        sa.Column("confidence_basis", sa.String(length=120), nullable=False),
        sa.Column("source_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "evidence_state IN ('verified', 'supported', 'inferred', 'unknown', 'contradicted')",
            name="ck_menu_item_evidence_state",
        ),
        sa.CheckConstraint(
            "availability_state IN ('menu_listed', 'availability_supported', "
            "'availability_unknown', 'temporarily_unavailable')",
            name="ck_menu_item_availability_state",
        ),
        sa.CheckConstraint(
            "price_state IN ('known', 'unknown')",
            name="ck_menu_item_price_state",
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low', 'unknown')",
            name="ck_menu_item_confidence",
        ),
        sa.CheckConstraint(
            "price_amount IS NULL OR price_amount > 0",
            name="ck_menu_item_price_positive",
        ),
        sa.CheckConstraint(
            "price_min IS NULL OR price_min > 0",
            name="ck_menu_item_price_min_positive",
        ),
        sa.CheckConstraint(
            "price_max IS NULL OR price_max > 0",
            name="ck_menu_item_price_max_positive",
        ),
        sa.CheckConstraint(
            "price_min IS NULL OR price_max IS NULL OR price_min <= price_max",
            name="ck_menu_item_price_range",
        ),
        sa.CheckConstraint(
            "availability_state NOT IN ('availability_supported', 'temporarily_unavailable') "
            "OR availability_evidence IS NOT NULL",
            name="ck_menu_item_availability_has_evidence",
        ),
        sa.CheckConstraint(
            "(price_state = 'unknown' AND price_amount IS NULL AND price_min IS NULL AND price_max IS NULL) "
            "OR (price_state = 'known' AND "
            "(price_amount IS NOT NULL OR price_min IS NOT NULL OR price_max IS NOT NULL))",
            name="ck_menu_item_price_state_values",
        ),
        sa.ForeignKeyConstraint(
            ["menu_document_id"],
            ["menu_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "menu_document_id",
            "normalized_name",
            "normalized_variant",
            "price_amount",
            "price_currency",
            name="uq_menu_item_document_fact",
        ),
    )
    op.create_index(
        op.f("ix_menu_item_evidence_menu_document_id"),
        "menu_item_evidence",
        ["menu_document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_menu_item_evidence_normalized_name"),
        "menu_item_evidence",
        ["normalized_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_menu_item_evidence_normalized_name"),
        table_name="menu_item_evidence",
    )
    op.drop_index(
        op.f("ix_menu_item_evidence_menu_document_id"),
        table_name="menu_item_evidence",
    )
    op.drop_table("menu_item_evidence")
    op.drop_index(
        op.f("ix_menu_documents_menu_source_id"),
        table_name="menu_documents",
    )
    op.drop_table("menu_documents")
    op.drop_index(op.f("ix_menu_sources_restaurant_identity_id"), table_name="menu_sources")
    op.drop_table("menu_sources")
    op.drop_index(
        op.f("ix_restaurant_identity_links_restaurant_identity_id"),
        table_name="restaurant_identity_links",
    )
    op.drop_table("restaurant_identity_links")
    op.drop_index(
        op.f("ix_restaurant_identities_normalized_name"),
        table_name="restaurant_identities",
    )
    op.drop_table("restaurant_identities")
