import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Path as PathParam, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .database import SessionLocal, engine, get_db
from .engine import (
    analyze_review_excerpts,
    capture_memory,
    group_recommendations,
    parse_intent,
    rank_dishes,
    intent_weights,
    ranking_weights,
    taste_transfer_comparison,
    update_profile_from_feedback,
    update_profile_from_rating,
)
from .evidence_discovery import search_menu_evidence
from .agent import AgentState, MAX_AGENT_STEPS, MAX_AGENT_TOOL_CALLS, run_bounded_food_flow
from .ai_providers import AIProviderError, HashEmbeddingProvider, QueryIntent, configured_embedding_provider, configured_llm_provider, llm_provider_configured
from .models import (
    Dish,
    EvidenceChunk,
    FoodEvent,
    FoodMemory,
    MenuDocument,
    MenuItemEvidence,
    MenuItemProposal,
    MenuSource,
    Restaurant,
    RestaurantIdentity,
    RestaurantIdentityLink,
    TasteProfile,
    User,
    utc_now,
)
from .providers import DemoMenuProvider, FictionalDemoProvider, UnknownMenuProvider
from .location_providers import (
    GeoPoint,
    PlaceLookupRateLimit,
    RestaurantProviderError,
    configured_restaurant_provider,
    restaurant_provider_status,
)
from .menu_evidence import (
    MenuEvidenceError,
    RestaurantIdentityCandidate,
    create_restaurant_identity,
    normalize_dish_name,
    restaurant_menu_evidence,
    resolve_restaurant_identity,
)
from .menu_evidence import _is_stale
from .menu_photos import (
    MenuPhotoError,
    NoMenuPhotoExtractor,
    configured_menu_photo_extractor,
    menu_photo_extractor_status,
    commit_menu_photo_proposals,
    create_manual_menu_photo_proposal,
    is_menu_photo_reviewer,
    review_menu_photo_proposal,
    upload_menu_photo,
)
from .menu_photo_storage import MenuPhotoDecoderUnavailable, MenuPhotoStorageError, maximum_upload_bytes
from .rag import RagError, RagQueryRateLimit, answer_from_evidence, generate_grounded_claims, index_approved_evidence, search_rag_context, validate_grounded_claims
from .restaurant_discovery import discover_restaurants, filter_places_by_radius
from .schemas import (
    FEATURE_NAMES,
    FoodFeedbackRequest,
    EvidenceSearchRequest,
    FoodMemoryRequest,
    FoodSearchRequest,
    GroupRecommendationRequest,
    LoginRequest,
    LocationSearchRequest,
    MenuPhotoProposalCreateRequest,
    MenuPhotoProposalReviewRequest,
    RegisterRequest,
    RagIndexRequest,
    RagQueryRequest,
    ReviewAnalysisRequest,
    RestaurantDiscoveryRequest,
    RestaurantIdentityCreateRequest,
    TasteTransferRequest,
    TasteProfileUpdate,
)
from .security import create_access_token, decode_access_token, hash_password, validate_security_config, verify_password

load_dotenv(Path(__file__).with_name(".env"))
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
# HTTPX includes full request URLs in INFO records; Nominatim URLs contain user-entered areas.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("tasteai")
provider = FictionalDemoProvider()
menu_provider = DemoMenuProvider(provider)
unknown_menu_provider = UnknownMenuProvider()
place_lookup_rate_limit = PlaceLookupRateLimit()
rag_query_rate_limit = RagQueryRateLimit()
menu_ocr_rate_limit = RagQueryRateLimit(per_client=2, global_limit=10)
DEFAULT_FEATURES = {key: 5 for key in FEATURE_NAMES}
DEFAULT_FEATURES["spice"] = 4
DEFAULT_FEATURES["adventure"] = 4
DEFAULT_FEATURES["price_sensitivity"] = 6


def migrate_schema() -> None:
    config = Config(str(Path(__file__).with_name("alembic.ini")))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" not in tables and tables:
        baseline_columns = {
            "users": {"id", "name", "email", "password_hash", "created_at"},
            "restaurants": {"id", "name", "city", "source_label", "is_fictional_demo"},
            "dishes": {
                "id", "restaurant_id", "name", "description", "cuisine", "price_inr",
                "vegetarian", "ingredients", "attributes", "source_label",
            },
            "taste_profiles": {
                "user_id", "features", "preferred_cuisines", "liked_ingredients",
                "disliked_ingredients", "dietary_restrictions", "updated_at",
            },
            "food_memories": {
                "id", "user_id", "kind", "subject", "detail", "permanence",
                "expires_at", "created_at",
            },
            "food_events": {
                "id", "user_id", "dish_id", "kind", "rating", "note", "created_at",
            },
        }
        if any(
            table not in tables
            or not columns <= {column["name"] for column in inspector.get_columns(table)}
            for table, columns in baseline_columns.items()
        ):
            raise RuntimeError(
                "Existing TasteAI database schema is not recognized; review and apply Alembic migrations manually."
            )
        profile_columns = {
            column["name"] for column in inspector.get_columns("taste_profiles")
        }
        command.stamp(
            config,
            "39a6c0d911fe" if "feature_provenance" in profile_columns else "21fa1dde6851",
        )
    command.upgrade(config, "head")


def seed_catalog(db: Session) -> None:
    for item in provider.restaurants():
        if db.get(Restaurant, item.id) is None:
            db.add(Restaurant(id=item.id, name=item.name, city=item.city, source_label=provider.source_label, is_fictional_demo=True))
    db.flush()
    for item in provider.dishes():
        existing = db.get(Dish, item.id)
        if existing is None:
            db.add(Dish(
                id=item.id,
                restaurant_id=item.restaurant_id,
                name=item.name,
                description=item.description,
                cuisine=item.cuisine,
                price_inr=item.price_inr,
                vegetarian=item.vegetarian,
                ingredients=list(item.ingredients),
                attributes=item.attributes,
                source_label=provider.source_label,
            ))
        elif existing.source_label == provider.source_label:
            existing.attributes = {**existing.attributes, **item.attributes}
    db.commit()
    logger.info("seeded fictional demo catalog: dishes=%d reviews=0", len(provider.dishes()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_security_config()
    restaurant_provider_status()
    if os.getenv("TASTEAI_AUTO_CREATE_SCHEMA", "true").lower() == "true":
        migrate_schema()
        with SessionLocal() as db:
            seed_catalog(db)
    yield


app = FastAPI(
    title="TasteAI",
    description="Local-first, explainable personal food recommendations.",
    version="0.1.0",
    lifespan=lifespan,
)
WEB_DIR = Path(__file__).with_name("web")
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.middleware("http")
async def log_request(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info("request method=%s path=%s status=%d duration_ms=%s", request.method, request.url.path, response.status_code, elapsed_ms)
    response.headers["X-Response-Time-ms"] = str(elapsed_ms)
    return response


def optional_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    header = request.headers.get("Authorization", "")
    if not header:
        return None
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue.")
    user_id = decode_access_token(header[7:])
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session is invalid or expired.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your account is no longer available.")
    return user


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = optional_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue.")
    return user


def current_menu_photo_reviewer(user: User = Depends(current_user)) -> User:
    try:
        permitted = is_menu_photo_reviewer(user.id)
    except MenuPhotoError as error:
        logger.error("invalid menu photo reviewer configuration")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Menu photo review is not configured.") from error
    if not permitted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Menu photo review access is not enabled for this account.")
    return user


def get_menu_photo_extractor():
    try:
        extractor = configured_menu_photo_extractor()
    except MenuPhotoError as error:
        logger.warning("external menu OCR configuration invalid: %s", type(error).__name__)
        extractor = NoMenuPhotoExtractor()
    try:
        yield extractor
    finally:
        _close_optional_provider(extractor)


def _close_optional_provider(provider_instance) -> None:
    close = getattr(provider_instance, "close", None)
    if callable(close):
        close()


def ensure_profile(db: Session, user_id: int) -> TasteProfile:
    profile = db.get(TasteProfile, user_id)
    if profile is None:
        profile = TasteProfile(
            user_id=user_id,
            features=DEFAULT_FEATURES.copy(),
            feature_provenance={},
            preferred_cuisines=[],
            liked_ingredients=[],
            disliked_ingredients=[],
            dietary_restrictions=[],
        )
        db.add(profile)
        db.flush()
    return profile


def save_memory(
    db: Session,
    user_id: int,
    kind: str,
    subject: str,
    detail: str,
    permanence: str,
    expires_at: datetime | None,
) -> FoodMemory:
    memory = db.scalar(select(FoodMemory).where(
        FoodMemory.user_id == user_id,
        FoodMemory.kind == kind,
        FoodMemory.subject == subject,
        FoodMemory.detail == detail,
    ))
    if memory is None:
        memory = FoodMemory(user_id=user_id, kind=kind, subject=subject, detail=detail, permanence=permanence, expires_at=expires_at)
        db.add(memory)
    else:
        memory.permanence = permanence
        memory.expires_at = expires_at
    return memory


def profile_payload(profile: TasteProfile) -> dict:
    return {
        "features": profile.features,
        "feature_provenance": profile.feature_provenance,
        "preferred_cuisines": profile.preferred_cuisines,
        "liked_ingredients": profile.liked_ingredients,
        "disliked_ingredients": profile.disliked_ingredients,
        "dietary_restrictions": profile.dietary_restrictions,
        "updated_at": profile.updated_at,
    }


def search_results(db: Session, user: User | None, request_data: FoodSearchRequest) -> dict:
    profile = ensure_profile(db, user.id) if user else TasteProfile(
        user_id=0,
        features=DEFAULT_FEATURES.copy(),
        feature_provenance={},
        preferred_cuisines=[],
        liked_ingredients=[],
        disliked_ingredients=[],
        dietary_restrictions=[],
    )
    intent = parse_intent(request_data.query, request_data.budget_max)
    if request_data.mode != "auto":
        intent["recommendation_mode"] = request_data.mode
    dishes = db.scalars(select(Dish).options(joinedload(Dish.restaurant))).all()
    events = list(
        db.scalars(
            select(FoodEvent)
            .options(joinedload(FoodEvent.dish))
            .where(FoodEvent.user_id == user.id)
            .order_by(FoodEvent.created_at)
        )
    ) if user else []
    memories = list(db.scalars(select(FoodMemory).where(FoodMemory.user_id == user.id))) if user else []
    known_dish_names = {
        normalize_dish_name(event.dish.name)
        for event in events
        if event.dish is not None and event.kind in {"tried", "save"}
    }
    evidence_event_ids = {
        event.evidence_id
        for event in events
        if event.evidence_id is not None and event.kind in {"tried", "save"}
    }
    if evidence_event_ids:
        known_dish_names.update(
            normalize_dish_name(item.name)
            for item in db.scalars(
                select(MenuItemEvidence).where(MenuItemEvidence.id.in_(evidence_event_ids))
            )
        )
    profile_exploration = int(profile.features.get("adventure", 4)) / 10
    exploration = max(0.0, min(1.0, request_data.exploration * 0.75 + profile_exploration * 0.25))
    has_food_intent = any((
        intent["protein"], intent["exclude_proteins"], intent["spice_level"],
        intent["attribute_targets"], intent["vegetarian"], intent["budget_max"],
        intent["budget_target"], intent["avoid"], intent["affordable"],
        intent["value_requested"], intent["unsupported_constraints"],
        intent["adventure_level"] == "high",
    ))
    location_only = bool(intent["location_requested"] or intent["restaurant_discovery"]) and not has_food_intent
    evidence_result = None
    if not location_only:
        evidence_result = search_menu_evidence(
            db,
            request_data.query,
            profile=profile if user else None,
            budget_max=request_data.budget_max,
            limit=request_data.limit,
            mode=request_data.mode,
            known_dish_names=known_dish_names,
        )
    if evidence_result and evidence_result["results"]:
        ranked = [
            {
                "dish_id": f"evidence:{item['evidence_id']}",
                "name": item["dish"]["name"],
                "description": item["dish"]["description"],
                "restaurant": item["restaurant"]["name"],
                "price_inr": int(Decimal(item["price"]["amount"])) if item["price"]["amount"] is not None else None,
                "source_label": "approved menu evidence",
                "score": item["ranking"]["score"],
                "components": item["ranking"]["components"],
                "why": item["why"],
                "explanation": item["explanation"],
                "evidence": item,
            }
            for item in evidence_result["results"]
        ]
    else:
        ranked = [] if location_only else rank_dishes(
            dishes, profile, events, memories, intent, exploration, request_data.limit
        )
    return {
        "intent": intent,
        "recommendations": ranked,
        "evidence_search": evidence_result,
        "pipeline": {
            "intent": "deterministic_authority",
            "candidate_source": (
                "approved_menu_evidence"
                if evidence_result and evidence_result["results"]
                else "fictional_demo_catalog"
            ),
            "hard_constraints_before_ranking": True,
            "grounded_explanations": True,
            "feedback_target": "evidence_id_or_dish_id",
            "fallback": (
                "demo_catalog_when_no_approved_evidence_match"
                if not location_only
                else "none_for_location_only_request"
            ),
        },
        "ranking_weights": ranking_weights(),
        "intent_weights": intent_weights(),
        "personalization": {
            "mode": intent["recommendation_mode"],
            "authority_order": [
                "safety_and_dietary_restrictions",
                "explicit_exclusions",
                "current_request",
                "budget",
                "location",
                "contextual_preferences",
                "taste_dna",
                "exploration",
            ],
        },
        "guest_mode": user is None,
        "clarification": (
            "The demo catalog can't verify this dietary requirement; no dishes were recommended."
            if intent["unsupported_constraints"] or (
                any(item.lower() not in {"vegetarian", "vegan", "plant-based"} for item in profile.dietary_restrictions)
            )
            else "This request contains contradictory requirements; no dishes were recommended."
            if intent["contradictions"]
            else "Use the optional nearby search section; location is requested only after you choose a lookup."
            if location_only and intent["location_requested"]
            else "Add an area to the optional nearby search, or enter a food craving to browse the fictional demo menu."
            if location_only
            else "No supported food preference was detected; showing the closest available menu options."
            if not has_food_intent
            else None
        ),
    }


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/terms", include_in_schema=False)
def terms_of_use():
    return FileResponse(WEB_DIR / "terms.html")


@app.get("/privacy", include_in_schema=False)
def privacy_policy():
    return FileResponse(WEB_DIR / "privacy.html")


@app.get("/api/v1/health")
def health():
    places = restaurant_provider_status()
    return {
        "status": "ok",
        "catalog_source": provider.source_label,
        "external_providers_enabled": places["places_enabled"],
        "restaurant_provider": places["provider"],
    }


@app.get("/api/v1/providers/status")
def providers_status():
    return restaurant_provider_status()


@app.get("/api/v1/ai/status")
def ai_status(db: Session = Depends(get_db)):
    llm_enabled = llm_provider_configured()
    embeddings_enabled = (
        os.getenv("TASTEAI_ALLOW_EXTERNAL_EMBEDDINGS", "false").strip().casefold() == "true"
        and bool(os.getenv("OPENAI_API_KEY"))
    )
    return {
        "llm": {"status": "enabled" if llm_enabled else "disabled", "provider": "openai" if llm_enabled else None},
        "embeddings": {"status": "external_enabled" if embeddings_enabled else "local_deterministic", "provider": "openai" if embeddings_enabled else "tasteai_hash"},
        "rag": {"status": "ready" if db.scalar(select(EvidenceChunk.id).limit(1)) else "empty", "indexed_chunks": db.query(EvidenceChunk).count()},
        "ocr": menu_photo_extractor_status(),
        "agent": {"status": "bounded_deterministic", "max_steps": MAX_AGENT_STEPS, "max_tool_calls": MAX_AGENT_TOOL_CALLS},
    }


@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    user = User(name=body.name, email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.flush()
        profile = TasteProfile(
            user_id=user.id,
            features=DEFAULT_FEATURES.copy(),
            feature_provenance={},
            preferred_cuisines=[],
            liked_ingredients=[],
            disliked_ingredients=[],
            dietary_restrictions=[],
        )
        db.add(profile)
        db.commit()
        db.refresh(user)
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with that email already exists.") from error
    return {"access_token": create_access_token(user.id), "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email}}


@app.post("/api/v1/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower().strip()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email or password is incorrect.")
    return {"access_token": create_access_token(user.id), "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email}}


@app.get("/api/v1/users/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email}


@app.get("/api/v1/users/me/taste-profile")
def get_taste_profile(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return profile_payload(ensure_profile(db, user.id))


@app.patch("/api/v1/users/me/taste-profile")
def update_taste_profile(body: TasteProfileUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    profile = ensure_profile(db, user.id)
    if body.features is not None:
        profile.features = {**profile.features, **body.features}
        provenance = dict(profile.feature_provenance or {})
        provenance.update({feature: "explicit" for feature in body.features})
        profile.feature_provenance = provenance
    for field in ("preferred_cuisines", "liked_ingredients", "disliked_ingredients", "dietary_restrictions"):
        value = getattr(body, field)
        if value is not None:
            setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile_payload(profile)


@app.get("/api/v1/food/catalog")
def catalog(limit: int = 50, db: Session = Depends(get_db)):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="limit must be between 1 and 100")
    dishes = db.scalars(select(Dish).options(joinedload(Dish.restaurant)).limit(limit)).all()
    return [{
        "dish_id": dish.id, "name": dish.name, "description": dish.description, "cuisine": dish.cuisine,
        "price_inr": dish.price_inr, "vegetarian": dish.vegetarian, "ingredients": dish.ingredients,
        "restaurant": dish.restaurant.name, "source_label": dish.source_label,
    } for dish in dishes]


@app.post("/api/v1/food/search")
def food_search(body: FoodSearchRequest, db: Session = Depends(get_db), user: User | None = Depends(optional_current_user)):
    return search_results(db, user, body)


@app.post("/api/v1/food/recommend")
def recommend(body: FoodSearchRequest, db: Session = Depends(get_db), user: User | None = Depends(optional_current_user)):
    return search_results(db, user, body)


@app.post("/api/v1/food/evidence-search")
def evidence_food_search(
    body: EvidenceSearchRequest,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_current_user),
):
    if body.use_taste_profile and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use your Taste DNA for menu evidence ranking.",
        )
    profile = ensure_profile(db, user.id) if body.use_taste_profile and user else None
    return search_menu_evidence(
        db,
        body.query,
        profile=profile,
        budget_max=body.budget_max,
        limit=body.limit,
    )


@app.post("/api/v1/rag/index")
def reindex_approved_evidence(
    body: RagIndexRequest,
    db: Session = Depends(get_db),
    _: User = Depends(current_menu_photo_reviewer),
):
    embedder = None
    try:
        embedder = configured_embedding_provider()
        result = index_approved_evidence(db, embedding_provider=embedder, limit=body.limit)
        db.commit()
        return result
    except (RagError, AIProviderError) as error:
        db.rollback()
        logger.warning("approved evidence indexing unavailable: %s", type(error).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Evidence indexing is unavailable; existing search remains available.") from error
    finally:
        _close_optional_provider(embedder)


@app.post("/api/v1/rag/query")
def rag_query(body: RagQueryRequest, request: Request, db: Session = Depends(get_db)):
    client_id = request.client.host if request.client else "unknown"
    if not rag_query_rate_limit.check(client_id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="RAG query limit reached; try again shortly.", headers={"Retry-After": "60"})
    effective_budget = body.budget_max
    if effective_budget is None:
        parsed_budget = parse_intent(body.query).get("budget_max")
        if parsed_budget is not None:
            effective_budget = Decimal(parsed_budget)
    try:
        embedder = None
        try:
            embedder = configured_embedding_provider()
            results = search_rag_context(
                db,
                body.query,
                embedding_provider=embedder,
                restaurant_identity_id=body.restaurant_identity_id,
                budget_max=effective_budget,
                limit=body.limit,
            )
        except AIProviderError:
            results = search_rag_context(
                db,
                body.query,
                embedding_provider=HashEmbeddingProvider(),
                restaurant_identity_id=body.restaurant_identity_id,
                budget_max=effective_budget,
                limit=body.limit,
            )
        finally:
            _close_optional_provider(embedder)
        grounded = answer_from_evidence(results)
        generation_status = "no_evidence" if not results else "disabled"
        llm = None
        try:
            llm = configured_llm_provider() if results else None
            if llm is not None:
                claims = generate_grounded_claims(llm, body.query, results)
                grounded = validate_grounded_claims(results, claims.claims)
                generation_status = "claims_validated" if grounded["grounding_status"] == "llm_claims_validated" else "deterministic_fallback"
        except AIProviderError as error:
            generation_status = "deterministic_fallback"
            logger.info("grounded RAG generation fell back to deterministic answer: %s", type(error).__name__)
        finally:
            _close_optional_provider(llm)
        return {
            **grounded,
            "retrieval": "hybrid_lexical_and_vector" if any(item.vector_score > 0 for item in results) else "lexical_fallback",
            "generation": {"status": generation_status, "provider": "openai" if generation_status == "claims_validated" else None},
            "result_count": len(results),
            "results": [item.__dict__ for item in results],
        }
    except RagError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@app.post("/api/v1/groups/recommend")
def recommend_group(body: GroupRecommendationRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    participants = [person.model_dump() for person in body.participants]
    if sum(person["use_my_taste_profile"] for person in participants) > 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only one attendee can use your Taste DNA.")
    profile = ensure_profile(db, user.id)
    memories = list(db.scalars(select(FoodMemory).where(FoodMemory.user_id == user.id)))
    dishes = db.scalars(select(Dish).options(joinedload(Dish.restaurant))).all()
    result = group_recommendations(dishes, participants, profile, memories)
    result["attendee_count"] = len(participants)
    result["scoring_note"] = (
        "Group scores are heuristic estimates. Each suggested venue has at least one listed demo dish "
        "per attendee that passes the provided constraints."
    )
    result["dietary_note"] = (
        "Dietary eligibility is checked only against the fictional menu flags and listed demo ingredients. "
        "The catalog cannot verify allergens, cross-contact, or food preparation."
    )
    return result


@app.post("/api/v1/food/taste-transfer")
def transfer_taste(body: TasteTransferRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    dish = db.scalar(select(Dish).options(joinedload(Dish.restaurant)).where(Dish.id == body.dish_id))
    if dish is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dish not found.")
    profile = ensure_profile(db, user.id)
    result = taste_transfer_comparison(dish, profile, body.friend_features, body.friend_rating)
    result["friend_consent_confirmed"] = body.consent_confirmed
    result["source_label"] = dish.source_label
    result["friend_rating_note"] = "Friend rating was supplied by you and is not independently verified."
    result["privacy_note"] = "Only the friend-provided feature values in this request were compared; no other account was accessed."
    return result


@app.get("/api/v1/dishes/{dish_id}")
def get_dish(dish_id: str, db: Session = Depends(get_db)):
    dish = db.scalar(select(Dish).options(joinedload(Dish.restaurant)).where(Dish.id == dish_id))
    if dish is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dish not found.")
    return {
        "dish_id": dish.id, "name": dish.name, "description": dish.description, "cuisine": dish.cuisine,
        "price_inr": dish.price_inr, "vegetarian": dish.vegetarian, "ingredients": dish.ingredients,
        "attributes": dish.attributes, "restaurant": dish.restaurant.name, "city": dish.restaurant.city,
        "source_label": dish.source_label, "reviews": [],
    }


@app.get("/api/v1/restaurants/providers/{place_id}")
def get_provider_restaurant(
    request: Request,
    place_id: str = PathParam(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$"),
):
    restaurant_provider = configured_restaurant_provider()
    if not restaurant_provider.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=restaurant_provider.reason or "Restaurant provider is disabled.",
        )
    _lookup_rate_limit(request)
    started = time.perf_counter()
    try:
        place = restaurant_provider.get_restaurant_details(place_id)
    except RestaurantProviderError as error:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.warning(
            "places_lookup provider=%s operation=place_details outcome=failure duration_ms=%.2f result_count=0 error_category=%s",
            restaurant_provider.name,
            duration_ms,
            error.category,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "places_lookup provider=%s operation=place_details outcome=success duration_ms=%.2f result_count=%d",
        restaurant_provider.name,
        duration_ms,
        int(place is not None),
    )
    if place is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant place was not found.",
        )
    return _restaurant_payload(place)


@app.get("/api/v1/restaurants/nearby")
def restaurants_nearby(city: str | None = None, db: Session = Depends(get_db)):
    # The demo catalog has no real locations, hours, or distances.
    restaurants = db.scalars(select(Restaurant).order_by(Restaurant.name)).all()
    return [{
        "id": item.id, "name": item.name, "city": item.city, "source_label": item.source_label,
        "distance_km": None, "availability": None,
    } for item in restaurants if city is None or item.city.lower() == city.lower()]


def _restaurant_payload(place, requested_food_query: str | None = None) -> dict:
    distance_known = place.distance_km is not None
    menu = unknown_menu_provider.get_menu(place.provider_id)
    rating_known = place.rating is not None
    rating_count_known = place.rating_count is not None
    price_level_known = place.price_level is not None
    return {
        "id": place.provider_id,
        "name": place.name,
        "address": place.address,
        "city": place.city,
        "cuisine": place.cuisine,
        "source_label": place.source_label,
        "distance_km": place.distance_km,
        "rating": place.rating,
        "rating_count": place.rating_count,
        "price_level": place.price_level,
        "maps_url": place.maps_url,
        "website_url": place.website_url,
        "phone": place.phone,
        "opening_hours": list(place.opening_hours),
        "types": list(place.types),
        "takeaway": place.takeaway,
        "delivery": place.delivery,
        "dietary_tags": list(place.dietary_tags),
        "attribution": place.attribution or place.source_label,
        "attribution_url": place.attribution_url,
        "maps_url_label": place.maps_url_label or (
            "Open in Google Maps" if place.source_label == "Google Maps" else "Open map"
        ),
        "recommendation_status": "candidate_only",
        "requested_food_query": requested_food_query,
        "menu_availability": menu.status,
        "menu_source": menu.source_label,
        "dishes": [item.name for item in menu.items],
        "review_evidence_status": "unknown",
        "provenance": {
            "identity": {"status": "provided", "source": place.source_label},
            "address": {
                "status": "provided" if place.address else "unknown",
                "source": place.source_label if place.address else None,
            },
            "location": {
                "status": "provided" if place.latitude is not None and place.longitude is not None else "unknown",
                "source": place.source_label if place.latitude is not None and place.longitude is not None else None,
            },
            "distance_km": {
                "status": "calculated" if distance_known else "unknown",
                "source": "TasteAI geodesic calculation" if distance_known else None,
            },
            "rating": {
                "status": "provided" if rating_known else "unknown",
                "source": place.source_label if rating_known else None,
            },
            "rating_count": {
                "status": "provided" if rating_count_known else "unknown",
                "source": place.source_label if rating_count_known else None,
            },
            "restaurant_price_level": {
                "status": "provided" if price_level_known else "unknown",
                "source": place.source_label if price_level_known else None,
            },
            "menu_price": {"status": "unknown", "source": None},
            "dish_availability": {"status": "unknown", "source": None},
            "review_evidence": {"status": "unknown", "source": None},
            "maps_url": {
                "status": "provided" if place.maps_url else "unknown",
                "source": place.source_label if place.maps_url else None,
            },
            "website_url": {
                "status": "provided" if place.website_url else "unknown",
                "source": place.source_label if place.website_url else None,
            },
        },
        "explanation": (
            "This is a restaurant candidate, not a dish recommendation. "
            "Dish availability, menu price, dietary suitability, and review evidence are unknown."
        ),
    }


def _lookup_rate_limit(request: Request) -> None:
    client_key = request.client.host if request.client else "unknown-client"
    retry_after = place_lookup_rate_limit.check(client_key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Restaurant lookup limit reached. Please wait before searching again.",
            headers={"Retry-After": str(retry_after)},
        )


def _lookup_places(body: LocationSearchRequest, request: Request, nearby: bool) -> dict:
    restaurant_provider = configured_restaurant_provider()
    results = []
    provider_operation = "nearby_search" if nearby else "text_search"
    request_sent = False
    excluded_outside_radius_count = 0
    excluded_missing_coordinates_count = 0
    if restaurant_provider.enabled:
        _lookup_rate_limit(request)
    started = time.perf_counter()
    try:
        if restaurant_provider.enabled:
            if nearby:
                if body.latitude is None or body.longitude is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Provide an explicit nearby location.",
                    )
                places = restaurant_provider.nearby_restaurants(
                    GeoPoint(body.latitude, body.longitude),
                    body.radius_meters,
                    body.limit,
                )
                request_sent = True
                places, excluded_outside_radius_count, excluded_missing_coordinates_count = (
                    filter_places_by_radius(
                        places,
                        GeoPoint(body.latitude, body.longitude),
                        body.radius_meters,
                    )
                )
            else:
                if not body.city:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Provide an area or city to search.",
                    )
                places = restaurant_provider.search_restaurants(
                    f"restaurants in {body.city}",
                    body.limit,
                )
                request_sent = True
            results = [
                _restaurant_payload(place, body.food_query)
                for place in places[:body.limit]
            ]
    except HTTPException:
        raise
    except RestaurantProviderError as error:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.warning(
            "places_lookup provider=%s operation=%s outcome=failure duration_ms=%.2f result_count=0 error_category=%s",
            restaurant_provider.name,
            provider_operation,
            duration_ms,
            error.category,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "places_lookup provider=%s operation=%s outcome=success duration_ms=%.2f result_count=%d",
        restaurant_provider.name,
        provider_operation,
        duration_ms,
        len(results),
    )
    return {
        "provider": restaurant_provider.name,
        "places_enabled": restaurant_provider.enabled,
        "reason": restaurant_provider.reason,
        "location_stored": False,
        "request_sent": request_sent,
        "excluded_outside_radius_count": excluded_outside_radius_count,
        "excluded_missing_coordinates_count": excluded_missing_coordinates_count,
        "requested_food_query": body.food_query,
        "food_intent": parse_intent(body.food_query or ""),
        "ranking_status": (
            "not_ranked_without_dish_evidence" if results else "no_restaurant_candidates"
        ),
        "recommendations": [],
        "results": results,
    }


@app.post("/api/v1/restaurants/search")
def search_restaurants(body: LocationSearchRequest, request: Request):
    if not body.city:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide an area or city to search.",
        )
    return _lookup_places(body, request, nearby=False)


@app.post("/api/v1/restaurants/discover")
def discover_restaurant_candidates(
    body: RestaurantDiscoveryRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_current_user),
):
    if body.use_taste_profile and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to use your Taste DNA during restaurant discovery.",
        )
    restaurant_provider = configured_restaurant_provider()
    if restaurant_provider.enabled:
        _lookup_rate_limit(request)
    profile = ensure_profile(db, user.id) if body.use_taste_profile and user else None
    started = time.perf_counter()
    try:
        result = discover_restaurants(db, restaurant_provider, body, profile=profile)
    except RestaurantProviderError as error:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.warning(
            "restaurant_discovery provider=%s outcome=failure duration_ms=%.2f result_count=0 error_category=%s",
            restaurant_provider.name,
            duration_ms,
            error.category,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "restaurant_discovery provider=%s outcome=success duration_ms=%.2f result_count=%d",
        restaurant_provider.name,
        duration_ms,
        len(result["results"]),
    )
    return result


def _restaurant_identity_payload(identity, db: Session) -> dict:
    links = db.scalars(
        select(RestaurantIdentityLink).where(
            RestaurantIdentityLink.restaurant_identity_id == identity.id
        ).order_by(RestaurantIdentityLink.id)
    ).all()
    return {
        "id": identity.id,
        "name": identity.name,
        "normalized_name": identity.normalized_name,
        "address": identity.address,
        "phone": identity.phone,
        "latitude": identity.latitude,
        "longitude": identity.longitude,
        "website_domain": identity.website_domain,
        "created_at": identity.created_at,
        "updated_at": identity.updated_at,
        "provider_links": [
            {
                "provider_name": link.provider_name,
                "provider_place_id": link.provider_place_id,
                "created_at": link.created_at,
            }
            for link in links
        ],
    }


@app.post("/api/v1/menu-evidence/restaurant-identities", status_code=status.HTTP_201_CREATED)
def create_menu_evidence_restaurant_identity(
    body: RestaurantIdentityCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(current_menu_photo_reviewer),
):
    candidate = RestaurantIdentityCandidate(**body.model_dump())
    try:
        resolution = resolve_restaurant_identity(db, candidate)
        if resolution.state.value == "conflict":
            raise MenuEvidenceError("Restaurant identity conflicts with an existing provider association.")
        if resolution.restaurant_identity_id:
            identity = db.get(RestaurantIdentity, resolution.restaurant_identity_id)
            if identity is None:
                raise MenuEvidenceError("Resolved restaurant identity no longer exists.")
            return {"created": False, "identity": _restaurant_identity_payload(identity, db)}
        identity = create_restaurant_identity(db, candidate)
        db.commit()
        db.refresh(identity)
        return {"created": True, "identity": _restaurant_identity_payload(identity, db)}
    except MenuEvidenceError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@app.get("/api/v1/menu-evidence/restaurant-identities/{identity_id}")
def get_menu_evidence_restaurant_identity(
    identity_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
):
    identity = db.get(RestaurantIdentity, identity_id)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant identity not found.")
    return _restaurant_identity_payload(identity, db)


@app.get("/api/v1/restaurants/{restaurant_id}")
def get_restaurant(restaurant_id: str, db: Session = Depends(get_db)):
    restaurant = db.scalar(select(Restaurant).options(joinedload(Restaurant.dishes)).where(Restaurant.id == restaurant_id))
    if restaurant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Restaurant not found.")
    menu = menu_provider.get_menu(restaurant_id)
    return {
        "id": restaurant.id, "name": restaurant.name, "city": restaurant.city,
        "source_label": restaurant.source_label, "distance_km": None, "availability": None,
        "menu_availability": menu.status, "menu_source": menu.source_label,
        "dishes": [{
            "dish_id": dish.id, "name": dish.name, "price_inr": dish.price_inr,
            "vegetarian": dish.vegetarian, "source_label": dish.source_label,
            "availability": "demo", "price_provenance": dish.source_label,
        } for dish in restaurant.dishes] if menu.status == "demo" else [],
    }


@app.get("/api/v1/menu-evidence/restaurants/{identity_id}")
def get_restaurant_menu_evidence(
    identity_id: str = PathParam(min_length=1, max_length=36),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
    db: Session = Depends(get_db),
):
    evidence = restaurant_menu_evidence(db, identity_id, limit=limit, offset=offset)
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant menu evidence not found.",
        )
    return evidence


def _menu_photo_document_payload(document: MenuDocument, *, duplicate: bool = False) -> dict:
    return {
        "document_id": document.id,
        "captured_at": document.captured_at,
        "uploaded_at": document.uploaded_at,
        "content_type": document.content_type,
        "content_sha256": document.content_sha256,
        "content_length_bytes": document.content_length_bytes,
        "image_width": document.image_width,
        "image_height": document.image_height,
        "extraction_status": document.extraction_status,
        "duplicate": duplicate,
    }


def _menu_photo_proposal_payload(proposal: MenuItemProposal) -> dict:
    return {
        "id": proposal.id,
        "document_id": proposal.document_id,
        "created_at": proposal.created_at,
        "item_name": proposal.item_name,
        "variant": proposal.variant,
        "proposed_price": str(proposal.proposed_price) if proposal.proposed_price is not None else None,
        "currency": proposal.currency,
        "proposed_availability": proposal.proposed_availability,
        "extraction_confidence": proposal.extraction_confidence,
        "menu_section": proposal.menu_section,
        "description": proposal.description,
        "extraction_page": proposal.extraction_page,
        "extraction_bounding_box": proposal.extraction_bounding_box,
        "confidence_basis": proposal.confidence_basis,
        "extraction_provider": proposal.extraction_provider,
        "extraction_provider_version": proposal.extraction_provider_version,
        "review_status": proposal.review_status,
        "created_by": proposal.created_by,
        "reviewed_by": proposal.reviewed_by,
        "reviewed_at": proposal.reviewed_at,
        "reviewer_notes": proposal.reviewer_notes,
        "corrected_item_name": proposal.corrected_item_name,
        "corrected_variant": proposal.corrected_variant,
        "corrected_price": str(proposal.corrected_price) if proposal.corrected_price is not None else None,
        "corrected_currency": proposal.corrected_currency,
        "corrected_availability": proposal.corrected_availability,
        "committed": proposal.committed_evidence_id is not None,
    }


@app.post("/api/v1/menu-photos", status_code=status.HTTP_201_CREATED)
async def upload_menu_photo_endpoint(
    request: Request,
    restaurant_identity_id: str = Header(alias="X-Restaurant-Identity-Id", min_length=1, max_length=36),
    captured_at: str | None = Header(default=None, alias="X-Menu-Captured-At", max_length=64),
    provider_name: str | None = Header(default=None, alias="X-Restaurant-Provider", max_length=100),
    provider_place_id: str | None = Header(default=None, alias="X-Restaurant-Place-Id", max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(current_menu_photo_reviewer),
    extractor=Depends(get_menu_photo_extractor),
):
    try:
        max_bytes = maximum_upload_bytes()
    except MenuPhotoStorageError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    if getattr(extractor, "provider_name", "not_configured") != "not_configured":
        if not menu_ocr_rate_limit.check(f"reviewer:{user.id}"):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Menu OCR limit reached; try again shortly.", headers={"Retry-After": "60"})
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The menu photo exceeds the configured upload limit.")
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Content-Length is invalid.") from error
    image = bytearray()
    async for chunk in request.stream():
        image.extend(chunk)
        if len(image) > max_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="The menu photo exceeds the configured upload limit.")
    content_type = request.headers.get("content-type")
    capture_time = None
    if captured_at:
        try:
            capture_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Capture time must be an ISO 8601 timestamp.") from error
        if capture_time.tzinfo is None or capture_time.utcoffset() is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Capture time must include a timezone.")
    try:
        document, duplicate = upload_menu_photo(
            db,
            restaurant_identity_id=restaurant_identity_id,
            uploaded_by=user.id,
            image=bytes(image),
            content_type=content_type,
            captured_at=capture_time,
            provider_name=provider_name,
            provider_place_id=provider_place_id,
            extractor=extractor,
        )
        db.commit()
        db.refresh(document)
        return _menu_photo_document_payload(document, duplicate=duplicate)
    except MenuPhotoDecoderUnavailable as error:
        db.rollback()
        logger.error("menu photo decoder is unavailable")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Menu photo decoding is unavailable on this server.") from error
    except (MenuPhotoStorageError, MenuPhotoError, MenuEvidenceError) as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@app.get("/api/v1/menu-photos/{document_id}/proposals")
def list_menu_photo_proposals(
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
    _: User = Depends(current_menu_photo_reviewer),
):
    document = db.get(MenuDocument, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu photo document not found.")
    proposals = db.scalars(select(MenuItemProposal).where(
        MenuItemProposal.document_id == document.id
    ).order_by(MenuItemProposal.id)).all()
    return {"document": _menu_photo_document_payload(document), "proposals": [_menu_photo_proposal_payload(item) for item in proposals]}


@app.post("/api/v1/menu-photos/{document_id}/proposals", status_code=status.HTTP_201_CREATED)
def create_menu_photo_proposal(
    body: MenuPhotoProposalCreateRequest,
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
    user: User = Depends(current_menu_photo_reviewer),
):
    try:
        proposal = create_manual_menu_photo_proposal(
            db,
            document_id,
            reviewer_id=user.id,
            **body.model_dump(),
        )
        db.commit()
        db.refresh(proposal)
        return _menu_photo_proposal_payload(proposal)
    except (MenuPhotoError, MenuEvidenceError) as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@app.patch("/api/v1/menu-photos/{document_id}/proposals/{proposal_id}")
def update_menu_photo_proposal(
    body: MenuPhotoProposalReviewRequest,
    document_id: str = PathParam(min_length=1, max_length=36),
    proposal_id: int = PathParam(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(current_menu_photo_reviewer),
):
    proposal = db.get(MenuItemProposal, proposal_id)
    if proposal is None or proposal.document_id != document_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu item proposal not found.")
    try:
        review_menu_photo_proposal(db, proposal, reviewer_id=user.id, changes=body.model_dump(exclude_unset=True))
        db.commit()
        db.refresh(proposal)
        return _menu_photo_proposal_payload(proposal)
    except (MenuPhotoError, MenuEvidenceError) as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@app.post("/api/v1/menu-photos/{document_id}/commit")
def commit_menu_photo_document(
    document_id: str = PathParam(min_length=1, max_length=36),
    db: Session = Depends(get_db),
    _: User = Depends(current_menu_photo_reviewer),
):
    try:
        committed = commit_menu_photo_proposals(db, document_id)
        db.commit()
        return {"document_id": document_id, "committed_count": len(committed), "evidence_ids": [item.id for item in committed]}
    except MenuPhotoError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except MenuEvidenceError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@app.post("/api/v1/food/feedback")
def food_feedback(body: FoodFeedbackRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    dish = db.get(Dish, body.dish_id) if body.dish_id else None
    evidence = db.get(MenuItemEvidence, body.evidence_id) if body.evidence_id else None
    if body.dish_id and dish is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dish not found.")
    if body.evidence_id and evidence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu evidence not found.")
    if evidence is not None:
        document = db.get(MenuDocument, evidence.menu_document_id)
        source = db.scalar(
            select(MenuSource)
            .join(MenuDocument, MenuDocument.menu_source_id == MenuSource.id)
            .where(MenuDocument.id == evidence.menu_document_id)
        )
        if (
            source is None
            or document is None
            or source.authorization_status != "approved"
            or evidence.evidence_state not in {"verified", "supported"}
            or _is_stale(document, source, utc_now())
        ):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Feedback requires approved supported menu evidence.")
    rating = body.rating
    if rating is None and body.action in {"like", "dislike"}:
        rating = 5 if body.action == "like" else 1
    kind = "save" if body.action == "save" else "reject" if body.action in {"reject", "wrong_dish", "different"} else body.action if body.action in {"too_expensive", "too_spicy", "not_spicy_enough", "too_oily"} else "tried"
    event = FoodEvent(
        user_id=user.id,
        dish_id=dish.id if dish else None,
        evidence_id=evidence.id if evidence else None,
        kind=kind,
        rating=rating,
        note=body.note,
    )
    db.add(event)
    profile = ensure_profile(db, user.id)
    if rating is not None and dish is not None:
        update_profile_from_rating(profile, dish, rating)
    learned_feature = update_profile_from_feedback(profile, body.action)
    if body.note:
        extracted = capture_memory(body.note)
        if extracted:
            subject, detail = extracted
            db.add(FoodMemory(user_id=user.id, kind="experience", subject=subject, detail=detail, permanence="inferred"))
    db.commit()
    return {
        "recorded": True,
        "action": body.action,
        "dish_id": dish.id if dish else None,
        "evidence_id": evidence.id if evidence else None,
        "profile_updated": rating is not None or learned_feature is not None,
        "learned_feature": learned_feature,
        "learning": "bounded_one_point_adjustment" if learned_feature else "interaction_recorded",
    }


def event_history(db: Session, user_id: int, kind: str | None = None) -> list[FoodEvent]:
    query = select(FoodEvent).options(joinedload(FoodEvent.dish).joinedload(Dish.restaurant)).where(FoodEvent.user_id == user_id)
    if kind:
        query = query.where(FoodEvent.kind == kind)
    return list(db.scalars(query.order_by(FoodEvent.created_at.desc()).limit(100)).unique())


@app.get("/api/v1/food/history")
def food_history(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return [{
        "id": event.id, "action": event.kind, "rating": event.rating, "note": event.note,
        "created_at": event.created_at, "dish_id": event.dish_id, "dish_name": event.dish.name,
    } for event in event_history(db, user.id)]


@app.get("/api/v1/food/saved")
def saved_food(db: Session = Depends(get_db), user: User = Depends(current_user)):
    seen = set()
    result = []
    for event in event_history(db, user.id, "save"):
        if event.dish_id not in seen:
            seen.add(event.dish_id)
            result.append({
                "dish_id": event.dish.id, "name": event.dish.name, "cuisine": event.dish.cuisine,
                "price_inr": event.dish.price_inr, "restaurant": event.dish.restaurant.name,
                "source_label": event.dish.source_label,
            })
    return result


@app.post("/api/v1/food-memory", status_code=status.HTTP_201_CREATED)
def create_food_memory(body: FoodMemoryRequest, db: Session = Depends(get_db), user: User = Depends(current_user)):
    extracted = capture_memory(body.text)
    if not extracted:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not identify a food preference. Try “never recommend paneer” or “avoid spicy food today”.")
    subject, detail = extracted
    permanence = "temporary" if body.permanence == "temporary" else "explicit" if body.permanence == "explicit" or body.text.lower().startswith("never ") else "inferred"
    expires = datetime.now(timezone.utc) + timedelta(days=1) if permanence == "temporary" else None
    memory = save_memory(db, user.id, "preference", subject, detail, permanence, expires)
    db.commit()
    db.refresh(memory)
    return {"id": memory.id, "subject": memory.subject, "detail": memory.detail, "permanence": memory.permanence, "expires_at": memory.expires_at}


@app.get("/api/v1/food-memory")
def list_food_memory(db: Session = Depends(get_db), user: User = Depends(current_user)):
    memories = db.scalars(select(FoodMemory).where(FoodMemory.user_id == user.id).order_by(FoodMemory.created_at.desc()))
    now = datetime.now(timezone.utc)
    return [{
        "id": memory.id, "kind": memory.kind, "subject": memory.subject, "detail": memory.detail,
        "permanence": memory.permanence, "expires_at": memory.expires_at,
        "active": memory.expires_at is None or memory.expires_at.replace(tzinfo=timezone.utc) > now,
    } for memory in memories]


@app.delete("/api/v1/food-memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_food_memory(memory_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    memory = db.scalar(select(FoodMemory).where(FoodMemory.id == memory_id, FoodMemory.user_id == user.id))
    if memory is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Food memory not found.")
    db.delete(memory)
    db.commit()


@app.post("/api/v1/agent/chat")
def agent_chat(
    body: FoodSearchRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(optional_current_user),
):
    memory_result = None
    extracted = capture_memory(body.query) if user else None
    if extracted and user:
        subject, detail = extracted
        permanence = "temporary" if "today" in body.query.lower() else "explicit" if body.query.lower().startswith("never ") else "inferred"
        expires = datetime.now(timezone.utc) + timedelta(days=1) if permanence == "temporary" else None
        memory = save_memory(db, user.id, "conversation", subject, detail, permanence, expires)
        db.commit()
        memory_result = {"subject": subject, "permanence": permanence}
    client_id = request.client.host if request.client else "unknown"

    def understand(query: str) -> dict:
        deterministic_intent = parse_intent(query, body.budget_max)
        hints = None
        provider_error = None
        llm = None
        try:
            llm = configured_llm_provider()
            if llm is not None and rag_query_rate_limit.check(client_id):
                hints = llm.generate_structured(
                    prompt_id="query_intent_v1",
                    user_input=query,
                    schema=QueryIntent,
                    max_output_tokens=256,
                ).model_dump()
        except AIProviderError as error:
            provider_error = type(error).__name__
            logger.info("optional query understanding fell back to deterministic parser: %s", provider_error)
        finally:
            _close_optional_provider(llm)
        return {
            "deterministic": deterministic_intent,
            "llm_hints": hints,
            "ranking_authority": "deterministic_parser_and_ranker",
            "llm_status": "used_for_hints_only" if hints is not None else "deterministic_fallback",
        }

    def retrieve(query: str) -> list[dict]:
        if not rag_query_rate_limit.check(client_id):
            return []
        embedder = None
        try:
            try:
                embedder = configured_embedding_provider()
                matches = search_rag_context(
                    db, query, embedding_provider=embedder,
                    budget_max=Decimal(body.budget_max) if body.budget_max is not None else None,
                    limit=min(body.limit, 5),
                )
            except AIProviderError:
                matches = search_rag_context(
                    db, query, embedding_provider=HashEmbeddingProvider(),
                    budget_max=Decimal(body.budget_max) if body.budget_max is not None else None,
                    limit=min(body.limit, 5),
                )
            return [item.__dict__ for item in matches]
        except RagError:
            return []
        finally:
            _close_optional_provider(embedder)

    def rank(state: AgentState) -> dict:
        state.constraints = {
            "budget_max": body.budget_max,
            "hard_constraints_from_deterministic_parser": state.intent["deterministic"].get("hard_constraints", []),
        }
        return search_results(db, user, body)

    def explain(state: AgentState) -> dict:
        from .rag import RetrievedEvidence

        evidence = [RetrievedEvidence(**item) for item in state.retrieved_context]
        return answer_from_evidence(evidence)

    state = run_bounded_food_flow(
        AgentState(user_id=user.id if user else None, query=body.query),
        understand=understand,
        retrieve=retrieve,
        rank=rank,
        explain=explain,
    )
    result = state.ranking
    result["memory_recorded"] = memory_result
    result["grounded_menu_evidence"] = state.explanation
    result["query_understanding"] = state.intent
    result["agent"] = state.final_response
    result["message"] = (
        f"I noted that preference and ranked {len(result['recommendations'])} demo options."
        if memory_result else f"I parsed your request and ranked {len(result['recommendations'])} demo options."
    )
    return result


@app.post("/api/v1/reviews/analyze")
def analyze_reviews(body: ReviewAnalysisRequest, _: User = Depends(current_user)):
    return analyze_review_excerpts(body.excerpts)


@app.post("/api/v1/restaurants/nearby")
def search_nearby_restaurants(body: LocationSearchRequest, request: Request):
    if body.city:
        return _lookup_places(body, request, nearby=False)
    return _lookup_places(body, request, nearby=True)
