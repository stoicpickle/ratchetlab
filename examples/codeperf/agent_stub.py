#!/usr/bin/env python3
"""
Fake agent for the demo.

A real setup would point `agent_cmd` at Claude Code, Codex, Cursor, or your own agent.
This stub writes different candidate implementations so the ratchet runner can prove that it
keeps improvements and discards bad experiments.
"""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "target.py"
ITERATION = int(os.environ.get("RATCHET_ITERATION", "1"))

VARIANTS: dict[int, str] = {
    1: '''"""Editable artifact for the code performance demo."""
from __future__ import annotations


def dedupe_preserve_order(items: list[int]) -> list[int]:
    """Return unique items in first-seen order using a set for O(n) membership."""
    seen: set[int] = set()
    result: list[int] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
''',
    2: '''"""Editable artifact for the code performance demo."""
from __future__ import annotations


def dedupe_preserve_order(items: list[int]) -> list[int]:
    """Return unique items in first-seen order using dict insertion order."""
    return list(dict.fromkeys(items))
''',
    3: '''"""Editable artifact for the code performance demo."""
from __future__ import annotations


def dedupe_preserve_order(items: list[int]) -> list[int]:
    """Broken candidate: fast but does not preserve order."""
    return sorted(set(items))
''',
    4: '''"""Editable artifact for the code performance demo."""
from __future__ import annotations


def dedupe_preserve_order(items: list[int]) -> list[int]:
    """Return unique items in first-seen order with local bindings."""
    seen: set[int] = set()
    result: list[int] = []
    seen_add = seen.add
    result_append = result.append
    for item in items:
        if item not in seen:
            seen_add(item)
            result_append(item)
    return result
''',
}

code = VARIANTS.get(ITERATION, VARIANTS[4])
TARGET.write_text(code, encoding="utf-8")
print(f"agent_stub wrote candidate variant for iteration {ITERATION}")
