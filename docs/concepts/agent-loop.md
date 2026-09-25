# Concept: An Agent Is a Loop with a Memory, a Stopping Rule, and a Grounding Contract

> Day 12 of the AI-layer apprenticeship. Day 11 built the tool layer and capped it
> at a **single tool turn** so termination existed before the loop did. Day 12
> lifts the cap: the single turn becomes a **loop** — user request → reason →
> choose a tool → execute → observe the result → reason again → choose the next
> action → final answer. What changes is underneath the loop: **agent state** is
> redefined (the transcript *is* the memory — there is no hidden variable),
> **observations** arrive as the same evidence record the harness has graded since
> Day 7 (now per step), **tool selection** is Day 11's recall@1 harvested on every
> iteration, and **termination** gains two new legitimate exits (`max_steps`,
> budget) beside the natural one (an answer turn with no `tool_calls`).
> Implementation: `ai_os/agent.py` (`run_agent`), `ai_os/trace.py` (per-step
> trace + `grounding_over_transcript`), `tools/mock_gateway.py` (multi-step
> scenarios), `tools/agent_smoke.py` (A1–A8).

## Problem

Everything the OS had built could *act* but not *investigate*. Day 11's
deliverable was one tool turn: a question answered by exactly one call, with
termination hard-coded as `max_tool_turns = 1`. A question like "what happens
after `CheckoutComplete` submits?" is two calls deep at minimum — the first
observation names a symbol the model did not know before it acted, and the second
query is *caused* by the first. Without a loop that question is unanswerable.

Three assumptions break the moment the turn becomes a loop, and each is a
degradation of "state" as software people usually mean it:

| "State" temptation | Reality | Cost of the temptation |
|---|---|---|
| a hidden variable (a `self.plan`, a scratch dict) the loop mutates | the transcript is the only state the model can see; a scratch dict is state *the model cannot see* | a plan the agent holds invisibly is a plan nothing verifies or corrects |
| "efficient" reuse of a field | every step re-reads the transcript the way Day 7 re-ran retrieval | the redundancy is the point: forgetting is a string problem, not a cache problem |
| a finished answer that "summarizes" | the final answer is a turn in the same transcript, grounded the same way | an answer that cites observations the transcript doesn't contain breaks `cited ⊆ retrieved` |

The audit splits into two records that must not be confused: the **transcript**
is the mechanism the model reasons over; the **ledger** (Day 4) is the audit of
money and steps. Day 4's ledger row bills one chat call; Day 12's per-step trace
row debugs one decision. Two tables, two owners.

## Agent state is the transcript — a deletion, not an addition

The loop's state is one immutable-growing `messages` list. The user request, every
assistant `tool_calls` turn, every `tool` observation, and the final answer *are*
the memory; nothing is mutated, nothing is dropped. Concretely, in
`run_agent`:

```text
transcript = [{"role": "user", "content": request}]
   … each step appends:
   {"role": "assistant", "content": completion.text or None, "tool_calls": [...calls]}
   {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": <observation>}
```

An **observation** is that `tool`-role message — and it is *the evidence record,
serialized*, never prose. There are the same three shapes Day 11 shipped:

- **top-K evidence** (Day 8 list, Day 10 `module#Symbol` records, `budget_chars`-clamped);
- **`{refused: true}`** — Day 7's gate as a return value, now arriving mid-transcript;
- **`{ok: false, error: "…"}`** — a rejection naming the field, now arriving mid-transcript.

The moment an observation is reformatted into a summary ("so I found…"), the
harness stops being able to check `cited ⊆ retrieved` on it. That is why
`_tool_message` returns `result.as_message()` and only ever *truncates* — a
recorded, string-level clamp (`obs_clamped: true`), computed against the result's
evidence ids so a clamped observation can never make a citation look fabricated.

## Termination — three exits, plus a narrower fourth

An agent is a loop with a stopping rule, and the trace must be able to say which
exit fired. `termination_reason` is **output, not debug**:

1. **`answer`** — the model's turn carried no `tool_calls`. It answered from the
   evidence in hand. This is the only exit Day 11 knew and it survives unchanged.
2. **`max_steps`** — the step cap fired. The loop does not invent a final step; it
   runs one final *plain* chat asking the model to answer strictly from
   already-gathered evidence and to say the loop stopped. `truncated: true`.
3. **`budget`** — the run's cumulative ledger cost tripped `budget_usd`. Money
   ends the loop the way it bytes anything in the OS: visibly, in the trace.
4. **`gave_up`** — the *narrower* guardrail exit: rejections exceeded
   `self_correct`. The loop appends the last rejection as an observation and the
   answer may name the stuck point honestly.

The pre-commit that makes these exits honest: **termination is a contract with
the user.** A transcript that dies at `max_steps` is a *failed-agent* transcript —
the answer must say so, not blur into a confident non-answer. `AGENT_TERMINATIONS`
carries the human text; `termination_note` is the system note handed to the forced
final turn.

### The check must read the *corrected* parser

Exit 1 is `if not completion.tool_calls:` — read off the completion the provider
already parsed. This is deliberate: Day 6 raised a *retryable* error on a `200`
with empty content (throttled OpenAI-compatible endpoints do this), and Day 11
gave that check a **sibling** — `content: null` *with* `tool_calls` is a valid
tool turn. The loop must not re-merge them by checking exit 1 on raw content. A
plain-text answer turn with empty content is still the Day 6 glitch (retry); an
empty turn with no calls *and* no text never reaches the loop as an "answer".

### The Day 13 boundary

An **infinite loop** is a loop that never hits a legitimate exit. Day 12 does not
build the taxonomy of *why* agents spiral (repeating the same wrong tool,
oscillating, re-issuing the same rejected call) — that is Day 13. Day 12 builds
the exits and makes them reachable on schedule, so "infinite" is impossible **by
construction**: `max_steps` and budget both fire no matter what the model emits.

### Retries wear two costumes

- **Provider retries** — Day 4's backoff+jitter, `Retry-After`, ordered fallback,
  per-attempt ledger rows — happen *inside* one `chat_with_tools` call. The loop
  never sees them; a retry storm is one ledger row to the loop.
- **Loop self-correction** — a rejected tool call re-issued with fixed arguments —
  is a *new step*, an observation, a bill, and a trace row. It is bounded by
  `self_correct`.

The rule carrying over into both: **refusal is an observation, not a retry
signal.** `search_saleor_code` returning `{refused: true}` is evidence the
question is out-of-corpus; re-issuing the identical call is the Day 8 fabrication
bug wearing a loop.

## The loop

```python
def run_agent(request, registry, gateway, *, max_steps, budget_chars,
              self_correct=2, budget_usd=None, max_tool_calls_per_step=None):
    transcript = [{"role": "user", "content": request}]
    steps, rejections, refusals = [], 0, 0
    while len(steps) < max_steps:
        completion = gateway.chat_with_tools(transcript, tools=registry.schemas())
        if not completion.tool_calls:                 # exit 1 — answered from evidence
            return final(completion.text, termination="answer")
        calls = completion.tool_calls
        if max_tool_calls_per_step is not None and len(calls) > max_tool_calls_per_step:
            deferred.extend(calls[max_tool_calls_per_step:]); calls = calls[:cap]
        observations, rows = [], []
        for call in calls:                            # each call its own observation
            vr = registry.validate_args(call.name, call.arguments)
            result = registry.execute(call.name, vr.args or {}) if vr.ok else vr
            observations.append(_tool_message(call, result, budget_chars))
            rows.append(_call_row(call, vr, result, clamped)); record rejection/refusal
        transcript += [assistant_turn(calls)] + observations
        steps.append(step_trace(len(steps) + 1, rows))
        if cost_cap and run_cost(ledger) >= cost_cap: return terminated(..., "budget")
        if rejections > self_correct:                 return terminated(..., "gave_up")
    return terminated(transcript, reason="max_steps")  # exit 2
```

Three properties are deliberate:

1. **`run_agent(max_steps=1)` is Day 11.** The single-turn tool answer is a
   configuration of the loop, not a separate program — which is why T1…T7 stay
   green *by construction*. `run_tool_answer` is literally
   `run_agent(max_steps=1, max_tool_calls_per_step=cap)`. A loop that changed a
   single-tool answer would be a bug wearing an agent.
2. **Observations are appended, never summarized.** The transcript grows by one
   `assistant` turn and one `tool` message per executed call; nothing is mutated
   and nothing is dropped. The model's "memory" is the accumulated conversation.
3. **Termination is a return value.** Exits 1–4 all return a run whose
   `termination_reason` is set. The final answer under a non-`answer` exit is
   *limited to already-gathered evidence* — the model is not asked to "complete"
   what the cap cut off.

### Parallel calls in a step, and their cost

A step may emit several `tool_calls` (Day 11 forbade two; the loop allows them).
Each is validated, executed, and observed separately — *parallelism is context,
not a discount*. One chat carried the `k` tool requests (one bill), each retrieval
step embeds its query (`k` bills), and all observations enter the one transcript.
`max_tool_calls_per_step` is Day 11's `max_tool_turns` cap expressed per step:
calls beyond it are **recorded, not executed** (`deferred_tool_calls`,
`loop_deferred: "day12"`).

## Walking Day 10's map (the canonical investigation)

Day 10 stored the map; Day 12 walks it. The A1 fixture, verbatim:

```text
"What happens after CheckoutComplete submits?"
  step 1  search_saleor_code("CheckoutComplete", kind="mutation", module="saleor/graphql/checkout")
          → CheckoutComplete { imports: [checkout.complete_checkout], deps: [complete_checkout] }
  step 2  search_saleor_code("complete_checkout", kind="function", module="saleor/checkout")
          → complete_checkout { … calls create_order_from_checkout }        ← caused by step 1
  final   cites CheckoutComplete (step 1) and complete_checkout (step 2) — cited ⊆ ∪ observations
```

The property that makes this Day 10's map come alive and not a toy: **step 2's
query names the symbol step 1's evidence carried.** The model did not know
`complete_checkout`'s name before observation 1; it read the `imports`/`deps`
field on the returned chunk and turned it into a new query. That is the difference
between an agent and a pipeline — step *n+1* is **caused by** step *n*'s
observation.

Grounding is enforced per step and therefore over the transcript: the union of
observations is the only source the final answer may cite
(`grounding_over_transcript`).

## Guardrails

1. **Self-correction is bounded.** A rejected call (bad args, unknown tool) may be
   re-issued at most `self_correct` times (default 2). Beyond that the loop records
   `gave_up`, appends the last rejection as the observation, and the final answer
   may *name* the stuck point honestly. The re-issue is possible **because** Day 11
   made rejection a message naming the field (a raised exception would have killed
   the turn), and the stop is verifiable **because** the trace counts rejections.
2. **Refusal is never reinterpreted.** A refused tool is not retried into the
   budget vault, and an all-refusals step does not license the final answer to
   restate. `observed_refusals` is what lets the harness tell "the agent pivoted"
   (legal) from "the agent fabricated" (illegal). `refusal_restated` flags the
   latter. The Day 8 warning file gains a loop-shaped row.
3. **Budget is a run-level cap, not a habit.** `budget_chars` clamps every
   observation (Day 11's `max_chars` discipline, per step) and the ledger's
   cumulative cost is checked each step (`budget_usd`). Day 3's warning
   ("cost multiplies by number of calls") arrives as a config, and only a config
   named in the trace keeps the multiplication honest.

## The reasoning-turn prompt — why the loop composes

The transcript opens with a `system` turn (`ai_os/agent.py:REASONING_PROMPT`), the
same "write the prompt for the grader" discipline Days 8–11 applied to schemas. It
states the three things the loop depends on and cannot enforce itself: tool results
are **evidence for the next step** (if a result names an import/helper, that name is
the *next query*); a **refusal is a finding, not an answer**; and a **corrected retry
is a different call**. Without it the model still calls tools and answers, but it
re-asks the question in new words instead of following what the evidence named — the
live run measured exactly that, then measured the fix. The prompt is content, not a
new mechanism: no code path changes, `max_steps`/budget/exit logic are untouched, and
the offline stub (which scripts turns and ignores the prompt) stays byte-identical.

## Reactive vs reflective — the seam to Day 16

Day 12's loop is **reactive**: the decision is made at each step from the current
transcript. Day 16's planner-decider is **reflective**: Day 8's decomposition done
by a model *before* acting. Both loop; the seam is *where the decision is made*.
Day 12 ships reactive and records the planner as Day 16's, because the reactive
loop already isolates every variable the planner will need (which tools, which
order, which stopping rule).

## Per-step trace — the unit of attribution

Day 7's per-query trace becomes Day 12's per-step trace; a step is as auditable as
a Day 7 query because the row shape is the same (`trace.step_trace`):

```text
step | tool_calls | calls ⇒ {name, args_parsed, args_valid, result_ok,
                             refused, error, evidence_ids, obs_clamped}
     | args_valid | result_ok | refused | rejections | observed_refusals
     | retrieved | cited | cited_subset_retrieved | [truncated | deferred]
```

`cited` can only be filled once the answer exists, so
`grounding_over_transcript` writes it in — annotating each step with the citations
belonging to *its* evidence. That is how "the answer cites symbols from ≥ 2
observations" becomes a MECH check, and how "the agent lost the thread between
steps" is diagnosable per step.

## Config knobs

| Param | Env | Default | Meaning |
|---|---|---|---|
| `max_steps` | `AGENT_MAX_STEPS` | `6` | exit 2 — the step cap |
| `budget_chars` | `AGENT_BUDGET_CHARS` | `4000` | per-observation clamp (Day 11's `max_chars`) |
| `self_correct` | `AGENT_SELF_CORRECT` | `2` | re-issues allowed after a rejection, per question |
| `budget_usd` | `AGENT_BUDGET_USD` | unset (`0`) | run-level cost cap → exit 3 |
| `max_tool_calls_per_step` | `TOOL_MAX_TURNS` (via `run_tool_answer`) | `1` | Day 11's cap, now per step |

## Cost shape

| Axis | Day 11 | Day 12 |
|---|---|---|
| a question costs | 1 tool-turn chat + 1 answer chat + 1 embed | **`tool_steps` chats + `tool_steps` embeds + 1 final chat** |
| tool *execution* | 0 chats, 0 embeds | unchanged — arithmetic over stored vectors |
| worst case | fixed 3 bills | **`max_steps × 2 + 2` bills** — bounded by config, reported per run |

The loop multiplies Day 11's shape by the number of steps — the cost is exactly
`tool_steps` retrieval embeds plus the per-step tool-turn chats plus one answer
turn, and the ledger's per-step rows are the audit trail under the multiply.

One extra chat makes the depth-1 guarantee true: after the last allowed tool step
the loop asks **once more**, so the model can answer *naturally* (exit 1) instead of
being handed a cap it never hit. At `max_steps=1` that extra turn is exactly Day 11's
answer turn — which is why `run_tool_answer` reproduces Day 11 rather than a
single-tool answer stamped with a false `max_steps` note. If that extra turn still
carries `tool_calls`, the cap fires: the calls are *recorded, not executed*
(`capped_tool_calls`), and the forced final answer says the loop stopped.

## Negatives (honest, with owners)

1. **The loop cannot yet survive its own failure modes.** Infinite/oscillating
   loops, contradictory observations, hallucinated tools, and wrong-but-valid
   calls have no guard beyond the cap. Owner: **Day 13**.
2. **The stub scripts the turns.** A1/A6's *real-model* composition — will a model
   follow an import to a `complete_checkout` it was never told, and keep citing
   each step — the stub cannot measure alone. `tools/agent_smoke.py --live` does,
   per run: with the reasoning-turn prompt in place the free
   `nvidia/nemotron-3-super-120b-a12b:free` model **does** walk the map (it carried
   `complete_checkout` → `create_order_from_checkout` → `process_payment` across
   steps). Without that prompt it re-asks rephrased versions of the question — the
   prompt, not the loop, was the missing piece. Day 21 rules on a stronger model.
3. **The data tools return fixtures, not Saleor.** `get_product/order/customer`
   are still tagged `pending_resolver: "day14"`. Owner: **Day 14**.
4. **The loop is reactive only.** It cannot plan before acting, so it burns a chat
   per step and cannot see that it is stuck. Owner: **Day 16** (the reflective
   planner-decider).
5. **The fabrication guard is still regex-shaped.** Ids are recognised by shape;
   an answer that refers to evidence in prose is not checked, because there is no
   id to check. Recorded, not hidden.

## Re-verify, in one line

```bash
python tools/agent_smoke.py           # A1–A8 + the gave_up bound + depth-1 + cost shape + T1–T7
python tools/agent_smoke.py --live    # A1/A6/A7 against the real gateway from .env
```

`PENDING`: whether a real model *composes* — chooses the second tool from the
first step's evidence and cites both observations to the end — is Day 21's
judgment on Day 12's harness. The stub proves the loop *carries* N turns; a real
key proves the model *drives* them, and `--live` now measures which.
