"""Day 14 §7/§8 — the data-agent question set (Q1–Q8) against the scripted stub.

No API key, no network, no Postgres. Reuses Day 11's offline registry (the Day-7
mini doc corpus + the Day-10 code tree) for the retrieval side, scripts the
*chat* wire turn by turn so the "model" emits a `run_saleor_sql` call, and runs
the SQL against the **offline seeded store** (`ai_os/data.py`'s in-memory sqlite
core subset, `PRAGMA query_only` = the read-only layer's offline stand-in).

    $ python tools/data_smoke.py

`MECH` here: the read-only seam (validator rejects before execution; the engine
refuses a write that bypasses the validator; nothing is deleted), the schema
card (curated core subset + glossary, budget-clamped, DB schema not
`schema.graphql`), the validator matrix, the row-limit/truncation arithmetic,
`no_data` as a finding, the SQL-error → `error_kind` mapping, and — the headline
— `asserted ⊆ returned`, proven on both a grounded answer and a fabricated one.

`PENDING`: whether a *real* model writes the right join and the right window for
"which products generated the most revenue last month" from the schema card, and
whether it grounds its explanation in the rows — the stub proves the *seam*
(SQL in, rows out, read-only held), a real key proves the *grounding*, and it is
Day 21's benchmark on Day 14's card and tool.

The no-regression gate re-runs Day 11's `tool_smoke.py` (T1…T7), Day 12's
`agent_smoke.py` (A1…A8), and Day 13's `failure_smoke.py` (F1…F9) as-is, in
subprocesses: a fourth corpus behind the same harness must not move a single one
of their numbers.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
TOOL_SMOKE = Path(__file__).resolve().parent / "tool_smoke.py"
AGENT_SMOKE = Path(__file__).resolve().parent / "agent_smoke.py"
FAILURE_SMOKE = Path(__file__).resolve().parent / "failure_smoke.py"
sys.path.insert(0, str(ROOT))

from ai_os.agent import run_agent  # noqa: E402
from ai_os.data import (  # noqa: E402
    ERROR_INVALID_SQL,
    GLOSSARY,
    DataAgent,
    SqliteDataStore,
    asserted_subset_returned,
    describe_table,
    make_data_store,
    schema_card_text,
    table_names,
    validate_readonly,
)
from ai_os.tools import (  # noqa: E402
    build_default_registry,
    code_retriever,
    doc_retriever,
)

from tools.mock_gateway import (  # noqa: E402
    D14_SQL_BAD_COLUMN,
    D14_SQL_CUSTOMERS,
    D14_SQL_DELETE,
    D14_SQL_LINES,
    D14_SQL_REVENUE,
)
from tools.tool_smoke import (  # noqa: E402
    CODE_TAU,
    DOC_TAU,
    _stub_up,
    build_registry,
    make_gateway,
    reset,
)

FAILS = 0
ROWS: list[dict] = []


def check(label: str, cond: bool, detail: str = ""):
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def record(qid: str, what: str, res: dict, *, call: dict | None = None,
           owner: str = "seam", asserted: bool | None = None):
    """One §8 data-table row, every cell a real run's."""
    call = call if call is not None else res["steps"][0]["calls"][0]
    ROWS.append({
        "id": qid, "what": what,
        "gen_sql": call.get("query_kind") == "generated",
        "kind": call.get("query_kind"),
        "validation": call.get("validation", "—"),
        "executed": bool(call.get("executed")),
        "row_count": call.get("row_count"),
        "truncated": bool(call.get("truncated")),
        "asserted": (res.get("asserted_subset_returned")
                     if asserted is None else asserted),
        "error_kind": call.get("error_kind"),
        "owner": owner,
        "elapsed_ms": call.get("db_elapsed_ms"),
        "cost_usd": res.get("ledger", {}).get("cost_usd", 0.0),
    })


def registry_with_data(data: DataAgent):
    """The seven tools with a *given* data seam (e.g. a small row limit)."""
    _reg, gw_doc, gw_code, doc_store, code_store = build_registry()
    return build_default_registry(
        doc=doc_retriever(gw_doc, doc_store),
        code=code_retriever(gw_code, code_store),
        data=data, doc_tau=DOC_TAU, code_tau=CODE_TAU)


# ============================================================ Q1–Q8 (MECH half)

def q1_aggregation(reg, gw):
    print("\n== Q1 — aggregation (the curriculum example)")
    reset(openai=["tc_d14_revenue", "answer_d14_revenue"])
    res = run_agent("Which products generated the most revenue last month?",
                    reg, gw, max_steps=4)
    call = res["steps"][0]["calls"][0]
    sql = call.get("gen_sql") or ""
    check("the model emitted a generated query (visible in the trace)",
          call.get("query_kind") == "generated" and "group by" in sql.lower(),
          sql[:70])
    check("the SQL joins order lines → variants → products (FKs from the card)",
          "from order_orderline" in sql.lower()
          and "join product_productvariant" in sql.lower()
          and "join product_product p" in sql.lower(), sql[:120])
    check("the window + DRAFT exclusion come from the glossary",
          "2026-08-01" in sql and "2026-09-01" in sql and "draft" in sql.lower())
    check("it executed and returned the three products",
          call.get("executed") and call.get("row_count") == 3,
          f"rows={call.get('row_count')}")
    check("the run records the generated SQL (res['gen_sql'])",
          D14_SQL_REVENUE in res["gen_sql"], str(res["gen_sql"])[:80])
    check("asserted ⊆ returned ok — every number traces to a row",
          res["asserted_subset_returned"] and not res["asserted_unsupported"],
          str(res["asserted_unsupported"]))
    check("fabricated? = no", not res["fabricated"])
    record("Q1", "revenue by product", res)
    return res


def q2_parameterized(reg, gw, agent):
    print("\n== Q2 — the parameterized tool completed (get_order, no stub tag)")
    direct = agent.get_order("1").as_dict()
    check("get_order('1') hits the store directly",
          direct["ok"] and direct["row_count"] == 1, str(direct.get("rows")))
    check("no pending_resolver on the record (the Day 11 stub is filled)",
          "pending_resolver" not in direct, str(list(direct)))

    reset(openai=["tc_d14_order", "answer_d14_orders"])
    res = run_agent("Fetch order 1", reg, gw, max_steps=4)
    call = res["steps"][0]["calls"][0]
    check("the loop reached the fixed-query tool (query_kind: fixed)",
          call.get("query_kind") == "fixed", str(call.get("query_kind")))
    check("it executed against the real store and returned 1 row",
          call.get("executed") and call.get("row_count") == 1,
          str(call.get("row_count")))
    check("the answer's figures are in the result (asserted ⊆ returned)",
          res["asserted_subset_returned"], str(res["asserted_unsupported"]))
    record("Q2", "fetch order 1 (parameterized)", res)
    return res


def q3_multitable_join(reg, gw):
    print("\n== Q3 — multi-table join (customers ⋈ orders)")
    reset(openai=["tc_d14_customers", "answer_d14_customers"])
    res = run_agent("Which customers placed the most orders?", reg, gw, max_steps=4)
    call = res["steps"][0]["calls"][0]
    sql = call.get("gen_sql") or ""
    check("the SQL joins account_user ⋈ order_order (the FK is the join)",
          "from account_user" in sql.lower() and "join order_order" in sql.lower(),
          sql[:100])
    check("three customers returned", call.get("row_count") == 3,
          str(call.get("row_count")))
    check("the answer's emails ⊆ the returned cells",
          res["asserted_subset_returned"], str(res["asserted_unsupported"]))
    check("fabricated? = no", not res["fabricated"])
    record("Q3", "top customers by orders", res)
    return res


def q4_empty(reg, gw, agent):
    print("\n== Q4 — empty result (no_data is a finding, not a failure)")
    direct = agent.run("SELECT id, status FROM order_order "
                       "WHERE created_at >= '2099-01-01'").as_dict()
    check("the query succeeds with an empty row set (a finding, not a failure)",
          direct["ok"] and direct["rows"] == [] and direct["note"] == "no rows",
          str(direct.get("note")))

    reset(openai=["tc_d14_empty", "answer_d14_empty"])
    res = run_agent("How many orders were placed in 2099?", reg, gw, max_steps=4)
    call = res["steps"][0]["calls"][0]
    check("the run is a *finding*: ok, executed, and the answer is grounded",
          call.get("executed") and res["asserted_subset_returned"],
          str(res["asserted_unsupported"]))
    check("zero evidence is fabricated (no invented row)",
          not res["fabricated"] and res["evidence"] == [])
    check("the answer says there were none (0 or 'no orders')",
          any(w in (res["answer"] or "").lower() for w in ("no orders", "0 rows", "none")),
          repr((res["answer"] or "")[:70]))
    record("Q4", "orders in 2099 (empty)", res)
    return res


def q5_readonly(reg, gw, agent):
    print("\n== Q5 — read-only enforced (two independent guards)")
    # Guard 1 — the validator, *before* execution.
    # Day 15 deliberate edit (recorded): the script's second entry is now
    # `answer_d15_policy` — the DELETE is a *policy* rejection, so the loop stops
    # instead of self-correcting, and the forced final turn reports the refusal.
    reset(openai=["tc_d14_delete", "answer_d15_policy"])
    res = run_agent("Delete every order (oops)", reg, gw, max_steps=4)
    call = res["steps"][0]["calls"][0]
    check("the validator rejected the DELETE before execution",
          call.get("validation") == "rejected" and call.get("executed") is False,
          f"validation={call.get('validation')} executed={call.get('executed')}")
    check("the rejection is typed invalid_sql",
          call.get("error_kind") == ERROR_INVALID_SQL, str(call.get("error_kind")))
    check("Day 15: the write is *classified* policy (not a mistake to fix)",
          call.get("rejection_class") == "policy", str(call.get("rejection_class")))
    check("Day 15: refused at parse by the write-head rule",
          call.get("safety_stage") == "parse", str(call.get("safety_stage")))
    check("the generated DELETE is still recorded (visible, gradable)",
          "delete" in (call.get("gen_sql") or "").lower(), str(call.get("gen_sql")))
    check("Day 15: the run STOPPED on the policy violation — no self-correction",
          res["termination_reason"] == "policy" and res["policy_violations"] == 1
          and res["tool_steps"] == 1,
          f"term={res['termination_reason']} violations={res['policy_violations']} "
          f"steps={res['tool_steps']}")
    check("Day 15: the policy stop fabricated nothing",
          not res["fabricated"] and res["asserted_subset_returned"],
          str(res["asserted_unsupported"]))
    check("nothing was deleted (orders still 5)",
          agent.run("SELECT COUNT(*) FROM order_order").rows == [[5]])

    # Guard 2 — the engine, when the validator is bypassed. The offline stand-in
    # for the Postgres `saleor_read_only` role + read-only transaction: layer 2
    # holds even if layer 1 is wrong.
    bypass = agent.run(D14_SQL_DELETE, validate=False)
    check("with the validator bypassed, the engine refuses the write",
          not bypass.ok and bypass.executed and "readonly" in (bypass.error or "").lower(),
          str(bypass.error))
    check("still nothing deleted (the second guard held)",
          agent.run("SELECT COUNT(*) FROM order_order").rows == [[5]])
    record("Q5", "DELETE injected", res, asserted=True, owner="validator + engine")

    # The validator matrix, asserted directly.
    print("  -- validator matrix")
    cases = [
        ("SELECT 1", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", True),
        ("INSERT INTO order_order VALUES ('x')", False),
        ("UPDATE order_order SET status = 'x'", False),
        ("DELETE FROM order_order", False),
        ("DROP TABLE order_order", False),
        ("TRUNCATE order_order", False),
        ("SELECT 1; DROP TABLE order_order", False),
        ("SELECT 'delete me' AS note", True),
        ("SELECT 1 -- DROP TABLE order_order\n", True),
    ]
    for sql, allowed in cases:
        ok, err, kind = validate_readonly(sql)
        check(f"validator: {'allows' if allowed else 'rejects'} {sql[:34]!r}",
              ok is allowed, err or "")
    return res


def q6_error_repairs(reg, gw, agent):
    print("\n== Q6 — SQL error repairs (the error maps to a kind, the loop fixes it)")
    direct = agent.run(D14_SQL_BAD_COLUMN).as_dict()
    check("an unknown column maps to error_kind: unknown_column",
          direct.get("error_kind") == "unknown_column", str(direct.get("error_kind")))
    check("the failed query *reached* the engine (executed: true)",
          direct.get("executed") is True, str(direct.get("executed")))

    reset(openai=["tc_d14_bad_column", "tc_d14_fix", "answer_d14_error"])
    res = run_agent("How many orders are not drafts?", reg, gw, max_steps=4)
    step1, step2 = res["steps"][0], res["steps"][1]
    check("step 1's bad column is a typed observation (not a crash)",
          step1["calls"][0].get("error_kind") == "unknown_column"
          and step1["calls"][0]["result_ok"] is False,
          str(step1["calls"][0].get("error_kind")))
    check("step 2 self-corrects with a *different* query and succeeds",
          step2["result_ok"] and step2["calls"][0].get("row_count") == 1
          and step2["calls"][0].get("gen_sql") != D14_SQL_BAD_COLUMN,
          str(step2["calls"][0].get("gen_sql")))
    check("no fabrication on the failure path",
          not res["fabricated"] and res["asserted_subset_returned"])
    record("Q6", "bad column → repair", res, call=step1["calls"][0], owner="schema grounding")
    return res


def q7_truncation():
    print("\n== Q7 — truncation honesty (a page is reported as a page)")
    data = DataAgent(SqliteDataStore(row_limit=3))
    direct = data.run(D14_SQL_LINES).as_dict()
    check("row_limit caps the result (3 of 7 lines)",
          direct["row_count"] == 3 and direct["truncated"] is True,
          f"rows={direct['row_count']} truncated={direct['truncated']}")
    check("truncation is *measured* (fetch limit+1 saw a 4th row)", direct["truncated"])

    reg = registry_with_data(data)
    gw = make_gateway()
    reset(openai=["tc_d14_lines", "answer_d14_truncated"])
    res = run_agent("List the order lines", reg, gw, max_steps=4)
    call = res["steps"][0]["calls"][0]
    check("the trace flags truncated: true with a capped row_count",
          call.get("truncated") is True and call.get("row_count") == 3,
          f"truncated={call.get('truncated')} rows={call.get('row_count')}")
    check("the explanation says it is a page ('truncated' / 'first 3')",
          "truncat" in (res["answer"] or "").lower()
          or "first 3" in (res["answer"] or "").lower(),
          repr((res["answer"] or "")[:70]))
    check("the page's figures are grounded (asserted ⊆ returned)",
          res["asserted_subset_returned"], str(res["asserted_unsupported"]))
    record("Q7", "lines ≫ limit (truncated)", res)
    data.store.close()
    return res


def q8_grounding(reg, gw, agent):
    print("\n== Q8 — `asserted ⊆ returned` (the day's headline invariant)")
    rows = agent.run(D14_SQL_REVENUE).as_dict()
    grounded = asserted_subset_returned(
        "Desk Lamp led with 96.0, then Blue T-Shirt at 59.97 and Coffee Mug at 38.0.",
        records=[rows])
    check("a grounded explanation passes (numbers ⊆ returned cells)",
          grounded["ok"], str(grounded["unsupported"]))
    fabricated = asserted_subset_returned(
        "Revenue was roughly 250000 in total.", records=[rows])
    check("an invented headline fails (250000 ∉ returned)",
          not fabricated["ok"] and "250000" in fabricated["unsupported"],
          str(fabricated))

    # The same guard over a *transcript*: the fabricated answer turn must be
    # flagged on the run object, not just by the unit function.
    reset(openai=["tc_d14_revenue", "answer_d14_fabricated"])
    res = run_agent("Which products generated the most revenue last month?",
                    reg, gw, max_steps=4)
    check("the run's `asserted_subset_returned` is False for the fabricated turn",
          res["asserted_subset_returned"] is False
          and "250000" in res["asserted_unsupported"],
          str(res["asserted_unsupported"]))
    record("Q8", "grounding (fabricated twin)", res, asserted=False, owner="generation")

    # And a *grounded* turn on the same query is True (the guard is not vacuous).
    reset(openai=["tc_d14_revenue", "answer_d14_revenue"])
    res2 = run_agent("Which products generated the most revenue last month?",
                     reg, gw, max_steps=4)
    check("the same query with a grounded answer is True (guard is not vacuous)",
          res2["asserted_subset_returned"] is True, str(res2["asserted_unsupported"]))
    return res2


# ===================================================== schema grounding (§2/§5)

def schema_grounding():
    print("\n== §2 — the schema card is the DB schema, not schema.graphql")
    card = schema_card_text()
    check("the core tables are named", all(t in card for t in table_names()),
          str(table_names()))
    check("the join FKs are stated (the joins are what make revenue-by-product writable)",
          "FK → product_product.id" in card and "FK → account_user.id" in card)
    check("the glossary is present (the semantic half)",
          all(term in card for term in GLOSSARY), str(list(GLOSSARY)))
    check("it grounds on DB tables, not GraphQL types",
          "product_product" in card and "CheckoutComplete" not in card
          and "schema.graphql" not in card)
    check("the curated card fits the default budget (no silent truncation)",
          "…[schema card truncated" not in card, f"chars={len(card)}")
    clamped = schema_card_text(max_chars=200)
    check("over budget, the cut is *marked* (never silent)",
          clamped.endswith("SALEOR_SQL_SCHEMA_BUDGET]"))

    print("  -- describe_saleor_schema (on-demand expansion)")
    known = describe_table("order_order")
    check("a known table describes its columns",
          known is not None and "total_net_amount" in known["columns"])
    check("an unknown table is not invented", describe_table("order_invoice") is None)


# ================================================================ regression

def no_regression():
    print("\n== Day 14 — no regression: the fourth corpus rides the same harness")
    for name, script in (("tools/tool_smoke.py", TOOL_SMOKE),
                         ("tools/agent_smoke.py", AGENT_SMOKE),
                         ("tools/failure_smoke.py", FAILURE_SMOKE)):
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                              capture_output=True, text=True)
        tail = ""
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                tail = line.strip()
                break
        check(f"{name} still exits 0", proc.returncode == 0, tail)
        if proc.returncode != 0:
            print(proc.stdout[-2500:])


# ----------------------------------------------------------------- reporting

def render():
    print("\n" + "=" * 122)
    print("   DAY 14 — THE DATA AGENT (Q1–Q8): the `asserted ⊆ returned` column never varies")
    print("=" * 122)
    print(f"{'q':<5}{'what':<31}{'gen':<5}{'kind':<11}{'valid':<13}{'exec':<6}"
          f"{'rows':<6}{'trunc':<7}{'asserted':<10}{'err_kind':<15}{'owner':<20}"
          f"{'ms':<8}cost")
    print("-" * 122)
    for r in ROWS:
        print(f"{r['id']:<5}{r['what']:<31}{'y' if r['gen_sql'] else 'n':<5}"
              f"{str(r['kind'] or '—'):<11}{str(r['validation']):<13}"
              f"{'y' if r['executed'] else 'n':<6}{str(r['row_count']):<6}"
              f"{'yes' if r['truncated'] else 'no':<7}"
              f"{'ok' if r['asserted'] else 'FAIL':<10}"
              f"{str(r['error_kind'] or '—'):<15}{r['owner']:<20}"
              f"{(r['elapsed_ms'] or 0):<8.2f}${r['cost_usd']:.8f}")
    print("-" * 122)
    bad = [r["id"] for r in ROWS if not r["asserted"] and r["id"] != "Q8"]
    print(f"asserted ⊆ returned = ok on every answered row: "
          f"{'YES' if not bad else 'NO — ' + ', '.join(bad)}")
    print("Q8 is the deliberate negative twin — the fabricated headline must read "
          "FAIL; a `fail` on any *other* answered row invalidates the run.")
    print("Zero embeds: the database does exact set logic, so no vectors are "
          "needed to *find* the data — the two chats are the query and the prose.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    proc = None
    if not _stub_up():
        proc = subprocess.Popen([sys.executable, str(STUB)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if _stub_up():
                break
            time.sleep(0.1)
        assert _stub_up(), "stub did not come up"

    try:
        reg, gw_doc, gw_code, doc_store, code_store = build_registry()
        gw = make_gateway()
        agent = DataAgent(make_data_store())

        print(f"stores: {len(doc_store)} doc chunks (STUB) / {len(code_store)} "
              f"code symbols (STUB)   τ_doc={DOC_TAU} τ_code={CODE_TAU}")
        print(f"data: seeded core subset (STUB, sqlite query_only)   "
              f"card={len(schema_card_text())} chars   row_limit=100")

        q1_aggregation(reg, gw)
        q2_parameterized(reg, gw, agent)
        q3_multitable_join(reg, gw)
        q4_empty(reg, gw, agent)
        q5_readonly(reg, gw, agent)
        q6_error_repairs(reg, gw, agent)
        q7_truncation()
        q8_grounding(reg, gw, agent)
        schema_grounding()
        no_regression()
        agent.store.close()
    finally:
        if proc is not None:
            proc.terminate()

    render()
    print("\n" + ("ALL DAY 14 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
