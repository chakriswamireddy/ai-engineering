# Concept: A Tool Is the Old Seams Made Declarative

> Day 11 of the AI-layer apprenticeship. Days 6–10 built retrievers that answer
> *questions* — but the answer still arrived as one string from `/rag`, and the
> OS affected nothing. Day 11 moves from "answering" to "doing": the corpus
> questions become **calls** (`search_saleor_docs()`, `search_saleor_code()`),
> the data half gets its first tools (`get_product/order/customer()`), and the
> model learns to ask for them through the Day-4 gateway. The **schema** is a new
> kind of interface (metadata written for a *chooser*, not a scorer), the **tool
> result is evidence in the same shape the harness has graded since Day 7**, and
> the **refusal gate** stops being a route outcome and becomes a return value.
> Implementation: `ai_os/tools.py`, `ai_os/agent.py`, `ai_os/contract.py`
> (`ToolCall`), the two providers' tool wire, `ai_os/gateway.py`
> (`chat_with_tools`).

## Problem

Everything an LLM agent does rests on one asymmetry: **the model can only know a
tool through the schema it is handed.** That schema is not API documentation; it
is metadata about the world written for a chooser. Days 8–9 learned this lesson
on the retrieval side — a chunk's `heading` decides rank-1 — and Day 11 pays it
out on the action side, where the stakes are a *tool selection* instead of a
*retrieval rank*.

Three things change at once, and each breaks an assumption an earlier day made:

| Earlier assumption | What tool calling changes | Consequence |
|---|---|---|
| The model returns a sentence | The model may return `tool_calls` instead (or beside) text | a new conversation turn, in both wire dialects |
| An empty 200 means "transient glitch, retry" (Day 6) | A tool turn legitimately has `content: null` | the Day-6 check must gain a sibling, never be weakened |
| Refusal is a *route* outcome (`/rag` says "not covered") | The gate now runs where retrieval runs | refusal becomes a **return value** with the same honesty |

The deepest one is the first. The Day-4 gateway promised that "providers only
know their own wire format" — Day 11 is where that promise is cashed: tool
calling arrives as **one opaque argument passed straight through** retries,
backoff, `Retry-After`, ordered fallback, and the per-attempt ledger row,
because a tool-turn chat call *is* a chat call.

## Schema: the model's interface

```json
{
  "type": "function",
  "function": {
    "name": "search_saleor_code",
    "description": "Search Saleor's *source code* and return top-K module#Symbol
                    records … A query about checkout legitimately hits BOTH the
                    GraphQL mutation (saleor/graphql/checkout/…) and the domain
                    service (saleor/checkout/…) — a cross-layer double-hit is
                    correct.",
    "parameters": {
      "type": "object",
      "properties": {
        "query":  {"type": "string"},
        "kind":   {"type": "string", "enum": ["function", "class", "mutation", "test", "module"]},
        "module": {"type": "string"},
        "k":      {"type": "integer", "minimum": 1, "maximum": 10, "default": 4}
      },
      "required": ["query"]
    }
  }
}
```

| Schema field | The Days 8–10 thing it is | Lesson |
|---|---|---|
| `name` | the tool's identifier; the model "calls" exactly this | names must stay stable — Day 12's loop *depends* on them |
| `description` | a chunk `heading` | the description is **retrieval for the model**: bury "returns `module#Symbol` evidence" and tool selection slips exactly like a bad heading slips rank-1 |
| `parameters` (+`required`) | the retrieval knobs (`kind`, `module`, `k`, `max_chars`) | every knob Day 8 built as a filter and Day 10 as metadata is now something *the model can express* |

Day 10's split-brain study became a **sentence in the description**. That is the
point of writing metadata for a chooser rather than for a human: the lesson the
corpus spent a fixture asserting is loaded into the tool so the model does not
rediscover it.

## The tool-call turn, in two dialects

```text
user:      "Where is checkout handled?"
assistant: {content: null, tool_calls: [{id: "call_ab12",
              function: {name: "search_saleor_code",
                         arguments: "{\"query\": \"where is checkout completed\", \"k\": 4}"}}]}
tool:      {role: "tool", tool_call_id: "call_ab12",
            content: "[{doc_id: saleor/checkout/complete_checkout.py#complete_checkout, score: 0.83, …}]"}
assistant: "Checkout is completed by complete_checkout (saleor/checkout/complete_checkout.py#complete_checkout)…"
```

| | OpenAI | Anthropic |
|---|---|---|
| request | `tools: [{type:"function", function:{name, description, parameters}}]` | `tools: [{name, description, input_schema}]` |
| model asks | `message.tool_calls[]` (`function.arguments` a JSON **string**) | `content` block type `tool_use` (`input` an object) |
| answer back | `role:"tool"` + `tool_call_id` | a **`user` message** holding a `tool_result` block (no `tool` role exists) |

The provider owns the dialect; the gateway and the agent own neither. Both
normalize into one `ToolCall` (`name`, `arguments`-as-string, `id`), which is
why `run_tool_answer` has no `if provider == …` anywhere.

### The regression hazard hiding in Day 6

`openai.py` raised a *retryable* error on a 200 with empty content — added
because throttled OpenAI-compatible endpoints return `200-with-nothing`. But a
model that calls a tool legitimately returns `content: null`. Left alone, the
gateway would retry a **successful** tool call as a glitch and burn budget while
the real call sat on the floor.

The fix is not to weaken Day 6's check; it is to give it a **sibling**:

```python
text = message.get("content") or ""
tool_calls = _tool_calls_from_message(message)
if not text.strip() and not tool_calls:      # null + no calls  → the glitch
    raise ProviderError(..., retryable=True)  # null + tool_calls → a valid turn
```

The two cases are separate rows in the suite (`empty` vs `tc_code`) so that
"fixing" one can never silently re-break the other. Editing a previous day's
honesty check is exactly the edit that re-breaks a corner.

## Validation is a contract (two levels of "no")

1. **Schema validation** — "do the arguments match the parameters?" Mechanical:
   missing `query`, an `enum` violation on `kind`, an unknown key, malformed
   JSON, an unknown tool name. Hand-rolled (Rule-1: this is a ten-line JSON-Schema
   subset, not a library).
2. **Semantic validation** — "is this question answerable from the corpus?" This
   is Day 7's `should_refuse`, now executed *inside* the tool.

Both failures are **results, not exceptions**, and both are *symmetric*:

```text
{ok: false, error: "missing required argument(s): ['query']"}     # you asked wrong
{ok: true,  refused: true, reason: "no defensible match…"}        # the corpus can't
```

That is what Day 12's loop self-corrects against. A raised exception would kill
the turn; a message keeps the conversation alive and tells the model what to fix.
The cardinal sin is the **validation-downgrade reflex** — casting a bad `kind`
or defaulting a missing `k` to "get the answer". Every coercion is a silent
contract violation that makes self-correction impossible.

## The five tools, and what a result *is*

| Tool | Reads from | Result when ok | When the gate fails |
|---|---|---|---|
| `search_saleor_docs(query, k=4, max_chars=None)` | `data/store.json` | top-K chunks (`doc_id`/`heading`/`score`/`position`/`text`), budget-clamped | `{refused: true}` |
| `search_saleor_code(query, kind=None, module=None, k=4)` | `data/code_store.json` | top-K `module#Symbol` records + line range + body | `{refused: true}` |
| `get_product(id)`, `get_order(id)`, `get_customer(id)` | seeded fixtures | the record, tagged `pending_resolver: "day14"` | `{ok: false, error: "no such id: …"}` — *data* validation, not the corpus gate |

**The result is the evidence record, not prose.** For the retrieval tools it is
the same top-K shape the harness has graded since Day 7, so `cited ⊆ retrieved`
stays plain set membership — the Day-6 invariant did not move to a weaker place,
it moved **home**, to where evidence is actually produced. `max_chars` rides in
the schema (Day 6's budget knob, now model-expressible), and rank priority wins
when clamping: a later record that overflows is dropped, never reordered past an
earlier one, and the top record is never dropped.

The data tools are **honest stubs**. Day 11 ships the *interface* Day 14 will
fill — schema, `id` validation, result shape — and every fixture says so with
`pending_resolver: "day14"`, so no one mistakes a fixture lookup for live Saleor.

## The tool-using answer path: two turns, one cap, one contract

```python
def run_tool_answer(question, registry, gateway, *, max_tool_turns=1):
    turn1 = gateway.chat_with_tools([user(question)], tools=registry.schemas())
    if not turn1.tool_calls:
        return final(turn1.text, turns=1)          # plain chat; the trace says so
    executed = turn1.tool_calls[:max_tool_turns]
    results  = [registry.run(tc.name, tc.arguments) for tc in executed]   # never raises
    turn2 = gateway.chat(user(question) + assistant(executed) + tool_results(results)
                         + (note_if_all_refused))
    return final(turn2.text, turns=2, ...)
```

Three decisions carry the day's honesty:

- **The answer contract is `cited ⊆ retrieved` on the tool's result set.** Every
  `module#Symbol` / `doc_id` the final answer names must be in the evidence the
  tool returned; an id that appears in the answer and *not* in the evidence is a
  **fabrication**, and the trace says so (`fabricated`, `cited_subset_retrieved`).
  This is Day 8's warning made enforceable — the evidence is in the ledger now.
- **A refusal is surfaced, never restated.** When every tool refused, the second
  turn is *told* so (a system note carrying the gate's terse refusal). An answer
  that quietly pretends to answer anyway is flagged (`refusal_restated`) rather
  than believed — the Day-8 fabrication wearing a hedge.
- **The single-turn cap is a config, not a constant.** `max_tool_turns = 1`
  keeps "agent" honest until Day 12; a model that emits more calls gets the
  round-one observation plus a `loop_deferred: day12` trace row. The deferred
  calls are kept (not silently dropped) so Day 12 can inherit them.

## Cost shape

| Axis | Days 6–10 | Day 11 |
|---|---|---|
| a question costs | 1 chat + 1 embed | **1 tool-turn chat + 1 answer chat + 1 embed** |
| tool execution | — | **0 chats** (retrieval is arithmetic over stored vectors) |
| build cost | stores cost builds | unchanged — the tools read the same stores |

Day 9's "zero new embeds at evidence time" survives the tool layer, because
tools run **retrieval**, not generation. The new permanent expense is exactly the
second chat turn, and money learns no new shape: the tool-turn chat is a chat
call, so it bills through the same ledger row as any other.

## What a single turn cannot do

The negatives, with owners: a single tool turn cannot **investigate** (multi-step
reasoning is **Day 12**'s loop, and the `max_tool_turns` knob is the seam it
lifts); the stubbed data tools return **no real Saleor data** (**Day 14**'s
resolvers); whether a *real* model chooses the right tool and spells the right
args is **Day 21**'s benchmark on this plumbing. Day 11 builds the stage and the
script; the actor's performance is measured later.

## Re-verify, in one line

```bash
python tools/tool_smoke.py     # T1–T7 + dialect parity + Day-6 sibling + no-regression
```

`PENDING`: the real providers' tool-response margins (streamed tool-call deltas,
`arguments` truncation, finite `id` formats) and the model's own tool choices —
proved the day a key has quota, judged by Day 21.
