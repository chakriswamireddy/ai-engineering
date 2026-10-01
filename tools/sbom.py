#!/usr/bin/env python3
"""WS0 — SBOM generator (CycloneDX 1.5 JSON, no third-party deps).

Reads the pinned ``requirements.txt`` (``name==version`` + ``--hash`` lines),
joins the installed distribution versions where available, and writes a
CycloneDX BOM to stdout (or ``--out sbom.json``). The Dockerfile build invokes
it so every image build produces an SBOM artifact; CI uploads ``sbom.json``.

A stdlib-only writer is deliberate: the SBOM tool must never add an
unpinned dependency to the thing it measures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements.txt"

PIN_RE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-+!]+)")


def parse_requirements() -> list[dict]:
    pins: dict[str, dict] = {}
    current: str | None = None
    for raw in REQ.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = PIN_RE.match(line)
        if m:
            current = m.group(1).lower().replace("_", "-")
            pins[current] = {"name": m.group(1), "version": m.group(2), "hashes": []}
            # hashes may trail on the same line after backslash continuation join
            pins[current]["hashes"].extend(re.findall(r"--hash=(\S+)", line))
            continue
        if line.startswith("--hash=") and current:
            pins[current]["hashes"].append(line.split("=", 1)[1].strip("\\ ").strip())
    return list(pins.values())


def installed_version(name: str) -> str | None:
    try:
        from importlib.metadata import version  # noqa: E402
        return version(name)
    except Exception:  # noqa: BLE001
        return None


def build_bom() -> dict:
    components = []
    for p in parse_requirements():
        ver = installed_version(p["name"]) or p["version"]
        comp: dict = {
            "type": "library",
            "name": p["name"],
            "version": p["version"],
            "purl": f"pkg:pypi/{p['name'].lower()}@{p['version']}",
        }
        if p["hashes"]:
            comp["hashes"] = [{"alg": h.split(':')[0].upper(), "content": h.split(':', 1)[1]}
                              for h in p["hashes"] if ":" in h]
        if ver != p["version"]:
            comp["properties"] = [{"name": "ws0:installed-version", "value": ver}]
        components.append(comp)
    bom_bytes = json.dumps(components, sort_keys=True).encode()
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": "ai-layer", "version": "ws0"},
        },
        "components": sorted(components, key=lambda c: c["name"].lower()),
        "ws0": {
            "pins": len(components),
            "sha256": hashlib.sha256(bom_bytes).hexdigest(),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="-", help="output path or - for stdout")
    args = ap.parse_args()
    bom = build_bom()
    text = json.dumps(bom, indent=2) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"SBOM: {len(bom['components'])} components -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
