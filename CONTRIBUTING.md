# Contributing to RatchetLab

Thanks for helping make RatchetLab sharper.

RatchetLab is intentionally small. The core project value is the locked boundary:

- one editable artifact;
- one protected evaluator;
- one explicit metric direction;
- hard gates before promotion;
- evidence for every attempt.

Please keep changes aligned with that shape.

## Local Setup

RatchetLab currently has no runtime dependencies outside the Python standard library.

```bash
python3 -m unittest discover
python3 ratchet.py doctor --config examples/codeperf/ratchet.toml
python3 ratchet.py --config examples/codeperf/ratchet.toml --iterations 4 --no-git
RATCHET_PROMPTOPT_PROVIDER=mock python3 examples/promptopt/eval.py
RATCHET_PROMPTOPT_PROVIDER=mock python3 ratchet.py --config examples/promptopt/ratchet.toml --iterations 0 --no-git
```

The prompt optimization example can call OpenAI for real scoring, but CI and normal contribution checks must stay offline.

## Pull Request Guidelines

- Prefer small, reviewable patches.
- Do not weaken scope protection, protected-file checks, restoration, or gate handling.
- Keep generated run evidence out of commits. `.ratchet/` is local-only.
- Add or update tests when changing runner behavior.
- Keep examples runnable without API keys unless the docs clearly label a command as provider-backed.

## Good First Areas

- Clearer docs and cookbook examples.
- More tests around edge cases in config, restoration, and eval parsing.
- Small improvements to proof receipts and run reports.
- Additional example loops only when they teach a distinct pattern.

## Deferrals

Please avoid adding dashboards, provider plugin frameworks, schedulers, or multi-agent orchestration until the small CLI harness is boringly reliable.
