from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    profile: Mapped["TasteProfile"] = relationship(back_populates="user", cascade="all, delete-orphan", uselist=False)


class TasteProfile(Base):
    __tablename__ = "taste_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    features: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    feature_provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    preferred_cuisines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    liked_ingredients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    disliked_ingredients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    dietary_restrictions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    user: Mapped[User] = relationship(back_populates="profile")


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    city: Mapped[str | None] = mapped_column(String(120))
    source_label: Mapped[str] = mapped_column(String(80), nullable=False)
    is_fictional_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dishes: Mapped[list["Dish"]] = relationship(back_populates="restaurant")


class Dish(Base):
    __tablename__ = "dishes"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    restaurant_id: Mapped[str] = mapped_column(ForeignKey("restaurants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cuisine: Mapped[str] = mapped_column(String(80), nullable=False)
    price_inr: Mapped[int] = mapped_column(Integer, nullable=False)
    vegetarian: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ingredients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_label: Mapped[str] = mapped_column(String(80), nullable=False)
    restaurant: Mapped[Restaurant] = relationship(back_populates="dishes")


class RestaurantIdentity(Base):
    __tablename__ = "restaurant_identities"
    __table_args__ = (
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR "
            "(latitude IS NOT NULL AND longitude IS NOT NULL)",
            name="ck_restaurant_identity_coordinate_pair",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_restaurant_identity_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_restaurant_identity_longitude",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    normalized_address: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(80))
    normalized_phone: Mapped[str | None] = mapped_column(String(32))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    website_domain: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class RestaurantIdentityLink(Base):
    __tablename__ = "restaurant_identity_links"
    __table_args__ = (
        UniqueConstraint("provider_name", "provider_place_id", name="uq_identity_provider_place"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    restaurant_identity_id: Mapped[str] = mapped_column(
        ForeignKey("restaurant_identities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_place_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MenuSource(Base):
    __tablename__ = "menu_sources"
    __table_args__ = (
        CheckConstraint(
            "authorization_status IN ('pending', 'approved', 'revoked')",
            name="ck_menu_source_authorization_status",
        ),
        CheckConstraint(
            "authorization_status != 'approved' OR "
            "(authorized_by IS NOT NULL AND authorization_reference IS NOT NULL)",
            name="ck_menu_source_approval_reference",
        ),
        CheckConstraint(
            "freshness_ttl_seconds >= 60 AND freshness_ttl_seconds <= 31536000",
            name="ck_menu_source_freshness_ttl",
        ),
        UniqueConstraint("provider_name", "source_identifier", name="uq_menu_source_identifier"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    restaurant_identity_id: Mapped[str] = mapped_column(
        ForeignKey("restaurant_identities.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_identifier: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    attribution: Mapped[str | None] = mapped_column(String(255))
    authorization_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    authorization_reference: Mapped[str | None] = mapped_column(String(255))
    authorized_by: Mapped[str | None] = mapped_column(String(120))
    freshness_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MenuDocument(Base):
    __tablename__ = "menu_documents"
    __table_args__ = (
        CheckConstraint("content_sha256 IS NULL OR length(content_sha256) = 64", name="ck_menu_document_sha256"),
        CheckConstraint("extraction_status IN ('not_started', 'not_configured', 'proposed', 'failed')", name="ck_menu_document_extraction_status"),
        UniqueConstraint("storage_key", name="uq_menu_documents_storage_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    menu_source_id: Mapped[str] = mapped_column(
        ForeignKey("menu_sources.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[str | None] = mapped_column(String(120))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(80))
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    storage_key: Mapped[str | None] = mapped_column(String(80))
    content_length_bytes: Mapped[int | None] = mapped_column(Integer)
    image_width: Mapped[int | None] = mapped_column(Integer)
    image_height: Mapped[int | None] = mapped_column(Integer)
    extraction_status: Mapped[str] = mapped_column(String(24), default="not_started", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MenuItemProposal(Base):
    __tablename__ = "menu_item_proposals"
    __table_args__ = (
        CheckConstraint("review_status IN ('pending', 'accepted', 'rejected')", name="ck_menu_item_proposal_review_status"),
        CheckConstraint("review_status = 'pending' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name="ck_menu_item_proposal_review_audit"),
        CheckConstraint("extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)", name="ck_menu_item_proposal_confidence"),
        CheckConstraint("proposed_price IS NULL OR proposed_price >= 0", name="ck_menu_item_proposal_price_nonnegative"),
        CheckConstraint("corrected_price IS NULL OR corrected_price >= 0", name="ck_menu_item_proposal_corrected_price_nonnegative"),
        UniqueConstraint("committed_evidence_id", name="uq_menu_item_proposals_committed_evidence_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("menu_documents.id", ondelete="CASCADE"), index=True, nullable=False)
    item_name: Mapped[str] = mapped_column(String(160), nullable=False)
    variant: Mapped[str | None] = mapped_column(String(80))
    proposed_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    proposed_availability: Mapped[str] = mapped_column(String(32), default="menu_listed", nullable=False)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    menu_section: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500))
    extraction_page: Mapped[int | None] = mapped_column(Integer)
    extraction_bounding_box: Mapped[list | None] = mapped_column(JSON)
    confidence_basis: Mapped[str] = mapped_column(String(120), nullable=False)
    extraction_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    extraction_provider_version: Mapped[str | None] = mapped_column(String(80))
    review_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_notes: Mapped[str | None] = mapped_column(String(1000))
    corrected_item_name: Mapped[str | None] = mapped_column(String(160))
    corrected_variant: Mapped[str | None] = mapped_column(String(80))
    corrected_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    corrected_currency: Mapped[str | None] = mapped_column(String(3))
    corrected_availability: Mapped[str | None] = mapped_column(String(32))
    committed_evidence_id: Mapped[int | None] = mapped_column(ForeignKey("menu_item_evidence.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class MenuItemEvidence(Base):
    __tablename__ = "menu_item_evidence"
    __table_args__ = (
        CheckConstraint(
            "evidence_state IN ('verified', 'supported', 'inferred', 'unknown', 'contradicted')",
            name="ck_menu_item_evidence_state",
        ),
        CheckConstraint(
            "availability_state IN ('menu_listed', 'availability_supported', 'availability_unknown', "
            "'temporarily_unavailable')",
            name="ck_menu_item_availability_state",
        ),
        CheckConstraint(
            "price_state IN ('known', 'unknown')",
            name="ck_menu_item_price_state",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low', 'unknown')",
            name="ck_menu_item_confidence",
        ),
        CheckConstraint(
            "price_amount IS NULL OR price_amount >= 0",
            name="ck_menu_item_price_nonnegative",
        ),
        CheckConstraint(
            "price_min IS NULL OR price_min >= 0",
            name="ck_menu_item_price_min_nonnegative",
        ),
        CheckConstraint(
            "price_max IS NULL OR price_max >= 0",
            name="ck_menu_item_price_max_nonnegative",
        ),
        CheckConstraint(
            "price_min IS NULL OR price_max IS NULL OR price_min <= price_max",
            name="ck_menu_item_price_range",
        ),
        CheckConstraint(
            "availability_state NOT IN ('availability_supported', 'temporarily_unavailable') "
            "OR availability_evidence IS NOT NULL",
            name="ck_menu_item_availability_has_evidence",
        ),
        CheckConstraint(
            "(price_state = 'unknown' AND price_amount IS NULL AND price_min IS NULL AND price_max IS NULL) "
            "OR (price_state = 'known' AND "
            "(price_amount IS NOT NULL OR price_min IS NOT NULL OR price_max IS NOT NULL))",
            name="ck_menu_item_price_state_values",
        ),
        UniqueConstraint(
            "menu_document_id",
            "normalized_name",
            "normalized_variant",
            "price_amount",
            "price_currency",
            name="uq_menu_item_document_fact",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    menu_document_id: Mapped[str] = mapped_column(
        ForeignKey("menu_documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(120))
    cuisine: Mapped[str | None] = mapped_column(String(120))
    food_type: Mapped[str | None] = mapped_column(String(120))
    dietary_attributes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    price_state: Mapped[str] = mapped_column(String(16), nullable=False)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(String(3))
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    variant: Mapped[str | None] = mapped_column(String(80))
    normalized_variant: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    availability_state: Mapped[str] = mapped_column(String(32), nullable=False)
    availability_evidence: Mapped[str | None] = mapped_column(String(240))
    evidence_state: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_basis: Mapped[str] = mapped_column(String(120), nullable=False)
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CanonicalDish(Base):
    __tablename__ = "canonical_dishes"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'retired')", name="ck_canonical_dish_status"),
        UniqueConstraint("normalized_name", name="uq_canonical_dish_normalized_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    cuisine: Mapped[str | None] = mapped_column(String(80))
    dietary_type: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class DishAlias(Base):
    __tablename__ = "dish_aliases"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_dish_alias_confidence"),
        CheckConstraint("review_status IN ('pending', 'approved', 'rejected')", name="ck_dish_alias_review_status"),
        CheckConstraint("review_status = 'pending' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)", name="ck_dish_alias_review_audit"),
        UniqueConstraint("normalized_alias", name="uq_dish_alias_normalized_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_dish_id: Mapped[str] = mapped_column(ForeignKey("canonical_dishes.id", ondelete="CASCADE"), index=True, nullable=False)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(200), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    review_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DishEvidenceLink(Base):
    __tablename__ = "dish_evidence_links"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_dish_evidence_link_confidence"),
        UniqueConstraint("evidence_id", name="uq_dish_evidence_link_evidence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_dish_id: Mapped[str] = mapped_column(ForeignKey("canonical_dishes.id", ondelete="CASCADE"), index=True, nullable=False)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("menu_item_evidence.id", ondelete="CASCADE"), index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    linked_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class EvidenceChunk(Base):
    """A searchable, provenance-preserving chunk derived only from eligible evidence."""

    __tablename__ = "evidence_chunks"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="ck_evidence_chunk_index"),
        CheckConstraint("embedding_dimension > 0", name="ck_evidence_chunk_embedding_dimension"),
        UniqueConstraint("evidence_id", "chunk_index", name="uq_evidence_chunk_position"),
    )

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("menu_item_evidence.id", ondelete="CASCADE"), index=True, nullable=False)
    canonical_dish_id: Mapped[str | None] = mapped_column(ForeignKey("canonical_dishes.id", ondelete="SET NULL"), index=True)
    restaurant_identity_id: Mapped[str] = mapped_column(ForeignKey("restaurant_identities.id", ondelete="CASCADE"), index=True, nullable=False)
    menu_source_id: Mapped[str] = mapped_column(ForeignKey("menu_sources.id", ondelete="CASCADE"), index=True, nullable=False)
    document_id: Mapped[str] = mapped_column(ForeignKey("menu_documents.id", ondelete="CASCADE"), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_state: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    embedding_model: Mapped[str] = mapped_column(String(120), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class FoodEvent(Base):
    __tablename__ = "food_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dish_id: Mapped[str | None] = mapped_column(ForeignKey("dishes.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("menu_item_evidence.id", ondelete="SET NULL"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    rating: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    dish: Mapped["Dish"] = relationship()


class FoodMemory(Base):
    __tablename__ = "food_memories"
    __table_args__ = (UniqueConstraint("user_id", "subject", "kind", "detail", name="uq_user_memory"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[str] = mapped_column(String(240), nullable=False)
    permanence: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
