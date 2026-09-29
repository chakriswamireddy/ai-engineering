"""Day 18 §8 — the memory question set (M1–M9) against the scripted stub.

No API key, no real network. Reuses Day 11's offline registry (the Day-7 mini doc
corpus + the Day-10 code tree + the Day-14 seeded data store) so the *loop* stays
byte-identical to the harness Days 7–17 grade, drives the Day-18 **memory
subsystem** over Day 17's store — episodic recall (`ai_os/memory.py`), the
distiller (`ai_os/distill.py`), the preference table, `forget`'s re-grounding —
and scripts only the distiller's one chat:

    $ python tools/memory_smoke.py

`MECH` here: `recall()` (Day 8's lexical scorer + Day 9's MMR/dedup + Day 7's τ +
Day 6's top-k, over stored signatures — 0 chats, 0 embeds), the distiller's
mint-time gates (sample size ≥ `MEMORY_MIN_SOURCES`, `authored ⊆ grounded`, no
principal names), semantic expiry (a concept below its strength τ acts absent
before its TTL), `forget`'s derived-aggregate re-grounding (every concept citing a
deleted episode is re-grounded or retired), the preference table's principal keying,
and the **M8 control** (memory off — or an empty store — ≡ Days 12–17, and Days
7–17's suites re-run unchanged). The two invariants read `ok` on every row that
recalls or mints: **`recalled ⊆ authored`** (Day 17) and **`authored ⊆ grounded`**
(Day 18's headline). `PENDING`: whether a *real* model distills a concept that is
actually correct, general, and useful — Day 21's benchmark on this subsystem.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "mock_gateway.py"
sys.path.insert(0, str(ROOT))

from ai_os.agent import run_agent  # noqa: E402
from ai_os.distill import (  # noqa: E402
    decay_concept,
    distill_and_mint,
    parse_concept,
    reprompt_action,
    retire_concept,
    validate_concept,
)
from ai_os.memory import (  # noqa: E402
    recall,
    recall_block,
    episodicize,
    read_preferences,
    remember_episode,
    set_preference,
    compose_signature,
    extract_entities,
)
from ai_os.state import (  # noqa: E402
    EPISODIC_KIND,
    PREFERENCE_KIND,
    SEMANTIC_KIND,
    StateStore,
    concept_key,
    episode_key,
)
from ai_os.trace import (  # noqa: E402
    concept_subset_grounded,
    memory_columns,
    recall_mode,
    recalled_subset_authored,
)

from tools.mock_gateway import (  # noqa: E402
    D18_CONCEPT,
)
from tools.tool_smoke import (  # noqa: E402
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


def record(fid: str, *, kind: str, outcome: str, score_tau: str = "—",
           sources: str = "—", strength: str = "—", rec_authored: bool = True,
           authored_grounded: bool = True, expired: bool = False,
           expired_by: str | None = None, deleted: bool = False,
           privacy_ok: bool = True, cost: float = 0.0, wall_ms: float = 0.0,
           owner: str = "—", ok: bool = True) -> None:
    ROWS.append({
        "id": fid, "kind": kind, "outcome": outcome, "score_tau": score_tau,
        "sources": sources, "strength": strength, "rec_authored": rec_authored,
        "authored_grounded": authored_grounded, "expired": expired,
        "expired_by": expired_by, "deleted": deleted, "privacy_ok": privacy_ok,
        "cost": cost, "wall_ms": wall_ms, "owner": owner, "ok": ok,
    })


class Clock:
    """Injectable wall-clock so TTL/decay arithmetic is deterministic."""

    def __init__(self, start: float | None = None) -> None:
        self.now = time.time() if start is None else start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def tmp_store(clock: Clock, *, default_ttl=None, principal: str = "default",
              enabled: bool = True) -> tuple[StateStore, str]:
    fd, path = tempfile.mkstemp(prefix="mem-d18-", suffix=".json")
    os.close(fd)
    store = StateStore(path, default_ttl=default_ttl, principal=principal,
                       clock=clock, enabled=enabled)
    return store, path


# ------------------------------------------------------------------ fixtures

# The four candidate memories the curriculum lists, routed to their real shelves
# (§7/M9): previous investigations → episodic; known Saleor concepts + past
# successful workflows → semantic (distilled); user preferences → a data table.
def mk_episode(tid: str, question: str, conclusion: str, evidence: list[str],
               claims: dict | None = None) -> dict:
    entities = extract_entities(question + " " + conclusion)
    return {
        "task_id": tid, "question": question, "tools": [], "entities": entities,
        "conclusion": conclusion, "evidence": list(evidence), "claims": claims or {},
        "signature": compose_signature(question, [], entities, conclusion),
    }


EP_E1 = mk_episode("e1", "why did order 4 fail checkout",
                   "checkout failure was caused by a declined payment",
                   ["order 4 status UNFULFILLED", "payment declined"],
                   {"checkout_failure_cause": "payment"})
EP_E2 = mk_episode("e2", "why did order 9 fail checkout",
                   "checkout failure was caused by a declined payment",
                   ["order 9 status UNFULFILLED", "payment declined"],
                   {"checkout_failure_cause": "payment"})
EP_E3 = mk_episode("e3", "why did order 12 fail checkout",
                   "checkout failure was caused by a declined payment",
                   ["order 12 status UNFULFILLED", "payment declined"],
                   {"checkout_failure_cause": "payment"})

# M1 — two finished investigations: one matching a new question, one not. The
# non-matching one is inserted *more recently* so "most recent" would serve it
# (the C1 ancestor on the memory shelf).
EP_MATCH = mk_episode("m1a", "checkout order 4 declined payment",
                      "checkout failed and the payment was declined",
                      ["order 4 status UNFULFILLED", "payment declined"])
EP_OTHER = mk_episode("m1b", "order fulfillment shipping status",
                      "the order was fulfilled and shipped",
                      ["order 7 status FULFILLED", "shipment created"])

M1_QUESTION = "checkout order 4"
MATCHING_SOURCES = [EP_E1, EP_E2]


def put_episode(store: StateStore, principal: str, ep: dict, *, run_id: str) -> None:
    store.put(principal, EPISODIC_KIND, episode_key(ep["task_id"]), ep,
              created_by={"run_id": run_id, "step": "end", "source": "agent"})


# ============================================================ M1 — recall relevance
def m1_episodic_recall(gw):
    print("\n== M1 — episodic recall: the MATCHING investigation, scored, above recency")
    store, path = tmp_store(Clock(), principal="m1")
    put_episode(store, "m1", EP_MATCH, run_id="r-m1")
    put_episode(store, "m1", EP_OTHER, run_id="r-m1")   # newer, but not matching
    t0 = time.perf_counter()
    res = recall(M1_QUESTION, "m1", store)
    wall_ms = (time.perf_counter() - t0) * 1000
    check("recall served the matching investigation (an episode)",
          res["recalled"] and res["recalled"][0]["task_id"] == "m1a"
          and res["recalled"][0]["served_as"] == "episode",
          str([(e["task_id"], e["score"]) for e in res["recalled"]]))
    check("...above the other, SCORED (not 'most recent')",
          len(res["recalled"]) >= 2
          and res["recalled"][0]["score"] > res["recalled"][1]["score"],
          str([(e["task_id"], e["score"]) for e in res["recalled"]]))
    check("the newer, non-matching episode is NOT served first",
          res["recalled"][0]["task_id"] != "m1b")
    check("recall is free: 0 chats, 0 embeds",
          res["cost"]["chats"] == 0 and res["cost"]["embeds"] == 0)
    check("served episodes are authored (`recalled ⊆ authored`)",
          all(e["provenance"] for e in res["recalled"]))
    # Empty shelf → nothing, the honest refusal (not a fabricated 'past').
    empty, epath = tmp_store(Clock(), principal="m1")
    empty_res = recall(M1_QUESTION, "m1", empty)
    check("an empty shelf returns NOTHING (refuse, never a hallucinated past)",
          empty_res["recalled"] == [] and empty_res["refused"] is True
          and recall_mode(empty_res) == "none")
    # Stale episode acts absent (Day 17's clock, on the recall path).
    stale_clock = Clock()
    stale, spath = tmp_store(stale_clock, default_ttl=10.0, principal="m1")
    put_episode(stale, "m1", EP_MATCH, run_id="r-m1")
    stale_clock.advance(11.0)
    check("a stale (expired) episode acts absent on the recall path",
          recall(M1_QUESTION, "m1", stale)["recalled"] == [])
    record("M1", kind="episodic", outcome="recalled",
           score_tau=f"top1 {res['recalled'][0]['score']} / τ {res['tau']}",
           sources="m1a", rec_authored=True, cost=0.0, wall_ms=wall_ms,
           owner="recall scorer")
    for p in (path, epath, spath):
        os.unlink(p)
    return res, wall_ms


# ============================================================ M2 — the distiller
def m2_distiller(gw):
    print("\n== M2 — the distiller: N agreeing episodes → one grounded concept")
    store, path = tmp_store(Clock(), principal="m2")
    for ep in MATCHING_SOURCES:
        put_episode(store, "m2", ep, run_id="r-m2")
    reset(openai=["answer_d18_concept"])
    out = distill_and_mint(MATCHING_SOURCES, store, "m2", gw, run_id="r-m2")
    rec = out.get("record")
    check("the distiller minted a concept (one chat, gated by code)",
          out["ok"] and rec is not None and rec.kind == SEMANTIC_KIND,
          str(out["validation"]["reasons"]))
    if rec is not None:
        payload = rec.payload
        check("the concept carries claim + source_episodes + strength",
              payload["claim"] == "checkout failure caused by declined payment"
              and payload["source_episodes"] == ["e1", "e2"]
              and 0.0 < payload["strength"] <= 1.0, str(payload))
        check("provenance: created_by source is `distiller` (an authorized author)",
              rec.created_by["source"] == "distiller"
              and rec.created_by["run_id"] == "r-m2", str(rec.created_by))
        check("the store admitted the distiller's write (`recalled ⊆ authored`)",
              recalled_subset_authored([rec]))
        check("the mint gate reads `authored ⊆ grounded = ok`",
              concept_subset_grounded(out["validation"]))
        # And it is now recallable, as a *concept*.
        res = recall("checkout failure declined payment", "m2", store)
        check("the minted concept is recallable (served as a concept, not an episode)",
              any(c["served_as"] == "concept" for c in res["recalled"]),
              str([(c["kind"], c["score"]) for c in res["recalled"]]))
    record("M2", kind="semantic", outcome="minted", sources="e1,e2",
           strength=f"{out.get('strength')}", rec_authored=True,
           authored_grounded=concept_subset_grounded(out["validation"]),
           cost=1.0, owner="distiller (1 chat)", ok=out["ok"])
    os.unlink(path)
    return out


# ==================================================== M3 — `authored ⊆ grounded`
def m3_groundedness(gw):
    print("\n== M3 — authored ⊆ grounded: over-claim / one-source / contradiction rejected")
    store, path = tmp_store(Clock(), principal="m3")
    for ep in MATCHING_SOURCES:
        put_episode(store, "m3", ep, run_id="r-m3")

    cases = [
        ("overclaim", "answer_d18_overclaim", "ungrounded"),
        ("one-source", "answer_d18_onesource", "sample_size"),
        ("contradiction", "answer_d18_contradict", "contradiction"),
    ]
    for label, scenario, needle in cases:
        reset(openai=[scenario])
        out = distill_and_mint(MATCHING_SOURCES, store, "m3", gw, run_id="r-m3")
        reasons = out["validation"]["reasons"]
        check(f"{label}: rejected at mint time ({needle})",
              not out["ok"] and out["record"] is None and needle in reasons,
              str(reasons))
        record(f"M3-{label}", kind="semantic", outcome="rejected", sources="—",
               rec_authored=False, authored_grounded=False, cost=1.0,
               owner="distiller mint gate", ok=not out["ok"])
    check("no softened record was written (the semantic shelf is empty)",
          store.list_for("m3", SEMANTIC_KIND) == [])
    # A one-episode candidate is an anecdote, proven directly on the gate too.
    v = validate_concept(parse_concept(D18_CONCEPT), [MATCHING_SOURCES[0]],
                         min_sources=2)
    check("a single-receipt lesson is refused by the sample-size rule",
          not v["ok"] and "sample_size" in v["reasons"], str(v["reasons"]))
    os.unlink(path)


# ============================================================ M4 — semantic expiry
def m4_semantic_expiry(gw):
    print("\n== M4 — semantic expiry: relevance decays BEFORE the clock bites")
    clock = Clock()
    store, path = tmp_store(clock, default_ttl=86400.0, principal="m4")
    dec = store.put("m4", SEMANTIC_KIND, concept_key("c-decay"),
                    {"claim": "checkout failure caused by declined payment",
                     "source_episodes": ["e1", "e2"], "strength": 0.0},
                    created_by={"run_id": "r-m4", "step": "d", "source": "distiller"},
                    ttl=86400.0, strength=0.0, recall_threshold=0.5)
    check("a concept below its strength τ decays by RELEVANCE, not the clock",
          dec is not None and dec.is_decayed()
          and not dec.is_expired(clock()) and dec.expires_at > clock(),
          f"expires_at={dec.expires_at} now={clock()}")
    res = recall("checkout failure declined payment", "m4", store)
    check("...it acts ABSENT on recall (unserved) before its TTL",
          res["recalled"] == [] and recall_mode(res) == "below-τ",
          f"mode={recall_mode(res)}")
    check("...but it is still ALIVE (visible to list_for — inspectable)",
          any(r.key == concept_key("c-decay")
              for r in store.list_for("m4", SEMANTIC_KIND)))

    # The re-prompt loop's verbs: a contradictory fresh observation re-distills; a
    # served-but-decayed concept is retired; sustained misses decay.
    healthy = store.put("m4", SEMANTIC_KIND, concept_key("c-live"),
                        {"claim": "checkout failure caused by declined payment",
                         "source_episodes": ["e1", "e2"]},
                        created_by={"run_id": "r-m4", "step": "d", "source": "distiller"},
                        ttl=86400.0, strength=0.9, recall_threshold=0.5)
    check("a fresh concept (strength 0.9 ≥ τ 0.5) IS served",
          recall("checkout failure declined payment", "m4", store)["recalled"] != [])
    decay_concept(store, "m4", concept_key("c-live"), rate=0.5)   # 0.9 → 0.45 < 0.5
    check("decay lowers strength below τ (alive but unserved)",
          healthy.is_decayed()
          and recall("checkout failure declined payment", "m4", store)["recalled"] == [])
    check("the re-prompt loop re-distills on a contradiction, retires a decayed concept",
          reprompt_action(healthy, missed=0, contradicted=True) == "re-distill"
          and reprompt_action(healthy, missed=0, contradicted=False) == "retire")
    record("M4", kind="semantic", outcome="unserved",
           score_tau="strength 0.0 / τ 0.5", expired=True, expired_by="relevance",
           authored_grounded=True, cost=0.0, owner="semantic expiry")
    retire_concept(store, "m4", concept_key("c-decay"))
    os.unlink(path)


# ============================================================ M5 — deletion
def m5_deletion(gw):
    print("\n== M5 — deletion end-to-end: forget re-grounds the concepts it sired")
    store, path = tmp_store(Clock(), principal="m5")
    for ep in MATCHING_SOURCES:
        put_episode(store, "m5", ep, run_id="r-m5")
    reset(openai=["answer_d18_concept"])
    out = distill_and_mint(MATCHING_SOURCES, store, "m5", gw, run_id="r-m5")
    cid = out["concept_id"]
    check("premise: a concept citing e1+e2 exists",
          store.get("m5", SEMANTIC_KIND, concept_key(cid)) is not None)
    removed = store.forget("m5", key=episode_key("e1"))
    check("forget removed the episode", removed == 1)
    check("the concept that cited it was RETIRED (now below sample size)",
          store.get("m5", SEMANTIC_KIND, concept_key(cid)) is None
          and any(r["action"] == "retired" for r in store.regrounded),
          str(store.regrounded))
    check("...unreachable through get/list_for/recall",
          store.get("m5", SEMANTIC_KIND, concept_key(cid)) is None
          and store.list_for("m5", SEMANTIC_KIND) == []
          and all(m["served_as"] != "concept"
                  for m in recall("checkout failure declined payment", "m5",
                                  store)["recalled"]))
    check("deletion is visible in the trace (re-grounding recorded)",
          bool(store.regrounded)
          and memory_columns(enabled=True, deleted=True)["deleted"] is True)

    # Re-grounding (not retirement) when enough sources survive.
    store2, path2 = tmp_store(Clock(), principal="m5b")
    for ep in (EP_E1, EP_E2, EP_E3):
        put_episode(store2, "m5b", ep, run_id="r-m5b")
    concept = store2.put("m5b", SEMANTIC_KIND, concept_key("c3"),
                         {"claim": "checkout failure caused by declined payment",
                          "source_episodes": ["e1", "e2", "e3"],
                          "episodes_considered": 3},
                         created_by={"run_id": "r-m5b", "step": "d",
                                     "source": "distiller"},
                         ttl=86400.0, strength=1.0, recall_threshold=0.5)
    store2.forget("m5b", key=episode_key("e1"))
    check("a concept with enough surviving sources is RE-GROUNDED (source list shrinks)",
          concept is not None and concept.payload["source_episodes"] == ["e2", "e3"]
          and any(r["action"] == "re-grounded" for r in store2.regrounded),
          str(store2.regrounded))
    record("M5", kind="semantic", outcome="(gone)", deleted=True, expired=False,
           authored_grounded=True, cost=0.0, owner="forget re-grounding")
    os.unlink(path)
    os.unlink(path2)


# ============================================================ M6 — privacy
def m6_privacy(gw):
    print("\n== M6 — privacy: principal-bounded reads; preferences are DATA; no names")
    store, path = tmp_store(Clock(), principal="u1")
    set_preference(store, "u1", "currency", "EUR", run_id="r-u1")
    put_episode(store, "u1", EP_MATCH, run_id="r-u1")
    prefs = read_preferences(store, "u1")
    check("a preference serves its OWN principal as cited data",
          len(prefs) == 1 and prefs[0]["value"] == "EUR"
          and prefs[0]["source"] == "user-stated", str(prefs))
    check("a preference is stored as kind=preference (a data table, not a memory)",
          store.get("u1", PREFERENCE_KIND, "/preference/currency") is not None)
    check("a CROSS-principal preference read returns NOTHING",
          read_preferences(store, "u2") == [])
    check("a cross-principal memory recall returns NOTHING (the keying)",
          recall(M1_QUESTION, "u2", store)["recalled"] == [])
    # `list_for` can show a principal everything about *them*, nothing about others.
    check("inspectability: list_for(u1, *) holds only u1's records",
          all(r.principal == "u1" for r in store.list_for("u1")))
    # A preference is never distilled: it is not an episodic record, so the
    # distiller's episodic-only batch cannot select it as a source (§7).
    check("a preference is never in a distiller batch (episodic-only selection)",
          store.get("u1", EPISODIC_KIND, "/preference/currency") is None
          and store.list_for("u1", SEMANTIC_KIND) == [])
    # A concept naming a principal is rejected at mint time (a code check).
    reset(openai=["answer_d18_principal"])
    out = distill_and_mint(MATCHING_SOURCES, store, "u1", gw, run_id="r-u1")
    check("a concept naming a principal is REJECTED (privacy is a mint check)",
          not out["ok"] and "principal_name" in out["validation"]["reasons"],
          str(out["validation"]["reasons"]))
    record("M6", kind="preference", outcome="none", privacy_ok=True,
           rec_authored=True, cost=1.0, owner="keying / mint gate")
    os.unlink(path)


# ============================================================ M7 — shelf boundary
def m7_boundary(gw):
    print("\n== M7 — shelf boundary: an episode is cited, never absorbed as a lesson")
    store, path = tmp_store(Clock(), principal="b1")
    put_episode(store, "b1", EP_MATCH, run_id="r-b1")
    before = gw.ledger.records.__len__()
    res = recall(M1_QUESTION, "b1", store)
    check("recall serves the episode WITHOUT distilling (no chat, no concept minted)",
          len(gw.ledger.records) == before
          and store.list_for("b1", SEMANTIC_KIND) == []
          and all(e["served_as"] == "episode" for e in res["recalled"]))
    block = recall_block(res)
    check("the recall block marks episodes AS episodes (do not generalize)",
          block is not None and "EPISODE" in block
          and "do not generalize" in block, str(block)[:80])
    check("no run-to-run bleed: a different principal recalls nothing",
          recall(M1_QUESTION, "b2", store)["recalled"] == [])
    record("M7", kind="episodic", outcome="recalled", sources="b1",
           rec_authored=True, cost=0.0, owner="episode-as-citation / keying")
    os.unlink(path)


# ============================================================ M8 — control
def m8_control(reg, gw):
    print("\n== M8 — control: memory off (and an empty store) ≡ Days 12–17")
    reset(openai=["tc_order", "answer_d14_orders"])
    baseline = run_agent("Fetch order 1", reg, gw)

    off_store, opath = tmp_store(Clock(), principal="c1", enabled=False)
    reset(openai=["tc_order", "answer_d14_orders"])
    off_run = run_agent("Fetch order 1", reg, gw, memory_store=off_store)

    empty_store, epath = tmp_store(Clock(), principal="c1")
    reset(openai=["tc_order", "answer_d14_orders"])
    empty_run = run_agent("Fetch order 1", reg, gw, memory_store=empty_store)

    def same(a, b):
        return (a["answer"] == b["answer"]
                and set(a["evidence"]) == set(b["evidence"])
                and a["termination_reason"] == b["termination_reason"])

    check("memory OFF ≡ Days 12–17 (identical answer / evidence / exit)",
          same(baseline, off_run), f"{off_run['termination_reason']}")
    check("memory ON with an EMPTY store ≡ Days 12–17 (silently optional)",
          same(baseline, empty_run), f"{empty_run['termination_reason']}")
    check("the run reports the switch: off → 'off'; empty on-store → 'on'/'none'",
          off_run["memory_on_off"] == "off"
          and empty_run["memory_on_off"] == "on"
          and empty_run["recall_mode"] == "none",
          f"{off_run['memory_on_off']}/{empty_run['memory_on_off']}/"
          f"{empty_run['recall_mode']}")
    # The deliverable's first arrow: a finished run indexes itself. Memory ON writes
    # exactly one *authored* episodic record at run-end; memory OFF writes nothing.
    indexed = empty_store.list_for("default", EPISODIC_KIND)
    check("memory ON: the finished run indexed itself (1 authored episodic record)",
          len(indexed) == 1 and bool(indexed[0].provenance())
          and empty_run["episodic_indexed"] is True, str([r.key for r in indexed]))
    check("memory OFF: the run wrote NOTHING (the OFF switch is real)",
          off_store.records == {} and off_run["episodic_indexed"] is False)
    # MEMORY_ENABLED=0 forces the subsystem off even with an enabled store — the
    # documented knob is wired, not decorative.
    os.environ["MEMORY_ENABLED"] = "0"
    try:
        env_off = run_agent("Fetch order 1", reg, gw, memory_store=empty_store)
    finally:
        os.environ.pop("MEMORY_ENABLED", None)
    check("MEMORY_ENABLED=0 forces memory off (the knob is wired, not decorative)",
          env_off["memory_on_off"] == "off"
          and env_off["recall_mode"] == "none"
          and env_off["episodic_indexed"] is False)
    record("M8", kind="—", outcome="memory off", rec_authored=False,
           authored_grounded=False, cost=0.0, wall_ms=0.0, owner="M8 control",
           ok=same(baseline, off_run) and same(baseline, empty_run))
    os.unlink(opath)
    os.unlink(epath)
    return baseline


# ============================================================ M9 — routing + meter
def m9_routing(gw, recall_wall_ms: float, fresh_cost: float):
    print("\n== M9 — curriculum routing: four memories → their real shelves; meter")
    clock = Clock()
    store, path = tmp_store(clock, principal="m9")
    # previous investigations → episodic
    put_episode(store, "m9", EP_MATCH, run_id="r-m9")
    # past successful workflow + known Saleor concept → semantic (distilled)
    reset(openai=["answer_d18_concept"])
    minted = distill_and_mint(MATCHING_SOURCES, store, "m9", gw, run_id="r-m9")
    # user preferences → a data table
    set_preference(store, "m9", "currency", "EUR", run_id="r-m9")

    episodic = store.list_for("m9", EPISODIC_KIND)
    semantic = store.list_for("m9", SEMANTIC_KIND)
    prefs = store.list_for("m9", PREFERENCE_KIND)
    check("previous investigations → episodic shelf",
          len(episodic) == 1 and episodic[0].kind == EPISODIC_KIND)
    check("past workflows + known concepts → semantic shelf (distilled)",
          len(semantic) == 1 and semantic[0].kind == SEMANTIC_KIND
          and semantic[0].payload["source_episodes"] == ["e1", "e2"])
    check("user preferences → a DATA TABLE (kind=preference), never a memory",
          len(prefs) == 1 and prefs[0].kind == PREFERENCE_KIND
          and store.get("m9", SEMANTIC_KIND, concept_key("currency")) is None)
    # The "only if useful" measurement: recall is free and serves the match; a
    # fresh investigation would have cost >= 1 chat (the baseline run's ledger).
    res = recall(M1_QUESTION, "m9", store)
    check("does recall beat a fresh investigation? — measured, not assumed",
          bool(res["recalled"]) and res["cost"]["chats"] == 0
          and fresh_cost > 0.0,
          f"recall {res['cost']['chats']} chats vs fresh ${fresh_cost:.8f}")
    record("M9", kind="all", outcome="measured",
           score_tau=f"τ-fit top1 {res['recalled'][0]['score'] if res['recalled'] else '—'}",
           sources="e1,e2", rec_authored=True, authored_grounded=True,
           cost=1.0, wall_ms=recall_wall_ms,
           owner="routing + only-if-useful meter", ok=bool(res["recalled"]))
    os.unlink(path)
    return res


# ---------------------------------------------------------------- regression
SUITES = ["test_day7.py", "test_day8.py", "test_day9.py", "test_day10.py",
          "tool_smoke.py", "agent_smoke.py", "failure_smoke.py", "data_smoke.py",
          "sql_attack_smoke.py", "plan_smoke.py", "state_smoke.py"]


def no_regression():
    print("\n== Day 18 — no regression: Days 7–17 re-run unchanged (memory adds no schema)")
    for name in SUITES:
        script = (ROOT / name) if name.startswith("test_") else (ROOT / "tools" / name)
        proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                              capture_output=True, text=True)
        tail = ""
        for line in reversed(proc.stdout.splitlines()):
            if line.strip():
                tail = line.strip()
                break
        check(f"{name} still exits 0", proc.returncode == 0, tail)
        if proc.returncode != 0:
            print(proc.stdout[-2000:])


# ------------------------------------------------------------------- reporting
def render():
    print("\n" + "=" * 150)
    print("   DAY 18 — MEMORY AS A CONTRACT (§9 memory table; recalled ⊆ authored AND "
          "authored ⊆ grounded on every row)")
    print("=" * 150)
    print(f"{'id':<12}{'kind':<11}{'minted/recalled':<16}{'score/τ':<22}{'sources':<10}"
          f"{'strength':<9}{'rec⊆auth':<10}{'auth⊆grd':<10}{'expired':<12}"
          f"{'deleted':<9}{'privacy':<9}{'wall_ms':<9}{'cost':<7}")
    print("-" * 150)
    for r in ROWS:
        exp = r["expired_by"] if r["expired"] else "no"
        print(f"{r['id']:<12}{str(r['kind']):<11}{r['outcome']:<16}"
              f"{r['score_tau']:<22}{str(r['sources']):<10}{str(r['strength']):<9}"
              f"{'ok' if r['rec_authored'] else '—':<10}"
              f"{'ok' if r['authored_grounded'] else '—':<10}"
              f"{exp:<12}{'y' if r['deleted'] else 'no':<9}"
              f"{'ok' if r['privacy_ok'] else 'FAIL':<9}"
              f"{r['wall_ms']:<9.3f}${r['cost']:.2f}")
    print("-" * 150)
    bad = [r["id"] for r in ROWS if not r["ok"]]
    inv1 = all(r["rec_authored"] for r in ROWS
               if r["outcome"] in ("recalled", "minted"))
    inv2 = all(r["authored_grounded"] for r in ROWS if r["outcome"] == "minted")
    print(f"recalled ⊆ authored = ok on every row that recalls: "
          f"{'YES' if inv1 else 'NO'}")
    print(f"authored ⊆ grounded = ok on every row that mints: "
          f"{'YES' if inv2 else 'NO'}")
    print(f"rows ok: {'ALL' if not bad else 'FAILURES ' + ', '.join(bad)}")
    print("Cost: the recall path is 0 chats / 0 embeds (lexical over stored signatures); "
          "the distiller is 1 chat per batch, gated by code. The M8 row is the "
          "counterweight: memory off ≡ Days 12–17.")


# --------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    args = ap.parse_args()  # noqa: F841

    proc = None
    try:
        if not _stub_up():
            proc = subprocess.Popen([sys.executable, str(STUB)],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            for _ in range(100):
                if _stub_up():
                    break
                time.sleep(0.1)
            if not _stub_up():
                raise SystemExit("stub did not come up")

        reg, _gw_doc, _gw_code, _doc_store, _code_store = build_registry()
        gw = make_gateway()
        print(f"memory: MEMORY_RECALL_TOP_K={os.getenv('MEMORY_RECALL_TOP_K', '5 (default)')} "
              f"MEMORY_RECALL_TAU={os.getenv('MEMORY_RECALL_TAU', '0.05 (default)')} "
              f"MEMORY_MIN_SOURCES={os.getenv('MEMORY_MIN_SOURCES', '2 (default)')} "
              f"MEMORY_DISTILL_TTL={os.getenv('MEMORY_DISTILL_TTL', '604800 (default)')}")

        _res, wall = m1_episodic_recall(gw)
        m2_distiller(gw)
        m3_groundedness(gw)
        m4_semantic_expiry(gw)
        m5_deletion(gw)
        m6_privacy(gw)
        m7_boundary(gw)
        baseline = m8_control(reg, gw)
        fresh_cost = baseline["ledger"]["cost_usd"]
        m9_routing(gw, wall, fresh_cost)
        no_regression()
    finally:
        if proc is not None:
            proc.terminate()

    render()
    print("\n" + ("ALL DAY 18 (MECH) CHECKS PASSED" if not FAILS
                  else f"{FAILS} CHECK(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
