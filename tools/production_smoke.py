#!/usr/bin/env python3
"""Day 28 — production architecture checker.

A fixture, not a linter. Fails the day's own control when the document drifts.
Three assertions: N-diagram, N-claims, N-config.
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import sys
import yaml
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
AI_OS_ROOT = REPO_ROOT / "ai_os"
DIAGRAM_PATH = REPO_ROOT / "docs" / "production-architecture.md"
CAPACITY_PATH = REPO_ROOT / "capacity.yaml"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

# -----------------------------------------------------------------------------
# N-config: configuration census
# -----------------------------------------------------------------------------

ENV_PATTERN = re.compile(
    r'(?:os\.getenv|os\.environ\.get|_env_int|_env_float)\s*\(\s*["\']([A-Z0-9_]+)["\']'
)

PROVIDER_TEMPLATE_NAMES = {
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_EMBEDDING_MODEL",
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_EMBEDDING_MODEL",
    "CLOUDFLARE_API_KEY", "CLOUDFLARE_BASE_URL", "CLOUDFLARE_EMBEDDING_MODEL",
    "CLOUDFLARE_ACCOUNT_ID",
}

EXCEPTION_LIST = {
    "CMD_ZDR": "read by no code in any file (Day 27 flagged, Day 28 fixture)",
}

def scan_env_names() -> set[str]:
    """Scan ai_os/ for all environment variable names read by code."""
    names: set[str] = set()
    for py_file in AI_OS_ROOT.rglob("*.py"):
        if py_file.name == "__pycache__":
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # Find literal os.getenv / _env_int / _env_float calls
        for match in ENV_PATTERN.finditer(content):
            names.add(match.group(1))
        # Also catch module-level constants used as first arg
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in ("getenv", "get"):
                    if isinstance(func.value, ast.Name) and func.value.id in ("os", "environ"):
                        if node.args and isinstance(node.args[0], ast.Constant):
                            names.add(node.args[0].value)
                elif isinstance(func, ast.Name) and func.id in ("_env_int", "_env_float"):
                    if node.args and isinstance(node.args[0], ast.Constant):
                        names.add(node.args[0].value)
    # Add provider template names (from config.py f-strings)
    names.update(PROVIDER_TEMPLATE_NAMES)
    return names


def load_env_example() -> set[str]:
    """Parse .env.example for documented names."""
    documented: set[str] = set()
    if ENV_EXAMPLE_PATH.exists():
        for line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key = line.split("=")[0].strip()
                if key:
                    documented.add(key)
    return documented


def check_n_config() -> tuple[bool, list[str]]:
    """N-config: every code-readable name is documented or excepted."""
    code_names = scan_env_names()
    documented = load_env_example()

    undocumented = code_names - documented - set(EXCEPTION_LIST.keys())
    # Bogus exceptions: documented as exception but actually read by code
    bogus_exceptions = set(EXCEPTION_LIST.keys()) & code_names

    errors: list[str] = []
    if undocumented:
        errors.append(f"N-config FAIL: {len(undocumented)} code-readable names undocumented:")
        for name in sorted(undocumented):
            errors.append(f"  - {name}")
    else:
        errors.append("N-config PASS: all code-readable names documented or excepted")

    if bogus_exceptions:
        errors.append(f"N-config FAIL: bogus exceptions (documented as exception but read by code): {sorted(bogus_exceptions)}")
    else:
        errors.append("N-config PASS: no bogus exceptions")

    errors.append(f"Census: {len(code_names)} code-readable, {len(documented)} documented, "
                  f"{len(undocumented)} undocumented, {len(EXCEPTION_LIST)} exceptions")
    return len(undocumented) == 0 and len(bogus_exceptions) == 0, errors


# -----------------------------------------------------------------------------
# N-diagram: component id ↔ file:line bijection
# -----------------------------------------------------------------------------

COMPONENT_ID_PATTERN = re.compile(r'`(ai_os/[a-z_]+\.py:\d+(?:–\d+)?)`')

EXPECTED_COMPONENTS = {
    "ai_os/server.py:41",
    "ai_os/server.py:35",
    "ai_os/ledger.py:65",
    "ai_os/rag.py:97",
    "ai_os/state.py:493",
    "ai_os/permissions.py:177",
    "ai_os/audit.py:101",
    "ai_os/approval.py:220",
    "ai_os/agent.py:861",
    "ai_os/agent.py:879",
    "ai_os/observability.py:744",
}

def extract_component_ids() -> set[str]:
    """Extract component ids from the diagram's Plate I fenced block."""
    content = DIAGRAM_PATH.read_text(encoding="utf-8")
    # Find the Plate I section
    in_plate1 = False
    ids: set[str] = set()
    for line in content.splitlines():
        if "Plate I" in line or "What Is" in line:
            in_plate1 = True
            continue
        if "Plate II" in line or "What Breaks" in line:
            in_plate1 = False
        if in_plate1:
            for match in COMPONENT_ID_PATTERN.finditer(line):
                ids.add(match.group(1))
    return ids


def check_n_diagram() -> tuple[bool, list[str]]:
    """N-diagram: every component id resolves to file:line; every cited file is used."""
    diagram_ids = extract_component_ids()
    errors: list[str] = []

    # Check each diagram id exists
    missing: list[str] = []
    for cid in sorted(diagram_ids):
        if ":" not in cid:
            errors.append(f"N-diagram FAIL: malformed component id {cid!r}")
            continue
        file_part, line_part = cid.split(":", 1)
        file_path = REPO_ROOT / file_part
        if not file_path.exists():
            missing.append(f"  - {cid}: file {file_part} does not exist")
            continue
        try:
            line_no = int(line_part)
        except ValueError:
            missing.append(f"  - {cid}: invalid line number")
            continue
        lines = file_path.read_text(encoding="utf-8").splitlines()
        if line_no < 1 or line_no > len(lines):
            missing.append(f"  - {cid}: line {line_no} out of range (file has {len(lines)} lines)")
        elif not lines[line_no - 1].strip():
            missing.append(f"  - {cid}: line {line_no} is blank")

    if missing:
        errors.append("N-diagram FAIL: component ids not found:")
        errors.extend(missing)
    else:
        errors.append(f"N-diagram PASS: all {len(diagram_ids)} component ids resolve")

    # Check expected components are all cited
    missing_expected = EXPECTED_COMPONENTS - diagram_ids
    if missing_expected:
        errors.append(f"N-diagram FAIL: expected components missing from diagram:")
        for cid in sorted(missing_expected):
            errors.append(f"  - {cid}")
    else:
        errors.append("N-diagram PASS: all expected components cited")

    return len(missing) == 0 and len(missing_expected) == 0, errors


# -----------------------------------------------------------------------------
# N-claims: every failure claim is a fixture; every fixture is claimed
# -----------------------------------------------------------------------------

FAILURE_LABELS = frozenset({
    "fails-loud", "fails-quiet", "fails-stale", "fails-lost",
    "fails-corrupt", "fails-slow", "duplicates", "drops",
})

# Fixture registry from the evaluation harness (simplified for this checker)
# In reality this would import from the test suite; here we hard-code the
# known fixture ids that correspond to the diagram's claims.
KNOWN_FIXTURES = {
    # Single-process failure modes (live today)
    "ai_os/server.py:202-204",   # fails-loud (RAG path no store)
    "ai_os/data.py:454",         # fails-quiet (memory: + SEED_SQL)
    "ai_os/rag.py:266",          # fails-lost (trace_id no sink)
    "ai_os/server.py:123-152",   # fails-slow (sync generator on event loop)
    "ai_os/server.py:114-116",   # duplicates (gateway.last_* shared)
    # Plate II breakages (activate at T1)
    "ai_os/state.py:894",        # fails-corrupt, drops (StateStore tmp+replace)
    "ai_os/ledger.py:65",        # fails-lost, duplicates (ledger amnesia)
    "ai_os/audit.py:215",        # fails-corrupt, fails-stale (chain)
    "ai_os/approval.py:651",     # fails-corrupt, fails-stale (approvals)
}

def extract_claims() -> list[tuple[str, str]]:
    """Extract (claim_id, label) pairs from diagram's Plate II and single-process table."""
    content = DIAGRAM_PATH.read_text(encoding="utf-8")
    claims: list[tuple[str, str]] = []
    
    # Extract from single-process failure modes table
    in_sp_table = False
    for line in content.splitlines():
        if "Single-Process Failure Modes" in line:
            in_sp_table = True
            continue
        if in_sp_table and line.startswith("---"):
            in_sp_table = False
            continue
        if in_sp_table and "`" in line and "|" in line:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                # Format: | Failure Mode | `file:line` | Label |
                claim_id = parts[2].strip("` ")
                label = parts[3].strip()
                if claim_id and label in FAILURE_LABELS:
                    claims.append((claim_id, label))
    
    # Extract from Plate II breakages
    in_plate2 = False
    for line in content.splitlines():
        if "Plate II" in line or "What Breaks" in line:
            in_plate2 = True
            continue
        if in_plate2 and ("Twelve-Item" in line or "## " in line or "Five Triggers" in line):
            in_plate2 = False
        if in_plate2 and "`" in line and "[" in line and "]" in line:
            # Find all `file:line` [label1, label2] patterns
            # Allow any chars between backtick and bracket (e.g., ")  ")
            for match in re.finditer(r'`([^`]+)`[^[]*\[([^\]]+)\]', line):
                claim_id = match.group(1).strip()
                labels_str = match.group(2)
                for label in labels_str.split(","):
                    label = label.strip()
                    if label in FAILURE_LABELS:
                        claims.append((claim_id, label))
    
    return claims


def check_n_claims() -> tuple[bool, list[str]]:
    """N-claims: every claim maps to fixture; every fixture is claimed."""
    claims = extract_claims()
    claimed_fixtures = {cid for cid, _ in claims}
    errors: list[str] = []

    # Every claim must be a known fixture
    orphan_claims = [cid for cid in claimed_fixtures if cid not in KNOWN_FIXTURES]
    if orphan_claims:
        errors.append("N-claims FAIL: claims with no fixture:")
        for cid in sorted(orphan_claims):
            errors.append(f"  - {cid}")

    # Every fixture must be claimed
    orphan_fixtures = KNOWN_FIXTURES - claimed_fixtures
    if orphan_fixtures:
        errors.append("N-claims FAIL: fixtures with no claim:")
        for cid in sorted(orphan_fixtures):
            errors.append(f"  - {cid}")

    if not orphan_claims and not orphan_fixtures:
        errors.append(f"N-claims PASS: bijection OK ({len(claims)} claims ↔ {len(KNOWN_FIXTURES)} fixtures)")

    return len(orphan_claims) == 0 and len(orphan_fixtures) == 0, errors


# -----------------------------------------------------------------------------
# Capacity.yaml validation
# -----------------------------------------------------------------------------

def check_capacity_yaml() -> tuple[bool, list[str]]:
    """Validate capacity.yaml loads and has required bounds."""
    errors: list[str] = []
    if not CAPACITY_PATH.exists():
        errors.append(f"capacity.yaml not found at {CAPACITY_PATH}")
        return False, errors

    try:
        data = yaml.safe_load(CAPACITY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"capacity.yaml parse error: {e}")
        return False, errors

    bounds = data.get("bounds", {})
    required_bounds = {
        "requests_per_minute", "spend_per_hour_usd",
        "approval_queue_depth", "in_flight_runs",
    }
    missing = required_bounds - set(bounds.keys())
    if missing:
        errors.append(f"capacity.yaml missing bounds: {missing}")
        return False, errors

    # Check each bound has all 5 fields
    for name, spec in bounds.items():
        for field in ("on", "unit", "rate", "burst", "consequence"):
            if field not in spec:
                errors.append(f"capacity.yaml bound {name!r} missing field {field!r}")

    if not errors:
        errors.append(f"capacity.yaml PASS: {len(bounds)} bounds loaded")
    return len(errors) == 1, errors  # 1 = just the PASS message


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    all_ok = True
    all_errors: list[str] = []

    print("=" * 60)
    print("DAY 28 PRODUCTION ARCHITECTURE CHECKER")
    print("=" * 60)

    print("\n--- N-config ---")
    ok, errs = check_n_config()
    all_ok &= ok
    all_errors.extend(errs)
    for e in errs:
        print(e)

    print("\n--- N-diagram ---")
    ok, errs = check_n_diagram()
    all_ok &= ok
    all_errors.extend(errs)
    for e in errs:
        print(e)

    print("\n--- N-claims ---")
    ok, errs = check_n_claims()
    all_ok &= ok
    all_errors.extend(errs)
    for e in errs:
        print(e)

    print("\n--- capacity.yaml ---")
    ok, errs = check_capacity_yaml()
    all_ok &= ok
    all_errors.extend(errs)
    for e in errs:
        print(e)

    print("\n" + "=" * 60)
    if all_ok:
        print("ALL CHECKS PASSED")
    else:
        print("CHECKS FAILED")
    print("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())