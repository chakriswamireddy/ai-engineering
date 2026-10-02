#!/usr/bin/env python3
"""WS3 — the write path fixture (W1–W6), offline except W4.

The mechanism WS4 wires to a workflow: policy-gated, transacted, measured
writes — and every refusal a typed observation, never an exception.

- W1 DELETE without approval → policy refusal, `wrote=False`, rows unchanged
- W2 UPDATE with approval + grant → 1 row written, `rows_changed` measured
- W3 engine error mid-write → rollback, rows unchanged
- W4 live Postgres: read-only role refused (fails closed), writer writes
  (skipped with a named reason when Docker is unavailable)
- W5 policy unit: reads, ungranted tables, denied columns, stacked DDL refused
- W6 the read path is untouched: `query()` reads after writes, refuses writes

W4 needs Docker (`postgres:15-alpine`); everything else is stdlib sqlite.
`SALEOR rows changed` is counted, not asserted as zero — the WS3 inversion.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_os.data import DataAgent, PostgresDataStore, SqliteDataStore  # noqa: E402
from ai_os.sql_safety import load_policy  # noqa: E402

FAILS = 0
WRITE_TABLES = ["product_product"]
WRITE_HEADS = ["insert", "update", "delete"]


def check(label: str, cond: bool, detail: str = "") -> bool:
    global FAILS
    if not cond:
        FAILS += 1
    print(f"[{'OK  ' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def fixture_policy():
    base = load_policy()
    return replace(base, statement_allow_write=list(WRITE_HEADS),
                   write_tables_allow=list(WRITE_TABLES), require_approval=True)


def fresh():
    store = SqliteDataStore()
    return store, DataAgent(store, policy=fixture_policy())


def count(store) -> int:
    return int(store.scalar("SELECT COUNT(*) FROM product_product"))


def w1_no_approval_no_write() -> None:
    store, agent = fresh()
    before = count(store)
    res = agent.run_write("DELETE FROM product_product WHERE id = 1",
                          principal="analyst", approval_id=None,
                          granted_tables=list(WRITE_TABLES))
    check("W1 DELETE without approval is refused",
          not res.ok and not res.executed and not res.wrote
          and res.error_kind == "policy_violation", res.error_kind or "")
    check("W1 rows unchanged after refusal", count(store) == before == 3,
          f"count={count(store)}")


def w2_approval_grant_writes_one() -> None:
    store, agent = fresh()
    res = agent.run_write("UPDATE product_product SET name = 'Red T-Shirt' WHERE id = 1",
                          principal="analyst", approval_id="apr-1",
                          granted_tables=list(WRITE_TABLES))
    check("W2 approved + granted UPDATE writes exactly 1 row",
          res.ok and res.wrote and res.rows_changed == 1
          and res.approval_id == "apr-1" and res.principal == "analyst",
          f"ok={res.ok} rows={res.rows_changed}")
    got = store.scalar("SELECT name FROM product_product WHERE id = 1")
    check("W2 the row carries the new value", got == "Red T-Shirt", str(got))
    check("W2 table still has all rows", count(store) == 3)


def w3_rollback_on_error() -> None:
    store, agent = fresh()
    before_name = store.scalar("SELECT name FROM product_product WHERE id = 2")
    res = agent.run_write("UPDATE product_product SET bogus_col = 'x' WHERE id = 2",
                          principal="analyst", approval_id="apr-1",
                          granted_tables=list(WRITE_TABLES))
    check("W3 engine error fails the write", not res.ok and not res.wrote,
          res.error_kind or "")
    check("W3 the failed write rolled back", res.rolled_back
          and store.scalar("SELECT name FROM product_product WHERE id = 2") == before_name,
          f"rolled_back={res.rolled_back}")


def _docker_ok() -> bool:
    return shutil.which("docker") is not None


def w4_postgres_roles() -> None:
    if not _docker_ok():
        print("[SKIP] W4 live Postgres (no docker on this host)")
        return
    name = "ws3pg"
    port = "54333"
    password = "ws3test"
    subprocess.run(["docker", "rm", "-f", name], check=False,
                   capture_output=True)
    try:
        proc = subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", name,
             "-e", f"POSTGRES_PASSWORD={password}", "-p", f"{port}:5432",
             "postgres:15-alpine"],
            capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"[SKIP] W4 live Postgres (docker run failed: "
                  f"{proc.stderr.strip()[:120]})")
            return
        if not _wait_pg(port, password):
            print("[SKIP] W4 live Postgres (server did not become ready)")
            return
        _seed_pg(port, password)
        reader_url = "postgres://saleor_read_only:roread@127.0.0.1:54333/postgres"
        reader = PostgresDataStore(reader_url)
        refused = reader.execute_write(
            "UPDATE t SET v = 'x'", "analyst", approval_id="apr-1")
        reader.close()
        check("W4 read-only role fails closed (no write session)",
              not refused.ok and not refused.wrote
              and "read-only role" in (refused.error or ""),
              (refused.error or "")[:100])
        writer_url = "postgres://writer:writer@127.0.0.1:54333/postgres"
        writer = PostgresDataStore(writer_url)
        wrote = writer.execute_write("UPDATE t SET v = 'wrote' WHERE id = 1",
                                     "analyst", approval_id="apr-1")
        writer.close()
        check("W4 writer role commits with measured rowcount",
              wrote.ok and wrote.wrote and wrote.rows_changed == 1,
              f"ok={wrote.ok} rows={wrote.rows_changed}")
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False,
                       capture_output=True)


def _wait_pg(port: str, password: str, tries: int = 30) -> bool:
    env = dict(os.environ, PGPASSWORD=password)
    for _ in range(tries):
        proc = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", port, "-U", "postgres",
             "-c", "SELECT 1"],
            capture_output=True, env=env)
        if proc.returncode == 0:
            return True
        time.sleep(1)
    return False


def _seed_pg(port: str, password: str) -> None:
    env = dict(os.environ, PGPASSWORD=password)
    sql = (
        "CREATE TABLE t (id INT PRIMARY KEY, v TEXT);"
        "INSERT INTO t VALUES (1, 'a');"
        # the documented read-only role (cf. replica_user.sql): SELECT only
        "CREATE ROLE saleor_read_only LOGIN PASSWORD 'roread';"
        "CREATE ROLE writer LOGIN PASSWORD 'writer';"
        "GRANT CONNECT ON DATABASE postgres TO saleor_read_only, writer;"
        "GRANT USAGE ON SCHEMA public TO saleor_read_only, writer;"
        "GRANT SELECT ON t TO saleor_read_only;"
        "GRANT SELECT, INSERT, UPDATE, DELETE ON t TO writer;"
    )
    subprocess.run(["psql", "-h", "127.0.0.1", "-p", port, "-U", "postgres",
                    "-c", sql], check=True, capture_output=True, env=env)


def w5_policy_units() -> None:
    _, agent = fresh()
    kw = {"principal": "analyst", "approval_id": "apr-1",
          "granted_tables": list(WRITE_TABLES)}
    sel = agent.run_write("SELECT * FROM product_product", **kw)
    check("W5 a read on the write path is refused",
          not sel.ok and sel.error_kind == "policy_violation")
    other = agent.run_write("DELETE FROM order_order WHERE id = 1", **kw)
    check("W5 a table outside write_tables_allow is refused",
          not other.ok and other.error_kind == "policy_violation"
          and not other.wrote,
          other.error_kind or "")
    deny = agent.run_write("UPDATE product_product SET password = 'x' WHERE id = 1",
                           **kw)
    check("W5 a denied column is refused before the engine",
          not deny.ok and deny.error_kind == "policy_violation",
          deny.error_kind or "")
    stacked = agent.run_write("DELETE FROM product_product WHERE id = 1; "
                              "DELETE FROM product_product WHERE id = 2", **kw)
    check("W5 stacked statements are refused",
          not stacked.ok and not stacked.wrote, stacked.error_kind or "")
    drop = agent.run_write("DROP TABLE product_product", **kw)
    check("W5 DDL heads are refused", not drop.ok and not drop.wrote,
          drop.error_kind or "")
    nog = agent.run_write("DELETE FROM product_product WHERE id = 1",
                          principal="analyst", approval_id="apr-1",
                          granted_tables=["order_order"])
    check("W5 a table outside the caller's grant is refused",
          not nog.ok and nog.error_kind == "policy_violation",
          nog.error_kind or "")


def w6_read_path_intact() -> None:
    store, agent = fresh()
    wrote = agent.run_write("UPDATE product_product SET name = 'Green' WHERE id = 3",
                            principal="analyst", approval_id="apr-1",
                            granted_tables=list(WRITE_TABLES))
    assert wrote.ok, wrote
    rows = store.query("SELECT name FROM product_product WHERE id = 3")
    check("W6 query() reads after writes", rows.ok and rows.rows == [["Green"]],
          str(rows.rows))
    refused = store.query("DELETE FROM product_product WHERE id = 3")
    check("W6 query() still refuses writes at the engine",
          not refused.ok and count(store) == 3, refused.error_kind or "")


def main() -> int:
    print("== WS3 — the write path (W1–W6) ==")
    w1_no_approval_no_write()
    w2_approval_grant_writes_one()
    w3_rollback_on_error()
    w4_postgres_roles()
    w5_policy_units()
    w6_read_path_intact()
    print("\n" + ("ALL WRITE CHECKS PASSED" if not FAILS else f"{FAILS} CHECK(S) FAILED"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
