from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FEATURE_NAMES = {
    "spice", "sweet", "sour", "salty", "richness", "oiliness", "smokiness",
    "crunch", "texture", "portion_size", "price_sensitivity", "adventure",
}
TASTE_MATCH_FEATURE_NAMES = FEATURE_NAMES - {"price_sensitivity", "adventure"}


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("name", "email")
    @classmethod
    def strip_fields(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if "@" not in value or "." not in value.rsplit("@", 1)[-1]:
            raise ValueError("Enter a valid email address")
        return value.lower()


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)


class TasteProfileUpdate(BaseModel):
    features: dict[str, int] | None = None
    preferred_cuisines: list[str] | None = None
    liked_ingredients: list[str] | None = None
    disliked_ingredients: list[str] | None = None
    dietary_restrictions: list[str] | None = None

    @field_validator("features")
    @classmethod
    def validate_features(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is not None:
            unknown = set(value) - FEATURE_NAMES
            if unknown:
                raise ValueError(f"Unknown taste features: {', '.join(sorted(unknown))}")
            if any(not 0 <= score <= 10 for score in value.values()):
                raise ValueError("Taste features must be between 0 and 10")
        return value


class FoodSearchRequest(BaseModel):
    query: str = Field(default="", max_length=500)
    budget_max: int | None = Field(default=None, ge=1, le=100000)
    exploration: float = Field(default=0.3, ge=0, le=1)
    limit: int = Field(default=10, ge=1, le=50)
    mode: str = Field(default="auto", max_length=20)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"auto", "usual", "different", "surprise"}:
            raise ValueError("Mode must be auto, usual, different, or surprise.")
        return value


class EvidenceSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    budget_max: int | None = Field(default=None, ge=1, le=100000)
    limit: int = Field(default=10, ge=1, le=50)
    use_taste_profile: bool = False

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a dish name to search menu evidence.")
        return value

    @field_validator("budget_max", "limit", mode="before")
    @classmethod
    def reject_boolean_limits(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Budget and result limit must be numeric values.")
        return value


class RagQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    restaurant_identity_id: str | None = Field(default=None, min_length=1, max_length=36)
    budget_max: Decimal | None = Field(default=None, gt=0, le=100000)
    limit: int = Field(default=5, ge=1, le=10)


class RagIndexRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)


class MenuPhotoProposalReviewRequest(BaseModel):
    review_status: str | None = None
    corrected_item_name: str | None = Field(default=None, max_length=160)
    corrected_variant: str | None = Field(default=None, max_length=80)
    corrected_price: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    corrected_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    corrected_availability: str | None = None
    reviewer_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("review_status")
    @classmethod
    def validate_review_status(cls, value: str | None) -> str | None:
        if value is not None and value not in {"accepted", "rejected"}:
            raise ValueError("Review status must be accepted or rejected.")
        return value

    @field_validator("corrected_item_name")
    @classmethod
    def normalize_corrected_item_name(cls, value: str | None) -> str | None:
        if value is not None:
            value = " ".join(value.split())
            if not value:
                raise ValueError("Corrected item name cannot be blank.")
        return value

    @field_validator("corrected_variant", "reviewer_notes")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None

    @field_validator("corrected_availability")
    @classmethod
    def validate_corrected_availability(cls, value: str | None) -> str | None:
        if value is not None and value not in {"menu_listed", "availability_unknown"}:
            raise ValueError("Photo review may confirm a menu listing or leave availability unknown.")
        return value

    @model_validator(mode="after")
    def require_changes(self) -> "MenuPhotoProposalReviewRequest":
        if not self.model_fields_set:
            raise ValueError("Provide a review decision or a correction.")
        return self


class MenuPhotoProposalCreateRequest(BaseModel):
    item_name: str = Field(min_length=1, max_length=160)
    variant: str | None = Field(default=None, max_length=80)
    price: Decimal | None = Field(default=None, ge=0, le=1000000, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    availability: str = "availability_unknown"
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_basis: str = Field(default="manual_reviewer_entry", min_length=1, max_length=120)
    reviewer_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("price", "confidence", mode="before")
    @classmethod
    def reject_boolean_measurements(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Price and confidence must be numeric values.")
        return value

    @field_validator("item_name")
    @classmethod
    def normalize_item_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Item name is required.")
        return value

    @field_validator("variant", "confidence_basis", "reviewer_notes")
    @classmethod
    def normalize_manual_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = " ".join(value.split())
            if not value and value is not None:
                raise ValueError("Text fields cannot be blank.")
        return value

    @field_validator("availability")
    @classmethod
    def validate_manual_availability(cls, value: str) -> str:
        if value not in {"menu_listed", "availability_unknown"}:
            raise ValueError("Availability must be menu_listed or availability_unknown.")
        return value

    @model_validator(mode="after")
    def validate_manual_price(self) -> "MenuPhotoProposalCreateRequest":
        if self.price is None and self.currency is not None:
            raise ValueError("Currency requires an explicit price.")
        if self.price is not None and self.currency is None:
            raise ValueError("A price requires an explicit three-letter currency code.")
        return self

class LocationSearchRequest(BaseModel):
    city: str | None = Field(default=None, min_length=1, max_length=150)
    food_query: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: int = Field(default=5000, ge=100, le=50000)
    limit: int = Field(default=10, ge=1, le=10)

    @field_validator("city", "food_query")
    @classmethod
    def strip_location_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def reject_boolean_coordinates(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Coordinates must be numeric latitude/longitude values.")
        return value

    @field_validator("radius_meters", "limit", mode="before")
    @classmethod
    def reject_boolean_limits(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Radius and result limit must be numeric values.")
        return value

    @model_validator(mode="after")
    def validate_location(self) -> "LocationSearchRequest":
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude:
            raise ValueError("Provide both latitude and longitude.")
        if self.city and has_latitude:
            raise ValueError("Choose an area search or a nearby search, not both.")
        if not self.city and not has_latitude:
            raise ValueError("Provide an area or an explicit nearby location.")
        return self


class RestaurantDiscoveryRequest(BaseModel):
    location_query: str | None = Field(default=None, min_length=1, max_length=150)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: int = Field(default=5000, ge=100, le=50000)
    cuisine: str | None = Field(default=None, min_length=1, max_length=80)
    food_query: str | None = Field(default=None, max_length=500)
    budget_max: int | None = Field(default=None, ge=1, le=100000)
    limit: int = Field(default=10, ge=1, le=10)
    use_taste_profile: bool = False

    @field_validator("location_query", "cuisine", "food_query")
    @classmethod
    def strip_discovery_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("Text inputs cannot be blank.")
        return value

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def reject_boolean_discovery_coordinates(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Coordinates must be numeric latitude/longitude values.")
        return value

    @field_validator("radius_meters", "budget_max", "limit", mode="before")
    @classmethod
    def reject_boolean_discovery_limits(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Radius, budget, and result limit must be numeric values.")
        return value

    @model_validator(mode="after")
    def validate_discovery_location(self) -> "RestaurantDiscoveryRequest":
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude:
            raise ValueError("Provide both latitude and longitude.")
        if has_latitude == bool(self.location_query):
            raise ValueError("Provide either an explicit location query or both coordinates.")
        return self


class RestaurantIdentityCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=80)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    website_url: str | None = Field(default=None, max_length=2048)
    provider_name: str | None = Field(default=None, min_length=1, max_length=100)
    provider_place_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("name", "address", "phone", "website_url", "provider_name", "provider_place_id")
    @classmethod
    def strip_identity_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        return value or None

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def reject_boolean_identity_coordinates(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("Coordinates must be numeric latitude/longitude values.")
        return value

    @model_validator(mode="after")
    def validate_identity_coordinates(self) -> "RestaurantIdentityCreateRequest":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude.")
        if (self.provider_name is None) != (self.provider_place_id is None):
            raise ValueError("Provide both provider name and provider place ID.")
        return self


class GroupParticipantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    query: str = Field(default="", max_length=500)
    dietary_restrictions: list[str] = Field(default_factory=list, max_length=10)
    ingredient_exclusions: list[str] = Field(default_factory=list, max_length=30)
    budget_max: int | None = Field(default=None, ge=1, le=100000)
    use_my_taste_profile: bool = False

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("dietary_restrictions", "ingredient_exclusions")
    @classmethod
    def normalize_constraints(cls, values: list[str]) -> list[str]:
        return [value.strip().lower() for value in values if value.strip()]


class GroupRecommendationRequest(BaseModel):
    participants: list[GroupParticipantRequest] = Field(min_length=2, max_length=8)


class TasteTransferRequest(BaseModel):
    dish_id: str = Field(min_length=1, max_length=80)
    friend_rating: int = Field(ge=1, le=5)
    friend_features: dict[str, int] = Field(min_length=1)
    consent_confirmed: bool

    @field_validator("friend_features")
    @classmethod
    def validate_friend_features(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = set(value) - FEATURE_NAMES
        if unknown:
            raise ValueError(f"Unknown taste features: {', '.join(sorted(unknown))}")
        if any(not 0 <= score <= 10 for score in value.values()):
            raise ValueError("Taste features must be between 0 and 10")
        if not set(value) & TASTE_MATCH_FEATURE_NAMES:
            raise ValueError("Include at least one comparable food taste feature.")
        return value

    @field_validator("consent_confirmed")
    @classmethod
    def require_consent(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Confirm that your friend agreed to share these taste features.")
        return value


class FoodFeedbackRequest(BaseModel):
    dish_id: str | None = Field(default=None, min_length=1, max_length=80)
    evidence_id: int | None = Field(default=None, ge=1)
    action: str
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if value not in {
            "tried", "like", "dislike", "save", "reject",
            "too_expensive", "too_spicy", "not_spicy_enough", "too_oily",
            "wrong_dish", "different",
        }:
            raise ValueError("Unsupported feedback action.")
        return value

    @model_validator(mode="after")
    def require_feedback_target(self):
        if self.dish_id is None and self.evidence_id is None:
            raise ValueError("Feedback must reference a dish or approved evidence.")
        return self


class FoodMemoryRequest(BaseModel):
    text: str = Field(min_length=2, max_length=500)
    permanence: str = "inferred"

    @field_validator("permanence")
    @classmethod
    def validate_permanence(cls, value: str) -> str:
        if value not in {"explicit", "inferred", "temporary"}:
            raise ValueError("Permanence must be explicit, inferred, or temporary")
        return value


class ReviewAnalysisRequest(BaseModel):
    excerpts: list[str] = Field(min_length=1, max_length=100)

    @field_validator("excerpts")
    @classmethod
    def validate_excerpts(cls, value: list[str]) -> list[str]:
        if any(not excerpt.strip() or len(excerpt) > 2000 for excerpt in value):
            raise ValueError("Review excerpts must contain 1–2000 characters")
        return [excerpt.strip() for excerpt in value]


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
