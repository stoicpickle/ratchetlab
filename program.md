# RatchetLab Program

## Goal

Improve the configured metric while preserving correctness and user-facing behavior.

## Editable scope

Edit only the paths listed in `RATCHET_ALLOWED_PATHS`. Do not modify evals, fixtures, test data, config, or the runner.

## Strategy

Prefer small, reviewable changes. Each iteration should test one clear hypothesis.

Good hypotheses:

- Replace a slow algorithm with an equivalent faster one.
- Remove unnecessary repeated work.
- Cache a pure computation safely.
- Simplify a prompt while preserving required outputs.
- Improve format compliance without weakening accuracy.

Bad hypotheses:

- Change the evaluator.
- Special-case hidden fixtures.
- Delete features.
- Return constant outputs.
- Make the benchmark easier instead of making the artifact better.

## Stop conditions

Stop when several iterations fail to improve the metric, or when the likely next changes would harm generality.

## Iteration note format

Before editing, write a one-sentence hypothesis in your own scratch notes. After the eval, infer why the result was kept or discarded.
