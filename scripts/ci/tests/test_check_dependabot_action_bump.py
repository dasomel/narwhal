import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT_PATH = str(Path(__file__).resolve().parent.parent / "check-dependabot-action-bump.py")

class TestCheckDependabotActionBump(unittest.TestCase):
  def setUp(self):
    self.tmpdir = tempfile.TemporaryDirectory()
    self.repo = Path(self.tmpdir.name)
    self._git(["init", "-b", "main"])
    self._git(["config", "user.name", "Test User"])
    self._git(["config", "user.email", "test@example.com"])
    self._git(["config", "commit.gpgsign", "false"])

  def tearDown(self):
    self.tmpdir.cleanup()

  def _git(self, args):
    return subprocess.run(
      ["git"] + args,
      cwd=str(self.repo),
      capture_output=True,
      text=True,
      check=True
    )

  def _commit(self, msg="commit"):
    self._git(["add", "."])
    self._git(["commit", "-m", msg, "--allow-empty"])
    return self._git(["rev-parse", "HEAD"]).stdout.strip()

  def _run_script(self, base, head, resolver_map=None, resolver_file=None):
    cmd = [sys.executable, SCRIPT_PATH, "--base", base, "--head", head]
    if resolver_map is not None:
      rpath = self.repo / "resolver.json"
      rpath.write_text(json.dumps(resolver_map), encoding="utf-8")
      cmd.extend(["--resolver-json", str(rpath)])
    elif resolver_file is not None:
      cmd.extend(["--resolver-json", str(resolver_file)])
    res = subprocess.run(cmd, cwd=str(self.repo), capture_output=True, text=True)
    try:
      data = json.loads(res.stdout)
    except Exception:
      data = {"stdout": res.stdout, "stderr": res.stderr}
    return res.returncode, data

  def test_single_valid_bump(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n"
    )
    head = self._commit("bump")

    code, data = self._run_script(
      base, head,
      resolver_map={"actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 0)
    self.assertTrue(data.get("exempt"))
    self.assertEqual(len(data.get("bumps", [])), 1)

  def test_two_workflow_files_valid(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    f1 = wf_dir / "ci.yml"
    f2 = wf_dir / "deploy.yaml"
    f1.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    f2.write_text(
      "name: Deploy\njobs:\n  dep:\n    steps:\n      - uses: ruby/setup-ruby@95ef2b042f9d7a56d8268cba8559e2842e2ad01b # v1.321.0\n"
    )
    base = self._commit("base")

    f1.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n"
    )
    f2.write_text(
      "name: Deploy\njobs:\n  dep:\n    steps:\n      - uses: ruby/setup-ruby@a0102e0972be65f351c307e2d64b9314a57c8073 # v1.324.0\n"
    )
    head = self._commit("bump both")

    code, data = self._run_script(
      base, head,
      resolver_map={
        "actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332",
        "ruby/setup-ruby@v1.324.0": "a0102e0972be65f351c307e2d64b9314a57c8073"
      }
    )
    self.assertEqual(code, 0)
    self.assertTrue(data.get("exempt"))
    self.assertEqual(len(data.get("bumps", [])), 2)

  def test_extra_non_uses_line_changed(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    name: Build Old\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    name: Build New\n    steps:\n      - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n"
    )
    head = self._commit("bump and edit name")

    code, data = self._run_script(
      base, head,
      resolver_map={"actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_ref_owner_changed(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: evil/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n"
    )
    head = self._commit("switch ref")

    code, data = self._run_script(
      base, head,
      resolver_map={"evil/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_sha_not_matching_resolver(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@0000000000000000000000000000000000000000 # v4.1.7\n"
    )
    head = self._commit("bad sha")

    code, data = self._run_script(
      base, head,
      resolver_map={"actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_malformed_missing_tag_comment(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332\n"
    )
    head = self._commit("missing comment")

    code, data = self._run_script(
      base, head,
      resolver_map={"actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_non_workflow_file_in_diff(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    readme = self.repo / "README.md"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    readme.write_text("# Old Readme\n")
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n"
    )
    readme.write_text("# New Readme\n")
    head = self._commit("bump and readme")

    code, data = self._run_script(
      base, head,
      resolver_map={"actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_empty_diff(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")
    code, data = self._run_script(base, base, resolver_map={})
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_resolver_error_missing_entry(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n"
    )
    head = self._commit("bump")

    code, data = self._run_script(base, head, resolver_map={})
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

    code, data = self._run_script(base, head, resolver_file=self.repo / "nonexistent.json")
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_added_run_line(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n      - run: echo pwned\n"
    )
    head = self._commit("bump and run line")

    code, data = self._run_script(
      base, head,
      resolver_map={"actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

  def test_indentation_change(self):
    wf_dir = self.repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf_file = wf_dir / "ci.yml"
    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n"
    )
    base = self._commit("base")

    wf_file.write_text(
      "name: CI\njobs:\n  build:\n    steps:\n        - uses: actions/checkout@692973e3d937129bcbf40652eb9f2f61becf3332 # v4.1.7\n"
    )
    head = self._commit("bump and indent change")

    code, data = self._run_script(
      base, head,
      resolver_map={"actions/checkout@v4.1.7": "692973e3d937129bcbf40652eb9f2f61becf3332"}
    )
    self.assertEqual(code, 1)
    self.assertFalse(data.get("exempt"))

if __name__ == "__main__":
  unittest.main()
