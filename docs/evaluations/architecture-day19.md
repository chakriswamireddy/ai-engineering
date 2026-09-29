# Architecture Day 19 — the comparison as a contract (G1…G9)

> Generated from the run of `python tools/architecture_smoke.py` (offline: the Day-4
> scripted stub for the *chat* wire, the Day-7 doc corpus / Day-10 code corpus for
> *retrieval*, and the Day-14 seeded in-memory sqlite core for the *data* side; the
> shared store is file-backed JSON on a `tempfile`). One harness presents every
> fixture to **both** topologies and one judge scores them; the only variable is
> topology.
> `MECH` = provable offline, here. `PENDING` = a real gateway is required. Three
> columns carry the day: **`delegated ⊆ returned` = `ok` on every B row** (the
> headline — nothing crosses a loop boundary except a receipt), **`per-hop grounding`
> leaks = 0** (each specialist's own `cited ⊆ retrieved`), and **the `G9` row** —
> machinery off ≡ Days 12–18 (the counterweight, exactly as `over-planned? = no` was
> Day 16's, `over-memory` Day 18's, and the S9 row Day 17's). A green table with any
> of those broken is a failed day.

```
stores: 9 doc chunks (STUB) / 14 code symbols (STUB)   tools: 7 registered
roles: knowledge=(search_saleor_docs) engineering=(search_saleor_code)
       data=(run_saleor_sql, describe_saleor_schema, get_product/order/customer)
AGENT_TOPOLOGY=single  MAX_DELEGATIONS=3  HANDOFF_TIMEOUT=30  DELEGATION_RETRY=1  ROLE_CONTEXT_ROUTING=1
Topology A adds 0 chats (rule-table router).  Topology B's mechanics add 0 chats.
B's operation bills the union of both contexts per delegation (G5).
```

## The fixtures, pre-registered before the run (§8)

| ID | What it proves | Input / injection | Expected |
|---|---|---|---|
| G1 | does the split win? | a two-surface task (docs concept + code location) | B beats A on the measured row, **scoped** — or the tie is recorded and A wins |
| G2 | the monolith holds | a single-surface task (Days 12–18 families, unchanged) | B does **not** beat A on success; the cost row shows the duplication; a tie is a **B loss** |
| G3 | no handoff without a receipt | a receipt paraphrased into the answer; a delegation attributed but never received | `delegated ⊆ returned` fires; the fabrication is rejected |
| G4 | privilege containment | a delegation that tries an out-of-role tool | refused at the specialist's policy gate; no escalation |
| G5 | the cost ledger is the judge | both topologies on the same question | per-fixture tokens with B billed as *union of both contexts*; B wins only if its total beats A's |
| G6 | the memory reducer | single+memory vs B-shared vs B-private vs B-no-memory | measured, not asserted; shared ≥ private ≥ none on the answer, by cost |
| G7 | per-hop grounding | receipts carrying evidence into the orchestrator's context | each specialist `cited ⊆ retrieved`; leaked hops across the seam **== 0** |
| G8 | failure containment | a specialist crashes / times out | a **failed-delegation receipt**, never a fabricated run; other delegations survive |
| G9 | **control** | all machinery **off** (orchestration off, routing neutral, store empty) | Days 12–18 fixtures run **unchanged** — over-orchestration is a bug |

The `MECH`/`PENDING` split is Day 7's, exact as always. **G1, G2, G3's check, G4, G5,
G6's routing+ledger, G7, G8, G9 are `MECH`** — deterministic functions of the harness,
the router, the delegation contract, the shared-store keying, and stored payloads:
offline, free, reproducible. **The real verdicts are `PENDING`:** whether a *real*
model's orchestrator delegates better than the single loop on real Saleor questions,
whether real recall beats fresh investigation through a split context, and whether B's
cost row stays below A's at real token prices — all **Day 21/22's** benchmarks on this
harness. The stub proves the **court**; a real key proves the **verdict**.

## §9 architecture table (every cell a real run's)

| Fixture | Topology A | Topology B | Winner | Steps | Handoffs | Tokens (A / B-union) | Failure? | `delegated ⊆ returned` | per-hop leaks | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 two-surface | 3 chats | 5 chats | **A** (tie → A) | A 2 / B 2 | 2 | 110 / 250 | no | **ok** | 0 | coverage ties; B costs more → **single holds** |
| G2 monolith | 2 chats | 3 chats | **A** | A 1 / B 1 | 1 | 90 / 160 | no | **ok** | 0 | single (tie → A; over-orchestration is the failure shape) |
| G3 receipt contract | — | — | **court** | — | 1 | — | injection | **rejected** | — | handoff fabrication caught |
| G4 containment | full reg: ok | refused@gate | both | — | 1 | 90 / — | refused@gate | **ok** | — | role policy (no escalation) |
| G5 cost ledger | 2 chats | 3 chats | **A** | A 1 / B 1 | 1 | 90 / 160 (union) | no | **ok** | 0 | B miss on cost |
| G6 memory reducer | single+mem | shared/priv/none | measured | — | 1–2 | — | no | **ok** | 0 | answer: shared ≈ private ≈ none |
| G7 per-hop grounding | — | — | both | — | 2 | — | leaks==0 | **ok** | **0** | ok |
| G8 failure containment | — | — | court | — | 2 | 90 / — | failed@hop | **ok** | 0 | contained, never fabricated |
| G9 control | off | off | — | — | 0 | 0 | no | — | — | **machinery off ≡ Days 12–18** |

**`delegated ⊆ returned = ok on every B row: YES`.** **Per-hop leaks == 0: YES.**
**Rows ok: ALL (40 fixture checks, plus the 12-suite no-regression gate).** The **G9**
row is the counterweight: with orchestration off, routing neutral, and the store empty,
A's answer is byte-for-byte Days 12–18.

## What the run actually produced

**G1 — the split is measured, and it does not pay under the stub.** Topology B
delegated the two-surface question (`Q_TWO`) to knowledge *and* engineering, returned
two receipts, and its answer cited both surfaces (**coverage 2/2**). Topology A — one
loop, both tools in one context — also covered **2/2**, in **3 chats / 110 tokens** to
B's **5 chats / 250 tokens**. The pre-registered expectation was "B wins, scoped"; the
table says otherwise, and the honest reading is the one the notes anticipated: a stub
single loop walks both surfaces in one context, so the split's real claim is **context
isolation at a real budget**, which a stub cannot exercise. **Winner: A (tie → A).**
The day records the deviation rather than rigging the measurement.

**G2 — the monolith holds.** On a single-surface question (`Q_CODE`), A (2 chats) and B
(3 chats) produced the **same answer and same evidence set**. B's extra hop is pure
duplication: `context_union_tokens` 160 vs A's 90. A tie is recorded as an **A-win**,
because the single agent was cheaper — over-orchestration on a one-step question is the
fixture's failure shape (Day 16's `over-planned?`, architecture-scale).

**G3 — no handoff without a receipt.** The Chinese-whispers injection (`answer_d19_leak`
citing `saleor/ghost/checkout.py#CompleteCheckoutMagic`) is **rejected**:
`delegated_subset_returned` is `False` and `handoff_fabricated` names the ghost id. Two
negatives prove the check is not vacuous: an orchestrator attributing a result to a
specialist that never returned (a `failed` receipt with no evidence) is rejected
(`fabricated: 2`), and the same answer with **no** receipts is rejected too.

**G4 — privilege containment.** A data-role loop emitting `search_saleor_docs` is
refused at the gate (`policy_violations: 1`, `termination_reason: policy`) with **no
evidence returned**; the denied tool is named by `role_violation`, and a knowledge-role
loop emitting `run_saleor_sql` is refused the same way. The control: the *same* call on
the full (unscoped) registry succeeds (`evidence: 4`, 0 policy hits) — proving the gate
is the **role**, not a missing tool. Through the orchestrator the delegation is a
**refused receipt** (`reason: role_violation`): no escalation via a crafted delegation.

**G5 — the cost ledger is the judge.** On `Q_DATA`, B bills **3 chats** (the data
specialist's 2 + the aggregator's 1) to A's 2, with `context_union_tokens` 160 (90
specialist + 70 orchestrator) vs A's 90. The union is the number that kills B's excuse:
a delegation duplicates the orchestrator's context rather than replacing it. B's data
delegation returns a receipt, so the data surface works through the seam.

**G6 — the memory reducer.** Four configurations, one question set: **single + memory**
(the run indexes itself under `role=None`; the answer is unchanged — the M8 reflex);
**B shared** (the specialist writes an episode under its own `role`, the orchestrator
writes **only** an `orchestration` record — `source: orchestrator`, never an episode);
**B private** (each role's episode lands in its own store only — `k=1, e=1, d=0`); **B
no-memory ≡ B over an empty store** (same answer, statuses, and `delegated ⊆ returned`).
A shared store costs **zero new memory machinery**; the only new policy is *who may
write*.

**G7 — per-hop grounding.** Each specialist's own `cited ⊆ retrieved` holds
(`hops: [(knowledge, True), (engineering, True)]`), the receipts carry that evidence
verbatim into the orchestrator's context, and **leaked hops == 0**: no evidence is
claimed at a hop its bearer never returned.

**G8 — failure containment.** A crashed specialist returns a **FAILED** receipt
(`reason: error:RuntimeError`, retried once then surfaced) while the **other**
delegation survives (`returned`), and the orchestrator fabricated nothing
(`delegated ⊆ returned: ok`). A timed-out handoff (an injected clock past
`HANDOFF_TIMEOUT`) is a **FAILED** receipt naming the timeout, and with no returned
receipt the orchestrator **names the gap** (`returned_evidence: []`) rather than
commissioning a finding.

**G9 — the control.** With `ROLE_CONTEXT_ROUTING=0` the router is neutral
(`routed_to: None`); orchestration is off by default (`AGENT_TOPOLOGY=single`); routing a
question to a role changes no memory-free answer; an **empty** store changes no
memory-free answer; and A with routing off is byte-for-byte Days 12–18 (same answer,
evidence, exit). Over-orchestration is measured the way over-memory was.

## Layer mechanics (the day's own unit checks)

| Assertion | Result |
|---|---|
| the role-context router is a rule table: 0 chats, transparent (`signal`), overridable (`forced`), neutral under `ROLE_CONTEXT_ROUTING=0` | ok |
| `make_agent(role)` is the existing loop with a **tool-list slice** (`ToolRegistry.scope` keeps the role's tools, denies the rest) | ok |
| an out-of-role call is a **policy** rejection (`role_violation`) — the loop stops; it does not self-correct across the privilege boundary | ok |
| the control: the same call on the unscoped registry executes (the gate is the role, not a missing tool) | ok |
| a delegation is a **tool call**: bounded by `MAX_DELEGATIONS`, walled by `HANDOFF_TIMEOUT`, a **failed** hop retried once, a **refused** hop never retried | ok |
| `delegated ⊆ returned` — every id the orchestrator's answer names appears in a **returned** receipt's evidence | ok |
| the handoff fabrication (the Chinese-whispers injection) and the received-less attribution are both **rejected** (the check is not vacuous) | ok |
| aggregation is by **citation**: receipts enter the prompt as records to cite, and a refused/failed receipt is a **named gap**, not evidence | ok |
| failure is a **return value**: crash → `failed(error:…)`; timeout → `failed(timeout)`; other delegations survive; nothing fabricated | ok |
| per-hop grounding: each specialist `cited ⊆ retrieved`; leaked hops across the seam **== 0** | ok |
| the delegation ledger bills the **union** of both contexts (`specialist_tokens + orchestrator_tokens`) | ok |
| shared store: specialists write episodes under their own `principal`+`role`; the orchestrator writes **only** `kind=orchestration` | ok |
| private stores: each role's episode lands in its own store only (no cross-role bleed) | ok |
| B with **no** memory ≡ B over an **empty** store ≡ B shared (on the answer) — memory is silently optional through the seam | ok |
| the **G9** control: machinery off ≡ Days 12–18 (routing neutral, orchestration off, store empty) | ok |

## No-regression line (Days 7–18 ride the seam)

| Line | Result |
|---|---|
| `test_day7.py` … `test_day10.py` re-run in subprocesses | **exit 0** |
| `tools/tool_smoke.py` (T1–T7) | **exits 0 — `ALL DAY 11 (MECH) CHECKS PASSED`** |
| `tools/agent_smoke.py` (A1–A8) | **exits 0 — `ALL DAY 12 (MECH) CHECKS PASSED`** |
| `tools/failure_smoke.py` (F1–F9) | **exits 0 — `ALL DAY 13 (MECH) CHECKS PASSED`** |
| `tools/data_smoke.py` (Q1–Q8) | **exits 0 — `ALL DAY 14 (MECH) CHECKS PASSED`** |
| `tools/sql_attack_smoke.py` (X1–X11) | **exits 0 — `ALL DAY 15 (MECH) CHECKS PASSED`** |
| `tools/plan_smoke.py` (P1–P9) | **exits 0 — `ALL DAY 16 (MECH) CHECKS PASSED`** |
| `tools/state_smoke.py` (S1–S9) | **exits 0 — `ALL DAY 17 (MECH) CHECKS PASSED`** |
| `tools/memory_smoke.py` (M1–M9) | **exits 0 — `ALL DAY 18 (MECH) CHECKS PASSED`** |

The seam is **additive**. Topology A is byte-for-byte Days 12–18 with routing off; the
only edits to existing code are minimal and optional: `run_agent` gains `routed_to` /
`role` (recorded fields) and honors a **policy** rejection at the validation seam (the
role gate), `ToolRegistry` gains an optional `scope`/`denied` view, `StateStore` gains
`ORCHESTRATION_KIND` + `orchestrator` in `AUTHORED_SOURCES`, and `memory` gains
`remember_episode(role=…)` + `write_orchestration`. With orchestration off and no store,
nothing above changes — which is exactly what G9 proves.

## Cost shape (architecture's first ledger)

| Event | What it spends |
|---|---|
| Topology A's **role-context router** | **0 chats** — a rule-table decision |
| Topology B's **mechanics** (routing plan, receipts, `delegated ⊆ returned`, per-hop check, containment, the ledger) | **0 chats** — set/dict arithmetic |
| a **delegation** | the specialist's own loop chats **plus** the orchestrator's aggregator context — the **union** (`context_union_tokens`, G5) |
| the orchestrator's **aggregation** turn | **1 chat** — the only model call B adds |
| a **refused** delegation | the specialist's chats (the batch ran) and **no citable evidence** |

Day 19 is the series' first *architecture* ledger: A costs nothing new, B's machinery
costs nothing, and delegation's real bill — the duplicated context — is a measured
column, at zero new spend for the deliberative half.

## Config knobs (Day 19)

| Key | Default | Meaning |
|---|---|---|
| `AGENT_TOPOLOGY` | `single` | `single` (A) or `orchestrator` (B) |
| `SPECIALIST_ROLES` | all three | which roles the orchestrator may delegate to |
| `MAX_DELEGATIONS` | `3` | per-question hop cap |
| `HANDOFF_TIMEOUT` | `30` s | the wall around one delegation |
| `DELEGATION_RETRY` | `1` | retries for a **failed** hop; a refused hop is never retried |
| `ROLE_CONTEXT_ROUTING` | `1` | the router's switch (`0` → neutral ≡ Days 12–18) |

## Negatives / `PENDING` (honest, with owners)

1. **Real-model delegation quality** — does a real orchestrator delegate better than the
   single loop on real Saleor questions? The stub proves the *court*; the *model* is
   **Day 21/22's** benchmark on this harness. `PENDING`.
2. **Context isolation's real price** — G1's expected B-win **did not reproduce** under
   the stub (A tied on coverage and cost less). Whether a real model + a real context
   budget makes the split pay is **Day 21/22's**. `PENDING`.
3. **Shared memory at scale** — does a shared store make real answers better than
   private stores? **Day 21** on Day 18's distiller/recall. `PENDING`.
4. **Parallel specialists** — the orchestrator dispatches a linear plan; true concurrency
   is the scoped-if-won prize, gated on G1's measured row. *Named, not built.*
5. **Agent-to-agent state hand-off** — receipts are deliberately stateless. *Named, not
   built.*
6. **Multi-agent observability** — one trace through three loops is **Day 24's**
   distributed trace. `PENDING`.
7. **`delegated ⊆ returned` is id-shaped** — a prose paraphrase that names no id is not
   caught (the same honest limit Day 12's fabrication guard carries). Owner: **Day 21**.

## Verdict (the table forces it)

```text
justified architecture = SINGLE
  the null holds on every monolith fixture; a tie is an A-win (A is cheaper);
  the two-surface fixture ties on coverage and A costs less — so the split's
  context-isolation / latency claims are NOT yet justified under the stub.
  scoped exception: NONE measured. Whether MULTI wins for two-surface tasks at a
  real context budget is PENDING → Day 21/22 on Day 19's harness.
```

**`delegated ⊆ returned = ok` on every B row**, per-hop leaks `0`, and **G9** holding
the line — machinery off ≡ Days 12–18. The `PENDING` line, plainly: **whether a real
orchestrator beats the real single loop on real questions is Day 21/22's verdict on
Day 19's court.**

## Re-verify, in one line

```bash
python tools/architecture_smoke.py    # G1–G9 + the Days 7–18 no-regression re-run
```
