"""WS0 guardrail: pytest fixtures shared by the Day suites.

The Day 26/27 suites were authored with a ``tmp: Path`` parameter (script-style
temporary directory). pytest's builtin is ``tmp_path`` — without this alias the
suites error at setup under ``pytest -q`` while passing as scripts. This alias
preserves both runners without editing seven test functions.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """Legacy alias: ``tmp`` behaves exactly like ``tmp_path``."""
    return tmp_path
