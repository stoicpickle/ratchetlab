# RatchetLab Cookbook

RatchetLab works best when the loop is small enough to audit.

## Build Your First Loop

Create five things:

```text
ratchet.toml   # loop config
program.md     # instructions for the agent
artifact.ext   # the only editable file
eval.py        # protected evaluator
README.md      # optional notes for humans
```

The evaluator must print one JSON object on stdout. The configured metric must be numeric.

```json
{"score": 0.82, "gates": {"correctness": true}}
```

## Choose the Editable Artifact

Good artifacts are small and reviewable:

- one prompt;
- one hot-path function;
- one SQL query;
- one SOP or workflow file;
- one data transformation recipe.

Avoid letting the agent edit the eval, fixtures, config, or broad source trees.

## Write the Eval

A good eval:

- is deterministic enough to compare attempts;
- prints JSON as its final JSON-looking stdout line;
- includes a scalar metric;
- uses gates for hard requirements such as correctness, schema validity, or safety;
- leaves enough failure detail for a human to inspect.

## Run Doctor First

```bash
python3 ratchet.py doctor --config ratchet.toml
```

Doctor checks config, scope, metric parsing, gates, jitter, and whether the eval mutates tracked workspace files. It does not run the agent.

## Inspect a Run

RatchetLab writes local evidence under `.ratchet/`:

- `journal.jsonl` records baseline and attempt summaries;
- `runs/<run-id>/manifest.json` records config and environment details;
- `runs/<run-id>/attempt-###/candidate.diff` shows the candidate change;
- `runs/<run-id>/attempt-###/proof-receipt.md` exists for kept changes;
- `runs/<run-id>/report.md` gives a compact run summary.

Keep `.ratchet/` local. It is run evidence, not source.
