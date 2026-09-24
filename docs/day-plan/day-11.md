# Day 11 — Tool Calling

## Objective

Move from "answering" to "doing". Days 6–10 built retrievers that answer *questions*; the answers still arrive as a single string from `/rag`, and the OS affects nothing. Today the corpus questions become **calls**: `search_saleor_docs()` and `search_saleor_code()` wrap exactly the two corpora Day 10 just made, `get_product()` / `get_order()` / `get_customer()` seed the data half (Day 14's first tools), and the model learns to ask for them. **Today builds the tool layer the Day 12 agent loop will run on: tool schemas the model can read, function calling through the Day 4 gateway, argument validation that rejects without crashing, and tool results that are evidence — not strings.** The deliverable is the first tool-using agent: a question answered by calling a real tool over a real store, with the answer grounded in the tool's result. The *loop* (reason → choose → execute → observe → repeat) is Day 12's; Day 11 builds the plumbing and restricts itself to a **single tool turn** so termination exists before the loop does.

## Duties Outline

### 1. Learn: Tool Schema — the Model's Interface (45 min)

- [ ] Accept the premise: a tool is a function exposed *to the model*, and the model's only knowledge of it is the schema — `name`, `description`, `parameters` (types + required). The schema is to the model what a chunk heading was to the Day 9 reranker: metadata that decides action.
- [ ] Learn why the **description is retrieval, not prose**: the model picks a tool by reading its description at generation time. A tool description that buries "this searches Saleor's source code and returns `module#Symbol` evidence" costs tool-selection accuracy exactly like a bad chunk heading costs rank-1. The Days 8–10 discipline of "write the metadata for a scorer" now applies to "write the description for a chooser".
- [ ] Derive the schema from what already exists: the Days 8–10 retrieval knobs (`kind`, `module` filter, `k`, `max_chars`) are the tool's `parameters`; the `Chunk` shape is the tool's result type. Tools are the old seams made declarative, not a new subsystem.
- [ ] Learn the two validation levels today owns: **schema validation** (do the args match the parameters? — mechanical) and **semantic validation** (is the question answerable from the corpus? — the Day 7 refusal gate, now a return value).
- [ ] Pre-commit the day's claim: a question answered by a tool call is only a *tool-using agent* if the answer cites the tool's evidence — an agent that calls a tool and then phrasely ignores its result is a RAG that PowerShelled.

### 2. Learn: Function Calling — the Wire Does the First Turn (45 min)

- [ ] Understand the new conversation turn: the model answers with `tool_calls: [{id, function: {name, arguments}}]` instead of (or beside) plain text; the harness executes; a `tool` role message carries the result back; the model answers grounded in it. One round trip is Day 11's whole world; the loop is Day 12's.
- [ ] Learn the two wire dialects the Day 4 abstraction exists to hide: OpenAI's `tools` + `message.tool_calls` + `role:"tool"` with `tool_call_id` vs Anthropic's `tool_use` / `tool_result` content blocks. The provider owns the dialect; the gateway and the agent own neither.
- [ ] Spot the Day 6 regression hazard *before* writing code: `openai.py` treats an empty-content 200 as a transient error to retry. When the model calls a tool, content is **legitimately** empty. The check must learn "empty + `tool_calls` present = a valid tool-turn", or the gateway retries successful tool calls into the budget vault.
- [ ] See why the `tools` payload is one argument, not a feature: it rides through the gateway untouched by retries/backoff/fallback/ledger, because the Day 4 seam already carries opaque request options. Tool calling is Day 4's promise ("providers only know their own wire format") paying out.
- [ ] Extend the stub honestly: `tools/mock_gateway.py` must be scripted to emit `tool_calls` (and to emit bad ones), so every mechanical test below runs offline and deterministically. The real providers' tool wire is `PENDING` until a key exists; the stub is the Day 4 pattern, reused.

### 3. Design: The Five Tools (30 min)

- [ ] Register the five tools with their schemas up front, named exactly as the curriculum and Day 30 expect:
  - `search_saleor_docs(query: str, k: int = 4, max_chars: int | None = None)` → top-K chunks from `data/store.json`, with `refused` when the fused-score gate fails. The result **is** the Day 7–9 trace record.
  - `search_saleor_code(query: str, kind: str | None, module: str | None, k: int = 4)` → `module#Symbol` records from `data/code_store.json`; `kind`/`module` are Day 10's metadata filters, now expressible by the model.
  - `get_product(id)`, `get_order(id)`, `get_customer(id)` → validated-`id` lookups over a small seeded fixture set; the real resolver (via `schema.graphql` / Saleor DB) is Day 14's, and Day 11 records it as the `PENDING` data seam.
- [ ] Decide what a tool result **is**: for the retrieval tools, the top-K evidence records (Day 8 list + Day 10 identifiers), truncated by `max_chars`, with `cited ⊆ retrieved` intact because a citation still points at a chunk. The result is not Markdown prose; it is the same shape the harness grades.
- [ ] Decide where the refusal gate lives: **inside the tool**. An out-of-corpus query makes the tool *return* `{refused: true}`, never manufactured results. Day 7's refusal was a route outcome (`/rag` answered "not covered"); Day 11's is a tool's return value — same honesty, new signature, and the model must be told refusal is not an answer.
- [ ] Limit the day: **one tool turn per request**. If the model emits a second `tool_calls` in its answer turn, Day 11 returns the observation and names Day 12. Termination discipline before the loop exists is what keeps "agent" honest tomorrow.
- [ ] Write the split-brain into the schema docs: `search_saleor_code`'s description must tell the model that "where is checkout completed" legitimately double-hits the mutation and the service — so a cross-layer double-hit reads as a feature, exactly as Day 10's fixtures encoded it.

### 4. Build: Gateway Tool Calling — Providers Learn Tools (1-1.5 hours)

- [ ] Extend the `Provider` contract with an optional `tools=` kwarg (schema list) and have `Completion` carry `tool_calls` when present. Rule-1: hand-rolled — the OpenAI body gains `"tools": [...]`; response parsing reads `message.tool_calls`; Anthropic gains native `tools` and folds `tool_use`/`tool_result`.
- [ ] Add `gateway.chat_with_tools(messages, tools, ...)` that is `chat()` with one extra argument passed through — retries, backoff+jitter, `Retry-After`, ordered fallback, per-attempt ledger rows: **unchanged**. A tool-turn chat call bills like any chat call; nothing new about money except the second turn.
- [ ] Patch the empty-content rule: a 200 with `content == null` **and** `tool_calls` present is a success carrying a tool request, not the Day 6 retryable glitch. This is the one edit where Day 11 must not "improve" a previous day's honesty check — the check is not wrong, it just needs a sibling.
- [ ] Teach the stub the dialect: scripted scenarios (good call, bad args, unknown tool, two calls, mid-call error) so the whole mechanical suite runs against `tools/mock_gateway.py` at `:8700`/`:8701`, with zero network and zero keys.
- [ ] Write the honest `PENDING` line: OpenAI-compatible and Anthropic real responses differ in the margins (streamed tool-calls deltas, function-call `arguments` truncation); the stub proves *our* side of the wire, and the real-provider side is verified the day a key has quota.

### 5. Build: The Tool Registry — Validation Is a Contract (1 hour)

- [ ] `ToolRegistry`: name → `{schema, fn}`; `schemas()` feeds the gateway; `execute(name, args)` dispatches. Unknown tool name → a **rejection result** (a message to the model), never an exception, never a forged run.
- [ ] Hand-rolled `validate_args(args, schema)` (Rule-1, the JSON-Schema subset we need): missing `required` → named in the rejection; wrong type → named with the expected type; unknown keys → named; malformed JSON → "malformed arguments". Every rejection is **human-readable and model-readable** — this is what lets Day 12's loop self-correct instead of crash.
- [ ] Make rejection a first-class result shape, symmetric with refusal: `{ok: False, error: "..."}` from a tool is a normal message in the conversation and the run reports it in the trace. Day 7's error taxonomy is for *providers*; Day 11's rejection shape is for *models*.
- [ ] Enforce the result contract mechanically: every retrieval-tool result respects `max_chars`, preserves `cited ⊆ retrieved`, and either returns top-K evidence or `refused`. The registry's tests are the contract's tests.
- [ ] Keep the data tools' stub honest: seeded records carry a `pending_resolver: "day14"` field so no one mistakes a fixture lookup for live Saleor data.

### 6. Build: The Tool-Using Answer Path (45 min)

- [ ] `run_tool_answer(question, registry, gateway)`: `chat_with_tools` with the registry's schemas → if `tool_calls`: validate args → execute → append the `tool` result → a second `chat()` turn → the final answer. No tool call → the answer is the plain chat reply, and the trace says so.
- [ ] Ground the answer contract: the final answer must reference the tool result's chunks (`doc_id` / `module#Symbol`), and the run's trace verifies `cited ⊆ retrieved` on **the tool's** result set — the Day 6 invariant moves home to where evidence is actually produced.
- [ ] Carry the refusal path: if every candidate tool returns `refused` (or validation fails), the final prompt tells the model so and the gate's terse refusal is surfaced — *never* an invented result to save the turn.
- [ ] Enforce the single-turn cap and record the violation in the trace as `loop_deferred: day12`; the cap is a config, not a constant.
- [ ] Wire the economics into the ledger: one request = 1 tool-turn chat + 1 answer chat + 1 embed (the tool's retrieval). Tool **execution** stays free arithmetic over stored vectors — Day 9's "zero new embeds" claim survives the tool layer, because tools run retrieval, not generation.

### 7. Test: The Tool Question Set (1 hour)

- [ ] Pre-register the Tool Fixtures (T1…T7) with expectations written **before** any run:
  - **T1** tool-selection obvious: "Where is checkout handled?" → `search_saleor_code`; result cites `complete_checkout` / `CheckoutComplete` (split-brain double-hit is correct).
  - **T2** tool-selection by corpus: "What do the docs say about creating a checkout?" → `search_saleor_docs`; result ⊆ the doc store.
  - **T3** data tool: "Fetch order 1" → `get_order(id="1")`; valid args; seeded record returned.
  - **T4** argument validity matrix: stub emits missing-required / wrong-type / unknown-key / malformed-JSON args → correct rejection result each time (no crash, message names the field).
  - **T5** unknown tool: stub emits `get_invoice()` → registry rejection result.
  - **T6** out-of-corpus refusal: "Debug the Shopify webhook handler" → `search_saleor_code` returns `refused: true`; final answer must not fabricate a `module#Symbol`.
  - **T7** single-turn cap: stub emits two `tool_calls` → observation returned, `loop_deferred: day12` recorded.
- [ ] Split the day honestly: T4/T5/T6-gate/T7 are `MECH` (pure functions of stored schemas, stores, and scripted stub responses); T1/T2/T3's *real-model* tool choice and argument spelling are `PENDING` until the real gateway — the stub only proves the plumbing can carry them.
- [ ] Re-run Days 7–10's fixtures as the no-regression gate: the retrieval tools call the **same** `retrieve()` the stub harness already grades, so the doc nine and the code fixtures must stay exactly as green — a tool layer that changes retrieval numbers is a bug wearing a feature.
- [ ] Apply Day 7's isolation discipline to a failure: right tool chosen but result missing the symbol → retrieval owns it; symbol in result but wrong final answer → generation owns it; wrong tool → schema/description owns it. Say which, never vibes.

### 8. Measure: Tool Calls as a Contract (45 min)

- [ ] Rule-4 in one table per run: | question | expected tool | tool chosen | args valid | result ⊆ retrieved | gate correct | turns | cost_usd | — every cell a real run's; the `tool chosen` vs `expected tool` column is Day 11's recall@1 for tool selection.
- [ ] Record the two cost rows honestly: per request **3 bills** now (tool-turn chat, answer chat, 1 embed) and the build-vs-run split inherited from Day 10 (stores cost builds; runs cost 1+1+1). The second chat turn is the tool layer's new permanent expense — put it next to Day 9's "zero new embeds" so the claims are both on the same page.
- [ ] Keep the negatives column: what a single tool turn cannot do (multi-step investigation — Day 12), what the stubbed data tools cannot do (real Saleor data — Day 14), and who owns each.
- [ ] The `PENDING` semantic line: whether a real model *chooses* the right tool, *spells* the right args, and *uses* the result — Day 21's benchmark on Day 11's plumbing. Today proves a real engine can carry a tool turn; it does not pretend to prove a model can drive one well.

### 9. Documentation (20 min)

- [ ] Write `docs/concepts/tool-calling.md` — schema as the model's interface, the tool-call conversation turn, validation-as-contract, refusal as a return value, the single-turn cap.
- [ ] Write `docs/evaluations/tool-day11.md` — the §8 tables, the pre-registered T1…T7 fixtures, the 3-bill cost shape, and the no-regression lines.
- [ ] Fill the Day 11 Daily Learning Record (Problem / Concept / Understanding / ... / Tomorrow).
- [ ] Record token economics: the two chat turns per tool answer, the one embed per retrieval, rejection/refusal turns charged like any turn.

## Deliverable

The first tool-using agent — a question answered by executing a real tool over a real store:

```text
question ("Where is checkout handled?")
     ↓
gateway.chat_with_tools(messages, tools=registry.schemas())          (Day 4 gateway + one kwarg)
     ↓
model: tool_calls=[{id, function:{name:"search_saleor_code",
                       arguments:{query:"where is checkout handled", kind:"function"}}}]
     ↓
registry.validate_args(args, schema)   →  invalid? rejection is a result message, never a crash
     ↓
registry.execute → search_saleor_code → Day 8 Retriever over data/code_store.json → gate τ
     ↓    (out-of-corpus? the tool returns {refused: true} — never manufactured evidence)
tool result message: top-K module#Symbol records (+ scores, cited ⊆ retrieved — the Day 6 invariant, at home)
     ↓
second chat turn: model's final answer grounding every claim in the tool's evidence
     ▼
 "Checkout is completed by complete_checkout
  (saleor/checkout/complete_checkout.py:NN), reached from the CheckoutComplete
  mutation (saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete)"
```

Days 6–10 made retrieval prove its top-K; Day 11 makes the model *ask* for it. The retrieval seams became function schemas, the refusal gate became a return value, and the Day 4 gateway carried it all without learning a new security model. Whatever a single tool turn cannot investigate goes into Day 12's backlog (the loop) and Day 14's (the data resolvers).

## Exit Check — answer in your own words

- What does the model know about a tool, and where does that knowledge live?
- Why is validation two levels, and why must a rejection be a *message*, not a crash?
- How did the refusal gate change shape today — and what stayed the same about honesty?
- What did the Day 6 "empty content is a retryable glitch" rule have to learn, and why is editing it dangerous?
- What does "the answer is the tool's evidence, not a paraphrase" mean for a citation — and which previous invariant enforces it?