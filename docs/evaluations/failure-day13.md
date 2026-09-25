# Failure Modes Day 13 — Failures as a Contract

> Generated from the run of `python tools/failure_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire, scripted turn-by-turn so the loop reads the
> script at iteration N, the Day-7 doc corpus / Day-10 14-symbol code corpus for
> *retrieval*, a **broken/flaky retriever** injected for the execution family, and an
> **injected clock** for the timeout wall). `MECH` = provable offline, here.
> `PENDING` = a real gateway is required. The headline invariant is the `fabricated?`
> column: it must read **`no`** on every row, forever.

```
stores: 9 doc chunks (STUB) / 14 code symbols (STUB)   τ_doc=0.05 τ_code=0.75
registry: search_saleor_docs, search_saleor_code, get_product, get_order, get_customer
max_steps=6 (default)  budget_chars=4000  self_correct=2  k=4
tool_timeout_s=30.0  repeat_window=4  tool_retries=1  fallbacks=1
```

## The fixtures, pre-registered before the run (§7)

| ID | Mode | Injected (stub) | Expected |
|---|---|---|---|
| F1 | wrong tool selection | model picks `search_saleor_docs` for a code question | miss attributed to the **description**; loop **pivots** to the code tool; `failure_kind: tool_miss`; no answer from the wrong corpus |
| F2 | invalid arguments | `kind="banana"` (enum) | rejection names the field; self-corrects within the Day 12 bound; `failure_kind: invalid_args`; A2 stays green |
| F3 | infinite loop | identical call re-emitted | **repeat detector** fires (`no_progress`) at step 2, before `max_steps=6` |
| F4 | oscillation | alternating tool A / tool B | **cycle detector** fires within `repeat_window`; `failure_kind: oscillation` |
| F5 | missing information | "Debug the Shopify webhook handler" | all tools refuse; one pivot; final answer "not covered"; zero fabricated symbols |
| F6 | contradictory results | step 1 and step 2 assert opposite values for one claim | trace flags `contradiction`; answer cites **both** observation ids; two negative twins must **not** fire |
| F7 | hallucinated tool | stub emits `get_invoice()` | registry unknown-tool rejection; **no** execution attempted; self-corrects |
| F8 | tool failure | tool raises (transient / persistent / both corpora down) | `{error_kind: "tool_error"}` observation; retry-once; then the fallback ladder; `tool_failure` when exhausted |
| F9 | timeout | stub hang + injected clock (+1000 s) | `tool_timeout_s` fires; `{error_kind: "timeout"}`; run survives; `tool_timeouts` recorded; `elapsed_ms` proves the wall |

The `MECH`/`PENDING` split is exactly Day 7's. **MECH** — F2, F3, F4, F6-*detection*,
F7, F8, F9, and F1's *pivot* — are pure functions of the registry, the stub's scripted
failures, and an **injected clock**: deterministic, offline, green, free. **PENDING** —
F1's and F5's *real-model* behavior (will a real model pick wrong, will it stop on "not
covered") and F6's *adjudication* — need the real gateway, and are Day 21's judgment on
Day 13's guards.

## §8 failure table (every cell a real run's)

| Mode | Injected | Detected? | `failure_kind` | Guard fired | Recovered? | Fallback used | fabricated? | Owner | elapsed_ms | Cost |
|---|---|---|---|---|---|---|---|---|---|---|
| F1 wrong tool | docs tool for a code question | yes | `tool_miss` | gate → pivot | yes (pivoted) | — | **no** | description | 0 | $0.00003720 |
| F2 bad args | `kind="banana"` (enum) | yes | `invalid_args` | validate + self-correct | yes (fixed) | — | **no** | validation | 0 | $0.00003720 |
| F3 repeat | identical call re-emitted | yes | `no_progress` | fingerprint | no (stopped) | — | **no** | loop state | 0 | $0.00003720 |
| F4 oscillation | `A,B,A,B` | yes | `oscillation` | fingerprint | no (stopped) | — | **no** | loop state | 0 | $0.00005040 |
| F5 missing info | all tools refuse | yes | `refused` | gate + one pivot | no (refused) | — | **no** | gate | 0 | $0.00003720 |
| F6 contradiction | two conflicting claim values | yes | `contradiction` | trace flag + cite both | — (flagged) | — | **no** | reconciliation | 0 | $0.00003720 |
| F7 hallucinated tool | `get_invoice()` | yes | `unknown_tool` | registry | yes (self-corrected) | — | **no** | registry | 0 | $0.00003720 |
| F8a tool failure | tool raises once | yes | `tool_error` | retry once, same args | yes | — | **no** | executor | 1 | $0.00003060 |
| F8b tool failure | tool always raises | yes | `tool_error` | retry → sibling (disclosed) | yes | yes | **no** | executor | 0 | $0.00003060 |
| F8c tool failure | both corpora down | yes | `tool_error` | ladder exhausted → stop | no (`tool_failure`) | yes | **no** | executor | 0 | $0.00003060 |
| F9 timeout | hang + injected clock | yes | `timeout` | `execute_bounded` wall | yes | — | **no** | executor | 1,000,000 | $0.00003060 |

**`fabricated? = no` on every row.** A `yes` anywhere invalidates the run no matter
how clean the other columns are.

## What the run actually produced

**F1 — wrong tool selection (the description owns it).** Step 1 issues
`search_saleor_docs` for a code question; the docs corpus refuses an out-of-vocabulary
query (`retrieved == []`, zero evidence). Step 2 **pivots** to `search_saleor_code` and
succeeds. The recovery is a *different corpus*, not a louder repeat, and the run
records `failure_kind: tool_miss` — the miss attributed to the **schema description**,
which is the Day 11 suspect re-rooted. No answer is manufactured from the wrong corpus.

**F2 — invalid arguments (validation owns it, self-correction fixes).** Step 1 emits
`kind="banana"`. `validate_args` rejects it with a message that names the field:
`argument 'kind' must be one of ['function', 'class', 'mutation', 'test', 'module'],
got 'banana'`. The type is `invalid_args` (a **rejection**, not a tool error — no
execution, no fingerprint, no ladder). Step 2 re-issues with *fixed* args and succeeds.
The assertion is deliberately paired: `json.loads(step1.args) !=
json.loads(step2.args)` — **A2 stays green** because the fingerprint treats the fixed
call as non-identical (the value is never normalized, only its key order and
spelling).

**F3 — infinite loop (the repeat detector fires before the cap).** The identical valid
`search_saleor_code` call is re-emitted. `detect_repeat` fires at **step 2**, with
`max_steps=6` still unspent: `termination_reason: no_progress`,
`no_progress_at_step: 2`, `tool_steps: 2`, `distinct_step_fingerprints: 1`. The
fingerprint cost **0 calls** — the ledger shows `records == tool_steps + 1` (the two
tool-turn chats plus the forced final answer), so the stop *saved* the four steps the
cap would have burned.

**F4 — oscillation (a legal sequence, an illegal shape).** Steps are `A` (`code`),
`B` (`docs`), `A`, `B`. `detect_oscillation(window=4)` fires at **step 4**, with no
repeat fired (`no_progress_at_step is None` — the two detectors are distinct and
adjacent calls differ) and `distinct_step_fingerprints: 2`. `termination_reason:
oscillation`.

**F5 — missing information (the gate owns it; refusal is the answer).** "Debug the
Shopify webhook handler" → both executed calls refuse (`observed_refusals == 2`,
across both corpora). One pivot is allowed and also refuses. The final answer restates
the gate's "not covered" (`refusal_restated: True`), and **zero evidence is
manufactured** (`evidence == []`). `failure_kind: refused`.

**F6 — contradictory results (flagged, not adjudicated).** Step 1's `claim_probe`
returns `order_created_by_checkout=True` (`complete_checkout`); step 2's returns
`False` (`OrderCreated`). The trace flags **one** contradiction row naming the field
and both values (`{True, False}`), and the answer cites **both** contentious
observations — `contradiction_covered: True`. Two negative twins prove the detector is
not trigger-happy: (a) two sources that **agree** → no row; (b) Day 10's **split-brain**
(`CheckoutComplete` mutation + `complete_checkout` service, two correct hits in two
layers) → no row. The failure case is also caught: an answer citing only one side
fails the completeness clause (`contradiction_covered: False`).

**F7 — hallucinated tool (the registry owns it; no execution).** The stub emits
`get_invoice()`. `validate_args` returns a typed `unknown_tool` rejection —
`unknown tool 'get_invoice' (known: [...])` — and **no execution is attempted** (empty
evidence for the phantom). The loop self-corrects on the next step.

**F8 — tool failure (the executor owns it; retry then fall back).** Three readings:
- **(a) transient.** A `FlakyRetriever` raises on its first call. The raise becomes a
  typed `tool_error` **observation**, never an exception. Rung 1 retries the **same**
  call once (`tool_retries == 1`) and recovers; `termination_reason: answer`.
- **(b) persistent.** `AlwaysBrokenRetriever` fails both attempts, so rung 2
  substitutes the sibling: `fallback_used: [{step: 1, from: search_saleor_code, to:
  search_saleor_docs, ok: True}]`, the substituted result carries `fallback_from`, and
  the evidence is real docs (the substitution is **disclosed**, not silent).
- **(c) ladder exhausted.** Both corpora down → rung 4 stops honestly:
  `termination_reason: tool_failure`, `observed_failures: 3` (original + retry +
  fallback), and the answer names the failure while citing only real evidence.

**F9 — timeout (the wall at the tool-call boundary).** With the injected `FakeClock`
(reads `0`, then `1000`), the first call *measures* a 1000 s wall — `elapsed_ms:
1,000,000 ≥ 5000` — and returns a typed `{error_kind: "timeout"}` observation
(`tool_timeouts: 1`). The run survives and recovers via the same-arg retry. The wall
itself is separately asserted: a 1 ms call under a 5 s wall is **not** a timeout
(`elapsed_ms: 1.0`), and the comparison is the clock's — deterministic, and real in
the sense that matters.

## No-regression line (Days 11–12 ride the guarded loop)

| Line | Result |
|---|---|
| `tools/agent_smoke.py` run unchanged in a subprocess | **exits 0 — `ALL DAY 12 (MECH) CHECKS PASSED`** |
| `tools/tool_smoke.py` run unchanged in a subprocess | **exits 0 — `ALL DAY 11 (MECH) CHECKS PASSED`** |
| A2 (self-correction) vs F2 (invalid args) | paired — the fingerprint treats the *fixed* call as non-identical, so F2 does not break A2 |
| A5 (`max_steps` on a legitimate long run) vs F3/F4 (early diagnosis) | distinguishable — A5 uses four distinct calls (`failure_kind: None`), F3/F4 fire on a spiral before the cap |
| Day 7–10 fixtures behind the tools | ride inside those suites (doc nine 9/9, code six 6/6) |

The guards are *around* the loop: a healthy run is byte-for-byte unchanged, and the two
neighbor pairs (A2/F2, A5/F3-F4) — the day's sharpest regression risk — get their own
assertions. A hardening layer that changes a passing run is a bug wearing a safety
vest.

## Cost shape (two currencies, both named)

| Event | Calls | Currency that moved |
|---|---|---|
| a **timeout** (F9) | 0 | `elapsed_ms: 1,000,000` — a wall, not a bill |
| the **fingerprint** (F3/F4) | 0 | arithmetic over strings |
| a **repeat/oscillation stop** | 0 | *saves* the `max_steps` steps the cap would have burned |
| the **fallback ladder** (F8b) | 1 embed + 1 chat | calls (a sibling tool is a real retrieval + turn) |

The worst-case `max_steps × 2 + 1` from Day 12 gets **lower** in exactly the runs that
used to be most expensive, because the fingerprint stops a spiral at step 2 instead of
step `max_steps`.

## Config knobs (Day 13)

| Env | Default | Meaning |
|---|---|---|
| `AGENT_TOOL_TIMEOUT_S` | `30.0` | the wall around one tool call (time, not money) |
| `AGENT_REPEAT_WINDOW` | `4` | the fingerprint's cycle window (`A,B,A,B`) |
| `AGENT_TOOL_RETRIES` | `1` | rung 1: same-arg execution retries, per call |
| `AGENT_FALLBACKS` | `1` | rung 2: sibling-tool substitutions, per run |
| `AGENT_MAX_STEPS` | `6` | Day 12 — the step cap (unchanged backstop) |
| `AGENT_BUDGET_CHARS` | `4000` | Day 12 — per-observation clamp |
| `AGENT_SELF_CORRECT` | `2` | Day 12 — re-issues allowed after a rejection |
| `AGENT_BUDGET_USD` | unset (`0`) | Day 12 — run-level cost cap |
| `TOOL_MAX_TURNS` | `1` | Day 11 — per-step call cap, via `run_tool_answer` |

## Negatives (honest, with owners)

1. **The guards cannot adjudicate a contradiction** or **judge** whether a real model
   spirals — only flag and require both sides cited. Owner: **Day 21**.
2. **The stub scripts the failures.** Whether a real model *trips* F1/F5/F6/F7 or
   *recovers* is `PENDING` until a real gateway. Owner: **Day 21**. The stub proves the
   guards *fire*.
3. **The stubbed tool failures cannot represent real latency** — real DB timeouts and
   resolver errors arrive with the data tools. Owner: **Day 14**.
4. **The fingerprint cannot catch slow-but-genuine progress** that still ends at the
   cap (A5). Owner: **Day 16** (the reflective planner, which decides *before* burning
   steps).
5. **The fabrication guard is still id-shaped.** It checks `module#Symbol`/`doc_id`
   mentions against the union of observations, not prose that ignores the evidence.
   Recorded, not hidden.

## Re-verify, in one line

```bash
python tools/failure_smoke.py     # F1–F9 + the fabricated? column + A1…A8 / T1…T7 re-run
```

`PENDING`: whether a *real* model trips F1/F5 or contradicts itself, and whether it
recovers or papers over — Day 21's judgment on Day 13's defenses. The one column that
never varies is `fabricated? = no`.
