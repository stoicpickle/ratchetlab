# Prompt Optimization Template

This is the first serious RatchetLab vertical: optimize one prompt against a protected extraction eval.

- Editable artifact: `system_prompt.md`
- Protected reality: `eval.py` + `dev_set.jsonl` + `holdout_set.jsonl`
- Metric: `score`, a scalar accuracy value
- Gates: schema/format behavior is enforced by the evaluator
- Ratchet rule: keep only if `score` improves and gates pass

## Offline smoke mode

Use mock mode to verify local wiring without network access:

```bash
RATCHET_PROMPTOPT_PROVIDER=mock python3 examples/promptopt/eval.py
RATCHET_PROMPTOPT_PROVIDER=mock python3 ratchet.py --config examples/promptopt/ratchet.toml --iterations 0 --no-git
```

Mock mode is smoke-only. It checks that files load, JSON parsing works, and the ratchet config can run. It is not a real prompt-quality score and should not be used to judge whether `system_prompt.md` improved.

## Real OpenAI scoring

To run the actual evaluator against OpenAI:

1. Set `OPENAI_API_KEY`.
2. Optionally set `RATCHET_OPENAI_MODEL` to the model you want to score with.
3. Confirm the selected model supports the Responses API structured-output path used by `eval.py`.
4. Initialize git and run the ratchet loop.

`eval.py` has a code default for `RATCHET_OPENAI_MODEL`, but model availability changes. Prefer setting the model explicitly in your environment or shell command for reproducible runs.

Example:

```bash
git init
git add .
git commit -m "baseline"
OPENAI_API_KEY=... RATCHET_OPENAI_MODEL=<your-model> python3 ratchet.py --config examples/promptopt/ratchet.toml --iterations 50
```

Do not let the agent edit `eval.py`, `dev_set.jsonl`, `holdout_set.jsonl`, `program.md`, or `ratchet.toml`. Otherwise it will optimize by quietly moving the goalposts, which is basically what humans do in quarterly planning decks but faster.

The baseline eval must pass before the loop will run. Add more representative dev and holdout cases before trusting this for a real workflow.
