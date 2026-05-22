# Code Performance Demo Program

## Goal

Maximize `score` from `eval.py` by improving `dedupe_preserve_order(items)` in `target.py`.

## Constraints

- Edit only `target.py`.
- Preserve exact behavior: unique items in first-seen order.
- Do not import heavy dependencies.
- Do not change the evaluator.
- Optimize for realistic correctness, not only the visible fixtures.

## Good ideas

- Replace O(n²) membership checks.
- Bind hot methods locally if it helps.
- Use standard library data structures with insertion-order behavior.

## Bad ideas

- Sorting output.
- Returning a set.
- Mutating input.
- Special-casing benchmark constants.
