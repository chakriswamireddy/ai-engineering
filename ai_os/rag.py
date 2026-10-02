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

import hashlib
import json
import os
import time
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
        # (score, chunk_id, ordinal) — the ordinal is a *unique* final
        # tie-break: the Day-10 code store has duplicate chunk_ids
        # (e.g. several `#Meta` chunks per module) and cosine ties are
        # common, so sorted() must never fall through to comparing Chunks.
        ranked = sorted((dot(q, c.vector), c.chunk_id, i, c)
                        for i, c in enumerate(self.chunks))
        return [(score, c) for score, _, _, c in ranked[-k:]][::-1]

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


# One budget per corpus — the same pair tools.py keeps as
# DEFAULT_DOC_BUDGET/DEFAULT_CODE_BUDGET (docs/evaluations/tool-day11.md).
# A code body (median ~1500 chars in the Day-10 store) cannot fit the doc
# budget: `k = 1200 // 1220 = 0` and the code corpus silently refuses.
# `budget_chars=None` everywhere below means "use this corpus's default".
CORPUS_BUDGETS: dict[str, int] = {"docs": 1200, "code": 4000}


def default_budget_chars(corpus_label: str | None) -> int:
    """`None` label (or unknown) → the doc-store budget (Day-6 behavior)."""
    return CORPUS_BUDGETS.get(corpus_label or "docs", 1200)


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
            budget_chars: int | None = None, max_tokens: int = 2048,
            temperature: float = 0.0,
            corpus_label: str | None = None) -> dict[str, Any]:
    """The one-function pipeline from day-6.md §6.

    Returns answer + citations + retrieval/generation accounting. When no
    chunk fits the context budget, it refuses *by code* (an honest
    `not covered` answer) instead of asking the model to invent one.

    Day 23 §7 — the run becomes a causal tree: `request → model.embed →
    retrieval (document events) → model.chat → final answer`, with an explicit
    prompt ref on the generation turn. Raw question and document text never
    enter the trace — only ids, counts, ranks, sizes, and hashes. The pipeline
    decisions (`k`, budget, gate, refusal) are exactly where they were.

    `corpus_label` names the store for the caller's benefit: every citation
    then carries `corpus: <label>` (additive — an unlabeled run keeps the
    Day-6 shape exactly). `budget_chars=None` uses the corpus default
    (`CORPUS_BUDGETS`: docs 1200, code 4000); an explicit int is the
    caller's budget and wins.
    """
    return _run_rag_common(gateway, [(corpus_label, store)], question,
                           k=k, budget_chars=budget_chars,
                           max_tokens=max_tokens, temperature=temperature)


def run_rag_multi(gateway, stores: list[tuple[str, Any]], question: str,
                  *, k: int | None = None, budget_chars: int | None = None,
                  max_tokens: int = 2048,
                  temperature: float = 0.0) -> dict[str, Any]:
    """Auto mode — ask NAMED stores in ONE pipeline (the `/rag` `auto` corpus).

    One embed, per-store retrieval merged into a single score-ordered
    candidate list, ONE generation: the model never routes a corpus (no
    classifier chat, no extra bill — the deterministic twin of
    `agent.route_question`'s "0 chats" virtue), and the answer cites
    whichever store actually held the evidence. Citations carry
    `corpus: <label>`; `retrieval.stores` reports each store's
    k/candidates/budget.

    `k` is per store (k=4 over docs+code → up to 8 candidates). Budget is
    ALSO per store: `budget_chars=None` → `CORPUS_BUDGETS` per label (each
    store's context slice is built under its own budget, so a code body is
    never crowded out of the doc budget); one explicit int applies the
    caller's budget to every store. Stores may not be None — the caller
    filters and discloses skips (`result["skipped"]`).
    """
    named = [(label, store) for label, store in stores if store is not None]
    if not named:
        raise ValueError("run_rag_multi: no stores to query")
    if any(not label for label, _ in named):
        raise ValueError("run_rag_multi: every store needs a label")
    return _run_rag_common(gateway, named, question,
                           k=k, budget_chars=budget_chars,
                           max_tokens=max_tokens, temperature=temperature)


def _run_rag_common(gateway, stores, question: str, *, k, budget_chars,
                    max_tokens, temperature) -> dict[str, Any]:
    q = question.strip()
    if not q:
        raise ValueError("question must not be empty")

    tr = obs.current_trace()
    own = tr is None or not getattr(tr, "recording", False)
    if own:
        tr = obs.start_trace("rag", attributes={
            "route": "/rag", "component": "rag",
            "question_sha256": obs.sha256_text(q),
            "question_chars": len(q)})
    else:
        tr.add_event("request", route="/rag",
                     question_sha256=obs.sha256_text(q), question_chars=len(q))
    try:
        result = _run_rag_traced(gateway, stores, q, k=k,
                                 budget_chars=budget_chars,
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


def _run_rag_traced(gateway, stores, q: str, *, k, budget_chars, max_tokens,
                    temperature, tr) -> dict[str, Any]:
    """Retrieval over 1..N named stores → per-store budgeted context → one chat.

    `stores` is `[(label | None, store), ...]` — single-store callers keep
    the Day-6 accounting exactly; labeled runs add `corpus` per citation and
    a `retrieval.stores` breakdown.
    """
    embeddings = gateway.embed([q])
    qvec = embeddings[0].vector

    # (label, store, k_i, budget_i) — k AND budget are per store: the caller
    # may pass one int for all (explicit override) or None → corpus default.
    planned: list[tuple[str | None, Any, int, int]] = []
    for label, store in stores:
        if store.dimension and len(qvec) != store.dimension:
            raise DimensionMismatch(
                f"query dim {len(qvec)} != store dim {store.dimension} — "
                "query was embedded by a different model"
                + (f" [{label} store]" if label else ""))
        budget_i = (budget_chars if budget_chars is not None
                    else default_budget_chars(label))
        k_i = (k if k is not None else
               k_from_budget(budget_i, store.avg_chunk_len(), len(store)))
        planned.append((label, store, k_i, budget_i))

    labels = [label for label, _, _, _ in planned if label]
    models = list(dict.fromkeys(st.embedding_model for _, st, _, _ in planned))
    dims = list(dict.fromkeys(st.dimension for _, st, _, _ in planned))
    total_budget = sum(b for _, _, _, b in planned)
    span_k = k if k is not None else max((k_i for _, _, k_i, _ in planned),
                                         default=0)
    with tr.span(obs.KIND_RETRIEVAL, "retrieval", component="rag",
                 span_kind=obs.SPAN_INTERNAL, attributes={
                     "query_sha256": obs.sha256_text(q), "k": span_k,
                     "store": ",".join(models), "store_dim": dims[0] if dims else 0,
                     "corpora": ",".join(labels) if labels else None,
                     "store_count": len(planned),
                     "store_version": STORE_VERSION, "store_kind": "local_file",
                     "budget_chars": total_budget}) as rspan:
        merged: list[tuple[float, Any, str | None]] = []
        per_store: list[tuple[str | None, int, list[tuple[float, Any]]]] = []
        store_stats: dict[str, Any] = {}
        for label, store, k_i, budget_i in planned:
            results = store.retrieve(qvec, k_i)
            per_store.append((label, budget_i, results))
            if label:
                store_stats[label] = {"k": k_i, "candidates": len(results),
                                      "budget_chars": budget_i}
            merged.extend((score, chunk, label) for score, chunk in results)
        # Tie-break on chunk_id: equal scores must not fall through to
        # comparing Chunk objects (TypeError — ties are common across stores).
        merged.sort(key=lambda t: (-t[0], t[1].chunk_id, t[2] or ""))
        if len(per_store) == 1:
            # Day-6 path unchanged: one store, one budget, rank order.
            _, budget_0, results_0 = per_store[0]
            context, used, kept = build_context(results_0, budget_0)
        else:
            # Auto: each store fills its OWN slice (docs 1200 / code 4000 by
            # default) — a 1500-char code body must not be crowded out by
            # higher-scoring 200-char doc paragraphs.
            slices: list[str] = []
            used = 0
            kept = []
            for _, budget_i, results_i in per_store:
                ctx_i, used_i, kept_i = build_context(results_i, budget_i)
                if ctx_i:
                    slices.append(ctx_i)
                    used += used_i
                    kept.extend(kept_i)
            context = "\n\n".join(slices)
        kept_ids = {c.chunk_id for _, c in kept}
        for rank, (score, chunk, label) in enumerate(merged, 1):
            _ev(rspan, "document", doc_id=chunk.doc_id, chunk_id=chunk.chunk_id,
                rank=rank, score=round(float(score), 4), chars=len(chunk.text),
                text_sha256=obs.sha256_text(chunk.text),
                corpus=label,
                kept=chunk.chunk_id in kept_ids,
                drop_reason=(None if chunk.chunk_id in kept_ids else "budget"))
        if rspan is not None:
            rspan.update(candidates=len(merged), context_chars=used,
                         kept=len(kept), dropped=len(merged) - len(kept))

    retrieval = {
        "model": embeddings[0].model,
        "input_tokens": embeddings[0].usage.input_tokens,
        "k": span_k,
        "candidates": len(merged),
        "context_chars": used,
        "cost_usd": round(gateway.ledger.cost(embeddings[0].model,
                                             embeddings[0].usage), 8),
    }
    if labels:
        retrieval["stores"] = store_stats

    def _cite(score, chunk, label):
        entry = citation(chunk, score)
        if label:
            entry["corpus"] = label
        return entry

    if not context or not kept:
        tr.add_event("gate_refused", reason="empty context after budget",
                     candidates=len(merged))
        tr.add_event("final_answer", refused=True, answer_chars=len(NOT_COVERED),
                     answer_sha256=obs.sha256_text(NOT_COVERED),
                     citations=len(merged), model=None)
        return {
            "answer": NOT_COVERED,
            "citations": [_cite(s, c, lbl) for s, c, lbl in merged],
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
                 citations=len(merged), model=completion.model,
                 finish_reason=completion.finish_reason,
                 prompt_version=RAG_PROMPT_REF.version)

    return {
        "answer": completion.text,
        "citations": [_cite(s, c, lbl) for s, c, lbl in merged],
        "retrieval": retrieval,
        "generation": {
            "model": completion.model,
            "provider": gateway.last_provider,
            "input_tokens": completion.usage.input_tokens,
            "output_tokens": completion.usage.output_tokens,
            "usage": completion.usage.as_dict(),
            "attempts": gateway.last_attempts,
            "latency_ms": gateway.last_latency_ms,
            "fallback_depth": getattr(gateway, "last_fallback_depth", 0),
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


def retrieve_remote(*_args: Any, **_kwargs: Any) -> list[tuple[float, Chunk]]:
    """Day 24 §7 — the seam a real *network* vector database will use.

    Today's `RagStore` is a local JSON file, so its read is an `internal` span in
    `_run_rag_traced` (no request left the process). When a remote store exists,
    this function is where the `client` span with injection belongs. It raises so
    it can never be mistaken for a working path — the day names the chain's
    Vector DB hop and does not fake it.
    """
    raise NotImplementedError(
        "remote vector store is PENDING: RagStore is a local file today; a "
        "network vector DB would emit a client span here (Day 24 names the seam)")


# ---------------------------------------------------------------------------
# Day 28 — identity-keyed store cache.
# ---------------------------------------------------------------------------

# Module-level cache slots: one for docs, one for code. A third is refused
# with a named error rather than silently evicting, because an LRU here would
# make "which store answered this" a question about timing.
_DOC_STORE_SLOT: tuple[RagStore, str, float, int, str] | None = None
_CODE_STORE_SLOT: tuple[RagStore, str, float, int, str] | None = None


def _store_identity(path: str, embedding_model: str, dimension: int) -> str:
    """Compute the store's identity: sha256(file) + embedding_model + dimension."""
    with open(path, "rb") as fh:
        content = fh.read()
    file_hash = hashlib.sha256(content).hexdigest()
    return f"{file_hash}|{embedding_model}|{dimension}"


def assert_store_matches(store: "RagStore", *, embedding_model: str = "",
                         dimension: int = 0) -> None:
    """WS5 — fail loudly on an embedding-space mismatch.

    The store's `embedding_model + dimension` is its identity: a query or a
    resumed build embedded by a different model lives in a different space,
    and silently comparing across spaces is a wrong-answer bug. Raises
    `DimensionMismatch` naming both sides (never a silent pass).
    """
    if embedding_model and store.embedding_model \
            and embedding_model != store.embedding_model:
        raise DimensionMismatch(
            f"embedding model mismatch: store is {store.embedding_model!r} "
            f"but the live provider is {embedding_model!r} — pin LLM_PROVIDERS "
            "to one embedding provider (WS5: ollama: or cloudflare:) and rebuild")
    if dimension and store.dimension and dimension != store.dimension:
        raise DimensionMismatch(
            f"embedding dim mismatch: store is dim {store.dimension} "
            f"({store.embedding_model}) but the live provider returned dim "
            f"{dimension} — refusing to compare across spaces")


def _load_store_cached_slot(
    path: str,
    slot: tuple[RagStore, str, float, int, str] | None,
    *,
    clock,
) -> tuple[RagStore, tuple[RagStore, str, float, int, str]]:
    """Load or return cached store, keyed on (mtime, size, sha256, embedding_model, dimension)."""
    st = os.stat(path)
    mtime = st.st_mtime
    size = st.st_size

    # Load the store header first to get embedding_model and dimension
    with open(path, encoding="utf-8") as fh:
        import json as _json
        payload = _json.load(fh)
    embedding_model = payload.get("embedding_model", "")
    dimension = int(payload.get("dimension", 0))
    identity = _store_identity(path, embedding_model, dimension)

    if slot is not None:
        cached_store, cached_identity, cached_mtime, cached_size, _ = slot
        if cached_mtime == mtime and cached_size == size and cached_identity == identity:
            return cached_store, slot

    # Parse the full store — this is the one-time cost per identity change
    store = RagStore.load(path)
    new_slot = (store, identity, mtime, size, path)
    return store, new_slot


def load_store_cached(path: str, *, clock=time.time) -> RagStore:
    """Load a RagStore with identity-keyed caching.

    Key: (mtime, size, sha256(file), embedding_model, dimension).
    The store itself is NOT modified — no field, no new constructor argument,
    no serialization change — so Days 6/10/21 fixtures and data/*.json on disk
    stay byte-compatible and the 262,182,307-byte file does not need rebuilding.

    The cache holds ONE docs store and ONE code store, matching the two corpora
    the repository actually has; a third is refused with a named error rather
    than silently evicting, because an LRU here would make "which store answered
    this question" a question about timing.

    STORE_CACHE=0 restores per-request parsing (the N9 off-switch).
    """
    import os as _os
    if _os.getenv("STORE_CACHE", "1").strip().lower() in {"0", "off", "false", "no", "none"}:
        return RagStore.load(path)

    global _DOC_STORE_SLOT, _CODE_STORE_SLOT

    # Determine which slot to use based on path
    is_code = "code" in path
    slot = _CODE_STORE_SLOT if is_code else _DOC_STORE_SLOT

    store, new_slot = _load_store_cached_slot(path, slot, clock=clock)

    if is_code:
        if _CODE_STORE_SLOT is not None and _CODE_STORE_SLOT[4] != path:
            raise RuntimeError("code store cache: a second code store path was requested; "
                               "only one code store is supported")
        _CODE_STORE_SLOT = new_slot
    else:
        if _DOC_STORE_SLOT is not None and _DOC_STORE_SLOT[4] != path:
            raise RuntimeError("doc store cache: a second doc store path was requested; "
                               "only one doc store is supported")
        _DOC_STORE_SLOT = new_slot

    return store