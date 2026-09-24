# Tool Calling Day 11 — Tool Calls as a Contract

> Generated from the run of `python tools/tool_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire, the in-process Day-7 doc corpus / Day-10
> 14-symbol code corpus for *retrieval*). `MECH` = provable offline, here.
> `PENDING` = a real gateway is required (whether a model *chooses* the right
> tool, *spells* the right args, and *uses* the result — Day 21).

`stores: 9 doc chunks (STUB) / 14 code symbols (STUB)`
`τ_doc=0.05  τ_code=0.75 (stub demo values, not calibrations)`
`k=4  max_tool_turns=1`

## The claim, pre-registered before the run (§7)

| ID | What it proves | Input (stub) | Expected |
|---|---|---|---|
| T1 | tool selection, obvious | "Where is checkout handled?" | `search_saleor_code`; evidence cites `complete_checkout` **and** `CheckoutComplete` (the split-brain double-hit is *correct*) |
| T2 | tool selection, by corpus | "What do the docs say about creating a checkout?" | `search_saleor_docs`; result ⊆ the doc store |
| T3 | data tool | "Fetch order 1" | `get_order(id="1")`; valid args; seeded record |
| T4 | argument validity matrix | missing `query` / wrong `kind` / unknown key / malformed JSON | correct `ToolResult.failure(...)` naming the field — no crash |
| T5 | unknown tool | stub emits `get_invoice()` | registry rejection result |
| T6 | out-of-corpus refusal | "Debug the Shopify webhook handler" | gate returns `refused`; no symbol fabricated; a restated refusal is *flagged* |
| T7 | single-turn cap | stub emits two `tool_calls` | one executed; `loop_deferred: day12` recorded |

## §8 rule-4 table (every cell a real run's)

| Question | Expected tool | Tool chosen | Args valid | result ⊆ retrieved | Gate | Turns | cost_usd |
|---|---|---|---|---|---|---|---|
| "Where is checkout handled?" | `search_saleor_code` | `search_saleor_code` | ok | ok (2× `CheckoutComplete` + `complete_checkout`) | pass | 2 | 0.00003060 |
| "What do the docs say about creating a checkout?" | `search_saleor_docs` | `search_saleor_docs` | ok | ok | pass | 2 | 0.00002280 |
| "Fetch order 1" | `get_order` | `get_order` | ok | — (data record, `pending_resolver: day14`) | — | 2 | 0.00002280 |
| `<tc_bad_args>` | `search_saleor_code` | `search_saleor_code` | **rejected** (`missing required argument(s): ['query']`) | — | — | 2 | 0.00002280 |
| `<tc_bad_kind>` | `search_saleor_code` | `search_saleor_code` | **rejected** (`'kind' must be one of [...]`) | — | — | 2 | 0.00002280 |
| `<tc_unknown_key>` | `search_saleor_code` | `search_saleor_code` | **rejected** (`unknown argument(s): ['colour']`) | — | — | 2 | 0.00002280 |
| `<tc_malformed>` | `search_saleor_code` | `search_saleor_code` | **rejected** (`malformed arguments: not valid JSON`) | — | — | 2 | 0.00002280 |
| `<get_invoice>` | `get_invoice` | `get_invoice` | **rejected** (`unknown tool 'get_invoice'`) | — | — | 2 | 0.00002280 |
| "Debug the Shopify webhook handler" | (refuse) | `search_saleor_code` | ok | **∅ — nothing manufactured** | **refused** | 2 | 0.00003060 |
| "Where is checkout handled?" (fabricated answer injected) | `search_saleor_code` | `search_saleor_code` | ok | ok | pass | 2 | 0.00003060 |
| `<two tool_calls>` | `search_saleor_code` | `search_saleor_code` | ok | ok | pass | 2 | 0.00002280 |

The **`tool chosen` vs `expected tool`** column is Day 11's recall@1 for tool
selection — stub-truth here (the stub's scripted call *is* the "truth" the
harness checks), a real model's verdict Day 21.

## What the run actually produced

**T1 — tool selection, obvious.** `search_saleor_code` chosen; the evidence
carries **both** halves of Saleor's split-brain
(`saleor/checkout/complete_checkout.py#complete_checkout` and
`saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete`) plus
the mutation's `perform_mutation` method and the real test — the double-hit is
correct, and every id is inside the code store (`cited ⊆ retrieved`).

**T2 — selection by corpus.** `search_saleor_docs` chosen; every returned
`doc_id` is a doc-store id (the code corpus was never consulted).

**T3 — the data tool.** `get_order(id="1")` validated and returned the seeded
record (`number: 1001`, `status: UNFULFILLED`) with `pending_resolver: "day14"`
on it, so a fixture lookup can never be mistaken for live Saleor data.

**T4 — the argument matrix.** All four wire-level violations produce a
`ToolResult.failure` whose message **names the field**, and the turn still
completes (2 turns, no exception). The malformed-JSON case is an OpenAI-wire
artifact (Anthropic sends an object, not a string), so the validator is *also*
exercised directly — malformed JSON, a wrong type (`query: 5`), and an unknown
key are each rejected by name.

**T5 — unknown tool.** `get_invoice` is a rejection result naming the tool and
listing the known ones; never a forged run, never a crash.

**T6 — refusal, twice over.** The out-of-corpus call is `refused` with **empty
evidence** (nothing manufactured), and the scripted answer that *restates* the
refusal ("Saleor does not appear to have…") is **flagged** (`refusal_restated`)
rather than believed. The second half injects a fabricated symbol
(`saleor/ghost/checkout.py#CompleteCheckoutMagic`) beside grounded evidence: the
harness catches it (`cited_subset_retrieved = False`,
`fabricated = ['saleor/ghost/checkout.py#CompleteCheckoutMagic']`). Both
directions of the answer contract are proved.

**T7 — the cap.** Two `tool_calls` in one turn → exactly **one** executed, the
second preserved as `deferred_tool_calls`, and `loop_deferred: day12` in the
trace. Termination discipline exists before the loop does.

**Dialect parity.** The same turn replayed over Anthropic's wire: `tool_use`
folds into the identical `ToolCall` (`arguments` normalized from `input` to a
JSON string) and the `tool_result` block carries the evidence back — the agent
contains no `if provider == …` branch.

**The Day-6 empty-content sibling, as two independent rows.** `content: null`
with **no** `tool_calls` is still the retryable glitch (2 attempts, then the
answer); `content: null` **with** `tool_calls` is a valid tool turn (**1**
attempt, no retry, empty text). Neither row can be "fixed" in a way that
silently re-breaks the other.

## No-regression lines (Days 7–10)

| Line | Result |
|---|---|
| the doc nine through the tool layer | **9/9 green**, Σ ΔMRR(D9−D8) **+1.000** — unchanged |
| the code six through the tool layer | **6/6 green** |
| the code tool's evidence | every id ⊆ the code store, ranked from `rank: 1` |
| the doc tool's evidence | every id ⊆ the doc store |

The tools call the **same** `Retriever.retrieve()` the harness grades, so the
tool layer is a new way to *ask*, not a new way to *answer*. A tool layer that
moved a retrieval number would be a bug wearing a feature.

## Cost shape (build vs run)

| Axis | Days 6–10 | Day 11 |
|---|---|---|
| a question costs | 1 chat + 1 embed | **1 tool-turn chat + 1 answer chat + 1 embed** (3 bills) |
| tool *execution* | — | **0 chats, 0 embeds** — arithmetic over stored vectors |
| build cost | stores cost builds | unchanged (the tools read the same two stores) |
| added ledger lines | — | **1** (the second chat turn), priced like any chat |

Asserted, not told: a tool answer bills exactly 2 successful chats + 1 embed,
and the retrieval gateway records **zero** chat calls during execution.

## Config knobs

| Env | Default | Meaning |
|---|---|---|
| `TOOL_GATE_TAU` | `0.55` | the fused-score gate behind `search_saleor_docs`/`_code` (per-corpus override via `doc_tau`/`code_tau`) |
| `TOOL_TOP_K` | `4` | default `k` when the model does not say |
| `TOOL_MAX_TURNS` | `1` | Day 11's single-turn cap — the one knob Day 12 turns |

Tool-result char budgets are module defaults (`DEFAULT_DOC_BUDGET = 1200`,
`DEFAULT_CODE_BUDGET = 4000`); `search_saleor_docs` additionally exposes
`max_chars` to the model.

## Negatives (honest, with owners)

1. **The stub's τ values are demo choices, not calibrations.** `τ_code=0.75` is
   picked only to exercise the refusal path; the day-10 run already recorded that
   the stub's answerable code top-1s (0.636–0.863) *overlap* CE1's, so no single
   τ separates E — the refusal signal must move semantic. Owner: **Day 21**.
2. **The stub scripts the tool choice.** T1/T2/T3 prove the *plumbing carries* a
   tool turn; whether a real model picks the right tool, spells `kind`
   correctly, and uses the evidence is unmeasured without a key. Owner: **Day 21**.
3. **Real provider margins are unverified.** Streamed tool-call deltas,
   `arguments` truncation and finite `id` formats differ across
   OpenAI-compatible endpoints; the stub proves *our* side of the wire. Owner:
   the day a key has quota.
4. **The data tools return fixtures, not Saleor.** `get_product/order/customer`
   are interface + validation + result shape, tagged `pending_resolver: "day14"`.
   Owner: **Day 14**.
5. **A single turn cannot investigate.** Multi-step reasoning, retries, and
   self-correction against a rejection are the loop's job. Owner: **Day 12**
   (the `max_tool_turns` knob and the `loop_deferred` backlog are its entry
   points).
6. **The fabrication guard is only as good as its regex.** Ids are recognised by
   shape (`…/path.py#Symbol`, `path/topic#heading`); an answer that refers to
   evidence in prose ("the checkout module") is not checked, because there is no
   id to check. Recorded, not hidden.

## Re-verify, in one line

```bash
python tools/tool_smoke.py     # T1–T7, dialect parity, Day-6 sibling, no-regression
```

`PENDING`: whether a real model *drives* the tools — Day 21's benchmark on this
harness.
