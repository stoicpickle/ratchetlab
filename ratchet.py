#!/usr/bin/env python3
"""
RatchetLab: a tiny, eval-locked improvement loop.

Pattern:
  agent proposes change -> immutable eval scores it -> keep only if metric improves -> otherwise revert.
"""
from __future__ import annotations

import argparse
import dataclasses
import difflib
import hashlib
import json
import os
import platform
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    print("RatchetLab requires Python 3.11+ for tomllib.", file=sys.stderr)
    raise


IGNORED_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ratchet",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "prompt-exports",
    "ratchetlab.egg-info",
    "venv",
}
IGNORED_FILES = {".DS_Store"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".pyd"}
TAIL_CHARS = 2000
APP_TITLE = "RatchetLab"
APP_TAGLINE = "keep only measured wins"


def banner() -> str:
    return f"{APP_TITLE} - {APP_TAGLINE}"


@dataclasses.dataclass(frozen=True)
class Config:
    name: str
    direction: str
    metric_name: str
    agent_cmd: str
    eval_cmd: str
    allowed_paths: list[Path]
    protected_paths: list[Path]
    timeout_seconds: int
    journal_path: Path
    workdir: Path


@dataclasses.dataclass(frozen=True)
class EvalResult:
    ok: bool
    metric: float | None
    payload: dict[str, Any]
    reason: str | None
    proc: subprocess.CompletedProcess[str]


@dataclasses.dataclass(frozen=True)
class ScopeReport:
    allowed_changed: list[str]
    protected_changed: list[str]
    disallowed_changed: list[str]
    added: list[str]
    modified: list[str]
    deleted: list[str]


@dataclasses.dataclass
class AttemptSummary:
    iteration: int
    event: str
    verdict_reason: str
    metric: float | None
    best_metric: float | None
    delta: float | None
    artifact_dir: Path
    proof_receipt_path: Path | None = None


@dataclasses.dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str
    details: list[str] = dataclasses.field(default_factory=list)


def _rel_paths(base: Path, values: list[str]) -> list[Path]:
    return [(base / value).resolve() for value in values]


def load_config(path: Path) -> Config:
    path = path.resolve()
    base = path.parent
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    section = raw.get("ratchet", {})

    required = ["name", "direction", "metric_name", "agent_cmd", "eval_cmd", "allowed_paths", "protected_paths"]
    missing = [key for key in required if key not in section]
    if missing:
        raise SystemExit(f"Missing required ratchet config key(s): {', '.join(missing)}")

    direction = str(section["direction"]).lower().strip()
    if direction not in {"maximize", "minimize"}:
        raise SystemExit("direction must be either 'maximize' or 'minimize'")

    workdir = (base / section.get("workdir", ".")).resolve()
    journal_path = (base / section.get("journal_path", ".ratchet/journal.jsonl")).resolve()

    return Config(
        name=str(section["name"]),
        direction=direction,
        metric_name=str(section["metric_name"]),
        agent_cmd=str(section["agent_cmd"]),
        eval_cmd=str(section["eval_cmd"]),
        allowed_paths=_rel_paths(base, list(section["allowed_paths"])),
        protected_paths=_rel_paths(base, list(section["protected_paths"])),
        timeout_seconds=int(section.get("timeout_seconds", 300)),
        journal_path=journal_path,
        workdir=workdir,
    )


def expand_command(cmd: str) -> str:
    return cmd.replace("{python}", shlex.quote(sys.executable))


def sha256_path(path: Path) -> str:
    if path.is_symlink():
        return "symlink:" + os.readlink(path)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_ignored_rel(rel: Path) -> bool:
    if any(part in IGNORED_DIRS for part in rel.parts):
        return True
    if rel.name in IGNORED_FILES:
        return True
    return rel.suffix in IGNORED_SUFFIXES


def iter_workspace_files(workdir: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, filenames in os.walk(workdir):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for filename in filenames:
            path = root_path / filename
            rel = path.relative_to(workdir)
            if _is_ignored_rel(rel):
                continue
            files.append(path)
    return sorted(files)


def workspace_manifest(workdir: Path) -> dict[str, str]:
    return {path.relative_to(workdir).as_posix(): sha256_path(path) for path in iter_workspace_files(workdir)}


def hash_paths(paths: list[Path]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            raise SystemExit(f"Protected/allowed path does not exist: {path}")
        if path.is_dir():
            for child in sorted(p for p in path.rglob("*") if p.is_file() and not _is_ignored_rel(p.relative_to(path))):
                hashes[str(child)] = sha256_path(child)
        else:
            hashes[str(path)] = sha256_path(path)
    return hashes


def validate_scope(cfg: Config) -> None:
    if not cfg.workdir.exists() or not cfg.workdir.is_dir():
        raise SystemExit(f"workdir does not exist or is not a directory: {cfg.workdir}")

    all_paths = cfg.allowed_paths + cfg.protected_paths
    for path in all_paths:
        if not path.exists():
            raise SystemExit(f"Configured path does not exist: {path}")
        if not path.is_relative_to(cfg.workdir):
            raise SystemExit(f"Configured path is outside workdir: {path}")

    for allowed in cfg.allowed_paths:
        for protected in cfg.protected_paths:
            if allowed == protected or allowed.is_relative_to(protected) or protected.is_relative_to(allowed):
                raise SystemExit(f"allowed_paths and protected_paths must not overlap: {allowed} / {protected}")


def _rel_to_workdir(path: Path, workdir: Path) -> str:
    return path.relative_to(workdir).as_posix()


def _path_matches(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def classify_scope(before: dict[str, str], after: dict[str, str], cfg: Config) -> ScopeReport:
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    deleted = sorted(before_keys - after_keys)
    modified = sorted(key for key in before_keys & after_keys if before[key] != after[key])
    changed = added + modified + deleted

    allowed: list[str] = []
    protected: list[str] = []
    disallowed: list[str] = []
    for rel in changed:
        abs_path = (cfg.workdir / rel).resolve()
        if _path_matches(abs_path, cfg.allowed_paths):
            allowed.append(rel)
        elif _path_matches(abs_path, cfg.protected_paths):
            protected.append(rel)
        else:
            disallowed.append(rel)

    return ScopeReport(
        allowed_changed=sorted(allowed),
        protected_changed=sorted(protected),
        disallowed_changed=sorted(disallowed),
        added=added,
        modified=modified,
        deleted=deleted,
    )


def snapshot_workspace(workdir: Path, snapshot_dir: Path) -> dict[str, str]:
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True)
    manifest = workspace_manifest(workdir)
    for rel in manifest:
        src = workdir / rel
        dest = snapshot_dir / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest, follow_symlinks=False)
    (snapshot_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def restore_workspace(workdir: Path, snapshot_dir: Path) -> dict[str, str]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        return workspace_manifest(workdir)

    snapshot_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_manifest = workspace_manifest(workdir)

    for rel in sorted(set(current_manifest) - set(snapshot_manifest), reverse=True):
        path = workdir / rel
        if path.exists() or path.is_symlink():
            path.unlink()

    for rel in sorted(snapshot_manifest):
        src = snapshot_dir / "files" / rel
        dest = workdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest, follow_symlinks=False)

    cleanup_empty_dirs(workdir)
    return workspace_manifest(workdir)


def cleanup_empty_dirs(workdir: Path) -> None:
    for root, _, _ in os.walk(workdir, topdown=False):
        root_path = Path(root)
        if root_path == workdir or any(part in IGNORED_DIRS for part in root_path.relative_to(workdir).parts):
            continue
        try:
            root_path.rmdir()
        except OSError:
            pass


def run_cmd(cmd: str, cwd: Path, timeout_seconds: int, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    expanded = expand_command(cmd)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        return subprocess.run(
            expanded,
            cwd=str(cwd),
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=merged_env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode(errors="replace")
        stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode(errors="replace")
        stderr = (stderr + f"\nCommand timed out after {timeout_seconds} seconds.").strip()
        return subprocess.CompletedProcess(expanded, -124, stdout, stderr)


def parse_json_from_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("Eval command did not print a JSON object on its final JSON-looking line.")


def evaluate(cfg: Config, env: dict[str, str] | None = None) -> EvalResult:
    proc = run_cmd(cfg.eval_cmd, cfg.workdir, cfg.timeout_seconds, env=env)
    if proc.returncode != 0:
        return EvalResult(False, None, {
            "error": "eval_failed",
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-TAIL_CHARS:],
            "stderr_tail": proc.stderr[-TAIL_CHARS:],
        }, "eval_failed", proc)

    try:
        payload = parse_json_from_stdout(proc.stdout)
    except Exception as exc:
        return EvalResult(False, None, {
            "error": "eval_parse_failed",
            "exception": repr(exc),
            "stdout_tail": proc.stdout[-TAIL_CHARS:],
            "stderr_tail": proc.stderr[-TAIL_CHARS:],
        }, "eval_parse_failed", proc)

    if cfg.metric_name not in payload:
        return EvalResult(False, None, payload, "metric_missing", proc)
    try:
        metric = float(payload[cfg.metric_name])
    except (TypeError, ValueError):
        return EvalResult(False, None, payload, "metric_not_numeric", proc)

    gates = payload.get("gates")
    if gates is not None and not isinstance(gates, dict):
        return EvalResult(False, metric, payload, "gates_not_object", proc)
    if isinstance(gates, dict) and not all(bool(value) for value in gates.values()):
        return EvalResult(False, metric, payload, "gate_failed", proc)

    return EvalResult(True, metric, payload, None, proc)


def is_better(candidate: float, best: float, direction: str, min_delta: float) -> bool:
    if direction == "maximize":
        return candidate > best + min_delta
    return candidate < best - min_delta


def git_is_repo(cwd: Path) -> bool:
    try:
        proc = run_cmd("git rev-parse --is-inside-work-tree", cwd, 10)
        return proc.returncode == 0 and proc.stdout.strip() == "true"
    except Exception:
        return False


def git_head(cwd: Path) -> str | None:
    proc = run_cmd("git rev-parse HEAD", cwd, 10)
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_is_clean(cwd: Path) -> bool:
    proc = run_cmd("git status --porcelain", cwd, 10)
    return proc.returncode == 0 and proc.stdout.strip() == ""


def git_commit(cwd: Path, paths: list[Path], message: str) -> None:
    rels = [str(p.relative_to(cwd)) if p.is_relative_to(cwd) else str(p) for p in paths]
    add_proc = run_cmd("git add " + " ".join(shlex.quote(x) for x in rels), cwd, 30)
    if add_proc.returncode != 0:
        raise RuntimeError(add_proc.stderr.strip() or "git add failed")
    commit_proc = run_cmd("git commit -m " + shlex.quote(message), cwd, 60)
    if commit_proc.returncode != 0:
        raise RuntimeError(commit_proc.stderr.strip() or "git commit failed")


def append_journal(cfg: Config, row: dict[str, Any]) -> None:
    cfg.journal_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg.journal_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def make_run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"


def tail_payload(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-TAIL_CHARS:],
        "stderr_tail": proc.stderr[-TAIL_CHARS:],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_text_maybe(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return [f"<binary file: {path.name}>\n"]


def snapshot_text(snapshot_dir: Path, rel: str) -> list[str]:
    path = snapshot_dir / "files" / rel
    return read_text_maybe(path)


def write_candidate_diff(cfg: Config, snapshot_dir: Path, scope: ScopeReport, diff_path: Path) -> None:
    rels = sorted(set(scope.allowed_changed))
    chunks: list[str] = []
    for rel in rels:
        before = snapshot_text(snapshot_dir, rel)
        after = read_text_maybe(cfg.workdir / rel)
        chunks.extend(difflib.unified_diff(before, after, fromfile=f"before/{rel}", tofile=f"after/{rel}"))
        if chunks and not chunks[-1].endswith("\n"):
            chunks[-1] += "\n"
    diff_path.write_text("".join(chunks) if chunks else "", encoding="utf-8")


def scope_to_json(scope: ScopeReport) -> dict[str, Any]:
    return dataclasses.asdict(scope)


def delta_for(candidate: float | None, best: float | None, direction: str) -> float | None:
    if candidate is None or best is None:
        return None
    return candidate - best if direction == "maximize" else best - candidate


def rel_artifact(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def write_proof_receipt(
    cfg: Config,
    run_id: str,
    iteration: int,
    attempt_dir: Path,
    before_metric: float,
    after_metric: float,
    eval_result: EvalResult,
    before_manifest: dict[str, str],
    after_manifest: dict[str, str],
    min_delta: float,
    config_path: Path,
) -> Path:
    receipt = {
        "run_id": run_id,
        "iteration": iteration,
        "verdict": "kept",
        "metric_name": cfg.metric_name,
        "before_metric": before_metric,
        "after_metric": after_metric,
        "delta": delta_for(after_metric, before_metric, cfg.direction),
        "direction": cfg.direction,
        "min_delta": min_delta,
        "gates": eval_result.payload.get("gates", {}),
        "config_path": str(config_path.resolve()),
        "workdir": str(cfg.workdir),
        "eval_command": expand_command(cfg.eval_cmd),
        "agent_command": expand_command(cfg.agent_cmd),
        "before_hashes": before_manifest,
        "after_hashes": after_manifest,
        "diff_path": "candidate.diff",
        "eval_payload": eval_result.payload,
    }
    json_path = attempt_dir / "proof-receipt.json"
    md_path = attempt_dir / "proof-receipt.md"
    write_json(json_path, receipt)
    md_path.write_text(
        "\n".join([
            f"# Proof Receipt: iteration {iteration}",
            "",
            "- Verdict: kept",
            f"- Metric: {cfg.metric_name}",
            f"- Before: {before_metric}",
            f"- After: {after_metric}",
            f"- Delta: {receipt['delta']}",
            f"- Direction: {cfg.direction}",
            f"- Gates: {json.dumps(receipt['gates'], sort_keys=True)}",
            "- Diff: candidate.diff",
        ]) + "\n",
        encoding="utf-8",
    )
    return md_path


def write_report(run_dir: Path, cfg: Config, run_id: str, baseline_metric: float | None, best_metric: float | None, attempts: list[AttemptSummary]) -> None:
    lines = [
        f"# RatchetLab Run {run_id}",
        "",
        "## Run Summary",
        "",
        f"- Loop: {cfg.name}",
        f"- Metric: {cfg.metric_name}",
        f"- Direction: {cfg.direction}",
        f"- Baseline: {baseline_metric}",
        f"- Best: {best_metric}",
        "",
        "## Attempts",
        "",
        "| Iteration | Event | Reason | Metric | Best | Delta | Receipt |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if attempts:
        for attempt in attempts:
            receipt = rel_artifact(attempt.proof_receipt_path, run_dir) if attempt.proof_receipt_path else ""
            lines.append(
                f"| {attempt.iteration} | {attempt.event} | {attempt.verdict_reason} | "
                f"{attempt.metric} | {attempt.best_metric} | {attempt.delta} | {receipt} |"
            )
    else:
        lines.append("|  |  |  |  |  |  |  |")
    lines.extend([
        "",
        "## Artifacts",
        "",
        "- Manifest: manifest.json",
        "- Attempt folders: attempt-###/",
    ])
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_run_manifest(
    run_dir: Path,
    run_id: str,
    cfg: Config,
    config_path: Path,
    use_git: bool,
    min_delta: float,
    baseline_manifest: dict[str, str],
) -> None:
    payload = {
        "run_id": run_id,
        "start_time": datetime.now(UTC).isoformat(),
        "config_path": str(config_path.resolve()),
        "workdir": str(cfg.workdir),
        "config": {
            "name": cfg.name,
            "direction": cfg.direction,
            "metric_name": cfg.metric_name,
            "agent_cmd": cfg.agent_cmd,
            "eval_cmd": cfg.eval_cmd,
            "allowed_paths": [str(p) for p in cfg.allowed_paths],
            "protected_paths": [str(p) for p in cfg.protected_paths],
            "timeout_seconds": cfg.timeout_seconds,
            "journal_path": str(cfg.journal_path),
        },
        "expanded_agent_cmd": expand_command(cfg.agent_cmd),
        "expanded_eval_cmd": expand_command(cfg.eval_cmd),
        "python": sys.version,
        "platform": platform.platform(),
        "git_enabled": use_git,
        "git_head": git_head(cfg.workdir) if use_git else None,
        "metric_name": cfg.metric_name,
        "direction": cfg.direction,
        "min_delta": min_delta,
        "baseline_hashes": baseline_manifest,
    }
    write_json(run_dir / "manifest.json", payload)


def common_journal_fields(run_id: str, attempt_dir: Path | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"run_id": run_id, "time": time.time()}
    if attempt_dir is not None:
        row["artifact_dir"] = str(attempt_dir)
    return row


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def summarize_metric_runs(metrics: list[float]) -> dict[str, float]:
    metric_min = min(metrics)
    metric_max = max(metrics)
    metric_mean = sum(metrics) / len(metrics)
    spread = metric_max - metric_min
    jitter = spread / max(abs(metric_mean), 1e-12)
    return {
        "mean": metric_mean,
        "min": metric_min,
        "max": metric_max,
        "spread": spread,
        "jitter": jitter,
    }


def check_gates_shape(payload: dict[str, Any]) -> DoctorCheck:
    if "gates" not in payload:
        return DoctorCheck("Gates", "INFO", ["no gates declared"])
    gates = payload.get("gates")
    if not isinstance(gates, dict):
        return DoctorCheck("Gates", "FAIL", ["gates must be a JSON object"])
    if not gates:
        return DoctorCheck("Gates", "PASS", ["gates object is empty"])

    details: list[str] = []
    has_false = False
    has_non_bool = False
    for name, value in sorted(gates.items()):
        details.append(f"{name}={json.dumps(value, sort_keys=True)}")
        if not bool(value):
            has_false = True
        if not isinstance(value, bool):
            has_non_bool = True

    if has_false:
        return DoctorCheck("Gates", "FAIL", details)
    if has_non_bool:
        return DoctorCheck("Gates", "WARN", details + ["gate values should be booleans"])
    return DoctorCheck("Gates", "PASS", details)


def worst_status(statuses: list[str]) -> str:
    order = {"FAIL": 3, "WARN": 2, "PASS": 1, "INFO": 0}
    return max(statuses, key=lambda status: order.get(status, -1)) if statuses else "INFO"


def format_doctor_report(loop_name: str, checks: list[DoctorCheck]) -> str:
    has_fail = any(check.status == "FAIL" for check in checks)
    has_warn = any(check.status == "WARN" for check in checks)
    verdict = "NOT READY" if has_fail else ("READY WITH WARNINGS" if has_warn else "READY")
    lines = [banner(), f"RatchetLab doctor: {loop_name}"]
    for check in checks:
        suffix = ""
        if check.details:
            suffix = " " + "; ".join(check.details)
        lines.append(f"{check.name}: {check.status}{suffix}")
    lines.append(f"Verdict: {verdict}")
    return "\n".join(lines) + "\n"


def run_doctor(config_path: Path, eval_runs: int = 3, jitter_warn_ratio: float = 0.05) -> int:
    checks: list[DoctorCheck] = []
    loop_name = str(config_path)

    try:
        cfg = load_config(config_path)
    except SystemExit as exc:
        checks.append(DoctorCheck("Config", "FAIL", [str(exc)]))
        print(format_doctor_report(loop_name, checks), end="")
        return 1
    except Exception as exc:
        checks.append(DoctorCheck("Config", "FAIL", [repr(exc)]))
        print(format_doctor_report(loop_name, checks), end="")
        return 1

    loop_name = cfg.name
    checks.append(DoctorCheck("Config", "PASS", [f"loaded {config_path}"]))

    try:
        validate_scope(cfg)
    except SystemExit as exc:
        checks.append(DoctorCheck("Scope", "FAIL", [str(exc)]))
        print(format_doctor_report(loop_name, checks), end="")
        return 1
    except Exception as exc:
        checks.append(DoctorCheck("Scope", "FAIL", [repr(exc)]))
        print(format_doctor_report(loop_name, checks), end="")
        return 1
    checks.append(DoctorCheck("Scope", "PASS", ["allowed/protected paths are valid"]))

    if git_is_repo(cfg.workdir):
        if git_is_clean(cfg.workdir):
            checks.append(DoctorCheck("Workspace", "PASS", ["git repo clean"]))
        else:
            checks.append(DoctorCheck("Workspace", "WARN", ["git repo has uncommitted changes; normal git-mode runs require a clean tree"]))
    else:
        checks.append(DoctorCheck("Workspace", "WARN", ["not a git repo; use --no-git snapshot mode for ratchet runs"]))

    metrics: list[float] = []
    eval_details: list[str] = []
    gate_checks: list[DoctorCheck] = []
    mutation_details: list[str] = []
    eval_env = {"PYTHONDONTWRITEBYTECODE": "1"}

    with tempfile.TemporaryDirectory(prefix="ratchet-doctor-") as tmp:
        snapshot_dir = Path(tmp) / "snapshot"
        before_manifest = snapshot_workspace(cfg.workdir, snapshot_dir)
        for run_index in range(1, eval_runs + 1):
            try:
                proc = run_cmd(cfg.eval_cmd, cfg.workdir, cfg.timeout_seconds, env=eval_env)
                if proc.returncode != 0:
                    eval_details.append(f"run {run_index}: eval_failed returncode={proc.returncode}")
                    continue

                try:
                    payload = parse_json_from_stdout(proc.stdout)
                except Exception as exc:
                    eval_details.append(f"run {run_index}: eval_parse_failed {exc}")
                    continue

                if cfg.metric_name not in payload:
                    eval_details.append(f"run {run_index}: metric_missing {cfg.metric_name}")
                    gate_checks.append(check_gates_shape(payload))
                    continue
                try:
                    metric = float(payload[cfg.metric_name])
                except (TypeError, ValueError):
                    eval_details.append(f"run {run_index}: metric_not_numeric {cfg.metric_name}={payload[cfg.metric_name]!r}")
                    gate_checks.append(check_gates_shape(payload))
                    continue

                metrics.append(metric)
                gate_checks.append(check_gates_shape(payload))
            finally:
                after_manifest = workspace_manifest(cfg.workdir)
                scope = classify_scope(before_manifest, after_manifest, cfg)
                changed = scope.allowed_changed + scope.protected_changed + scope.disallowed_changed
                if changed:
                    if scope.protected_changed:
                        mutation_details.append(f"run {run_index}: protected changed {', '.join(scope.protected_changed)}")
                    if scope.allowed_changed:
                        mutation_details.append(f"run {run_index}: allowed changed {', '.join(scope.allowed_changed)}")
                    if scope.disallowed_changed:
                        mutation_details.append(f"run {run_index}: disallowed changed {', '.join(scope.disallowed_changed)}")
                restore_workspace(cfg.workdir, snapshot_dir)

    if eval_details:
        checks.append(DoctorCheck("Eval", "FAIL", eval_details))
    else:
        checks.append(DoctorCheck("Eval", "PASS", [f"{len(metrics)}/{eval_runs} runs produced numeric {cfg.metric_name}"]))

    if metrics:
        summary = summarize_metric_runs(metrics)
        metric_status = "WARN" if summary["jitter"] > jitter_warn_ratio else "PASS"
        checks.append(DoctorCheck(
            f"Metric {cfg.metric_name}",
            metric_status,
            [
                f"mean={summary['mean']:.6g}",
                f"min={summary['min']:.6g}",
                f"max={summary['max']:.6g}",
                f"spread={summary['spread']:.6g}",
                f"jitter={summary['jitter']:.2%}",
            ],
        ))

    if gate_checks:
        gate_status = worst_status([check.status for check in gate_checks])
        gate_details: list[str] = []
        seen_details: set[str] = set()
        for check in gate_checks:
            for detail in check.details:
                if detail not in seen_details:
                    gate_details.append(detail)
                    seen_details.add(detail)
        checks.append(DoctorCheck("Gates", gate_status, gate_details))
    else:
        checks.append(DoctorCheck("Gates", "INFO", ["not checked because eval payload was invalid"]))

    if mutation_details:
        checks.append(DoctorCheck("Mutation", "FAIL", mutation_details))
    else:
        checks.append(DoctorCheck("Mutation", "PASS", ["eval did not change tracked workspace files"]))

    print(format_doctor_report(loop_name, checks), end="")
    return 1 if any(check.status == "FAIL" for check in checks) else 0


def run_loop(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    validate_scope(cfg)
    use_git = (not args.no_git) and git_is_repo(cfg.workdir)
    if use_git and not git_is_clean(cfg.workdir):
        raise SystemExit("Git worktree must be clean before running RatchetLab in git mode.")

    print(banner())

    run_id = make_run_id()
    run_dir = cfg.workdir / ".ratchet" / "runs" / run_id
    snapshot_dir = run_dir / "snapshot"
    run_dir.mkdir(parents=True, exist_ok=True)

    current_manifest = snapshot_workspace(cfg.workdir, snapshot_dir)
    write_run_manifest(run_dir, run_id, cfg, args.config, use_git, args.min_delta, current_manifest)

    baseline = evaluate(cfg)
    attempts: list[AttemptSummary] = []
    if not baseline.ok or baseline.metric is None:
        append_journal(cfg, {
            **common_journal_fields(run_id),
            "event": "baseline_failed",
            "verdict_reason": baseline.reason or "baseline_failed",
            "payload": baseline.payload,
        })
        write_report(run_dir, cfg, run_id, None, None, attempts)
        print(f"[{cfg.name}] baseline failed: {baseline.reason}. report={run_dir / 'report.md'}")
        return 1

    best = baseline.metric
    print(f"[{cfg.name}] baseline {cfg.metric_name}={best:.6g} direction={cfg.direction} git={use_git}")
    append_journal(cfg, {
        **common_journal_fields(run_id),
        "event": "baseline",
        "metric": best,
        "payload": baseline.payload,
    })
    write_report(run_dir, cfg, run_id, best, best, attempts)

    for i in range(1, args.iterations + 1):
        attempt_dir = run_dir / f"attempt-{i:03d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        env = {
            "RATCHET_ITERATION": str(i),
            "RATCHET_BEST_METRIC": str(best),
            "RATCHET_METRIC_NAME": cfg.metric_name,
            "RATCHET_DIRECTION": cfg.direction,
            "RATCHET_ALLOWED_PATHS": os.pathsep.join(str(p) for p in cfg.allowed_paths),
            "RATCHET_PYTHON": sys.executable,
        }
        attempt_before_manifest = dict(current_manifest)
        print(f"\n[{cfg.name}] iteration {i}: agent editing allowed artifact(s)")
        started = time.time()
        agent_proc = run_cmd(cfg.agent_cmd, cfg.workdir, cfg.timeout_seconds, env=env)
        duration = time.time() - started
        write_json(attempt_dir / "agent.json", {**tail_payload(agent_proc), "duration_seconds": duration})

        after_agent_manifest = workspace_manifest(cfg.workdir)
        scope = classify_scope(attempt_before_manifest, after_agent_manifest, cfg)
        write_json(attempt_dir / "scope.json", scope_to_json(scope))
        write_candidate_diff(cfg, snapshot_dir, scope, attempt_dir / "candidate.diff")

        eval_result: EvalResult | None = None
        reason: str | None = None
        event = "discarded"
        metric: float | None = None
        receipt_path: Path | None = None

        if agent_proc.returncode != 0:
            reason = "agent_failed"
        elif scope.protected_changed:
            reason = "protected_changed"
        elif scope.disallowed_changed:
            reason = "disallowed_changed"
        else:
            eval_result = evaluate(cfg)
            metric = eval_result.metric
            write_json(attempt_dir / "eval.json", {
                "ok": eval_result.ok,
                "metric": eval_result.metric,
                "reason": eval_result.reason,
                "payload": eval_result.payload,
                **tail_payload(eval_result.proc),
            })
            if not eval_result.ok or eval_result.metric is None:
                reason = eval_result.reason or "eval_failed"
            elif is_better(eval_result.metric, best, cfg.direction, args.min_delta):
                reason = "kept"
                event = "kept"
            else:
                reason = "metric_not_improved"

        if eval_result is None:
            write_json(attempt_dir / "eval.json", {"ok": False, "reason": "not_run"})

        before_best = best
        delta = delta_for(metric, before_best, cfg.direction)

        if event == "kept" and eval_result is not None and eval_result.metric is not None:
            best = eval_result.metric
            receipt_path = write_proof_receipt(
                cfg,
                run_id,
                i,
                attempt_dir,
                before_best,
                best,
                eval_result,
                attempt_before_manifest,
                after_agent_manifest,
                args.min_delta,
                args.config,
            )
            current_manifest = snapshot_workspace(cfg.workdir, snapshot_dir)
            if use_git:
                git_commit(cfg.workdir, cfg.allowed_paths, f"ratchet: {cfg.metric_name}={best:.6g}")
            print(f"[{cfg.name}] kept: candidate={best:.6g} best={best:.6g} delta={delta:.6g}")
        else:
            current_manifest = restore_workspace(cfg.workdir, snapshot_dir)
            print(f"[{cfg.name}] discarded: reason={reason} candidate={metric} best={best}")

        summary = AttemptSummary(i, event, reason or "metric_not_improved", metric, best, delta, attempt_dir, receipt_path)
        attempts.append(summary)
        journal_row = {
            **common_journal_fields(run_id, attempt_dir),
            "event": event,
            "verdict_reason": summary.verdict_reason,
            "iteration": i,
            "metric": metric,
            "best_metric": best,
            "delta": delta,
            "payload": eval_result.payload if eval_result else {},
            "agent_stdout_tail": agent_proc.stdout[-TAIL_CHARS:],
            "agent_stderr_tail": agent_proc.stderr[-TAIL_CHARS:],
            "duration_seconds": duration,
            "diff_path": str(attempt_dir / "candidate.diff"),
        }
        if receipt_path is not None:
            journal_row["proof_receipt_path"] = str(receipt_path)
        append_journal(cfg, journal_row)
        write_report(run_dir, cfg, run_id, baseline.metric, best, attempts)

    print(f"\n[{cfg.name}] done. best {cfg.metric_name}={best:.6g}. journal={cfg.journal_path}. report={run_dir / 'report.md'}")
    return 0


def build_loop_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an eval-locked ratchet loop.",
        epilog="Use 'ratchet doctor --help' to inspect the read-only preflight command.",
    )
    parser.add_argument("--config", type=Path, default=Path("ratchet.toml"))
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--min-delta", type=float, default=0.0, help="Minimum metric improvement required to keep a change.")
    parser.add_argument("--no-git", action="store_true", help="Use file snapshots instead of git commits.")
    return parser


def build_doctor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ratchet.py doctor", description="Check whether a ratchet config is ready to run.")
    parser.add_argument("--config", type=Path, default=Path("ratchet.toml"))
    parser.add_argument("--eval-runs", type=positive_int, default=3, help="Number of eval runs used to check health and jitter.")
    parser.add_argument("--jitter-warn-ratio", type=nonnegative_float, default=0.05, help="Warn when metric spread/mean exceeds this ratio.")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "doctor":
        args = build_doctor_parser().parse_args(raw_args[1:])
        return run_doctor(args.config, eval_runs=args.eval_runs, jitter_warn_ratio=args.jitter_warn_ratio)

    args = build_loop_parser().parse_args(raw_args)
    return run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
