# Day 11 Notes — Tool Calling

> Notes answering `docs/day-plan/day-11.md`.
> Days 6–10 built retrievers that answer questions; the answer still arrives as one string and the OS affects nothing. Day 11 moves from "answering" to "doing": the corpus questions become **calls** — `search_saleor_docs()` / `search_saleor_code()` wrap exactly the two corpora Day 10 made, `get_product()` / `get_order()` / `get_customer()` seed the data half — and the model learns to ask for them through the Day 4 gateway. What changes is underneath the tool: the **schema** is a new kind of interface (metadata written for a chooser, not a scorer), the **tool result** is evidence in the same shape the harness has graded since Day 7, and the **refusal gate** (Day 7/8) stops being a route outcome and becomes a return value. Day 11 restricts itself to a **single tool turn**: the loop is Day 12, but termination discipline exists before the loop does.
> The single idea: **a tool is the old seams made declarative — the model proposes an action, the harness validates and executes it, and the answer is grounded in the tool's evidence, so the honesty invariant Days 6–10 fought for (`cited ⊆ retrieved`) moves home to where evidence is actually produced.**

---

## 1. Tool Schema — the Model's Interface

Everything an LLM agent does rests on a small asymmetry: the model can only know a tool through the schema it is handed, and the schema is one JSON document per tool. Day 11's first lesson is that this document is not API documentation; it is **metadata about the world written for a chooser**, with the same stakes as the metadata Days 8–9 wrote for a scorer.

```json
{
  "type": "function",
  "function": {
    "name": "search_saleor_code",
    "description": "Retrieve Saleor source symbols from the code store. Returns top-K "
                    "module#Symbol records with scores. A query about checkout legitimately "
                    "hits both the mutation (saleor/graphql/checkout/...) and the service "
                    "(saleor/checkout/...) — a double-hit is correct.",
    "parameters": {
      "type": "object",
      "properties": {
        "query":     {"type": "string"},
        "kind":      {"type": "string", "enum": ["function", "class", "mutation", "test"]},
        "module":    {"type": "string"},
        "k":         {"type": "integer", "minimum": 1, "maximum": 10, "default": 4}
      },
      "required": ["query"]
    }
  }
}
```

Three properties matter, and they map onto machinery that already exists:

| Schema field | The Days 8–10 thing it is | Lesson |
|---|---|---|
| `name` | the tool's identifier; the model "calls" exactly this | keep names stable — Day 12's loop will *depend* on them; a renamed tool is a broken `tool_calls` |
| `description` | a chunk `heading` — the highest-leverage text the scorer/chooser reads | the description is **retrieval for the model**: bury "returns module#Symbol evidence" and tool selection slips exactly like a bad heading slips rank-1 |
| `parameters` (+`required`) | the retrieval knobs (`kind`, `module`, `k`, `max_chars`) as declarative fields | every knob Day 8 built as a filter argument and Day 10 as metadata is now something *the model can express* |

The discipline transfers whole: Day 8's rule was "write the stopword-lean query for the scorer"; Day 11's is "write the description for the chooser". The split-brain study from Day 10 became a sentence in the `search_saleor_code` description — the schema carries the lesson forward so the model doesn't have to rediscover it.

### Two validation levels

The day walks a line between two kinds of "no", and mixing them is the classic agent bug:

1. **Schema validation** — "do the arguments match the parameters?" Mechanical. Missing `query`, an `enum` violation on `kind`, an unknown key. This is checked with a hand-rolled validator (Rule-1: we need a ten-line subset of JSON Schema, not a library).
2. **Semantic validation** — "is this question answerable from the corpus?" This is the Day 7 refusal gate (`should_refuse` on the fused score), now executed *inside* the tool and returned as a value.

The pre-commit, stated plainly so the day can be graded: **a question answered by a tool call is only a tool-using agent if the answer cites the tool's evidence.** A model that calls `search_saleor_code` and then answers from vibes is a RAG with extra steps — and the trace has a column for exactly that.

---

## 2. Function Calling — the Wire Does the First Turn

A tool call is a new kind of conversation turn, and the wire knows about it before anything else:

```text
user:    "Where is checkout handled?"
assistant: {content: null, tool_calls: [{id: "call_ab12", type: "function",
              function: {name: "search_saleor_code",
                         arguments: "{\"query\": \"where is checkout handled\", \"kind\": \"function\"}"}}]}
tool:    {role: "tool", tool_call_id: "call_ab12",
          content: "[{module#Symbol: saleor/checkout/complete_checkout.py#complete_checkout, score: 0.83, ...}, ...]"}
assistant: "Checkout is completed by complete_checkout (saleor/checkout/complete_checkout.py:NN)..."
```

The two dialects the Day 4 abstraction exists to hide:

| | OpenAI | Anthropic |
|---|---|---|
| request | `tools: [{type:"function", function:{name, description, parameters}}]` on the body | `tools: [{name, description, input_schema}]` |
| model asks | `message.tool_calls[]` (with `id`, `function.arguments` as a JSON **string**) | `content` block type `tool_use` |
| answer back | `role:"tool"` message with `tool_call_id` | `content` block type `tool_result` |

The provider owns the dialect; the gateway and the agent own neither. That division is Day 4's entire bet, and it is the reason today is small: `chat_with_tools(messages, tools, ...)` is `chat()` with one opaque argument passed through the unchanged retry/backoff/fallback/ledger machinery.

### The regression hazard hiding in Day 6

`openai.py` currently raises a retryable error on a 200 whose `content` is empty — added in Day 6 because throttled OpenAI-compatible endpoints return `200-with-nothing`. But a model that calls a tool *legitimately* returns `content: null` with `tool_calls` present. If the check is not extended, the gateway will retry a **successful** tool call as a glitch and burn budget while the real call sits on the floor. The fix is not to weaken the Day 6 check; it is to give it a sibling: empty content **and** empty `tool_calls` → still the transient error; empty content with `tool_calls` → a valid tool-turn completion. Editing a previous day's honesty check is exactly the kind of edit that silently re-breaks a corner — so the two cases are tested as two independent rows, not one.

### The stub grows a voice

Deterministic offline tests need the stub (`tools/mock_gateway.py`) to emit `tool_calls` — the good kind (valid `search_saleor_code` args), and the bad kind (missing `required`, wrong `enum`, unknown tool name, two calls in one turn, malformed `arguments` string). That is the Day 4 pattern reused: script the stub, test our side of the wire, and keep the real providers' tool dialect as the `PENDING` seam — OpenAI-compatible endpoints differ in the margins (streamed tool-call deltas, truncated `arguments`), and those margins get verified the day a key has quota.

---

## 3. The Five Tools

Day 10's backlog said it: "the retrieval seams become function schemas". Concretely:

| Tool | Reads from | Result when ok | Result when the gate fails |
|---|---|---|---|
| `search_saleor_docs(query, k=4, max_chars=None)` | `data/store.json` (doc store — Day 6) | top-K chunks, `doc_id`/`heading`/`score`/`position`, budget-clamped | `{refused: true}` — out-of-corpus is a return value |
| `search_saleor_code(query, kind=None, module=None, k=4)` | `data/code_store.json` (code store — Day 10) | top-K `module#Symbol` records + line ranges | `{refused: true}` |
| `get_product(id)`, `get_order(id)`, `get_customer(id)` | seeded fixture records | the record (`pending_resolver: "day14"` on it) | `{ok: false, error: "no such id"}` — *this* is schema/data validation, not the corpus gate |

The design decisions worth recording:

1. **A tool result is the evidence record, not prose.** For the retrieval tools the result **is** the Day 7–9 trace record and the Day 10 `module#Symbol` list — same shape the harness grades since Day 7. That is what makes `cited ⊆ retrieved` still decidable: a citation points at a chunk, whether the chunk came from `/rag` or from inside a tool. The Day 6 invariant didn't move to a weaker place; it moved *home*, to where evidence is actually produced.
2. **The gate lives inside the tool.** Day 7's refusal was `/rag` answering "not covered" — a route outcome. Day 11's is a tool *returning* `{refused: true}` — a value a model can read. Same τ, same fused score, same honesty; a new signature. And the day must tell the model explicitly that a refusal is not an answer to restate.
3. **The data tools are honest stubs.** `get_product/order/customer` validate the `id` argument and return records from a small seeded set, each tagged `pending_resolver: "day14"` so no one mistakes fixture lookup for live Saleor data. Day 14 builds the real resolvers (via `schema.graphql` / the Saleor DB); Day 11 ships the *interface* they will fill. The interface — schema, `id` validation, result shape — is the deliverable; the data is the `PENDING` seam.
4. **`max_chars` rides in the schema.** The budget knob Day 6 built is now a model-expressible parameter. A 10-chunk result that exceeds `max_chars` is clamped before it enters the conversation — tool results are charged against the context budget like any evidence.
5. **The split-brain is in the description.** "Where is checkout completed" double-hitting the mutation and the service is loaded into `search_saleor_code`'s description, exactly as Day 10's fixtures encoded it. The knowledge Day 10 spent a fixture asserting is now something the model is told before it picks up the tool.

---

## 4. Gateway Tool Calling

The changes land in the three files Day 4 made, and nowhere else:

- **`ai_os/contract.py`** grows `ToolCall` (name, arguments-as-string, optional id); `Completion` carries `tool_calls: list[ToolCall] | None` when the model asks instead of types.
- **`ai_os/providers/openai.py`** adds the `tools` body field when present, parses `message.tool_calls` (including the JSON string inside `function.arguments`), and — the one tricky edit — treats `content == null` with `tool_calls` as a success. Everything else (headers, error taxonomy, `usage` normalization) is untouched.
- **`ai_os/providers/anthropic.py`** adds native `tools`, and recognizes a `tool_use` content block, mapping it into the same `ToolCall` shape; the agent-side never sees the dialect.
- **`ai_os/gateway.py`** gains `chat_with_tools(messages, tools, ...)`: `chat()` plus one argument passed straight through. Retries, backoff+jitter, `Retry-After`, ordered fallback, and the per-attempt ledger row all behave as on any chat call — because **a tool-turn chat call is a chat call**. Money does not learn a new shape on Day 11; it just sees a second turn.

The design bet is that the `tools` payload is an opaque argument, not a feature: the gateway never validates, inspects, or partially forwards it. That is Day 4's promise ("providers only know their own wire format") paying out the first real dividend — tool calling arrives as a pass-through, not a subsystem.

The stub is extended the same way it always is: `tools/mock_gateway.py` gets a scripted tool-call mode (good call / bad args / unknown tool / two calls / malformed arguments), and each scenario is a pushed button, not a real dependency.

The `PENDING` seam, written honestly: the stub proves *our* side of the wire mechanically; the real OpenAI/Anthropic tool response margins (streamed tool-call deltas, argument truncation, finite tool-call `id` formats) stay unverified until a key has quota — the same wall Day 6 hit with chat.

---

## 5. The Tool Registry — Validation Is a Contract

```python
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, dict] = {}          # name -> {schema, fn}

    def register(self, name, schema, fn): ...
    def schemas(self):                              # what the gateway sends the model
        return [{"type": "function", "function": s} for s in self._tools.values()]

    def validate_args(self, name, args_raw: str):   # Rule-1 JSON-Schema subset
        if name not in self._tools:
            return ToolResult.failure(f"unknown tool '{name}'")
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            return ToolResult.failure("malformed arguments: not valid JSON")
        missing = [p for p in schema.required if p not in args]
        if missing:
            return ToolResult.failure(f"missing required argument(s): {missing}")
        for k, v in args.items():                   # type / enum checks
            ...
        return ToolResult.ok(args)

    def execute(self, name, args): ...               # dispatch, budget, format
```

The contract has four rules, each enforced by the registry's own tests:

1. **Rejection is a result, not an exception.** Every failure — missing `query`, wrong `kind` enum, unknown key, malformed JSON, unknown tool name — is a `ToolResult.failure(...)` whose message names the offending field. Day 12's loop *needs* this: a self-correcting agent re-issues a call with the correct args when the rejection tells it what was wrong. A raised exception would kill the turn; a result keeps the conversation alive.
2. **Rejection is symmetric with refusal.** `{ok: false, error: "..."}` from validation and `{refused: true}` from the gate are two kinds of "no" at two levels, but both are normal messages. Day 7's error taxonomy is for *providers* (5xx, 401); Day 11's rejection shape is for *models* (you asked wrong, here is how).
3. **The result contract is mechanical.** Every retrieval-tool result respects `max_chars`, keeps `cited ⊆ retrieved`, and returns top-K evidence *or* `refused` — never a middle state. The registry's tests are the contract's tests; a tool that leaks a budget-violating result fails them before it can leak into an answer.
4. **The data stubs are tagged.** Seeded records carry `pending_resolver: "day14"`, so the trace can distinguish "fixture answer" from "real answer" the way Day 6's stub store distinguished synthetic from real vectors.

---

## 6. The Tool-Using Answer Path

```python
def run_tool_answer(question, registry, gateway, *, max_tool_turns=1):
    # turn 1 — the model proposes
    completion = gateway.chat_with_tools(
        [{"role": "user", "content": question}], tools=registry.schemas())
    if not completion.tool_calls:
        return final_answer(completion.text, turns=1)          # plain chat; trace says so

    # harness validates + executes
    result, ledger = [], []
    for tc in completion.tool_calls[:max_tool_turns]:
        vr = registry.validate_args(tc.name, tc.arguments)
        result.append(vr if not vr.ok else registry.execute(tc.name, vr.args))
        ledger.append((tc.name, tc.arguments, vr, result[-1]))

    # turn 2 — the model answers from the tool's evidence
    conversation = user(question) + assistant(tool_calls=completion.tool_calls) \
                 + tool_results(result) + explicit_note_if_refused
    final = gateway.chat(conversation)
    return final_answer(final.text, turns=2, tool_ledger=ledger)
```

Three decisions carry the day's honesty:

- **The answer contract is `cited ⊆ retrieved` on the tool's result set.** The final answer must reference chunks the tool actually returned (`module#Symbol` on Day 11); the trace verifies it the same way Day 7 did, because the result shape is the same. A tool-using agent whose answers cite nothing is a "tool-using" agent the metrics will not recognize.
- **Refusal is not restated, it is surfaced.** If `search_saleor_code` returns `{refused: true}` for the Shopify-webhook question, the second-turn prompt tells the model "every tool refused for out-of-corpus" and the gate's terse refusal is the answer. Inventing a plausible symbol to save the turn is the exact Day 8 warning, now enforceable because the trace can see the refusal in the ledger.
- **Single-turn cap, as a config.** `max_tool_turns = 1` is enforced in code; a model that emits a second `tool_calls` in its answer turn gets the observation (the results of round one) plus a trace row `loop_deferred: day12`. Termination discipline before the loop exists is what keeps "agent" honest tomorrow — the cap is Day 11's explicit boundary, and Day 12 is where it gets lifted.

Economics, written plainly: one question = **1 tool-turn chat + 1 answer chat + 1 embed** (the tool's retrieval embed). Tool *execution* is arithmetic over stored vectors — Day 9's "zero new embeds at rerank/context time" claim survives the tool layer because tools run retrieval, not generation. The second chat turn is the tool layer's permanent new bill; the ledger sees it like any call.

---

## 7. Test: The Tool Question Set

Expected facts written **before** the run, like every day since Day 7:

| ID | What it proves | Input (stub) | Expected |
|---|---|---|---|
| T1 | tool-selection, obvious | "Where is checkout handled?" | `search_saleor_code`; result cites `saleor/checkout/complete_checkout.py#complete_checkout` + `CheckoutComplete` (split-brain double-hit is *correct*) |
| T2 | tool-selection, by corpus | "What do the docs say about creating a checkout?" | `search_saleor_docs`; result ⊆ doc store |
| T3 | data tool | "Fetch order 1" | `get_order(id="1")`; valid args; seeded record returned |
| T4 | argument validity matrix | missing `query` / wrong `kind` / unknown key / malformed JSON | correct `ToolResult.failure(...)` naming the field — no crash |
| T5 | unknown tool | stub emits `get_invoice()` | registry rejection result |
| T6 | out-of-corpus refusal | "Debug the Shopify webhook handler" | `search_saleor_code` returns `refused: true`; final answer does not fabricate a symbol |
| T7 | single-turn cap | stub emits two `tool_calls` | observation returned; `loop_deferred: day12` in the trace |

The `MECH`/`PENDING` split is the same shape as Day 7's: T4, T5, T6-gate, and T7 are pure functions of stored schemas, stored stores, and scripted stub responses — deterministic, offline, green. T1–T3's *real-model* tool choice and argument spelling are `PENDING` under the stub: the stub proves **the plumbing can carry** a tool turn, and a real key proves **the model drives it** — Day 21's benchmark, on Day 11's harness.

Two obligations, unchanged in spirit:

1. **No regression on Days 7–10.** The retrieval tools call the same `retrieve()` the harness already grades, so the doc nine and the code fixtures must stay exactly as green. A tool layer that changes retrieval numbers is a bug wearing a feature — the tools are a new way to *ask*, not a new way to *answer*.
2. **Isolation discipline on any failure.** Right tool but result missing the symbol → retrieval owns it. Symbol in the result but wrong final answer → generation owns it (read the right evidence, lost it). Wrong tool → the **schema description** owns it — Day 11's new first suspect, which is why the description is treated as a testable artifact. Say which layer, don't shrug.

---

## 8. Measure: Tool Calls as a Contract

Rule-4 as a table, one row per question, every cell a real run's:

| Question | Expected tool | Tool chosen | Args valid | result ⊆ retrieved | Gate correct | Turns | cost_usd |
|---|---|---|---|---|---|---|---|
| "Where is checkout handled?" | `search_saleor_code` | ... | ... | ... | ... | 2 | ... |
| "What do the docs say about creating a checkout?" | `search_saleor_docs` | ... | ... | ... | ... | 2 | ... |
| "Fetch order 1" | `get_order` | ... | ... | — | — | 2 | ... |
| "Debug the Shopify webhook handler" | (refuse) | ... | ... | — | must be | 2 | ... |
| ... | | | | | | | |

The **`tool chosen` vs `expected tool`** column is Day 11's recall@1 for tool selection — the metric the loop will optimize from Day 12 onward. It is `MECH` under the stub (the stub's scripted calls are the "truth" the harness checks) and `PENDING` for a real model.

The cost shape, recorded twice so it cannot be misread: **per request, 3 bills** — tool-turn chat, answer chat, 1 embed — versus Days 6–10's 1 chat + 1 embed. The add is exactly the second chat turn. Meanwhile build-vs-run cost inherits Day 10's split unchanged: stores cost tens of thousands of build embeds; a run costs 1+1+1. The claim and the bill sit on the same page: Day 9's "zero new embeds at evidence time" is still true, and the new permanent expense is a chat turn, not an embedding.

The negatives column, with owners: a single tool turn cannot investigate (multi-step reasoning is Day 12's loop), the stubbed data tools return no real Saleor data (Day 14's resolvers), and the model-choice verdicts are unmeasured without a key (Day 21). Empty negatives would mean the day learned nothing; these have owners, not wishes.

---

## 9. Documents

- `docs/concepts/tool-calling.md` — schema as the model's interface, the tool-call conversation turn (OpenAI vs Anthropic dialect behind one gateway argument), validation-as-contract, refusal as a return value, single-turn cap.
- `docs/evaluations/tool-day11.md` — the §8 tables (tool-selection recall, argument-validity, refusal, no-regression lines) and the T1…T7 pre-registered fixtures.
- PROGRESS rollup: Day 11 mechanics move to `DONE` on the stub (tool wire, registry, validation, refusal-as-value, single-turn cap verified offline); the tool-selection semantic cells stay `PARTIAL` until a real gateway run confirms the model actually chooses and drives a tool.

---

## 10. Daily Learning Record

### Problem

The OS could answer but not act. Every previous day's deliverable was a string: `/rag` returned an answer, and the answer affected nothing — no retrieval call the user could audit, no symbol the model had decisively chosen. The problem is to let the model *propose* a tool call, have the harness validate and execute it, and make the answer's grounding auditable — without breaking the contract everything previous earned: same gateway, same evidence shape, same refusal honesty, same measured comparison.

### Concept

**A tool is the old seams made declarative, and validation is a contract with the model.** The schema (`name` + `description` + `parameters`) is the model's only knowledge of the world-action — the description is retrieval-for-a-chooser, with the same stakes as the metadata Days 8–9 wrote for a scorer. Function calling is a new conversation turn that the wires already understand (OpenAI `tool_calls` vs Anthropic `tool_use`), hidden behind the Day 4 gateway's pass-through argument — tool calling arrives as a kwarg, not a subsystem. Argument validation is two levels: **schema** (mechanical, hand-rolled JSON-Schema subset, and a rejection that is a message — the thing Day 12's loop will self-correct against) and **semantic** (the Day 7 refusal gate, now a tool's *return value* instead of a route outcome). The Day 6 invariant moves home: a tool result is the same evidence record the harness has graded since Day 7, so `cited ⊆ retrieved` stays decidable inside a tool call. And the day is bounded by a **single tool turn**, because termination discipline must exist before the loop does.

### Understanding

The unlock is that **the tool layer inherits everything and invents only the turn.** The retrieval seams from Days 8–10 became schemas; the filters (`kind`, `module`) became parameters; the gate became a return value; the gateway carried it all without learning a new security model (retries, backoff, fallback, ledger all unchanged — a tool-turn chat is a chat). The second understanding is about *what can go wrong silently*: the Day 6 "empty content is a retryable glitch" rule would have retried successful tool calls as failures, which makes the edit of an old honesty check the most dangerous change on the day — fixed by giving the rule a sibling, never by weakening it. The third, the honest one: the stub proves **our side of the wire** — schema, registry, validation, cap, refusal — and the real model's *choice* of tool and *spelling* of args is `PENDING` until a key has quota. Day 11 built the stage and the script; the actor's performance is Day 21's measure.

### Implementation

- `ai_os/providers/openai.py` (extended) — `tools` body field, parse `message.tool_calls`; empty-`content` **with** `tool_calls` is a valid tool-turn, not the Day 6 transient error (both cases tested independently).
- `ai_os/providers/anthropic.py` (extended) — native `tools`; `tool_use`/`tool_result` content blocks folded into the same `ToolCall` shape.
- `ai_os/contract.py` (extended) — `ToolCall`; `Completion.tool_calls`.
- `ai_os/gateway.py` (extended) — `chat_with_tools(messages, tools, ...)` = `chat()` + one pass-through argument.
- `ai_os/tools.py` (new) — `ToolRegistry` (register/schemas/validate_args/execute), the five tools (`search_saleor_docs`, `search_saleor_code`, `get_product/order/customer`), hand-rolled validator, `ToolResult` with `ok`/`failure`/`refused` shapes.
- `ai_os/agent.py` (new) — `run_tool_answer`: two-turn orchestration, single-turn cap (`max_tool_turns`), refusal surfacing, trace fields (`tool_ledger`, `loop_deferred`).
- `tools/mock_gateway.py` (extended) — scripted `tool_calls` scenarios (good / bad args / unknown tool / two calls / malformed JSON).
- `tools/tool_smoke.py` (new) — offline suite for T1–T7 (mechanical half).
- `docs/concepts/tool-calling.md`, `docs/evaluations/tool-day11.md`.

### Failure

1. **The schema-description failure (the new Family C).** Two tools, similar descriptions → the model picks the wrong corpus (docs vs code). The description is a testable artifact because tool choice is recall@1: rewrite the description like you'd rewrite a chunk heading, and re-run.
2. **The empty-content retry trap.** Content legitimately null on a tool call; an unextended Day 6 check retries successful calls into the budget vault. The two cases (`null`+`tool_calls` vs `null` only) are separate test rows so "fixing" one never re-breaks the other.
3. **Validation-downgrade reflex.** On a failing arg it is tempting to coerce (cast `"kind"` to lowercase, default missing `k`) to "get the answer". Every coercion is a silent contract violation: the rejection must name the field and stay a message — coercion is what makes Day 12's self-correction impossible.
4. **Refusal-as-answer.** The model restates an out-of-corpus refusal as if its own reasoning ("Saleor does not appear to have a Shopify handler") — which looks helpful and is exactly the Day 8 fabrication wearing a hedge. The trace's refusal cell is the guard: a refused tool whose answer pretends to answer is a logged failure.
5. **The trench of the second turn.** Two chat calls means twice the latency and tokens gone to context-fuelling; the `max_chars` clamp and `max_tool_turns` cap are the budget floor applied to the conversation, not just the retrieval.

### Debugging

Everything Day 11 is `MECH` under the stub: schema validation is pure string/map arithmetic; the registry's rejection messages are deterministic; `cited ⊆ retrieved` on a tool result is set membership; the cap is a counter; the refusal is the gate function from Day 7 reused. The debug loop is Day 7's, with one new first suspect: *wrong tool → the description owns it*. T1 returns `search_saleor_docs`? Read the two descriptions through the model's eyes — identical wording is the bug, and the description is editable. Right tool, missing symbol → retrieval (Day 8's job). Symbol present, wrong answer → generation (Day 21's job). Right call happens at rank 2 instead of 1 → the model waffled between corpora; the description or the `kind` enum needs a sharper edge. The trace says which, never the vibes.

### Evaluation

The §8 tables in `docs/evaluations/tool-day11.md`: tool-selection recall@1 (stub-truth now, real-model later), argument-validity rate (how many calls survived validation), refusal correctness (T6 must not restate), no-regression lines (doc nine + code fixtures still green through tools), and the 3-bill cost row per run. The numbers a future observer can carry: whether the tool layer moved any retrieval number (it must not), how often rejection named the field correctly, and whether the single-turn cap never leaked a second execution. The `PENDING` line, plainly: whether a real model chooses `search_saleor_code` over `search_saleor_docs` and spells `kind` right is Day 21's judgment on Day 11's plumbing.

### Architecture

The claim of the day is an architectural repeat of Day 10's: **the system survived a capability, not just a corpus change — and stayed small.** The additions are a pass-through argument on the Day 4 gateway, a registry that owns schema + validation + execution, and a two-turn orchestrator; the evidence side (retriever, rerank, MMR, dedup, gate) is *called*, never modified. Tools are the seams made declarative — which is exactly what makes them composable on Day 12: the loop is just `run_tool_answer` called repeatedly with the observation appended, and the only thing to lift is `max_tool_turns`. Day 11's tools are also Day 30's scenarios standing on the same piles: `search_saleor_code` owns scenario 2 ("where is this implemented"), `get_order` + `search_saleor_code` own scenario 4 (customer investigation) and scenario 5 ("why is this test failing"), and the doc tool owns scenario 1. **Day 11 added an action layer, not a subsystem; the pass-through is what bought that.**

### Questions

- Is a *single* tool turn (one skeleton key) the right amount of agency, or does the cap hide a real failure to compose tools that Day 12 will only discover worse?
- When the description is doing retrieval work, what does "write the description for the chooser" cost relative to its payoff — and when does the description start needing Day 8's rewrite discipline?
- Does the second chat turn break the "1 embed + 1 chat" cost story the harness records, and should the tool layer bill tool *execution* too (it measures real ms)?
- Which of the five tools is hardest for a real model to argue against — and does the schema's `enum` surface enough? (Family C, wearing a `kind` field.)
- When T6 refuses correctly under the stub, does the *real* model's restatement survive the trace's refusal cell — or does the fix live in the prompt? Day 21 check.

### Tomorrow

Day 12 — Agent Loop: the single turn becomes a loop. User request → reason → choose → execute → observe → reason again → final answer. Day 11's `run_tool_answer` generalizes by lifting `max_tool_turns` and appending observations instead of terminating; the registry and the stub are exercised by the loop as-is, and Day 11's tool-selection recall@1 becomes the loop's per-step telemetry. Day 13's failure modes (wrong tool, invalid args, infinite loops, contradictory results, hallucinated tools) are the loop's debt, maturing on schedules Day 11 refused to start early. Whatever the single turn could not investigate — multi-step investigation, real data — goes into Day 12's and Day 14's backlogs, with the tools ready and the harness grading.