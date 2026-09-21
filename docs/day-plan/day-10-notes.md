# Day 10 Notes — Code RAG

> Notes answering `docs/day-plan/day-10.md`.
> Day 10 changes the corpus, not the system. Days 6–9 built a RAG that answers *what Saleor's documentation says*; it still cannot answer *where it happens*. Today the corpus stops being prose and becomes Saleor source — but the contract Days 7–9 fought for is kept line-for-line: same `(score, Chunk)` seam, same fixture harness, same `cited ⊆ retrieved` invariant, same separation of `MECH` (provable without a model) from `PENDING` (needs the real gateway). What changes is underneath the seam: **ingestion** (AST symbol extraction instead of markdown chunking), **metadata** (`symbol/kind/signature/imports` instead of `heading/position`), and **the intended question** — from "what does the doc say?" to "where is it implemented?".
> The single idea: **Day 6–9 treated paragraphs as the retrieval unit; Day 10 treats symbols as the atom, the file as the container, and the dependency graph as the extra dimension prose never had.**

---

## 1. Why Source Code Is Not Prose

Everything Days 3–9 learned about retrieval was learned on the assumption that meaning lives in *sentences*. Code upends that assumption in four ways, and each one changes a decision the earlier days made:

| Prose assumption | What code does instead | Consequence |
|---|---|---|
| Meaning is in the sentence | Meaning is in the *identifier* and the *structure* | a token "parse a function body" question |
| Tokenizer splits words; stopwords are noise | Identifiers are blends: `checkoutComplete`, `OrderCreateFromCheckout`, `complete_checkout` | tokenization must split on case + `_` *before* matching |
| A document is a page with a heading | A module is a container holding symbols; the directory tree is the table of contents | `doc_id` `heading` ≥ min-replaceable by `module#Symbol` |
| Context is sequential prose | Meaning is distributed over what a symbol calls and is called by | the *dependency graph* is new retrieval metadata prose never had |

The deepest one is the last. A function in Saleor rarely means anything alone: `complete_checkout` is understood through `create_order_from_checkout`, the `Checkout` state it reads, the `Payment` it settles, the webhook event it fires. That is not paraphrasable into prose because it **is** structure. A doc-RAG looks for the passage that says what something does; a code-RAG looks for the node where something happens — and the node's edges are imports, calls, and the module it lives in.

The places where code *does* behave like prose are exactly where the old toolbox still works, and working them is what makes the day tractable:

- **Docstrings and comments** — the prose layer, written in Near-English, safe to embed as-is.
- **Type hints and signatures** — a compressed form of meaning that oscillates between prose and symbols (`checkout: Checkout, payment_data: None`).
- **Tests** — executable specs. A test's name is documentation; its body is a truth table. The Day 30 scenario *"why is this test failing?"* is a retrieval question whose expected evidence is a `tests/` module, and the harness has to be able to find it.

### The tokenizer's double job

Day 8 built a tokenizer that split on non-word chars and dropped stopwords, and it worked because prose's noise words (`the`, `how`, `do`) carry no signal. Code's tokens are the *opposite*: `checkout`, `complete`, `order`, `create` are dense with meaning, and the same identifier appears three spellings. The moment the tokenizer splits camelCase and `_`, one logical token (`checkoutComplete`) fans out into three physical tokens (`checkout`, `Complete`, `complete`) — so "complete checkout", `checkoutComplete`, `checkout_complete`, and `CheckoutComplete` all collide in the same lexical bucket. That collision is *desirable*: it is the identifier-level version of Day 8's lexical gap fix, and it costs a few lines of tokenizer.

The pre-commit, stated plainly so the day can be graded: **retrieval over code must answer symbol questions** — "where / which function / which module" — with cited `module#Symbol` evidence, and the same fixture harness proves it. Not "the answer looked plausible". The harness.

---

## 2. Code Structure As Retrieval Metadata

### The hierarchy replaces heading/position

Day 6 stored `doc_id/heading/position` per chunk because prose is organized by headings inside pages. Code has a stricter, richer hierarchy:

```text
repository (saleor/)                 the corpus root
  └─ package (checkout/, order/, payment/, graphql/)      = domain decomposition, the "chapter titles"
      └─ module (complete_checkout.py)                    = one concern per file
          └─ symbol (complete_checkout, CheckoutComplete) = the atomic retrieval unit
              └─ signature / docstring / body             = the chunk's prose + logic
                  └─ imports / called names               = the dependency edges
```

The directory layout is the grounding Saleor already ships: `saleor/checkout/` exists because checkout is a domain; `saleor/graphql/checkout/` exists because the API is a separate domain. A query about "checkout" can be routed by module prefix before any vector math — the metadata-filter pattern Day 8 built for prose, finally needed for real.

### The five facts per symbol

| Fact | Why it matters for retrieval |
|---|---|
| **name** | the highest-IDF token in the corpus (Day 8's rare-token signal, code edition) |
| **kind** (function/class/method/mutation) | the *filter* axis: "where is the mutation" vs "where is the service" is a `kind` filter, not a new query |
| **signature** | compressed meaning, grammar of types |
| **docstring** | the prose anchor — embed-safe, quotable as a citation |
| **dependencies** (imports + called names) | the graph that cross-module questions (Family B) walk |

Plus the container (module path — the citation and the filter) and the exercising tests, which are themselves symbols in the store.

### Saleor's split-brain

Saleor is Django business logic under a Graphene GraphQL API, and "the same thing" genuinely lives twice: `CheckoutComplete` (the mutation in `saleor/graphql/checkout/mutations/checkout_complete.py`) and `complete_checkout` (the service in `saleor/checkout/complete_checkout.py`). Both are the *correct* answer to "where is checkout completed?" — one is the API entry, one is the mechanism. Day 10's fixtures must encode that a cross-layer double-hit is a *feature* (retrieval found both halves of the split-brain), and the answer must be able to say "the mutation, which calls the service". That is a world the docs corpus never asked for: docs rarely have two pages for one concept; Saleor structurally has two layers.

### `schema.graphql` — the generated corpus that breaks prose assumptions

`saleor/graphql/schema.graphql` is a single generated file containing every type, mutation, field, and argument — closest thing the codebase has to an index of the entire API, and the worst thing Day 6's windowed chunker could be pointed at: no prose headings, one 40 KB "paragraph", machine density. It earns its own rule in §3: files that cannot be symbol-split take the windowed path *with a symbol tag* — never embedded whole.

### Tests as documentation

Day 30 scenario 5 ("why is this test failing?") and Day 11's very first tool (`search_saleor_code()`) share a target: the answer to "why" lives in `tests/`, where intent is encoded as assertions. Indexing production code without tests is indexing a contract without its counter-parties. Tests ship in the store from Day 10, not as an afterthought.

---

## 3. The Code Ingestion Pipeline — Two Seams, One Contract

Day 9's architecture lesson — *judge / diversify / compress are seams, not a blob* — is inherited verbatim. Day 10 adds the ingestion half as two more seams, deliberately mirroring Day 6:

```text
ingest_code(path)      # NEW — ast walk → symbol table          (mirrors Day 6 documents_from_markdown)
build_code_store       # NEW — embed interface text per symbol   (mirrors Day 6 build_rag_store)
   │
   ▼
store.retrieve(...)    # Day 8 Retriever, code tokenization     (signature untouched)
   ▼
rerank → MMR → dedup → build_context                            (Day 9, zero lines changed)
   ▼
/rag                                                             (Day 6, zero lines changed)
```

The contract that makes this additive rather than risky:

1. **Same `Chunk` shape.** `chunk_id`, `doc_id`, `source`, `heading`, `position`, `text`, `vector` all stay; code adds `symbol`, `kind`, `signature`, `docstring`, `imports`, `dependencies`. Day 9's `rel()` reads `c.heading` and `c.text`; code chunks put the identifier + docstring where `heading` used to be, so the reranker's heading signal works on symbols without a code-specific edit.
2. **Same dimension lock and resumable build.** A `code_store.json` is a second store, not a replacement; the doc store and the code store coexist behind the same server config, exactly like two corpora in one `corpus/`.
3. **`cited ⊆ retrieved` stays decidable.** A citation is now `module#Symbol:line`, which is still a `Chunk` reference; the invariant Day 6 held and Day 9 re-asserted is mechanical and unchanged.
4. **Refusal gate stays on the fused score.** An out-of-corpus identifier question (`PENDING` in §7, Family E) must refuse, never forge a plausible-but-nonexistent symbol — the Day 8 warning applied to identifiers.

### The chunking rule: symbol as the atomic unit

Day 6's 200/40 window reducer exists to keep prose chunks paragraph-sized. Run it on code and it does the unforgivable: it cuts a `def` in half, splits `class CheckoutComplete` from its body, and presents the two fragments as independent chunks that share no boundary context. Symbol-level chunking is the code analog of Day 6's "a document is the average of several meanings" — **a module is the sum of several symbols, and the retrieval unit is one symbol**. The rule, in order:

1. every `FunctionDef` / `AsyncFunctionDef` / class / method is one chunk, from signature to the last body line;
2. top-level statement runs that belong to no symbol (module constants, wiring) group into one small chunk per module;
3. files too large or too generated for symbol splitting (`schema.graphql`, migrations) fall back to the windowed chunker **tagged with their enclosing symbol**, because a single 40 KB symbol chunk is a budget catastrophe rather than an answer (Day 6's budget floor, now protecting against symbol bloat).

### The interface-vs-body embedding split

The cost-shaping decision of the whole day. A symbol has two texts with different jobs:

- **Interface text** = name + signature + docstring. This is what *retrieval* should match — it says "what the symbol does", in the mixture of identifiers and prose the query itself uses.
- **Body** = the full source. This is what *generation* should cite — the evidence, potentially hundreds of lines.

Embedding the interface text keeps the vector thin (a symbol like `complete_checkout` embeds as a few hundred tokens) and the match honest (retrieval answers "which symbol is about checkout completion?", not "which symbol has checkout completion's *body text*"). The body rides as plain `text` for the context builder. There is a real risk to record, not hide: **interface-only embedding can miss a symbol whose *behavior* (body) matches but whose name/docstring don't**, e.g. a poorly-named helper. The mitigation is that `dependencies` and the docstring are part of the interface, and that retrieval over the body is a `PENDING` knob to compare on Day 21.

---

## 4. The Symbol Extractor — AST, stdlib, no parser

`ast` is the standard library's own parser, and using it is the Rule-1 (first-principles) move: we are not installing tree-sitter to learn what a `FunctionDef` is. The extractor is a `NodeVisitor`:

```python
import ast

class SymbolExtractor(ast.NodeVisitor):
    def visit_ClassDef(self, node):      # + visit_FunctionDef, visit_AsyncFunctionDef
        ...
    def collect_imports(self, tree):     # ast.Import / ast.ImportFrom — static, cheap, correct
    def docstring_of(self, node):        # first statement if Expr(Str)
    def signature_of(self, node):        # ast.unparse(args/returns)  — name + args + return hint
    def symbol_range(self, node):        # first_line..last_line → slice the body text exactly
```

Design decisions, each owned:

- **The split test is structural.** The only assertion that matters is "no chunk starts mid-`def` or ends mid-`return`". The AST gives `(first_line, last_line)` for every definition, so chunk boundaries are *positions in source*, never character windows across them.
- **Dependencies are static and one-directional.** Imports (`from saleor.order.utils import create_order_from_checkout`) are correct by construction and tell us the graph semantically points at the *order* domain. Call-resolution (who *actually* calls `complete_checkout`?) is a whole-program analysis that belongs to Day 12/16's agent; Day 10 records called-name text as `deps` and does not pretend to resolve it.
- **Qualified names defeat the `checkout` collision.** A symbol is stored as `saleor/checkout/complete_checkout.py#complete_checkout`, never as the bare identifier — the `module#Symbol` key is the citation, the filter axis, and the dedup key all at once.
- **The bound is a guard, not a knife.** N-large / M-byte files take the windowed path with a symbol tag; this is Day 6's "budget floor" instinct (never let one unit starve the corpus) applied to symbol size.

`PENDING` seam: the extractor is Python-only because Saleor is Python. A language-agnostic parser is the exact upgrade path if the corpus ever grows a JavaScript storefront — and it plugs in behind the same `ingest_code` seam, which is the honest way to leave it.

---

## 5. The Code Store

`build_code_store.py` reuses Day 6's builder wholesale — per-chunk ledger, resumable saves, dimension-lock on the persisted store — with one input swap: iterating `corpus_code/saleor/**/*.py` via `SymbolExtractor` instead of `documents_from_markdown`.

Three things the store makes real that were theory before:

1. **Metadata filtering finally pays.** Day 8 §5 was honest that filtering saved microseconds on 250 prose chunks and mattered as *pattern*. Code is the day the pattern earns its keep: filtering by `kind` (`mutation` vs `function` vs `class` vs `test`) or by module prefix (`saleor/graphql/checkout`) shrinks a symbolic search space of tens of thousands of symbols before any scoring runs. The prose filter was a prototype; the code filter is a product decision.
2. **`imports`/`dependencies` are first-class fields.** They are the map for "where does `checkoutComplete` end up?" — the GraphQL mutation chunk's imports point at the checkpoint service, the service chunk's imports point at `order`/`payment`. *Finding the map is Day 10; walking it (agent-style, turn-by-turn) is Day 12/16.* Day 10's deliverable is that the map *exists* and is queryable.
3. **The store keeps two corpora truthfully.** `RagStore` already persists `embedding_model`/`dimension`; a code store alongside the doc store is additive, and the server route `/rag` gains a `corpus=` selector. No existing store is invalidated; Day 8/9's green rows must still be green on the doc store — the regression contract.

The symmetry test that makes the whole thing a *comparison*: the identical metric functions from Days 7–9 (`recall@K`, `MRR`, `Δrank`, `duplicate_ratio`, `should_refuse`, `cited ⊆ retrieved`) run against code chunks, with fixtures asserting on `module#Symbol` sets exactly as Day 7's asserted on `doc_id` sets. The harness did not need a code dialect — that is the point.

---

## 6. Retrieval Over Symbols

The Day 8 `Retriever` is used as-is. The interesting work is vocabulary:

### Code tokenization

```python
def code_tokens(text):
    return [t for t in re.split(r"[^a-zA-Z0-9_]+|(?<=[a-z])(?=[A-Z])", text)
            if len(t) > 1 and t not in STOP]
```

- splits `checkoutComplete` → `checkout`, `Complete`; `complete_checkout` → `complete`, `checkout`; lowercased they collide into shared tokens.
- keeps identifiers (removes the "word-boundary" instinct that would treat `complete_checkout` as one unknown word) but still drops the prose stopwords.
- this is Day 8's `Lexicon` with a code-friendly tokenizer; IDF is computed once over the symbol store, and `complete_checkout`'s tokens are rare-and-discriminative exactly as Day 8 hoped.

### The code rewrite table

```python
CODE_REWRITES = {
    "checkout complete": "complete_checkout CheckoutComplete",
    "complete a checkout": "complete_checkout CheckoutComplete",
    "create an order": "create_order_from_checkout OrderCreateFromCheckout",
    "is checkout stuck": "checkout validation errors invalid checkout",
}
```

Same logic as Day 8's rule table, re-targeted: user phrasing → Saleor identifier vocabulary → what the embedder and the Lexicon both speak. It is small, corrigible, and defeats itself if a rule is missing — which is the fixture that justifies the `PENDING` gateway-rewrite tier.

### Ambiguity resolves by filter, not luck

The `checkout` collision (Django app, GraphQL type, module, variable, field) is Family C in code's clothing. One identifier, five meanings, one embedding. Day 8's answer was metadata filtering; code makes it concrete: a query whose tokens match many modules drops to `filter=kind` or `filter=module_prefix`, and the wrong-sense results collapse. Where the doc question could only be *helped* by a filter, the code question is *reduced* by one.

### Day 9 carried over, verbatim

The evidence side does not learn the corpus changed:

- rerank's **heading signal** reads `kind`/`symbol` — a query identifier in the symbol name is the strongest section-level match code has.
- rerank's **rare-token signal** becomes "query identifier in signature or docstring".
- **MMR** culls near-duplicate *methods* — Saleor's test factories and mock methods are Family D twins, high mutual cosine, and MMR's `λ·rel − (1−λ)·sim` is precisely the arithmetic that keeps one real representative while dropping its clones.
- **dedup (`sim_tau`)** drops the import-window twin, with Day 9's guard never similarity-dropping the top chunk.
- **`best_first` ordering** leads the context with the symbol most likely to be the answer.

### The code citation

Answers cite `module#Symbol:line`. Because every citation is still a `Chunk` reference, `cited ⊆ retrieved` remains a set-membership check (Day 7 `MECH`). Line numbers inside a chunk turn "retrieval evidence" into "navigation target" — the assistant's answer becomes clickable in the source, which is the difference between a code assistant and a code *index*.

---

## 7. Test: The Code Question Set

Expected evidence written **before** running, as `module#Symbol` sets — the Day 7 pre-registration rule applied to code:

| ID | Family | Question | Expected (the run's job to verify, shape pre-registered) |
|---|---|---|---|
| CA1 | obvious | "Where is checkout completed?" | `saleor/checkout/complete_checkout.py#complete_checkout`, `saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete` (split-brain double-hit is *correct*) |
| CB1 | cross-module | "What happens between checkout completion and order creation?" | the service symbol that builds the `Order` from the `Checkout` (`create_order_from_checkout`), plus its imports into `order/` |
| CB2 | cross-module | "Which module fires the webhook when a payment succeeds?" | `saleor/payment/` processing symbol + `saleor/webhook/event_types.py#WebhookEventAsyncType` |
| CC1 | name collision | "What can the identifier `checkout` refer to in the codebase?" | at least the Django model, the GraphQL type, and the module — *and* the fixture's `must_not` set |
| CD1 | near-duplicate | "Show the test that exercises checkout completion." | one real `tests/` module, not a family of mock twins |
| CE1 | out-of-corpus | "Where is the Shopify webhook handler?" | *must refuse* — no such symbol exists (`gate`, fused score, never a forged identifier) |

Every row's expected symbols are named before the run; exact paths for the ones I am least certain of are confirmed at ingest and written into the fixture — that is the pre-registration discipline, and it closes the gap between "I know Saleor" and "the index agrees".

Two parallel obligations:

1. **No regression on the doc corpus.** The nine Day 7/8/9 questions re-run against the doc store must stay exactly as green as Day 9 left them. Two stores, one harness; the code corpus is additive, never a rewrite.
2. **Isolation tests apply unchanged.** Expected symbol ∉ top-K → retrieval; in top-K, dropped by budget → context; in context, wrong answer → oracle test (inject the symbol's body as context) — the exact decision tree from Day 7 §4, with `symbol` in place of `doc_id`.

The semantic line is declared `PENDING` and stated plainly: whether a real chat model, given the right symbol, produces the right `file:line` is a generation question for Day 21's benchmark. Today proves the harness can *find* the symbol; it does not pretend to prove the model can *use* it.

---

## 8. Measure: Doc-RAG vs Code-RAG

One harness, two datasets, one Rule-4 table per dataset, and the two tables side by side is the deliverable Day 21 inherits as its two benchmarks:

| Axis | Doc corpus (Day 6–9) | Code corpus (Day 10) |
|---|---|---|
| retrieval unit | chunk (200/40 window) | symbol (AST-bound) |
| expected evidence | `doc_id#heading` | `module#Symbol` |
| metric set | recall@K, MRR, Δrank, dup-ratio — identical | identical (no dialect) |
| gate | fused score ≥ τ | fused score ≥ τ — no forged identifiers |
| build cost | ~250 embeds | tens of thousands (one per symbol) — recorded as _build_ rows, not run rows |
| run cost | 1 embed + 1 chat | 1 embed + 1 chat — same |

The cost-shape change is the one number most likely to be misread, so it is stated twice: **build-time embedding explodes** (every symbol is a ledger line, and Saleor has tens of thousands) and **run-time cost does not change at all** (one embed per query, one chat per answer). The resumable/managed builder from Day 6 exists precisely so a throttled multi-hour build never costs the corpus twice — that is why it is inherited, not reinvented.

The negatives column, written honestly: the code corpus cannot cover **data** (what's in the database — Day 14's Text-to-SQL), the walkable **call graph** is Day 12/16's agent territory (Day 10 stores the map; it does not navigate it), and the semantic verdict on answer quality belongs to Day 21. An empty negatives column would mean the day learned nothing; this column has owners, not wishes.

---

## 9. Documents

- `docs/concepts/code-rag.md` — code vs prose, symbol as the atomic unit, AST extraction, interface-vs-body embedding, dependency metadata, the two seams (`ingest_code`, `build_code_store`), and the identifier-tokenizer.
- `docs/evaluations/code-day10.md` — the §8 tables (doc + code), the pre-registered code fixtures (CA1…CE1), and the build-vs-run cost split.
- PROGRESS rollup: Day 10 status moves to `DONE` for the mechanics (symbol extraction, code store, retrieval over symbols verified on fixtures) and `PARTIAL` until a real gateway confirms the code assistant actually answers `file:line` questions correctly.

---

## 10. Daily Learning Record

### Problem

The OS can answer *what the docs say* but not *where it happens*. "Where is checkout handled?", "Where is order creation implemented?", "Which module handles payments?" are unanswerable by Days 6–9, not because retrieval is bad but because the corpus is prose. The problem is to add a whole new corpus — Saleor's source — without breaking the contract everything previous earned: same seams, same harness, same refusal gate, and a graded comparison, not a parade.

### Concept

**Code is a different kind of writing with a different retrieval unit.** The atom is the **symbol**, not the paragraph; the container is the **file/module**, whose directory layout is the domain decomposition; and the extra dimension prose lacks is the **dependency graph** — a symbol is a node whose meaning is distributed over the symbols it imports and calls. Because identifiers are blends (`checkoutComplete` / `complete_checkout` / "complete checkout"), tokenization must split case and underscores before any lexical scoring, which makes Day 8's lexical gap fix dramatically more useful in code than in prose. Embedding splits the symbol into **interface** (name + signature + docstring — what retrieval matches) and **body** (the cited evidence — what generation reads), which is the decision that keeps build cost proportionate and run cost flat. Day 9's evidence stack (rerank, MMR, dedup, ordering) is inherited unchanged because it never depended on what the text was — only on vectors, tokens, and the `Chunk` shape.

### Understanding

The unlock is that **the system did not need to change — the corpus did.** Days 8–9 built a retriever and an evidence engine that are agnostic to prose-vs-code; they read `(score, Chunk)` and signal-weighted metadata. Everything code-specific lives in ingestion (AST), metadata (`symbol/kind/signature/imports`), and vocabulary (code tokenization + the code rewrite table). The second understanding is subtler: Saleor's split-brain (business logic in `saleor/`, API in `saleor/graphql/`) makes a "correct" answer a *double-hit* — the mutation and the service both match "where is checkout completed" — and fixtures have to encode that as a feature, not a bug. And the third, the honest one: the day proves the harness can *find* the right symbol; it is `PENDING` whether a real model turns the right symbol into the right `file:line:why`. The boundary is Day 21's, and naming it today is what keeps Day 21 honest.

### Implementation

- `ai_os/ingest_code.py` — `SymbolExtractor` (`ast.NodeVisitor` over `ClassDef`/`FunctionDef`/`AsyncFunctionDef`): name, kind, signature, docstring, imports, deps, `(first_line, last_line)`, body slice; `module#Symbol` ids; N/M-byte guard routes big files to the windowed path.
- `ai_os/retrieval.py` (extended, not replaced) — `code_tokens` splitter (camelCase + `_`), `CODE_REWRITES`, filter by `kind`/`module_prefix`.
- `tools/build_code_store.py` — mirrors `build_rag_store` (ledger, resume, dimension lock) over `corpus_code/saleor/**/*.py`; embeds interface text, stores body as `text`, `imports`/`dependencies` as fields.
- `data/code_store.json` — the second store; `/rag` accepts `corpus=code`.
- `fixtures` — CA1…CE1 pre-registered `module#Symbol` sets; Day 7–9 doc fixtures re-run as the no-regression gate.
- `docs/evaluations/code-day10.md`, `docs/concepts/code-rag.md`.

### Failure

The failures are the transplant of Days 7–9's, and naming them early is the way to catch them:

1. **Name collision** (Family C, code edition): `checkout` as Django app, GraphQL type, module, field. One identifier, five senses — this is why the fixture asserts on `module#Symbol`, never on bare names, and why the filter axis exists.
2. **Near-duplicate symbols** (Family D): Saleor's test factories and mock methods are twins — high mutual cosine, near-identical text. Spoiler for Day 7's warning: hybrid lexical scoring will *reward* them, which is precisely why MMR and dedup are inherited, not rebuilt.
3. **The interface-vs-body blind spot**: a symbol whose *behavior* matches but whose name/docstring don't will be missed by interface-only embedding. Recorded as `PENDING` (body-embedding sweep) on Day 21, not hidden.
4. **The generated-behemoth trap**: point Day 6's window chunker at `schema.graphql` and it produces prose-shaped chunks of a 40 KB machine file. The N/M-byte guard is the honest mitigation.

### Debugging

Everything is `MECH` under the stub gateway: symbol extraction is a pure function of source text; the split test ("no chunk starts mid-`def`") is a structural assertion; `retrieve` over code is arithmetic on stored vectors and identifier tokens; `cited ⊆ retrieved` is set membership. The debug loop is Day 7's: pick a fixture (CB1), run retrieval-only, and read the top-K — did the service symbol outrank its imports? If the mutation appears but the service doesn't, the query's identifiers matched the GraphQL layer but not the domain layer, and the fix is either the filter axis (`kind=mutation`) or the rule table — the trace says which, never the vibes.

### Evaluation

The two tables in `docs/evaluations/code-day10.md`. Same metric machinery as Days 8–9 (recall@K, MRR, Δrank, dup-ratio, gate correctness) on **two** datasets — doc and code — so Day 21 inherits a pair of benchmarks, not a single aspirational one. The numbers a future observer can carry forward: how many of the code fixtures clear top-K, whether the doc nine stayed green (the no-regression line), and the build-vs-run cost split (tens of thousands of build embeds; one embed + one chat per run-time query). The `PENDING` line: whether a real chat model turns the right symbol into the right `file:line` answer — Day 21's semantic verdict, on Day 10's harness.

### Architecture

The architecture claim of the day is that **the plan survived a corpus change** — that is an architectural property, not a lucky accident. `/rag`, `build_context`, rerank, MMR, dedup: zero-line changes. The additions are two seams at the ingestion end (`ingest_code`, `build_code_store`) and a vocabulary layer inside the unchanged `Retriever` contract. The OS now has two grounded corpora behind one interface — which is exactly what Day 11's `search_saleor_docs()` and `search_saleor_code()` tools will stand on, and Day 12's agent will switch between. **Day 10 added a corpus, not a subsystem; the seams are what bought that.**

### Questions

- Is the interface-only embedding the right trade, or does the body-blind spot cost more real queries than the token savings earn? Day 21's body-embedding sweep answers it.
- Does `module#Symbol` dedup hold up when two symbols share a name across packages (same-kind twins in `checkout/` and `order/`)? The `module` half of the key is doing real work; is it ever insufficient?
- How much does the static imports map under-support cross-module questions before the call-graph walk (Day 12/16) becomes mandatory? CB1 is the seam's stress test.
- Is `best_first` slicing a long symbol's body the right presentation, or does a 300-line function need sub-symbol splitting? The budget floor has opinions.
- When Family E refuses on a *fused* score, does the code corpus change the calibrated τ relative to the doc corpus? Same gate, new score distribution — Day 21 recalibration list.

### Tomorrow

Day 11 — Tool Calling: the corpus questions become *calls*. `search_saleor_docs()` and `search_saleor_code()` — exactly the two corpora Day 10 just made — plus `get_product` / `get_order` / `get_customer` for the data half (Day 14's first tools). The retrieval seams become function schemas; the agent loop starts. Day 10's `module#Symbol` citations and the two-store harness are the ground Day 11's tools call back into — and Day 21's benchmark inherits, as its baseline, two tables: the doc nine and the code fixtures.