# Agent Loop Day 12 — The Loop as a Contract

> Generated from the run of `python tools/agent_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire, scripted turn-by-turn so the loop reads the
> script at iteration N, and the in-process Day-7 doc corpus / Day-10 14-symbol
> code corpus for *retrieval*). `MECH` = provable offline, here. `PENDING` = a real
> gateway is required. The real-model reading is now **measured** too — see the LIVE
> section: the free model composes once the loop carries a reasoning prompt, and the
> *strength* of that composition across models is Day 21's.

`stores: 9 doc chunks (STUB) / 14 code symbols (STUB)`
`registry: search_saleor_docs, search_saleor_code, get_product, get_order, get_customer`
`max_steps=6 (default)  budget_chars=4000  self_correct=2  k=4`

## The claim, pre-registered before the run (§7)

| ID | What it proves | Input (stub) | Expected |
|---|---|---|---|
| A1 | multi-step investigation | "What happens after CheckoutComplete submits?" | ≥2 `search_saleor_code` steps following `imports`; final answer cites symbols from ≥2 observations |
| A2 | self-correction | step 1 emits bad args (`kind="mutations"`) | step 2 re-issues with fixed args; trace shows 1 rejection + 1 success |
| A3 | pivot-after-refusal | code tool refuses a docs question | step 2 uses `search_saleor_docs`; no fabricated symbol |
| A4 | parallel calls | stub emits two `tool_calls` one turn | both execute, both observed, both cited or acknowledged |
| A5 | termination on `max_steps` | stub keeps asking past the cap | `truncated: true`, `termination_reason: max_steps`, answer names the limit |
| A5b | termination on budget | `budget_usd=1e-9` | exit 3: `termination_reason: budget`, surfaced |
| A6 | grounding across steps | final answer's citations | `cited ⊆ ∪(step observations)` — the multi-turn invariant |
| A7 | zero-tool question | "Summarize checkout in one sentence" | first answer turn has no `tool_calls`; `tool_steps: 0` |
| A8 | parallel capacity guard | three tool calls in one step | all validated/executed/observed within budget; no dropped observations |
| — | self-correction bound | `self_correct=1`, repeat the bad call | `gave_up`, answer names the stuck point |

## §8 per-step table (every cell a real run's)

| Step | Expected action | Tool chosen | Args valid | result_ok | Gate | Cum. steps | Run cost |
|---|---|---|---|---|---|---|---|
| A1·1 | `search_saleor_code` | `search_saleor_code` | ok | ok | pass | 1 | $0.00003720 |
| A1·2 | `search_saleor_code` | `search_saleor_code` | ok | ok | pass | 2 | $0.00003720 |

The **`expected action` vs `tool chosen`** column is Day 11's recall@1 **harvested
per step** — the metric the loop optimizes from Day 13 on, and the one column whose
real-model cells stay `PENDING` under the stub (the stub's scripted turns are the
"truth" the harness checks).

## §8 per-run table (every cell a real run's)

| Run | Expected | Termination | Steps | Turns | Refusals | Rejections | Truncated | Cost |
|---|---|---|---|---|---|---|---|---|
| A1 | ≥2 `search_saleor_code` steps; cite ≥2 observations | `answer` | 2 | 3 | 0 | 0 | False | $0.00003720 |
| A2 | 1 rejection + 1 fixed call, then answer | `answer` | 2 | 3 | 0 | 1 | False | $0.00003720 |
| A3 | step 1 refused → step 2 pivots to docs | `answer` | 2 | 3 | 1 | 0 | False | $0.00003720 |
| A4 | two calls, one step, both observed | `answer` | 1 | 2 | 0 | 0 | False | $0.00003060 |
| A5 | `max_steps=3` → truncated, named | `max_steps` | 3 | 4 | 0 | 0 | True | $0.00005040 |
| A5b | budget cap → exit 3 | `budget` | 1 | 2 | 0 | 0 | True | $0.00003060 |
| A6 | `cited ⊆ ∪(step observations)` | `answer` | 2 | 3 | 0 | 0 | False | $0.00003720 |
| A7 | no `tool_calls` → `tool_steps: 0` | `answer` | 0 | 1 | 0 | 0 | False | $0.00001620 |
| A8 | three calls, one step, all observed | `answer` | 1 | 2 | 0 | 0 | False | $0.00003060 |
| gave_up | `self_correct=1` → `gave_up` | `gave_up` | 2 | 3 | 0 | 2 | True | $0.00003720 |
| depth1 | `max_steps=1` → Day 11 exactly | `answer` | 1 | 2 | 0 | 0 | False | $0.00003060 |

Termination reasons:
- `answer` — exit 1 — the model answered from the evidence it had (no `tool_calls`)
- `max_steps` — exit 2 — the step cap fired; why it never converged is Day 13's taxonomy
- `budget` — exit 3 — the run's cost budget was spent; the answer is limited to already-gathered evidence
- `gave_up` — the self-correction bound was reached; the loop stopped naming the stuck point

## What the run actually produced

**A1 — multi-step investigation (walking Day 10's map).** Two `search_saleor_code`
steps; the natural exit fired. Step 1's evidence is the mutation
`saleor/graphql/checkout/mutations/checkout_complete.py#CheckoutComplete`; step 2's
query is **`{"query": "complete_checkout", "kind": "function", "module":
"saleor/checkout", "k": 4}`** — the symbol step 1's evidence carried, so step 2 is
*caused by* step 1, not scripted blind. Step 2's evidence is a *different*
observation (`…complete_checkout.py#complete_checkout` +
`…complete_checkout.py#create_order_from_checkout`), every id ⊆ the code store. The
final answer cites from both steps
(`step1.cited=['…checkout_complete.py#CheckoutComplete']`,
`step2.cited=['…complete_checkout.py#complete_checkout']`), `cited ⊆ ∪ observations`
holds, and `turns == tool_steps + 1`. The map is real: the mutation's evidence text
itself names `complete_checkout` — Day 10 recorded it, Day 12 walked it.

**A2 — self-correction.** Step 1 is rejected with a message naming the field:
`argument 'kind' must be one of ['function', 'class', 'mutation', 'test', 'module'],
got 'mutations'`. Step 2 re-issues a *different* call (`kind` dropped, query
sharpened) and succeeds, returning four `module#Symbol` records. The trace counts
exactly **1 rejection + 1 success**, and the loop answered after the fix — the
re-issue is a new step, not a louder repeat.

**A3 — pivot after refusal.** Step 1 (`search_saleor_code`) refused and manufactured
nothing (empty evidence). Step 2 legally pivots to `search_saleor_docs` — not the
refused call again — and its evidence is entirely ⊆ the doc store. The refusal is
recorded (`observed_refusals == 1`), not retried, and no symbol is fabricated; the
answer grounds on `quickstart/api#create-a-checkout` and
`developer/checkout/api-guide#completing-checkout`.

**A4 — parallel calls.** One step carried two `tool_calls`
(`search_saleor_code` + `get_order`); both executed, both names preserved in the
step row, and the run stays grounded — the second call is never dropped to "save
tokens".

**A5 — the cap, and its budget sibling.** `max_steps=3` runs exactly three tool
steps, terminates with `termination_reason: max_steps`, `truncated: true`, and a
forced final turn (`turns == 4`) whose answer names the limit ("I reached the step
limit before finishing…"). Nothing is fabricated. The over-asking turn at the cap
is *recorded, not executed* (`capped_tool_calls`): the model kept asking past step
3, and the harness records that call without a fourth observation — which is why a
capped run costs one chat more than the pre-fix bound. With `budget_usd=1e-9`, the
run terminates with exit 3 (`budget`) after gathering at least one step of evidence,
and the answer surfaces the budget stop rather than a silent confident non-answer.

**A6 — grounding over the transcript.** On the positive run every citation lies in
the union of the step observations. On the negative run the scripted answer names
`saleor/ghost/checkout.py#CompleteCheckoutMagic`, which no observation contains →
`cited_subset_retrieved = False`, `fabricated =
['saleor/ghost/checkout.py#CompleteCheckoutMagic']`. The multi-turn variant of the
Day 7 invariant is proved in both directions.

**A7 — zero-tool question.** "Summarize checkout in one sentence." → no `tool_calls`
at all, `tool_steps: 0`, one turn, `no_tool_call: true`, nothing fabricated against
an empty observation set. The loop can also *not* loop.

**A8 — parallel capacity.** Three `tool_calls` in one step
(`search_saleor_code`, `get_order`, `search_saleor_docs`): all three validated,
executed, and observed; the flat `tool_ledger` keeps all three; records from all
three tools enter one transcript (mixed corpora). The cheapest bug — execute the
first, summarize the rest — is asserted away.

**The self-correction bound (`gave_up`).** `self_correct=1` with the bad call
repeated → rejections exceed the bound, `gave_up: true`, and the answer names the
stuck point ("I could not fix the rejected call within the self-correction…")
without inventing a fix.

## No-regression line (Day 11)

| Line | Result |
|---|---|
| `tools/tool_smoke.py` run unchanged in a subprocess | **exits 0 — `ALL DAY 11 (MECH) CHECKS PASSED`** |
| T1…T7 through `run_agent(max_steps=1)` | green as-is — a loop that changed a single-tool answer would be a bug wearing an agent |
| Days 7–10 fixtures behind the tools | ride inside that suite (doc nine 9/9, code six 6/6) |

The loop added a driver, not a new retrieval path.

## Cost shape (asserted, not told)

| Axis | Day 11 | Day 12 (A1 run) |
|---|---|---|
| chats | 2 | **3** = `tool_steps` (2) + 1 answer turn |
| embeds | 1 | **2** = one per retrieval step (tool execution is free arithmetic) |
| retrieval-gateway chat calls during execution | 0 | **0** |
| run cost | $0.00003060 | **$0.00003720** |
| worst case | fixed 3 bills | **`max_steps × 2 + 2` bills** |

The harness asserts `chats == tool_steps + 1`, `embeds == tool_steps`, and zero chat
calls against the retrieval gateway; the loop multiplies Day 11's shape by the number
of steps, and Day 3's warning arrives as a config the ledger bills.

## Config knobs (Day 12)

| Env | Default | Meaning |
|---|---|---|
| `AGENT_MAX_STEPS` | `6` | exit 2 — the step cap |
| `AGENT_BUDGET_CHARS` | `4000` | per-observation clamp (Day 11's `max_chars`, per step) |
| `AGENT_SELF_CORRECT` | `2` | re-issues allowed after a rejection, per question |
| `AGENT_BUDGET_USD` | unset (`0`) | run-level cost cap → exit 3 |
| `TOOL_MAX_TURNS` | `1` | Day 11's per-step call cap, consumed via `run_tool_answer` |

## Negatives (honest, with owners)

1. **The loop cannot yet survive its own failure modes** — infinite/oscillating
   loops, contradictory observations, hallucinated tools, a wrong-but-valid call.
   Day 12 gives them reachable exits; the taxonomy and the guards are **Day 13**'s.
2. **The stub scripts the turns.** A1/A6's *real-model* composition is measured by
   `--live` against the real gateway; with the reasoning prompt in place the free
   `nvidia/nemotron-3-super-120b-a12b:free` model **does** walk the map (see the LIVE
   section). Day 21 rules on a stronger model. Owner: **Day 21**.
3. **The data tools return fixtures, not Saleor.** `get_product/order/customer` are
   still tagged `pending_resolver: "day14"`. Owner: **Day 14**.
4. **The loop is reactive only.** It cannot plan before acting and cannot see that
   it is stuck; it burns a chat per step. Owner: **Day 16** (reflective planner).
5. **Grounding is still id-shaped.** The fabrication guard checks
   `module#Symbol`/`doc_id` mentions, not prose that ignores the evidence. Recorded,
   not hidden.
6. **`PENDING` semantic line — now measured.** Day 12 proves the harness runs N tool
   turns end-to-end, enforces grounding on the accumulated transcript, **and** that a
   real model composes a multi-step investigation once the loop prompts it correctly
   (the LIVE section). Day 21 still owns the *strength* of that composition across
   models and scenarios; the *container* and a first passing reading are today's.

## Depth-1 is Day 11 (and the bug that must never ship)

`run_tool_answer` *is* `run_agent(max_steps=1)`, so the depth-1 loop must be Day 11
exactly: one tool turn, then one **natural** answer turn. The loop earns this by
asking once more after the last allowed tool step and taking the `answer` exit — it
must **not** treat "the one step is spent" as "the cap fired" and inject the
`max_steps` note into a single-tool answer. The `depth1` row proves it: `answer`,
`tool_steps: 1`, `turns: 2`, `capped_tool_calls: []`, and an answer that does **not**
say "step limit". A regression here is a loop that changes what a single tool turn
does — a bug wearing an agent — and the check exists because a real model *echoes*
the injected note (the live run below caught exactly that before the fix).

## LIVE — the `PENDING` line, measured (`agent_smoke.py --live`)

`--live` swaps the *chat* wire for the real gateway from `.env`; retrieval stays on
the in-process stub stores (the seam the MECH suite grades). The harness **contract**
is hard-checked; the **composition** cells are `measure`d, never gated. A provider
outage (free-tier `429`, or a 200 with empty content) reports `[SKIP]` and exits **2**,
so "could not measure" is never confused with "measured and wrong" (exit 1).

**Provider / model required.** No configuration change was needed. The existing
`LLM_PROVIDERS` slot (`openai:` → OpenRouter, model
`nvidia/nemotron-3-super-120b-a12b:free`) is **tool-capable** — it advertises `tools`
in OpenRouter's `supported_parameters`, and it composes once the loop prompts it
correctly. `.env` is unchanged; the key is a **free-tier** OpenRouter key (free models
only, ~50 requests/day).

**The fix (a plan §6 item that was missing).** `run_agent` sent the model only the
user question + tool schemas — there was **no reasoning-turn prompt**. With no
instruction that *observations are evidence for the next step*, the model re-asked the
question in new words (`step 1: "CheckoutComplete"` → `step 2: "what happens after
checkoutComplete"` → `step 3: "checkoutComplete"`) and never followed the mutation's
service. Adding `ai_os/agent.py:REASONING_PROMPT` ("act on the evidence; a refusal is
a finding, not an answer; a corrected retry is a different call") fixes it at the root
— the loop was sound; the prompt was absent.

**Result** — `python tools/agent_smoke.py --live`, model
`nvidia/nemotron-3-super-120b-a12b:free` via OpenRouter:

| Cell | Kind | Result |
|---|---|---|
| the model used a tool; every call validated | contract | **pass** |
| `cited ⊆ ∪ observations`; observations ⊆ docs ∪ code | contract | **pass** |
| a legitimate exit fired | contract | **pass** |
| ≥2 steps, second symbol never named by the user | measured | **pass** (5 steps) |
| a later step's query names a symbol from earlier evidence, not the question | measured | **pass** — `step 2 ← complete_checkout`, `step 3 ← create_order_from_checkout`, `step 4 ← process_payment`, `step 5 ← create_from_checkout` |
| the answer cites ≥2 observations | measured | **pass** |
| A7 zero-tool: a trivial prompt → no `tool_calls` | contract | **pass** (`tool_steps: 0`) |

The model selected the **code** tool, received the graded evidence, and **carried it
into each next step**: it read the mutation's body (which names its service), issued
`complete_checkout`, then `create_order_from_checkout`, then `process_payment` — none
of which appear in the user's question — and answered from the accumulated
observations with `cited ⊆ ∪ observations` and no fabrication. A single-run probe
(`--live`'s A1 with per-step query printing) reached the same chain and exited
*naturally* (`termination: answer`, 4 steps); the suite run hit the 5-step cap while
still descending — a legitimate exit, not a failure.

**Limitation.** The free-tier daily request cap (50/day) is easily exhausted by
repeated live runs; the suite then exits 2 with `[SKIP]` rather than a false FAIL
(observed today). The model is non-deterministic, so the composition cell is a
per-run reading; on a day with quota, a stronger paid tool-calling model is the more
stable instrument.

## Re-verify, in one line

```bash
python tools/agent_smoke.py           # A1–A8 + gave_up + depth-1 + cost shape + T1–T7 re-run
python tools/agent_smoke.py --live    # A1/A6/A7 against the real gateway from .env
```

`PENDING`: whether a real model *drives* multi-step well — Day 21's benchmark on
this harness. `--live` measures it per run, and the free model **does** compose with
the reasoning prompt in place (see the LIVE section for the exact result and the
free-tier quota limitation).
