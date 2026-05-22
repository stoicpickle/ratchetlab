# Release Checklist

Use this before tagging a public release.

## Repo Hygiene

- Confirm the public repository is named `RatchetLab` or `ratchetlab`.
- Confirm `LICENSE` has the intended copyright holder.
- Confirm `.ratchet/`, `prompt-exports/`, caches, local env files, and OS/editor artifacts are not tracked.
- Search for local absolute paths before publishing:

```bash
rg -n "/Users/|OPENAI_API_KEY=.+|sk-" .
```

## Verification

Run the offline verification bundle:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python3 -m unittest discover
python3 ratchet.py doctor --config examples/codeperf/ratchet.toml
python3 ratchet.py --config examples/codeperf/ratchet.toml --iterations 4 --no-git
RATCHET_PROMPTOPT_PROVIDER=mock python3 examples/promptopt/eval.py
RATCHET_PROMPTOPT_PROVIDER=mock python3 ratchet.py --config examples/promptopt/ratchet.toml --iterations 0 --no-git
```

Restore demo files after the ratchet run:

```bash
git checkout -- examples/codeperf/target.py
```

## Public Framing

- README first screen explains what RatchetLab does.
- Quick demo works without an API key.
- Promptopt mock mode is clearly labeled smoke-only.
- Real provider-backed scoring is documented as optional.
- Roadmap separates current capability from later product ideas.

## Tagging

For v0.1, tag only after CI passes from a clean checkout:

```bash
git tag v0.1.0
git push origin v0.1.0
```
