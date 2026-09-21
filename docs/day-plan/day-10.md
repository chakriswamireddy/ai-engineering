# Day 10 — Code RAG

## Objective

Make the AI understand Saleor source code. Days 6–9 built a doc-RAG that answers *what Saleor's documentation says*; it still cannot answer *where it happens* — "Where is checkout handled?", "Where is order creation implemented?", "Which module handles payments?" live in the source tree, not the docs. **Today the corpus stops being prose and becomes Saleor source: files, functions, classes, imports, dependencies, symbols.** The retriever side (Day 8) and the evidence side (Day 9) carry over unchanged; Day 10 changes the *ingestion* and the *question set*, and nothing else. The deliverable is a Saleor Code Assistant graded by the same fixture harness Days 7–9 used — because a code assistant that cannot prove its own top-K is the same lie a doc assistant tells.

## Duties Outline

### 1. Learn: Why Source Code Is Not Prose (45 min)

- [ ] Accept the premise: every prior day assumed *paragraphs*. Code is a different kind of writing — information lives in **identifiers** (`OrderCreateFromCheckout`, `complete_checkout`) and **structure** (directory layout, imports, nesting), not in sentences. A prose chunker run over a file is a vandal: it slices definitions in half and calls the halves paragraphs.
- [ ] Name the layer code adds that docs don't have: **the dependency graph**. A symbol's meaning is distributed over the symbols it calls and is called by; a function is a node, not a paragraph.
- [ ] Understand where code and prose actually meet: **docstrings, comments, type hints, and tests** are the prose layer. That is where ordinary retrieval can reach in — and where `PENDING` semantics will eventually be judged.
- [ ] Learn the tokenizer's double job in code: identifiers are blends (`checkoutComplete` = `checkout` + `complete`), so tokenization must *split on case and underscore* before it can match. The corpus's rarest, most discriminative tokens are now API names, not words.
- [ ] Pre-commit the day's claim: retrieval over code should answer *symbol questions* — "where / which function / which module" — with cited `module#symbol` evidence, and the same fixture harness must prove it.

### 2. Learn: Code Structure As Retrieval Metadata (45 min)

- [ ] Translate the Day 6 metadata scheme (`doc_id/heading/position`) into code's hierarchy: repository → package → module → symbol (class/function/method) → signature → body. The **directory layout is the domain decomposition** — `saleor/checkout/`, `saleor/order/`, `saleor/payment/` are the corpus's chapter titles.
- [ ] Decide the five facts a code index must record per symbol: **name, kind** (function/class/method/mutation), **signature**, **docstring**, **dependencies** (imports + called names) — plus its **container** (module path) and the **tests** that exercise it.
- [ ] Learn Saleor's split-brain: business logic in the `saleor/` Django apps vs the GraphQL layer in `saleor/graphql/`. "Create an order" has a mutation (`CheckoutComplete`) *and* a service (`complete_checkout`) — both are correct answers, and ranking must know how to say so.
- [ ] Note the generated `saleor/graphql/schema.graphql` as a giant, heading-less retrieval corpus of every mutation/type/field — a strong stress test for code chunking.
- [ ] Treat **tests as executable specs**: `tests/` under each app documents intended behavior. "Why is this test failing?" (Day 30 scenario 5) is a retrieval question over tests, not over prose.

### 3. Design: The Code Ingestion Pipeline (30 min)

- [ ] Keep the two seams Days 6–9 earned: `ingest_code(path) → corpus_code/` mirrors Day 6's corpus fetch; `build_code_store` mirrors Day 6's `build_rag_store`. Same `Chunk` shape, same `RagStore`, same dimension lock — so `/rag`, `retrieve`, rerank, MMR, and the context builder compile untouched.
- [ ] Extend the `Chunk` metadata for symbols, *without* breaking the citation contract: `symbol`, `kind`, `signature`, `docstring`, `imports`, `dependencies`; `doc_id` becomes `module#Symbol`. `source`/`heading` stay for compatibility, so `cited ⊆ retrieved` keeps meaning.
- [ ] Set the chunking rule: **the symbol is the atomic unit**, not the character window. A function or class body is one chunk; only top-level statement runs (and generated behemoths like `schema.graphql` and migrations) fall back to the windowed chunker, tagged with their symbol.
- [ ] Rehost Day 7's Families C and D in code before writing any code: **name collision** (`checkout` = Django model, GraphQL type, module, field), **near-duplicate symbols** (families of test factories and mock methods), and camelCase/snake_case twins (`OrderCreateFromCheckout` vs `order_create_from_checkout`). Code has its own ambiguity; Day 10's fixtures are written for it.
- [ ] Decide what gets *embedded*: the **interface text** (name + signature + docstring) is what retrieval should match ("what does it do"); the **body** rides as plain text for the context builder. Embedding bodies would drown the interface in tokens and budget.

### 4. Build: The Symbol Extractor — AST, stdlib, no parser (1-1.5 hours)

- [ ] Use Python's `ast` to walk each module: `FunctionDef` / `AsyncFunctionDef` / `ClassDef` / methods — collecting name, decorators, args, docstring (first statement), and imports from `Import`/`ImportFrom`.
- [ ] Extract **dependencies statically**: imports are cheap and correct; *call* resolution is expensive and belongs to Day 12/16's agent. Day 10 records imports and the called-name text, not the resolved callee object.
- [ ] Emit a symbol table per module: `module#QualifiedName → {kind, signature, docstring, imports, deps, first_line, last_line, body}` — the atomic retrieval unit, one per symbol.
- [ ] Hold the **split test**: no window may cut through a class or function body; the extraction never produces a chunk that begins `def` and ends mid-return.
- [ ] Bound the corpus side: files over N symbols or M bytes take the prose path (`schema.graphql`, migrations), because a 40 KB single-chunk symbol is a budget catastrophe, not an answer.
- [ ] Write the `PENDING` seam honestly: AST is Python-only; a language-agnostic parser is the upgrade path for any non-Python file, but it is not shipped today.

### 5. Build: The Code Store (45 min)

- [ ] `build_code_store.py`: iterate `corpus_code/saleor/**/*.py`, extract symbols, embed interface text per symbol, persist `data/code_store.json` with the same resumable / dimension-locked builder as Day 6.
- [ ] Spend Day 8 §5's promise: **metadata filtering now pays**. The store filters by `kind` (function vs mutation vs test), by module prefix (`saleor/checkout`), by `has_docstring` — the knobs the doc corpus was too small to need.
- [ ] Keep `imports`/`dependencies` as first-class chunk fields: the maps that let "where does `checkoutComplete` end up?" step from the GraphQL module to the order service through imports. *Finding the map is Day 10; walking it is Day 12/16.*
- [ ] Symmetry test: the identical `recall@K`/`MRR`/`Δrank`/dup-ratio machinery runs against code chunks; fixtures assert on `module#Symbol` sets, the way Day 7's asserted on doc ids.

### 6. Build: Retrieval Over Symbols (45 min)

- [ ] Reuse the Day 8 `Retriever` unchanged; the difference is the vocabulary. Code tokenization splits camelCase and `_` and keeps identifiers as tokens, so `complete_checkout`, `checkoutComplete`, `CheckoutComplete` and the user phrase "complete checkout" collide — Day 8's lexical scorer, retrained for identifiers.
- [ ] Add the code-rule rewrite table (the `/code` dialect of Day 8's rewrite): "checkout complete" → `complete_checkout` / `CheckoutComplete`; the mapping is small, testable, and corrigible.
- [ ] Let ambiguity resolve by filter, not luck: `kind` or `module` filter separates the senses of `checkout`; an out-of-corpus question gets the same refusal gate, on the fused score, never a forged identifier.
- [ ] Carry Day 9 verbatim: rerank's rare-token signal becomes "query identifier in the chunk's symbol name/signature", MMR culls near-duplicate *methods*, `sim_tau` dedup drops an import-window twin. The evidence side does not know the corpus changed.
- [ ] Add the code citation: answers cite `module#symbol:line`, and `cited ⊆ retrieved` stays green — the invariant that kept every prior harness decidable.

### 7. Test: The Code Question Set (1 hour)

- [ ] Pre-register a code mirror of Families A–E with expectations written **before running**, as `module#Symbol` sets: A obvious ("Where is checkout completed?"), B cross-module flow ("What happens between checkout completion and order creation?"), C name collision ("What can the identifier `checkout` refer to?"), D near-duplicate symbols, E out-of-corpus (a symbol that does not exist in Saleor).
- [ ] Re-run the Day 7/8/9 doc questions in parallel to prove **no regression**: the code corpus is additive — a second store behind the same harness — and the contract is unchanged.
- [ ] Apply Day 7's isolation tests to every failure: symbol ∈ top-K (retrieval), survives context (context), oracle with the expected symbol's body (generation).
- [ ] Mark the semantic line `PENDING` and state it plainly: whether a real chat model, given the right symbol, produces the right `file:line` is Day 21's measure. Today proves the harness, not the answers.

### 8. Measure: Doc-RAG vs Code-RAG (45 min)

- [ ] One harness, two datasets, one Rule-4 table: `question | corpus | recall@K | MRR | dup-ratio | Δrank | embeds | chat | cost` — Day 21 inherits both columns as its two benchmarks.
- [ ] Record the cost-shape change honestly: a code store is **symbols, not chunks** — tens of thousands of embed calls at build time, but still one embed and one chat at query time. Build cost and run cost are different rows.
- [ ] Keep the negatives column: what the code corpus cannot cover (data — Day 14), what retrieval still misses (call-graph as a first-class index), and who owns each.

### 9. Documentation (20 min)

- [ ] Write `docs/concepts/code-rag.md` — code vs prose, symbol extraction, interface-embedding, dependency metadata, the two seams.
- [ ] Write `docs/evaluations/code-day10.md` — the §8 table and the code-question fixture set.
- [ ] Fill the Day 10 Daily Learning Record (Problem / Concept / Understanding / ... / Tomorrow).
- [ ] Record token economics: the build-time embed count (per symbol) and the run-time cost (still 1 embed + 1 chat per query).

## Deliverable

```text
Saleor source (github.com/saleor/saleor)
      ↓ clone → corpus_code/saleor/
      ↓ ingest_code(): ast walk → symbol table (module#Symbol → kind/signature/docstring/imports/lines)
      ↓ build_code_store(): embed interface text → data/code_store.json (dimension-locked)
      ↓ retrieve (Day 8: code tokenization + filter by kind/module)
      ↓ rerank / MMR / context (Day 9: unchanged)
      ↓ /rag (unchanged)
      ▼
   Saleor Code Assistant
   "Where is checkout handled?" → complete_checkout (saleor/checkout/complete_checkout.py),
                                 CheckoutComplete (saleor/graphql/checkout/mutations/checkout_complete.py)
```

Days 6–9 answered the documentation questions. Day 10 answers **"where is it implemented?"** — cited `module#Symbol` evidence, same harness, same refusal gate, same measured comparison. Whatever the code corpus still cannot answer goes into Day 11's backlog (tools: `search_saleor_code()` is the next day's first tool) and Day 14's (data).

## Exit Check — answer in your own words

- What does a dependency graph give code retrieval that sentence retrieval never had?
- Why must the chunk boundary respect symbols, and what breaks when it is a character window?
- What is the difference between embedding a symbol's *interface* and embedding its *body*, and why does that split decide the cost?
- Which Day 7 failure families reappear in code, wearing new names?
- What did "the same fixture harness" mean today — and why is a code assistant that cannot prove its top-K a lie?