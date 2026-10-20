import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "check-portal-release-ref.py"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


class ReleaseRef(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        git(d, "init", "-q")
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(d, "config", k, v)
        git(d, "commit", "-q", "--allow-empty", "-m", "one")
        cls.one = git(d, "rev-parse", "HEAD")
        git(d, "commit", "-q", "--allow-empty", "-m", "two")
        cls.two = git(d, "rev-parse", "HEAD")
        git(d, "tag", "portal/v1.2.3", cls.two)
        git(d, "tag", "-a", "-m", "annotated", "portal/v1.3.0", cls.two)  # annotated: must peel to the commit
        git(d, "tag", "portal/v9.9.9", cls.one)
        git(d, "branch", "portal/v1.2.3x")
        cls.dir = d

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_script(self, *args):
        r = subprocess.run(["python3", str(SCRIPT), *args], cwd=self.dir, text=True, capture_output=True)
        return r.returncode, r.stderr

    def test_release_push_tag_ok(self):
        self.assertEqual(self.run_script("--mode", "release", "--event", "push", "--ref-name", "portal/v1.2.3", "--sha", self.two)[0], 0)

    def test_release_annotated_tag_ok(self):
        self.assertEqual(self.run_script("--mode", "release", "--event", "push", "--ref-name", "portal/v1.3.0", "--sha", self.two)[0], 0)

    def test_release_dispatch_on_branch_named_like_tag_refused(self):
        rc, err = self.run_script("--mode", "release", "--event", "workflow_dispatch", "--ref-name", "portal/v1.2.3", "--sha", self.two)
        self.assertEqual(rc, 1)
        self.assertIn("tag push", err)

    def test_release_tag_pointing_elsewhere_refused(self):
        rc, err = self.run_script("--mode", "release", "--event", "push", "--ref-name", "portal/v9.9.9", "--sha", self.two)
        self.assertEqual(rc, 1)
        self.assertIn("points at", err)

    def test_release_nonmatching_name_refused(self):
        for name in ("v1.2.3", "portal/v1.2", "portal/v1.2.3/../x", "portal/v1.2.3x"):
            self.assertEqual(self.run_script("--mode", "release", "--event", "push", "--ref-name", name, "--sha", self.two)[0], 1, name)

    def test_release_missing_tag_refused(self):
        rc, err = self.run_script("--mode", "release", "--event", "push", "--ref-name", "portal/v7.7.7", "--sha", self.two)
        self.assertEqual(rc, 1)
        self.assertIn("does not exist", err)

    def test_release_short_sha_refused(self):
        self.assertEqual(self.run_script("--mode", "release", "--event", "push", "--ref-name", "portal/v1.2.3", "--sha", self.two[:7])[0], 1)

    def test_publish_tag_ok_and_mismatch_refused(self):
        self.assertEqual(self.run_script("--mode", "publish", "--event", "push", "--ref-type", "tag", "--ref-name", "portal/v1.2.3", "--sha", self.two)[0], 0)
        self.assertEqual(self.run_script("--mode", "publish", "--event", "workflow_dispatch", "--ref-type", "tag", "--ref-name", "portal/v1.2.3", "--sha", self.one)[0], 1)

    def test_publish_dispatch_from_ordinary_branch_ok(self):
        self.assertEqual(self.run_script("--mode", "publish", "--event", "workflow_dispatch", "--ref-type", "branch", "--ref-name", "main", "--sha", self.two)[0], 0)

    def test_publish_dispatch_from_release_looking_branch_refused(self):
        self.assertEqual(self.run_script("--mode", "publish", "--event", "workflow_dispatch", "--ref-type", "branch", "--ref-name", "portal/v1.2.3", "--sha", self.two)[0], 1)

    def test_repo_arg_reads_tags_from_other_checkout(self):
        # the validator lives in one checkout (cwd here is an unrelated empty repo), the tag in another
        with tempfile.TemporaryDirectory() as other:
            git(other, "init", "-q")
            args = ("--mode", "release", "--event", "push", "--ref-name", "portal/v1.2.3", "--sha", self.two)
            r = subprocess.run(["python3", str(SCRIPT), *args], cwd=other, text=True, capture_output=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("does not exist", r.stderr)
            r = subprocess.run(["python3", str(SCRIPT), *args, "--repo", self.dir], cwd=other, text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_repo_arg_mismatch_still_refused(self):
        with tempfile.TemporaryDirectory() as other:
            git(other, "init", "-q")
            r = subprocess.run(
                ["python3", str(SCRIPT), "--mode", "release", "--event", "push", "--ref-name", "portal/v9.9.9",
                 "--sha", self.two, "--repo", self.dir],
                cwd=other, text=True, capture_output=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("points at", r.stderr)

    def test_tagged_repo_with_trusted_symlink_has_no_effect(self):
        # portal-release.yml layout: validator from the trusted root, tag lookup in tagged/ whose
        # tree carries symlinks named `trusted` / `scripts` pointing at . and .git (PR #355 P2-1)
        with tempfile.TemporaryDirectory() as root:
            tagged = Path(root) / "tagged"
            tagged.mkdir()
            git(tagged, "init", "-q")
            for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
                git(tagged, "config", k, v)
            (tagged / "trusted").symlink_to(".")
            (tagged / "scripts").symlink_to(".git")
            git(tagged, "add", "trusted", "scripts")
            git(tagged, "commit", "-q", "-m", "evil tree")
            sha = git(tagged, "rev-parse", "HEAD")
            git(tagged, "tag", "portal/v2.0.0", sha)
            r = subprocess.run(
                ["python3", str(SCRIPT), "--mode", "release", "--event", "push", "--ref-name", "portal/v2.0.0",
                 "--sha", sha, "--repo", "tagged"],
                cwd=root, text=True, capture_output=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((tagged / ".git").is_dir())
            r = subprocess.run(
                ["python3", str(SCRIPT), "--mode", "release", "--event", "push", "--ref-name", "portal/v2.0.0",
                 "--sha", self.two, "--repo", "tagged"],
                cwd=root, text=True, capture_output=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("points at", r.stderr)


class ReleaseWorkflowLayout(unittest.TestCase):
    """portal-release.yml: trusted default branch at the root, tagged commit only under tagged/."""

    @classmethod
    def setUpClass(cls):
        wf = Path(__file__).resolve().parents[3] / ".github/workflows/portal-release.yml"
        cls.text = wf.read_text(encoding="utf-8")
        cls.steps = yaml.safe_load(cls.text)["jobs"]["release"]["steps"]

    def checkouts(self):
        return [s for s in self.steps if str(s.get("uses", "")).startswith("actions/checkout@")]

    def test_root_checkout_is_default_branch_without_credentials(self):
        root = self.checkouts()[0]
        self.assertNotIn("path", root["with"])
        self.assertEqual(root["with"]["ref"], "${{ github.sha }}")
        self.assertIs(root["with"]["persist-credentials"], False)

    def test_single_checkout_and_no_workflow_run_ref(self):
        # D9 (#395): Scorecard Dangerous-Workflow flags a checkout whose ref comes from workflow_run
        cos = self.checkouts()
        self.assertEqual(len(cos), 1)
        for co in cos:
            self.assertNotIn("workflow_run", str(co.get("with", {}).get("ref", "")))

    def test_tagged_worktree_only_after_ref_proof(self):
        runs = [s.get("run", "") for s in self.steps]
        proof = next(i for i, r in enumerate(runs) if "check-portal-release-ref.py" in r)
        wt = next(i for i, r in enumerate(runs) if "git worktree add --detach tagged" in r)
        self.assertLess(proof, wt)
        self.assertIn('"$RUN_SHA"', runs[wt])

    def test_validator_runs_from_root_against_workspace(self):
        run = next(s["run"] for s in self.steps if "check-portal-release-ref.py" in s.get("run", ""))
        self.assertIn("python3 scripts/ci/check-portal-release-ref.py", run)
        self.assertIn("--repo .", run)
        self.assertNotIn("tagged/scripts", run)

    def test_release_inputs_are_blobs_of_regular_files(self):
        run = next(s["run"] for s in self.steps if "ls-tree" in s.get("run", ""))
        self.assertIn("100644|100755", run)
        self.assertIn('git -C tagged show', run)
        for banned in ("portal/CHANGELOG.md >", "files: |\n            portal/"):
            self.assertNotIn(banned, self.text)
        self.assertNotIn("working-directory", self.text)


if __name__ == "__main__":
    unittest.main()
