# RatchetLab Roadmap

## Current State

RatchetLab is now a small eval-locked improvement harness, not just a sketch.

Implemented today:

- TOML config loading for name, metric, direction, commands, scope, timeout, journal path, and workdir.
- `{python}` command expansion so child Python commands run under the same interpreter as `ratchet.py`.
- Git and no-git modes.
- Workspace snapshots and restoration for rejected candidates.
- Allowed/protected path validation.
- Protected and disallowed change detection.
- JSON metric parsing from eval stdout.
- Optional hard gates: a candidate with any falsey gate is rejected even if its scalar score improves.
- Structured verdict reasons such as `kept`, `metric_not_improved`, `gate_failed`, `agent_failed`, `eval_failed`, `protected_changed`, and `disallowed_changed`.
- JSONL journals.
- Per-run manifests, per-attempt artifacts, candidate diffs, proof receipts for kept changes, and compact reports under `.ratchet/runs/<run-id>/`.
- A unittest suite covering the runner contract and example smoke paths.
- `examples/codeperf`, a fully local demo with a fake agent and correctness gate.
- `examples/promptopt`, a prompt optimization template with protected dev/holdout data, OpenAI scoring, and offline mock smoke mode.
- `ratchet doctor`, a read-only preflight that validates config, scope, eval output, gates, jitter, and eval workspace stability before running an agent.

The core boundary remains:

```text
The agent may touch the artifact.
The agent may not touch reality.
```

## Product Thesis

RatchetLab is an eval-locked improvement loop:

```text
editable artifact -> agent mutation -> protected eval -> keep/discard -> journal -> repeat
```

The human defines the editable surface and what “better” means. The runner keeps only measured wins and leaves evidence for every attempt.

## Current Showcase Contract

A good RatchetLab loop has:

- One small editable artifact.
- One protected evaluator.
- A scalar metric with explicit maximize/minimize direction.
- Optional hard gates for correctness, schema, safety, or format.
- Restorable rejected changes.
- Local evidence in `.ratchet/`, ignored by git.

The eval JSON contract is intentionally simple:

```json
{
  "score": 0.82,
  "gates": {"correctness": true},
  "metrics": {},
  "failures": []
}
```

Only the configured scalar metric is required. `gates`, `metrics`, and `failures` make results easier to audit.

## Near-Term Work

### 1. Polish trust artifacts

Goal: make accepted-change evidence easy to inspect and hard to misunderstand.

- Keep proof receipts small and explicit: before metric, after metric, gates, hashes, config path, workdir, and candidate diff path.
- Improve `report.md` readability without turning it into a dashboard.
- Add short docs explaining how to inspect `.ratchet/runs/<run-id>/` after a run.
- Preserve old `.ratchet/` data as local-only evidence; do not migrate generated artifacts.

### 2. Strengthen promptopt as the first serious vertical

Goal: make prompt optimization credible without making local smoke tests depend on network access.

- Keep mock mode clearly labeled as smoke-only.
- Document OpenAI env/config requirements for real scoring.
- Keep model selection configurable through `RATCHET_OPENAI_MODEL`.
- Expand fixtures only when there is a real use case, not as synthetic benchmark padding.
- Consider separate dev and holdout commands later if the runner needs a stricter promotion boundary.

### 3. Improve docs and examples

Goal: make a new user successful in minutes.

- Keep README verification commands current, including the doctor-first preflight.
- Keep `examples/codeperf/sample_run.txt` illustrative rather than exact; timing scores vary by machine.
- Add a short cookbook for building a new loop: choose artifact, write eval, define metric, protect reality.
- Add real-agent command examples only when they can be tested locally.

### 4. Add optional experiment memory

Goal: make repeated failures useful without letting agents rewrite the eval.

- Summarize rejected attempts into a small memory file.
- Pass it read-only through an env var such as `RATCHET_MEMORY_PATH`.
- Keep memory optional and generated; the core runner should still work without it.

## Later Roadmap

These are useful after the trust boundary and promptopt example feel polished:

- Dev/holdout command split in the runner.
- Budget metadata: wall time, token use, provider cost, and eval latency.
- Human approval mode for risky domains.
- Eval-first wizard for scaffolding a new loop.
- Cookbook templates for prompt, SQL, SOP, workflow, and scheduling optimization.
- Pressure-test mode for extra adversarial or holdout cases before promotion.
- Stalled-loop summaries explaining why the ratchet may have stopped improving.

## Explicit Deferrals

Do not spend near-term effort on:

- Dashboard/UI.
- Portfolio scheduler.
- Multi-agent orchestration.
- Provider plugin architecture.
- New vertical examples beyond `promptopt`.
- Broad runner refactors.
- Physical local-folder rename to match `RatchetLab` unless the repo owner explicitly chooses it.

## Recommended Next Slice

Focus on polish, trust, and promptopt:

1. Verify docs commands stay green.
2. Keep proof receipts and reports legible.
3. Make promptopt real-scoring setup unambiguous while preserving offline mock smoke tests.
4. Add only examples or abstractions that reinforce the locked-eval boundary.

That sequence preserves the core magic: the loop only compounds if the evaluator stays honest.
