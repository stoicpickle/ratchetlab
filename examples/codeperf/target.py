"""
Editable artifact for the code performance demo.

The agent is allowed to modify this file only. The evaluator will test correctness and speed.
"""
from __future__ import annotations


def dedupe_preserve_order(items: list[int]) -> list[int]:
    """Return unique items in first-seen order."""
    result: list[int] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
