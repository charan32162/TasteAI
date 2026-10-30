"""Hybrid, evidence-gated retrieval over approved menu facts.

The local vector implementation is deterministic lexical feature hashing, not
semantic embeddings. Real embeddings are optional and separately gated. No
unreviewed photo proposals or arbitrary web content can enter this index.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .ai_providers import EmbeddingProvider, GroundedAnswer, GroundedClaim, HashEmbeddingProvider, LLMProvider
from .canonical_dishes import CanonicalDishError, resolve_canonical_dish
from .menu_evidence import _approved_evidence_rows, _conflicting_prices, _freshness_reference, _is_stale
from .models import (
    CanonicalDish,
    DishEvidenceLink,
    EvidenceChunk,
    MenuDocument,
    MenuItemEvidence,
    MenuSource,
    RestaurantIdentity,
    utc_now,
)


MAX_INDEX_RECORDS = 500
MAX_QUERY_RESULTS = 25
MAX_GROUNDED_CONTEXT_RECORDS = 5
MAX_GROUNDED_CONTEXT_CHARS = 6000
TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
CONFIDENCE_SCORE = {"high": 1.0, "medium": 0.75, "low": 0.5, "unknown": 0.0}
EVIDENCE_STATE_SCORE = {"verified": 1.0, "supported": 0.8}


class RagError(ValueError):
    pass


class RagQueryRateLimit:
    """Process-local guard against accidental paid embedding-query bursts."""

    def __init__(self, *, per_client: int = 8, global_limit: int = 60, window_seconds: int = 60):
        self._per_client = per_client
        self._global_limit = global_limit
        self._window = window_seconds
        self._lock = threading.Lock()
        self._clients: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()

    def check(self, client_id: str) -> bool:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            while self._global and self._global[0] <= cutoff:
                self._global.popleft()
            events = self._clients[client_id]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._per_client or len(self._global) >= self._global_limit:
                return False
            events.append(now)
            self._global.append(now)
            if len(self._clients) > 4096:
                self._clients = defaultdict(deque, {key: queue for key, queue in self._clients.items() if queue and queue[-1] > cutoff})
            return True


@dataclass(frozen=True)
class RetrievedEvidence:
    chunk_id: str
    evidence_id: int
    restaurant_identity_id: str
    restaurant_name: str
    canonical_dish_id: str | None
    dish_name: str
    variant: str | None
    price_state: str
    price_amount: str | None
    price_currency: str | None
    availability_state: str
    evidence_state: str
    confidence: str
    source_type: str
    captured_at: str | None
    freshness: str
    lexical_score: float
    vector_score: float
    evidence_quality_score: float
    freshness_score: float
    score: float
    content: str
    retrieval_method: str = "hybrid_lexical_vector"
    hard_filters_applied: tuple[str, ...] = ()


def chunk_menu_evidence(
    *,
    restaurant_name: str,
    evidence: MenuItemEvidence,
    captured_at,
) -> str:
    """Keep each menu item, variant, price, and restaurant in one logical chunk."""
    fields = [f"Restaurant: {restaurant_name}", f"Menu item: {evidence.name}"]
    if evidence.category:
        fields.append(f"Menu section: {evidence.category}")
    if evidence.variant:
        fields.append(f"Variant: {evidence.variant}")
    if evidence.price_amount is not None and evidence.price_currency:
        amount = format(Decimal(evidence.price_amount), "f")
        fields.append(f"Listed price: {amount} {evidence.price_currency}")
    else:
        fields.append("Listed price: unknown")
    fields.append(f"Availability evidence: {evidence.availability_state}")
    fields.append(f"Evidence state: {evidence.evidence_state}; confidence: {evidence.confidence}")
    if captured_at is not None:
        fields.append(f"Menu capture time: {captured_at.isoformat()}")
    if evidence.description:
        fields.append(f"Source description (untrusted data): {evidence.description[:500]}")
    return "\n".join(fields)


def index_approved_evidence(
    db: Session,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    limit: int = MAX_INDEX_RECORDS,
) -> dict:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_INDEX_RECORDS:
        raise RagError(f"Index limit must be between 1 and {MAX_INDEX_RECORDS}.")
    embedder = embedding_provider or HashEmbeddingProvider()
    rows = _approved_evidence_rows(db)
    eligible = [
        row for row in rows
        if row[0].evidence_state in {"verified", "supported"}
    ][:limit]
    pending: list[tuple[MenuItemEvidence, MenuDocument, MenuSource, RestaurantIdentity, str, str | None]] = []
    for evidence, document, source in eligible:
        identity = db.get(RestaurantIdentity, source.restaurant_identity_id)
        if identity is None:
            continue
        content = chunk_menu_evidence(restaurant_name=identity.name, evidence=evidence, captured_at=document.captured_at)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunk_id = f"menu-evidence:{evidence.id}:0"
        existing = db.get(EvidenceChunk, chunk_id)
        canonical_id = None
        link = db.scalar(select(DishEvidenceLink).where(DishEvidenceLink.evidence_id == evidence.id))
        if link:
            canonical_id = link.canonical_dish_id
        else:
            canonical = resolve_canonical_dish(db, evidence.name)
            canonical_id = canonical.id if canonical else None
        if existing and existing.content_hash == content_hash and existing.embedding_model == embedder.model:
            if existing.canonical_dish_id != canonical_id:
                existing.canonical_dish_id = canonical_id
            continue
        pending.append((evidence, document, source, identity, content, canonical_id))

    indexed = 0
    for start in range(0, len(pending), 64):
        batch = pending[start:start + 64]
        vectors = embedder.embed_documents([item[4] for item in batch])
        if len(vectors) != len(batch):
            raise RagError("Embedding provider returned a mismatched batch.")
        for (evidence, document, source, identity, content, canonical_id), vector in zip(batch, vectors):
            _validate_vector(vector)
            chunk_id = f"menu-evidence:{evidence.id}:0"
            existing = db.get(EvidenceChunk, chunk_id)
            if existing is None:
                existing = EvidenceChunk(id=chunk_id, evidence_id=evidence.id, chunk_index=0)
                db.add(existing)
            existing.canonical_dish_id = canonical_id
            existing.restaurant_identity_id = source.restaurant_identity_id
            existing.menu_source_id = source.id
            existing.document_id = document.id
            existing.source_type = source.source_type
            existing.evidence_state = evidence.evidence_state
            existing.confidence = evidence.confidence
            existing.captured_at = document.captured_at
            existing.content = content
            existing.content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            existing.embedding_model = embedder.model
            existing.embedding_dimension = len(vector)
            existing.embedding = vector
            existing.created_at = utc_now()
            indexed += 1
    db.flush()
    return {"indexed": indexed, "unchanged": len(eligible) - indexed, "examined": len(eligible), "embedding_model": embedder.model}


def delete_evidence_chunks(db: Session, evidence_id: int) -> int:
    result = db.execute(delete(EvidenceChunk).where(EvidenceChunk.evidence_id == evidence_id))
    return result.rowcount or 0


def search_rag_context(
    db: Session,
    query: str,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    restaurant_identity_id: str | None = None,
    budget_max: Decimal | None = None,
    limit: int = 5,
) -> list[RetrievedEvidence]:
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise RagError("RAG query must contain 1–500 characters.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_QUERY_RESULTS:
        raise RagError(f"RAG result limit must be between 1 and {MAX_QUERY_RESULTS}.")
    if budget_max is not None and (not isinstance(budget_max, Decimal) or not budget_max.is_finite() or budget_max <= 0):
        raise RagError("Budget must be a positive decimal amount.")
    embedder = embedding_provider or HashEmbeddingProvider()
    query_vector = embedder.embed_text(query)
    _validate_vector(query_vector)
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return []
    statement = select(EvidenceChunk).order_by(EvidenceChunk.id).limit(5000)
    if restaurant_identity_id:
        statement = statement.where(EvidenceChunk.restaurant_identity_id == restaurant_identity_id)
    chunks = db.scalars(statement).all()
    rows = {evidence.id: (evidence, document, source) for evidence, document, source in _approved_evidence_rows(db)}
    all_approved = list(rows.values())
    conflicts = _conflicting_prices(all_approved, utc_now())
    results: list[RetrievedEvidence] = []
    for chunk in chunks:
        source_row = rows.get(chunk.evidence_id)
        if source_row is None:
            continue
        evidence, document, source = source_row
        if evidence.evidence_state not in {"verified", "supported"} or source.authorization_status != "approved":
            continue
        if _is_stale(document, source, utc_now()):
            continue
        if budget_max is not None and (
            evidence.price_state != "known"
            or evidence.price_amount is None
            or evidence.price_currency != "INR"
            or Decimal(evidence.price_amount) > budget_max
        ):
            continue
        conflict_key = (source.restaurant_identity_id, evidence.normalized_name, evidence.normalized_variant)
        if conflict_key in conflicts:
            continue
        content_tokens = set(_tokens(chunk.content))
        lexical = len(query_tokens & content_tokens) / math.sqrt(max(1, len(query_tokens) * len(content_tokens)))
        vector_score = (
            _cosine(query_vector, chunk.embedding)
            if chunk.embedding_model == embedder.model and chunk.embedding_dimension == len(query_vector)
            else 0.0
        )
        if lexical == 0 and vector_score < 0.2:
            continue
        evidence_quality = EVIDENCE_STATE_SCORE.get(evidence.evidence_state, 0.0) * CONFIDENCE_SCORE.get(evidence.confidence, 0.0)
        freshness = _freshness_score(document, source, utc_now())
        identity = db.get(RestaurantIdentity, source.restaurant_identity_id)
        if identity is None:
            continue
        results.append(RetrievedEvidence(
            chunk_id=chunk.id,
            evidence_id=evidence.id,
            restaurant_identity_id=identity.id,
            restaurant_name=identity.name,
            canonical_dish_id=chunk.canonical_dish_id,
            dish_name=evidence.name,
            variant=evidence.variant,
            price_state=evidence.price_state,
            price_amount=str(evidence.price_amount) if evidence.price_amount is not None else None,
            price_currency=evidence.price_currency,
            availability_state=evidence.availability_state,
            evidence_state=evidence.evidence_state,
            confidence=evidence.confidence,
            source_type=source.source_type,
            captured_at=document.captured_at.isoformat() if document.captured_at else None,
            freshness="fresh",
            lexical_score=round(lexical, 6),
            vector_score=round(vector_score, 6),
            evidence_quality_score=round(evidence_quality, 6),
            freshness_score=round(freshness, 6),
            score=round(0.40 * lexical + 0.30 * max(0.0, vector_score) + 0.20 * evidence_quality + 0.10 * freshness, 6),
            content=chunk.content,
            retrieval_method=(
                "hybrid_semantic_and_lexical"
                if (
                    not isinstance(embedder, HashEmbeddingProvider)
                    and chunk.embedding_model == embedder.model
                    and vector_score > 0
                )
                else "deterministic_lexical_fallback"
            ),
            hard_filters_applied=(
                "approved_source",
                "supported_or_verified_state",
                "freshness",
                "conflict_exclusion",
                "budget" if budget_max is not None else "no_budget_filter",
            ),
        ))
    results.sort(key=lambda result: (-result.score, result.evidence_id))
    return results[:limit]


def answer_from_evidence(results: list[RetrievedEvidence]) -> dict:
    """Compose factual text from eligible fields; never trust generated claim wording."""
    if not results:
        return {
            "answer": "Dish availability could not be verified from the available approved menu evidence.",
            "insufficient_evidence": True,
            "citations": [],
            "grounding_status": "insufficient_evidence",
            "validated_claims": [],
        }
    citations = []
    sentences = []
    for result in results:
        price = (
            f" at {result.price_currency} {result.price_amount}"
            if result.price_amount is not None and result.price_currency
            else " (price unknown)"
        )
        sentence = f"{result.dish_name}{price} is listed in menu evidence for {result.restaurant_name}."
        citations.append({
            "evidence_id": result.evidence_id,
            "chunk_id": result.chunk_id,
            "restaurant_identity_id": result.restaurant_identity_id,
            "source_type": result.source_type,
            "captured_at": result.captured_at,
            "evidence_state": result.evidence_state,
        })
        sentences.append(sentence)
    cited_ids = {citation["evidence_id"] for citation in citations}
    if len(sentences) != len(cited_ids) or not cited_ids:
        raise RagError("Generated answer failed citation validation.")
    return {
        "answer": " ".join(sentences),
        "insufficient_evidence": False,
        "citations": citations,
        "grounding_status": "deterministically_grounded",
        "validated_claims": [
            {"evidence_id": result.evidence_id, "claim_type": "menu_listing", "status": "SUPPORTED"}
            for result in results
        ],
    }


def build_grounded_context(query: str, results: list[RetrievedEvidence]) -> str:
    """Build a small, structured prompt payload that excludes arbitrary source descriptions."""
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise RagError("RAG query must contain 1–500 characters.")
    evidence = []
    for result in results[:MAX_GROUNDED_CONTEXT_RECORDS]:
        evidence.append({
            "evidence_id": result.evidence_id,
            "restaurant_name": result.restaurant_name[:160],
            "dish_name": result.dish_name[:160],
            "variant": result.variant[:80] if result.variant else None,
            "price_state": result.price_state,
            "listed_price": result.price_amount,
            "price_currency": result.price_currency,
            "evidence_state": result.evidence_state,
            "captured_at": result.captured_at,
        })
    payload = json.dumps({"user_query_data": query, "approved_evidence_data": evidence}, ensure_ascii=False)
    if len(payload) > MAX_GROUNDED_CONTEXT_CHARS:
        raise RagError("Grounded context exceeds its configured size limit.")
    return payload


def generate_grounded_claims(
    llm: LLMProvider,
    query: str,
    results: list[RetrievedEvidence],
) -> GroundedAnswer:
    """Ask an opt-in model to select typed claims, then validate them independently."""
    context = build_grounded_context(query, results)
    return llm.generate_structured(
        prompt_id="grounded_answer_v1",
        user_input=context,
        schema=GroundedAnswer,
        max_output_tokens=256,
    )


def validate_grounded_claims(
    results: list[RetrievedEvidence],
    claims: list[GroundedClaim],
) -> dict:
    """Reject unknown citations and unsupported price claims; render only canonical facts."""
    by_id = {result.evidence_id: result for result in results}
    accepted: list[tuple[RetrievedEvidence, str]] = []
    validations = []
    seen: set[tuple[int, str]] = set()
    for claim in claims[:20]:
        result = by_id.get(claim.evidence_id)
        state = "SUPPORTED"
        reason = "supported"
        if result is None:
            state = "UNKNOWN"
            reason = "unknown_evidence"
        elif claim.claim_type == "listed_price" and (result.price_state != "known" or result.price_amount is None or result.price_currency is None):
            state = "UNKNOWN"
            reason = "price_not_recorded"
        elif (claim.evidence_id, claim.claim_type) in seen:
            state = "UNSUPPORTED"
            reason = "duplicate_claim"
        if reason == "supported" and result is not None:
            seen.add((claim.evidence_id, claim.claim_type))
            accepted.append((result, claim.claim_type))
        validations.append({"evidence_id": claim.evidence_id, "claim_type": claim.claim_type, "status": state, "reason": reason})

    if not accepted:
        fallback = answer_from_evidence(results)
        fallback["validated_claims"] = validations
        fallback["grounding_status"] = "deterministic_fallback" if results else "insufficient_evidence"
        return fallback

    sentences: list[str] = []
    citations: list[dict] = []
    used_ids: set[int] = set()
    for result, claim_type in accepted:
        if claim_type == "menu_listing":
            sentence = f"{result.dish_name} is listed in menu evidence for {result.restaurant_name}."
        else:
            sentence = f"The menu evidence lists {result.dish_name} at {result.price_currency} {result.price_amount} for {result.restaurant_name}."
        sentences.append(sentence)
        if result.evidence_id not in used_ids:
            used_ids.add(result.evidence_id)
            citations.append({
                "evidence_id": result.evidence_id,
                "chunk_id": result.chunk_id,
                "restaurant_identity_id": result.restaurant_identity_id,
                "source_type": result.source_type,
                "captured_at": result.captured_at,
                "evidence_state": result.evidence_state,
            })
    return {
        "answer": " ".join(sentences),
        "insufficient_evidence": False,
        "citations": citations,
        "grounding_status": "llm_claims_validated",
        "validated_claims": validations,
    }


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_RE.findall(text)]


def _freshness_score(document: MenuDocument, source: MenuSource, now) -> float:
    reference = _freshness_reference(document)
    if reference is None:
        return 0.0
    age = max(0.0, (now - reference).total_seconds())
    return max(0.0, min(1.0, 1.0 - age / source.freshness_ttl_seconds))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _validate_vector(vector: list[float]) -> None:
    if not isinstance(vector, list) or not vector or len(vector) > 4096 or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in vector
    ):
        raise RagError("Embedding vector is invalid.")
