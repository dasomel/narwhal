import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("mdc", ROOT / "scripts/ci/markdownlint-changed.py")
mdc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mdc)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


def commit(cwd, msg):
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)
    return git(cwd, "rev-parse", "HEAD")


class PortalBaseline(unittest.TestCase):
    """PR base A has no portal/; B is the import (baseline); C is a later PR change."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        git(d, "init", "-q")
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
            git(d, "config", k, v)
        (d / "README.md").write_text("# root\n", encoding="utf-8")
        cls.a = commit(d, "A")
        (d / "portal").mkdir()
        (d / "portal/legacy.md").write_text("# legacy\n\nsee https://example.invalid/bare\n", encoding="utf-8")
        (d / "portal/sub").mkdir()
        (d / "portal/sub/deep.md").write_text("# deep\n\nsee https://example.invalid/deep\n", encoding="utf-8")
        (d / "docs.md").write_text("# docs\n\nsee https://example.invalid/docs\n", encoding="utf-8")
        cls.b = commit(d, "B import")
        (d / "portal/legacy.md").write_text("# legacy\n\nsee https://example.invalid/bare\n\nnew https://example.invalid/new\n", encoding="utf-8")
        (d / "portal/added.md").write_text("# added\n\nsee https://example.invalid/added\n", encoding="utf-8")
        commit(d, "C")
        cls.dir = d

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ranges(self, base, baseline):
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            return mdc.changed_line_ranges(base, baseline)
        finally:
            os.chdir(cwd)

    def lines(self, ranges, path):
        return sorted(n for r in ranges.get(path, []) for n in r)

    def test_imported_unchanged_files_have_no_changed_lines(self):
        r = self.ranges(self.a, self.b)
        self.assertNotIn("portal/sub/deep.md", r)
        self.assertEqual(self.lines(r, "portal/legacy.md"), [4, 5])  # only what C added, not the legacy bare URL at line 3

    def test_added_portal_file_is_fully_changed(self):
        self.assertEqual(self.lines(self.ranges(self.a, self.b), "portal/added.md"), [1, 2, 3])

    def test_non_portal_file_is_still_measured_against_the_pr_base(self):
        r = self.ranges(self.a, self.b)
        self.assertEqual(self.lines(r, "docs.md"), [1, 2, 3])

    def test_absent_baseline_falls_back_to_the_pr_base(self):
        r = self.ranges(self.a, "0" * 40)
        self.assertEqual(self.lines(r, "portal/sub/deep.md"), [1, 2, 3])
        self.assertEqual(self.lines(r, "portal/legacy.md"), [1, 2, 3, 4, 5])

    @unittest.skipUnless(subprocess.run(["which", "markdownlint"], capture_output=True).returncode == 0, "markdownlint not installed")
    def test_end_to_end_gate(self):
        (self.dir / ".markdownlint.json").write_text('{"default": true}\n', encoding="utf-8")
        script = str(ROOT / "scripts/ci/markdownlint-changed.py")
        env = dict(os.environ)
        r = subprocess.run(["python3", script, "--base", self.a, "--portal-baseline", self.b], cwd=self.dir, text=True, capture_output=True, env=env)
        # C added bare URLs (legacy.md line 5, added.md line 3) and docs.md line 3 is changed vs A: must fail
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("portal/legacy.md:5", r.stderr)
        self.assertIn("portal/added.md:3", r.stderr)
        self.assertNotIn("portal/legacy.md:3", r.stderr)
        self.assertNotIn("portal/sub/deep.md", r.stderr)


if __name__ == "__main__":
    unittest.main()
