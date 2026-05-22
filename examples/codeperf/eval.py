#!/usr/bin/env python3
"""
Immutable evaluator for the code performance demo.

Prints one JSON object containing:
  - score: higher is better
  - correct: boolean
  - latency_ms: median latency across benchmark rounds
"""
from __future__ import annotations

import importlib.util
import json
import random
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "target.py"


def load_target():
    spec = importlib.util.spec_from_file_location("codeperf_target", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load target.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reference(items: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def make_cases() -> list[list[int]]:
    rng = random.Random(20260521)
    cases: list[list[int]] = []
    for _ in range(40):
        # Duplicates are frequent, which makes the naive O(n²) implementation suffer.
        cases.append([rng.randrange(0, 800) for _ in range(2500)])
    cases.append([])
    cases.append([1, 1, 1, 1])
    cases.append([3, 2, 3, 1, 2, 4])
    return cases


def main() -> None:
    module = load_target()
    fn = module.dedupe_preserve_order
    cases = make_cases()

    for case in cases:
        got = fn(case)
        expected = reference(case)
        if got != expected:
            print(json.dumps({
                "score": 0.0,
                "correct": False,
                "gates": {"correctness": False},
                "latency_ms": None,
                "reason": "wrong_answer",
                "example": case[:20],
                "got": got[:20] if isinstance(got, list) else repr(got),
                "expected": expected[:20],
            }))
            return

    timings: list[float] = []
    for _ in range(7):
        start = time.perf_counter()
        for case in cases:
            fn(case)
        timings.append((time.perf_counter() - start) * 1000.0)

    latency_ms = statistics.median(timings)
    # A simple bounded speed score. Correctness gates the score above.
    score = 1000.0 / (1.0 + latency_ms)
    print(json.dumps({
        "score": score,
        "correct": True,
        "gates": {"correctness": True},
        "latency_ms": latency_ms,
        "rounds": len(timings),
        "cases": len(cases),
    }))


if __name__ == "__main__":
    main()
