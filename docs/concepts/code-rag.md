# Concept: Code Is a Different Kind of Writing

> Day 10 of the AI-layer apprenticeship. Days 6–9 built a RAG that answers
> *what Saleor's documentation says*. Day 10 makes it answer **where it
> happens** — "Where is checkout handled?", "Which module creates the order?" —
> by changing the **corpus**, not the system. The retriever (Day 8) and the
> evidence engine (Day 9) carry over with **zero lines changed**; the additions
> are two seams at the ingestion end (`ingest_code`, `build_code_store`) and a
> vocabulary layer inside the unchanged `Retriever` contract.
> Implementation: `ai_os/ingest_code.py`, `ai_os/retrieval.py` (`code_tokens`,
> `CODE_REWRITES`, `kind`/`module_prefix` filters), `tools/build_code_store.py`.

## Problem

Every assumption Days 3–9 made about retrieval was learned on *sentences*. Code
breaks four of them, and each break changes a decision the earlier days made:

| Prose assumption | What code does instead | Consequence |
|---|---|---|
| Meaning is in the sentence | Meaning is in the **identifier** and the **structure** | a token "parse a function body" question |
| The tokenizer splits words; stopwords are noise | Identifiers are blends: `checkoutComplete`, `complete_checkout` | split on case + `_` *before* matching |
| A document is a page with a heading | A module is a container of symbols; the directory tree is the TOC | `doc_id` → `module#Symbol` |
| Context is sequential prose | Meaning is distributed over what a symbol **calls and imports** | the **dependency graph** is new metadata |

The deepest one is the last. A function in Saleor rarely means anything alone:
`complete_checkout` is understood through `create_order_from_checkout`, the
`Checkout` state it reads, and the `Payment` it settles. That is not
paraphrasable into prose because it **is** structure. A doc-RAG looks for the
passage that says what something does; a code-RAG looks for the node where
something happens — and the node's edges are imports and calls.

The places where code *does* behave like prose are exactly where the old
toolbox still works: **docstrings, comments, type hints, and tests**. Tests are
executable specs — a test's name is documentation, its body a truth table — so
they ship in the store from Day 10, not as an afterthought.

## The two seams (what didn't change)

```text
seed_code/saleor/**/*.py → corpus_code/saleor/**/*.py
  └▶ ingest_code(path)         # NEW — ast walk → Symbol table          (mirrors Day 6 documents_from_markdown)
       └▶ build_code_store()   # NEW — embed interface text per symbol   (mirrors Day 6 build_rag_store)
            └▶ data/code_store.json      # the second store, dimension-locked
                 └▶ retrieve()           # Day 8 Retriever + code vocabulary  (signature untouched)
                      └▶ rerank → MMR → dedup → build_context   # Day 9, zero lines changed
                           └▶ /rag (corpus="code")              # Day 6, zero lines changed
```

The contract that makes this *additive* rather than risky:

- **Same `Chunk` shape.** `chunk_id/doc_id/source/heading/position/text/vector`
  all stay; code adds `symbol/kind/signature/docstring/imports/dependencies` as
  optional fields, so a prose store loads unchanged and a code store rides the
  same `RagStore`. `heading` carries the symbol name, so Day 9's heading signal
  works on symbols without a code-specific edit.
- **Same dimension lock and resumable build.** `code_store.json` is a *second*
  store, never a replacement — the doc store and the code store coexist.
- **`cited ⊆ retrieved` stays decidable.** A citation is `module#Symbol:line`,
  still a `Chunk` reference; the invariant Day 6 held is mechanical and
  unchanged. The `:line` makes a retrieval hit a *navigation target*.
- **The refusal gate stays on the fused score.** An out-of-corpus identifier
  question must **refuse**, never forge a plausible-but-nonexistent symbol.

## The chunking rule: the symbol is the atomic unit

Day 6's 200/40 window exists to keep prose chunks paragraph-sized. Run it on a
file and it cuts a `def` in half and presents the halves as independent chunks
that share no boundary context. The rule, in order:

1. every function / class / method is one chunk, from signature to the last body
   line (the AST gives `(first_line, last_line)` — a boundary is a position in
   source, never a character window across one);
2. a module's top-level statement run (constants, wiring) groups into one small
   `kind="module"` chunk;
3. the fallback is **per chunk, not per file**: only a single chunk whose *own*
   body overflows `MAX_CHUNK_CHARS` (40 KB) is split into Day-6 windows, tagged
   with its enclosing symbol (`WebhookPlugin#window3`) or `window<N>` for a
   module run. A large-but-legit module (`complete_checkout.py` is 71 KB,
   `graphql/checkout/types.py` 54 KB) keeps one chunk per symbol — a *file-level*
   gate windows those whole and loses the very symbols the corpus must cite.

The test that makes this provable is structural and runs over the whole corpus:
**no chunk starts `def` and ends mid-`return`.**

The `module#Symbol` key must be *unique* to be a citation, a filter axis, and a
dedup key at once, so nested classes carry their scope (`User.Meta`, not bare
`Meta` — Saleor has 15 `class Meta`s in one module). `ingest_code` qualifies them
at emission; the residual collisions are genuine `@overload`s that share a name
by definition.

## Interface-vs-body: the cost-shaping decision

A symbol has two texts with different jobs:

- **Interface text** = `name + signature + docstring`. This is what *retrieval*
  embeds — "what the symbol does", in the mixture of identifiers and prose the
  query itself speaks.
- **Body** = the full source. This is what *generation* cites — the evidence,
  potentially hundreds of lines.

Embedding the interface keeps the vector thin (`complete_checkout` embeds as a
few hundred tokens) and the match honest (retrieval answers "which symbol is
about checkout completion?", not "which symbol contains checkout's body text").
There is a real, recorded blind spot: **interface-only embedding can miss a
symbol whose *behavior* matches but whose name/docstring don't.** The mitigation
is that the docstring and dependencies are part of the interface; a body-embedding
sweep is a `PENDING` knob for Day 21, not hidden.

## The identifier tokenizer

Prose's noise words (`the`, `how`, `do`) carry no signal; code's tokens are the
opposite. The moment the tokenizer splits camelCase and `_`, one logical token
fans out into shared buckets:

```text
complete_checkout  → {complete, checkout}
checkoutComplete   → {checkout, complete}
"complete checkout"→ {complete, checkout}
```

That collision is *desirable* — it is the identifier-level version of Day 8's
lexical-gap fix, and it costs a few lines:

```python
def code_tokens(text):
    return [t.lower() for raw in re.split(r"[^0-9a-zA-Z]+", text)
            for t in re.split(r"(?<=[a-z0-9])(?=[A-Z])", raw)
            if len(t) > 1 and t not in STOP]
```

The same rule table returns, retargeted at identifiers (`CODE_REWRITES`:
"checkout complete" → `complete_checkout CheckoutComplete`). And ambiguity
resolves by **filter, not luck**: `kind` (`mutation` vs `function` vs `test`) or
`module_prefix` (`saleor/graphql/checkout`) shrinks a symbolic search space
before any scoring runs. Day 8's metadata filter was a prototype on ~250 prose
chunks; the code corpus is where the pattern earns its keep.

## Dependency metadata: the map, not the walk

`imports` and `dependencies` (called names) are first-class chunk fields. They
are the map for "where does `checkoutComplete` end up?" — the mutation chunk's
imports point at the domain service, the service's at `order`/`payment`. **Day 10
makes the map exist and queryable; walking it turn-by-turn is Day 12/16's agent.**
Call *resolution* is a whole-program analysis that belongs there, so Day 10
records called-name text and does not pretend to resolve it.

## Saleor's split-brain, as a feature

Saleor is Django business logic under a Graphene GraphQL API, so "the same
thing" genuinely lives twice: `CheckoutComplete` (the mutation in
`saleor/graphql/checkout/mutations/checkout_complete.py`) and `complete_checkout`
(the service in `saleor/checkout/complete_checkout.py`). Both are the *correct*
answer to "where is checkout completed?" — one is the API entry, one the
mechanism. A cross-layer double-hit is a **feature** (retrieval found both halves
of the split-brain), and the fixtures encode it as one.

## What "the same harness" bought

The identical metric functions from Days 7–9 — `recall@K`, `MRR`, `Δrank`,
`duplicate_ratio`, `should_refuse`, `cited ⊆ retrieved` — run against code
chunks, with fixtures asserting on `module#Symbol` sets exactly as Day 7's
asserted on `doc_id` sets. The harness needed **no code dialect** — that is the
point. The architecture claim of the day is that *the plan survived a corpus
change*: `/rag`, `build_context`, rerank, MMR, dedup are zero-line changes. The
OS now has two grounded corpora behind one interface — the ground Day 11's
`search_saleor_docs()` and `search_saleor_code()` tools will stand on.

## Cost shape

| Axis | Doc corpus | Code corpus |
|---|---|---|
| retrieval unit | chunk (200/40 window) | symbol (AST-bound) |
| expected evidence | `doc_id#heading` | `module#Symbol` |
| **build** embeds | ~1 per chunk | **one per symbol** (tens of thousands for Saleor) |
| **run** cost | 1 embed + 1 chat | 1 embed + 1 chat — **same** |

Build-time embedding explodes; run-time cost does not change at all. The
resumable/managed builder from Day 6 exists precisely so a throttled multi-hour
build never costs the corpus twice — that is why it is inherited, not
reinvented.

The free tier makes the explosion concrete: Cloudflare Workers AI's free plan is
**10,000 neurons/day**, and the fixed extractor yields **29,156 symbols** for the
full Saleor tree (≈18k neurons — *two* free days; the fixture-relevant domains
alone are 10,672 symbols ≈ 6.7k neurons — one day). The builder therefore stops
and **preserves** the partial store if the gateway ever falls through to a
*different* embed model mid-build (dim 768 → 2048), instead of wiping work it
cannot afford to redo — pin `LLM_PROVIDERS` to one embed provider and resume.
