from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RATCHET = ROOT / "ratchet.py"

spec = importlib.util.spec_from_file_location("ratchet", RATCHET)
ratchet = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["ratchet"] = ratchet
spec.loader.exec_module(ratchet)


class RatchetTests(unittest.TestCase):
    def test_expand_command_python_placeholder(self) -> None:
        self.assertEqual(ratchet.expand_command("{python} script.py"), f"{ratchet.shlex.quote(sys.executable)} script.py")

    def test_expand_command_without_placeholder(self) -> None:
        self.assertEqual(ratchet.expand_command("echo ok"), "echo ok")

    def test_parse_json_from_stdout_uses_last_json_line(self) -> None:
        self.assertEqual(ratchet.parse_json_from_stdout('log\n{"score": 1}\nmore\n{"score": 2}\n'), {"score": 2})

    def test_parse_json_from_stdout_raises(self) -> None:
        with self.assertRaises(ValueError):
            ratchet.parse_json_from_stdout("no json here")

    def test_workspace_manifest_ignores_local_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            (td / "target.txt").write_text("tracked", encoding="utf-8")
            (td / ".venv").mkdir()
            (td / ".venv" / "pyvenv.cfg").write_text("ignored", encoding="utf-8")
            (td / "prompt-exports").mkdir()
            (td / "prompt-exports" / "plan.md").write_text("ignored", encoding="utf-8")
            (td / "ratchetlab.egg-info").mkdir()
            (td / "ratchetlab.egg-info" / "PKG-INFO").write_text("ignored", encoding="utf-8")

            self.assertEqual(ratchet.workspace_manifest(td), {"target.txt": ratchet.sha256_path(td / "target.txt")})

    def test_is_better_honors_direction_and_delta(self) -> None:
        self.assertTrue(ratchet.is_better(11, 10, "maximize", 0.5))
        self.assertFalse(ratchet.is_better(10.2, 10, "maximize", 0.5))
        self.assertTrue(ratchet.is_better(9, 10, "minimize", 0.5))
        self.assertFalse(ratchet.is_better(9.8, 10, "minimize", 0.5))

    def make_project(self, td: Path, agent_code: str, eval_code: str, target_text: str = "baseline") -> Path:
        (td / "target.txt").write_text(target_text, encoding="utf-8")
        (td / "agent.py").write_text(textwrap.dedent(agent_code), encoding="utf-8")
        (td / "eval.py").write_text(textwrap.dedent(eval_code), encoding="utf-8")
        (td / "program.md").write_text("protected", encoding="utf-8")
        (td / "ratchet.toml").write_text(textwrap.dedent("""
            [ratchet]
            name = "test-loop"
            direction = "maximize"
            metric_name = "score"
            agent_cmd = "{python} agent.py"
            eval_cmd = "{python} eval.py"
            allowed_paths = ["target.txt"]
            protected_paths = ["eval.py", "program.md", "ratchet.toml"]
            timeout_seconds = 10
            journal_path = ".ratchet/journal.jsonl"
        """).strip() + "\n", encoding="utf-8")
        return td / "ratchet.toml"

    def run_ratchet(self, td: Path, iterations: int = 1) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RATCHET), "--config", str(td / "ratchet.toml"), "--iterations", str(iterations), "--no-git"],
            cwd=td,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def run_doctor(self, td: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RATCHET), "doctor", "--config", str(td / "ratchet.toml"), *extra_args],
            cwd=td,
            text=True,
            capture_output=True,
            timeout=30,
        )

    def journal_rows(self, td: Path) -> list[dict]:
        path = td / ".ratchet" / "journal.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def latest_run_dir(self, td: Path) -> Path:
        runs = sorted((td / ".ratchet" / "runs").iterdir())
        self.assertTrue(runs)
        return runs[-1]

    def test_baseline_failure_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(td, "Path('agent-ran').write_text('yes')", "raise SystemExit(2)")
            proc = self.run_ratchet(td)
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((td / "agent-ran").exists())
            self.assertEqual(self.journal_rows(td)[0]["event"], "baseline_failed")

    def test_allowed_improvement_is_kept_and_receipted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('target.txt').write_text('better')",
                "import json\nfrom pathlib import Path\nprint(json.dumps({'score': 2 if Path('target.txt').read_text() == 'better' else 1}))",
            )
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((td / "target.txt").read_text(encoding="utf-8"), "better")
            run_dir = self.latest_run_dir(td)
            self.assertTrue(next(run_dir.glob("attempt-001/proof-receipt.md")).exists())
            receipt = json.loads((run_dir / "attempt-001" / "proof-receipt.json").read_text(encoding="utf-8"))
            self.assertLess(receipt["before_metric"], receipt["after_metric"])
            self.assertNotEqual(receipt["before_hashes"], receipt["after_hashes"])
            self.assertNotEqual(receipt["before_hashes"]["target.txt"], receipt["after_hashes"]["target.txt"])
            self.assertEqual(receipt["workdir"], str(td.resolve()))
            self.assertEqual(receipt["config_path"], str((td / "ratchet.toml").resolve()))
            self.assertTrue((run_dir / "report.md").exists())
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "kept")

    def test_metric_non_improvement_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('target.txt').write_text('worse')",
                "import json\nprint(json.dumps({'score': 1}))",
            )
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((td / "target.txt").read_text(encoding="utf-8"), "baseline")
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "metric_not_improved")

    def test_protected_modification_is_rejected_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('eval.py').write_text('broken')",
                "import json\nprint(json.dumps({'score': 1}))",
            )
            original = (td / "eval.py").read_text(encoding="utf-8")
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((td / "eval.py").read_text(encoding="utf-8"), original)
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "protected_changed")

    def test_disallowed_modification_is_rejected_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('hacked.txt').write_text('oops')",
                "import json\nprint(json.dumps({'score': 1}))",
            )
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertFalse((td / "hacked.txt").exists())
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "disallowed_changed")

    def test_agent_failure_restores_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('target.txt').write_text('dirty')\nraise SystemExit(3)",
                "import json\nprint(json.dumps({'score': 1}))",
            )
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((td / "target.txt").read_text(encoding="utf-8"), "baseline")
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "agent_failed")

    def test_eval_parse_failure_restores_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('target.txt').write_text('candidate')",
                "from pathlib import Path\nprint('not json' if Path('target.txt').read_text() == 'candidate' else '{\"score\": 1}')",
            )
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((td / "target.txt").read_text(encoding="utf-8"), "baseline")
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "eval_parse_failed")

    def test_failed_gate_rejects_higher_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('target.txt').write_text('candidate')",
                "import json\nfrom pathlib import Path\ncandidate = Path('target.txt').read_text() == 'candidate'\nprint(json.dumps({'score': 3 if candidate else 2, 'gates': {'correctness': not candidate}}))",
            )
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((td / "target.txt").read_text(encoding="utf-8"), "baseline")
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "gate_failed")

    def test_malformed_gates_rejected_by_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('target.txt').write_text('candidate')",
                "import json\nfrom pathlib import Path\ncandidate = Path('target.txt').read_text() == 'candidate'\nprint(json.dumps({'score': 3, 'gates': False} if candidate else {'score': 1}))",
            )
            proc = self.run_ratchet(td)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertEqual((td / "target.txt").read_text(encoding="utf-8"), "baseline")
            self.assertEqual(self.journal_rows(td)[-1]["verdict_reason"], "gates_not_object")

    def test_doctor_does_not_run_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "from pathlib import Path\nPath('agent-ran').write_text('yes')",
                "import json\nprint(json.dumps({'score': 1, 'gates': {'correctness': True}}))",
            )
            proc = self.run_doctor(td, "--eval-runs", "1")
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertFalse((td / "agent-ran").exists())
            self.assertIn("RatchetLab doctor", proc.stdout)
            self.assertIn("READY", proc.stdout)

    def test_doctor_eval_parse_failure_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(td, "print('agent')", "print('not json')")
            proc = self.run_doctor(td, "--eval-runs", "1")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Eval: FAIL", proc.stdout)
            self.assertIn("eval_parse_failed", proc.stdout)
            self.assertIn("NOT READY", proc.stdout)

    def test_doctor_gate_failure_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "print('agent')",
                "import json\nprint(json.dumps({'score': 1, 'gates': {'correctness': False}}))",
            )
            proc = self.run_doctor(td, "--eval-runs", "1")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Gates: FAIL", proc.stdout)
            self.assertIn("correctness=false", proc.stdout)
            self.assertIn("NOT READY", proc.stdout)

    def test_doctor_detects_and_restores_eval_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "print('agent')",
                "import json\nfrom pathlib import Path\nPath('program.md').write_text('mutated')\nprint(json.dumps({'score': 1, 'gates': {'correctness': True}}))",
            )
            original = (td / "program.md").read_text(encoding="utf-8")
            proc = self.run_doctor(td, "--eval-runs", "1")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("Mutation: FAIL", proc.stdout)
            self.assertIn("protected changed program.md", proc.stdout)
            self.assertEqual((td / "program.md").read_text(encoding="utf-8"), original)

    def test_doctor_rejects_and_restores_allowed_and_disallowed_eval_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(
                td,
                "print('agent')",
                "import json\nfrom pathlib import Path\nPath('target.txt').write_text('mutated')\nPath('hacked.txt').write_text('oops')\nprint(json.dumps({'score': 1, 'gates': {'correctness': True}}))",
            )
            proc = self.run_doctor(td, "--eval-runs", "1")
            self.assertEqual(proc.returncode, 1)
            self.assertIn("allowed changed target.txt", proc.stdout)
            self.assertIn("disallowed changed hacked.txt", proc.stdout)
            self.assertEqual((td / "target.txt").read_text(encoding="utf-8"), "baseline")
            self.assertFalse((td / "hacked.txt").exists())

    def test_legacy_loop_cli_still_works_after_doctor_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(td, "print('agent')", "import json\nprint(json.dumps({'score': 1}))")
            proc = self.run_ratchet(td, iterations=0)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("baseline", proc.stdout)
            self.assertNotIn("RatchetLab doctor", proc.stdout)

    @unittest.skipUnless(shutil.which("git"), "git not installed")
    def test_git_dirty_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            self.make_project(td, "print('agent')", "import json\nprint(json.dumps({'score': 1}))")
            subprocess.run(["git", "init"], cwd=td, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=td, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=td, check=True)
            subprocess.run(["git", "add", "."], cwd=td, check=True)
            subprocess.run(["git", "commit", "-m", "baseline"], cwd=td, check=True, capture_output=True)
            (td / "target.txt").write_text("dirty", encoding="utf-8")
            proc = subprocess.run([sys.executable, str(RATCHET), "--config", str(td / "ratchet.toml"), "--iterations", "1"], cwd=td, text=True, capture_output=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("clean", proc.stderr + proc.stdout)

    def test_codeperf_eval_outputs_correctness_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / "RatchetLab"
            shutil.copytree(ROOT, td, ignore=shutil.ignore_patterns(".git", ".ratchet", "__pycache__", "*.pyc", ".DS_Store"))
            proc = subprocess.run(
                [sys.executable, str(td / "examples/codeperf/eval.py")],
                cwd=td,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout.splitlines()[-1])
            self.assertTrue(payload["correct"])
            self.assertTrue(payload["gates"]["correctness"])
            self.assertIsInstance(payload["score"], (int, float))

            (td / "examples/codeperf/target.py").write_text(
                "def dedupe_preserve_order(items: list[int]) -> list[int]:\n    return []\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(td / "examples/codeperf/eval.py")],
                cwd=td,
                text=True,
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout.splitlines()[-1])
            self.assertFalse(payload["correct"])
            self.assertFalse(payload["gates"]["correctness"])
            self.assertEqual(payload["reason"], "wrong_answer")

    def test_doctor_codeperf_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / "RatchetLab"
            shutil.copytree(ROOT, td, ignore=shutil.ignore_patterns(".git", ".ratchet", "__pycache__", "*.pyc", ".DS_Store"))
            proc = subprocess.run(
                [sys.executable, str(td / "ratchet.py"), "doctor", "--config", str(td / "examples/codeperf/ratchet.toml")],
                cwd=td,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("RatchetLab doctor", proc.stdout)
            self.assertIn("Eval", proc.stdout)
            self.assertIn("Metric score", proc.stdout)
            self.assertIn("READY", proc.stdout)

    def test_codeperf_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / "RatchetLab"
            shutil.copytree(ROOT, td, ignore=shutil.ignore_patterns(".git", ".ratchet", "__pycache__", "*.pyc", ".DS_Store"))
            proc = subprocess.run(
                [sys.executable, str(td / "ratchet.py"), "--config", str(td / "examples/codeperf/ratchet.toml"), "--iterations", "2", "--no-git"],
                cwd=td,
                text=True,
                capture_output=True,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("done", proc.stdout)

    def test_promptopt_mock_eval_outputs_gated_score(self) -> None:
        env = dict(**os.environ, RATCHET_PROMPTOPT_PROVIDER="mock")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "examples/promptopt/eval.py")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout.splitlines()[-1])
        self.assertIn("score", payload)
        self.assertEqual(payload["cases"], 21)
        self.assertTrue(payload["gates"]["valid_json"])
        self.assertEqual(payload["provider"], "mock")

    def test_promptopt_ratchet_baseline_smoke_with_mock_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp) / "RatchetLab"
            shutil.copytree(ROOT, td, ignore=shutil.ignore_patterns(".git", ".ratchet", "__pycache__", "*.pyc", ".DS_Store"))
            env = dict(**os.environ, RATCHET_PROMPTOPT_PROVIDER="mock")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(td / "ratchet.py"),
                    "--config",
                    str(td / "examples/promptopt/ratchet.toml"),
                    "--iterations",
                    "0",
                    "--no-git",
                ],
                cwd=td,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("baseline", proc.stdout)
            self.assertTrue(any((td / ".ratchet" / "runs").glob("*/report.md")))


if __name__ == "__main__":
    unittest.main()
