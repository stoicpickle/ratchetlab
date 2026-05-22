# Prompt Optimization Program

## Goal

Maximize extraction accuracy on the fixed eval set while keeping the prompt short, strict, and general.

## Editable artifact

Edit only `system_prompt.md`.

## Constraints

- Return JSON only.
- Preserve the exact output schema.
- Use `null` for unknown values.
- Do not include examples copied verbatim from the eval sets.
- Do not modify `eval.py`, `dev_set.jsonl`, or `holdout_set.jsonl`.

## Good ideas

- Clarify ambiguous fields.
- Add compact decision rules for urgency.
- Tighten format compliance.
- Add anti-hallucination instructions.

## Bad ideas

- Memorizing eval examples.
- Changing the schema.
- Adding long prompt bloat that improves one case while weakening generality.
