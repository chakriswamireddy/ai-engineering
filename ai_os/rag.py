"""Day 6 §4–§6 — the RAG pipeline: persisted store, retrieval, context, chat.

```
question ──▶ embed query ──▶ retrieve top-k ──▶ build context ──▶ chat(context + question) ──▶ answer
                            (store)            (budget floor)
```

Everything here is orchestration over Days 3–5 pieces:
retrieval is Day 5's brute-force scan (see `ai_os.search`), generation is
Day 4's gateway (retries, fallback, ledger included). The new surface:
chunks carry metadata (source/heading/position = the citation layer), the
store is file-backed and *dimension-locked*, and the context budget is what
stops retrieval from overflowing the window.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from ai_os import observability as obs
from ai_os.search import dot, normalize

STORE_VERSION = 1
NOT_COVERED = "not covered by the documentation"

SYSTEM_PROMPT = (
    "Answer ONLY from the context below, which is {used} chars of Saleor documentation.\n"
    "If the context cannot answer the question, say: '{not_covered}'.\n"
    "Cite the source heading for each claim.\n\n{context}"
)

# Day 23 §6 — the RAG prompt's provenance. The semantic name says which intent
# the template represents; the hash says which exact bytes ran. A template edit
# changes the hash without a human remembering to bump a version string.
RAG_PROMPT_VERSION = "v1"
RAG_PROMPT_REF = obs.PromptRef.of("rag-answer", RAG_PROMPT_VERSION, SYSTEM_PROMPT)


class RagError(Exception):
    pass


class StoreVersionError(RagError):
    pass


class DimensionMismatch(RagError):
    pass


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    source: str
    heading: str
    position: int
    text: str
    vector: list[float]
    # Day 10 — code metadata. Prose chunks leave these at their defaults, so
    # the Day-6 shape is unchanged; code chunks carry the symbol facts the
    # retriever and the citation layer spend without a code-specific dialect.
    symbol: str | None = None
    kind: str | None = None
    signature: str | None = None
    docstring: str | None = None
    imports: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


def citation(chunk: Chunk, score: float | None = None) -> dict[str, Any]:
    out = {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "source": chunk.source,
        "heading": chunk.heading,
        "position": chunk.position,
        "score": None if score is None else round(float(score), 4),
    }
    if chunk.symbol:
        # A code citation is `module#Symbol:line` — the line makes a retrieval
        # hit a navigation target. `doc_id` stays `module#Symbol` (no line) so
        # `cited ⊆ retrieved` remains plain set membership (Day 7 MECH).
        out.update({
            "symbol": chunk.symbol,
            "kind": chunk.kind,
            "signature": chunk.signature,
            "line": chunk.position,
        })
    return out


class RagStore:
    """The Day 5 `Index`, now file-backed, metadata-carrying, dimension-locked.

    - vectors are normalized at write time (the Day 5 zero-vector bug has no
      second chance to come back);
    - `embedding_model` + `dimension` are locked on first add / on load, so a
      query embedded by a *different* model is a hard error — two models'
      vectors live in different spaces, and silently comparing them is a
      wrong-answer bug;
    - persistence means boot = load (ms), rebuild = re-run ingest+embed.
    """

    def __init__(self, chunks: list[Chunk] | None = None,
                 embedding_model: str = "", dimension: int = 0):
        self.chunks = list(chunks or [])
        self.embedding_model = embedding_model
        self.dimension = dimension
        for c in self.chunks:
            self._enforce(c.vector)
        if not self.dimension and self.chunks:
            self.dimension = len(self.chunks[0].vector)

    # --------------------------------------------------------------- writes

    def _enforce(self, vector: list[float]) -> None:
        if self.dimension and len(vector) != self.dimension:
            raise DimensionMismatch(
                f"chunk vector dim {len(vector)} != locked store dim {self.dimension}")
        if not self.dimension:
            self.dimension = len(vector)

    def add(self, chunk: Chunk) -> None:
        self._enforce(chunk.vector)
        chunk.vector = normalize(chunk.vector)
        self.chunks.append(chunk)

    # -------------------------------------------------------------- reads

    def avg_chunk_len(self) -> float:
        return (sum(len(c.text) for c in self.chunks) / len(self.chunks)
                if self.chunks else 0.0)

    def __len__(self) -> int:
        return len(self.chunks)

    def retrieve(self, query_vector: list[float], k: int = 5
                 ) -> list[tuple[float, Chunk]]:
        """Brute-force top-k (Day 5), returning metadata-carrying chunks."""
        q = normalize(query_vector)
        if self.dimension and len(q) != self.dimension:
            raise DimensionMismatch(
                f"query dim {len(q)} != locked store dim {self.dimension} — "
                "the query was embedded by a different model, refusing to "
                "compare vectors from different spaces")
        if not self.chunks or k <= 0:
            return []
        ranked = sorted((dot(q, c.vector), c.chunk_id, c) for c in self.chunks)
        return [(score, c) for score, _, c in ranked[-k:]][::-1]

    # ------------------------------------------------------------ persist

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {
            "version": STORE_VERSION,
            "embedding_model": self.embedding_model,
            "dimension": self.dimension,
            "chunks": [
                {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "source": c.source,
                 "heading": c.heading, "position": c.position,
                 "text": c.text, "vector": c.vector,
                 "symbol": c.symbol, "kind": c.kind, "signature": c.signature,
                 "docstring": c.docstring, "imports": c.imports,
                 "dependencies": c.dependencies}
                for c in self.chunks
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    @classmethod
    def load(cls, path: str) -> "RagStore":
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("version") != STORE_VERSION:
            raise StoreVersionError(
                f"store version {payload.get('version')} != {STORE_VERSION} — "
                "re-run ingest+embed to rebuild")
        store = cls(embedding_model=payload.get("embedding_model", ""),
                    dimension=int(payload.get("dimension", 0)))
        keys = ("chunk_id", "doc_id", "source", "heading", "position", "text",
                "vector", "symbol", "kind", "signature", "docstring",
                "imports", "dependencies")
        for c in payload.get("chunks", []):
            # Missing code keys (a Day-6/9 doc store) fall through to the
            # dataclass defaults, so one loader reads both corpora.
            chunk = Chunk(**{k: c[k] for k in keys if k in c})
            store.add(chunk)
        return store


def k_from_budget(budget_chars: int, avg_chunk_len: float, n: int) -> int:
    """K is a *budget* decision, not a quality knob.

    `k = min(budget_chars // avg_chunk_len, n)`. When the budget can't fit
    even one chunk, k is 0 → empty retrieval → the honest refusal path.
    """
    if n <= 0 or budget_chars <= 0 or avg_chunk_len <= 0:
        return 0
    return min(int(budget_chars // avg_chunk_len), n)


def build_context(results: list[tuple[float, Chunk]], budget_chars: int = 1200,
                  ) -> tuple[str, int, list[tuple[float, Chunk]]]:
    """Join retrieved chunks in *rank order*, respecting the budget floor.

    Rank priority wins: a later chunk that doesn't fit is dropped, never
    reordered past an earlier one. Returns (context, used_chars, kept).
    """
    parts: list[str] = []
    used = 0
    kept: list[tuple[float, Chunk]] = []
    for score, chunk in results:
        if used + len(chunk.text) > budget_chars:
            continue
        parts.append(chunk.text)
        used += len(chunk.text)
        kept.append((score, chunk))
    return "\n\n".join(parts), used, kept


def run_rag(gateway, store, question: str, *, k: int | None = None,
            budget_chars: int = 1200, max_tokens: int = 2048,
            temperature: float = 0.0) -> dict[str, Any]:
    """The one-function pipeline from day-6.md §6.

    Returns answer + citations + retrieval/generation accounting. When no
    chunk fits the context budget, it refuses *by code* (an honest
    `not covered` answer) instead of asking the model to invent one.

    Day 23 §7 — the run becomes a causal tree: `request → model.embed →
    retrieval (document events) → model.chat → final answer`, with an explicit
    prompt ref on the generation turn. Raw question and document text never
    enter the trace — only ids, counts, ranks, sizes, and hashes. The pipeline
    decisions (`k`, budget, gate, refusal) are exactly where they were.
    """
    q = question.strip()
    if not q:
        raise ValueError("question must not be empty")

    tr = obs.current_trace()
    own = tr is None or not getattr(tr, "recording", False)
    if own:
        tr = obs.start_trace("rag", attributes={
            "route": "/rag", "question_sha256": obs.sha256_text(q),
            "question_chars": len(q)})
    else:
        tr.add_event("request", route="/rag",
                     question_sha256=obs.sha256_text(q), question_chars=len(q))
    try:
        result = _run_rag_traced(gateway, store, q, k=k, budget_chars=budget_chars,
                                 max_tokens=max_tokens, temperature=temperature,
                                 tr=tr)
    except BaseException as exc:
        if own:
            obs.finish_trace(tr, status=obs.status_for_error(exc),
                             error=obs.error_fact(exc))
        raise
    if own:
        obs.finish_trace(tr)
    if getattr(tr, "recording", False):
        result["trace_id"] = tr.trace_id
    return result


def _ev(span, event: str, **attrs: Any) -> None:
    """Emit an event when tracing is on; no-op on the `NullTrace` path (O9)."""
    if span is not None:
        span.add_event(event, **attrs)


def _run_rag_traced(gateway, store, q: str, *, k, budget_chars, max_tokens,
                    temperature, tr) -> dict[str, Any]:
    embeddings = gateway.embed([q])
    qvec = embeddings[0].vector
    if store.dimension and len(qvec) != store.dimension:
        raise DimensionMismatch(
            f"query dim {len(qvec)} != store dim {store.dimension} — "
            "query was embedded by a different model")

    if k is None:
        k = k_from_budget(budget_chars, store.avg_chunk_len(), len(store))
    with tr.span(obs.KIND_RETRIEVAL, "retrieval", attributes={
            "query_sha256": obs.sha256_text(q), "k": k,
            "store": store.embedding_model, "store_dim": store.dimension,
            "budget_chars": budget_chars}) as rspan:
        results = store.retrieve(qvec, k)
        context, used, kept = build_context(results, budget_chars)
        kept_ids = {c.chunk_id for _, c in kept}
        for rank, (score, chunk) in enumerate(results, 1):
            _ev(rspan, "document", doc_id=chunk.doc_id, chunk_id=chunk.chunk_id,
                rank=rank, score=round(float(score), 4), chars=len(chunk.text),
                text_sha256=obs.sha256_text(chunk.text),
                kept=chunk.chunk_id in kept_ids,
                drop_reason=(None if chunk.chunk_id in kept_ids else "budget"))
        if rspan is not None:
            rspan.update(candidates=len(results), context_chars=used,
                         kept=len(kept), dropped=len(results) - len(kept))

    retrieval = {
        "model": embeddings[0].model,
        "input_tokens": embeddings[0].usage.input_tokens,
        "k": k,
        "candidates": len(results),
        "context_chars": used,
        "cost_usd": round(gateway.ledger.cost(embeddings[0].model,
                                             embeddings[0].usage), 8),
    }

    if not context or not kept:
        tr.add_event("gate_refused", reason="empty context after budget",
                     candidates=len(results))
        tr.add_event("final_answer", refused=True, answer_chars=len(NOT_COVERED),
                     answer_sha256=obs.sha256_text(NOT_COVERED),
                     citations=len(results), model=None)
        return {
            "answer": NOT_COVERED,
            "citations": [citation(c, s) for s, c in results],
            "retrieval": retrieval,
            "generation": None,
            "refused": True,
        }

    system = SYSTEM_PROMPT.format(used=used, not_covered=NOT_COVERED, context=context)
    completion = gateway.chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": q}],
        temperature=temperature, max_tokens=max_tokens,
        prompt_ref=RAG_PROMPT_REF)
    tr.add_event("final_answer", refused=False,
                 answer_sha256=obs.sha256_text(completion.text),
                 answer_chars=len(completion.text),
                 citations=len(results), model=completion.model,
                 finish_reason=completion.finish_reason,
                 prompt_version=RAG_PROMPT_REF.version)

    return {
        "answer": completion.text,
        "citations": [citation(c, s) for s, c in results],
        "retrieval": retrieval,
        "generation": {
            "model": completion.model,
            "provider": gateway.last_provider,
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
            "usage": completion.usage.as_dict(),
            "attempts": gateway.last_attempts,
            "latency_ms": gateway.last_latency_ms,
            "cost_usd": round(gateway.ledger.cost(completion.model,
                                                 completion.usage), 8),
        },
        "refused": False,
    }


def store_path_from_env(default: str = "data/store.json") -> str:
    return os.getenv("RAG_STORE_PATH", default).strip() or default


def load_store_from_env(default: str = "data/store.json") -> RagStore:
    return RagStore.load(store_path_from_env(default))


def code_store_path_from_env(default: str = "data/code_store.json") -> str:
    """Day 10 — the second corpus lives in a second file. Same store shape,
    same dimension lock; the doc store is never invalidated."""
    return os.getenv("RAG_CODE_STORE_PATH", default).strip() or default


def load_code_store_from_env(default: str = "data/code_store.json") -> RagStore:
    return RagStore.load(code_store_path_from_env(default))