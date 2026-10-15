import os
import subprocess
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
