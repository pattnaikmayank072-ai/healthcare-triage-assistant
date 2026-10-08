"""Clinical Knowledge Agent: public-guideline-only, local hybrid retrieval.

No documents are bundled by this phase. Callers must explicitly ingest verified public
material from the authorities named in the project plan. Embedding and reranker callbacks
are expected to run locally; no provider/API client is implemented here.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agents.safety import SafetyAgent
from schemas import KnowledgePassage, KnowledgeSearchResult

APPROVED_SOURCES = ("NHM", "MoHFW", "IMNCI", "WHO ETAT", "ICMR", "NCDC")


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in re.findall(r"[^\W_]+", text, flags=re.UNICODE) if len(token) > 1]


def _cosine(left: Sequence[float] | None, right: Sequence[float] | None) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(x) ** 2 for x in left))
    right_norm = math.sqrt(sum(float(x) ** 2 for x in right))
    if not left_norm or not right_norm:
        return None
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


@dataclass(frozen=True)
class _IndexedPassage:
    public_passage: KnowledgePassage
    terms: frozenset[str]
    embedding: tuple[float, ...] | None


class ClinicalKnowledgeAgent:
    """Index and retrieve only explicitly-public passages from the plan's authorities."""

    def __init__(self,
                 local_embedder: Callable[[str], Sequence[float]] | None = None,
                 local_reranker: Callable[[str, list[KnowledgePassage]], list[KnowledgePassage]] | None = None,
                 safety_agent: SafetyAgent | None = None,
                 chunk_chars: int = 1200,
                 overlap_chars: int = 160):
        if chunk_chars < 200 or overlap_chars < 0 or overlap_chars >= chunk_chars:
            raise ValueError("invalid chunk size or overlap")
        self.local_embedder = local_embedder
        self.local_reranker = local_reranker
        self.safety = safety_agent or SafetyAgent()
        self.chunk_chars = chunk_chars
        self.overlap_chars = overlap_chars
        self._index: list[_IndexedPassage] = []
        self._indexed_documents: set[str] = set()

    @property
    def passage_count(self) -> int:
        return len(self._index)

    def _embed(self, text: str) -> tuple[float, ...] | None:
        if self.local_embedder is None:
            return None
        try:
            vector = tuple(float(value) for value in self.local_embedder(text))
            if not vector or not all(math.isfinite(x) for x in vector):
                return None
            return vector
        except Exception:
            # Failed local embeddings degrade to deterministic keyword search.
            return None

    def _chunks(self, text: str) -> list[str]:
        clean = re.sub(r"\s+", " ", text).strip()
        chunks: list[str] = []
        start = 0
        while start < len(clean):
            end = min(start + self.chunk_chars, len(clean))
            if end < len(clean):
                boundary = clean.rfind(" ", start + self.chunk_chars // 2, end)
                if boundary > start:
                    end = boundary
            chunk = clean[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(clean):
                break
            start = max(start + 1, end - self.overlap_chars)
        return chunks

    def ingest_public_document(self, *, source: str, section: str, text: str,
                               citation_url: str = "", is_public: bool = False) -> int:
        """Add a verified public-source document. Private/synthetic case data is rejected."""
        canonical = next((name for name in APPROVED_SOURCES if name.casefold() == source.strip().casefold()), None)
        if is_public is not True or canonical is None:
            raise ValueError("only explicitly public documents from plan-approved sources may be indexed")
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000_000:
            raise ValueError("public guideline text is empty or exceeds the ingestion limit")
        if citation_url and not citation_url.lower().startswith("https://"):
            raise ValueError("citation_url must use HTTPS")
        sentinel = self.safety.scan_input(text)
        if not sentinel.safe:
            raise ValueError("document quarantined by the Injection Sentinel")
        redacted_check = self.safety.redact(text, pass_number=1)
        redacted_check = self.safety.redact(redacted_check, pass_number=2)
        if redacted_check != text:
            raise ValueError("document contains possible personal identifiers; sanitize before indexing")

        document_id = hashlib.sha256(
            f"{canonical}\0{section}\0{citation_url}\0{text}".encode("utf-8")
        ).hexdigest()[:24]
        if document_id in self._indexed_documents:
            return 0
        chunks = self._chunks(text)
        for ordinal, chunk in enumerate(chunks):
            passage_id = f"{document_id}-{ordinal:04d}"
            passage = KnowledgePassage(
                source=canonical, section=section.strip()[:200], excerpt=chunk,
                citation_url=citation_url, score=0.0, document_id=document_id,
                passage_id=passage_id,
            )
            self._index.append(_IndexedPassage(passage, frozenset(_tokens(chunk)), self._embed(chunk)))
        self._indexed_documents.add(document_id)
        return len(chunks)

    def search(self, query: str, top_k: int = 3) -> KnowledgeSearchResult:
        if top_k not in (3, 4, 5):
            raise ValueError("top_k must be between 3 and 5")
        clean_query = query.strip() if isinstance(query, str) else ""
        # Do not echo potentially sensitive symptom text in the retrieval response.
        response_query = "[redacted query]"
        if clean_query and not self.safety.scan_input(clean_query).safe:
            raise ValueError("knowledge query quarantined by the Injection Sentinel")
        if not clean_query or not self._index:
            return KnowledgeSearchResult(status="no_guideline_match", query=response_query,
                                         passages=[], message="no guideline match")

        query_terms = frozenset(_tokens(clean_query))
        query_embedding = self._embed(clean_query)
        ranked: list[tuple[float, KnowledgePassage]] = []
        for item in self._index:
            overlap = len(query_terms & item.terms) / max(1, len(query_terms))
            semantic = _cosine(query_embedding, item.embedding) if query_embedding and item.embedding else None
            semantic_score = max(0.0, semantic) if semantic is not None else None
            if semantic_score is None:
                score = overlap
            elif overlap > 0:
                score = 0.6 * overlap + 0.4 * semantic_score
            else:
                score = 0.4 * semantic_score
            if overlap > 0 or (semantic_score is not None and semantic_score >= 0.5):
                ranked.append((max(0.0, min(1.0, score)), item.public_passage))
        ranked.sort(key=lambda item: (-item[0], item[1].source, item[1].section, item[1].passage_id))
        candidates = [passage.model_copy(update={"score": score}) for score, passage in ranked[:max(top_k * 3, 15)]]

        if candidates and self.local_reranker is not None:
            try:
                indexed_by_id = {item.passage_id: item for item in candidates}
                rerank_input = [item.model_copy(deep=True) for item in candidates]
                reordered = self.local_reranker(clean_query, rerank_input)
                if isinstance(reordered, list) and reordered and all(
                    isinstance(item, KnowledgePassage) and item.passage_id in indexed_by_id
                    for item in reordered
                ):
                    # Reuse indexed passages; reranker output may not alter evidence or citations.
                    candidates = list({item.passage_id: indexed_by_id[item.passage_id]
                                       for item in reordered}.values())
            except Exception:
                pass

        selected = candidates[:top_k]
        if not selected:
            return KnowledgeSearchResult(status="no_guideline_match", query=response_query,
                                         passages=[], message="no guideline match")
        return KnowledgeSearchResult(status="matched", query=response_query,
                                     passages=selected, message="")
