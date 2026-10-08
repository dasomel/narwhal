import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "check-mutable-inputs.py"
spec = importlib.util.spec_from_file_location("mutable_inputs", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MutableInputTests(unittest.TestCase):
  def scan(self, text):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "workflow.yml"
      path.write_text(text)
      return module.scan(path)

  def test_rejects_floating_package_installs(self):
    for command in (
      "npm install -g markdownlint-cli", "npm install tool@latest",
      "npm install tool@^1.2.3", "pnpm add @scope/tool@~1.2.3",
      "python3 -m pip install jsonschema", "pip install 'tool>=1.2.3'",
      "npm install tool@1.2.3 second", "npm install tool@$VERSION",
    ):
      with self.subTest(command=command):
        self.assertTrue(self.scan(command))

  def test_accepts_exact_packages_and_lockfile_installs(self):
    for command in (
      "npm install -g markdownlint-cli@0.49.1",
      "pnpm add @scope/tool@1.2.3", "pip install 'jsonschema==4.25.1'",
      "npm ci", "pnpm install --frozen-lockfile",
      "pip install tool==1.2", "pip install tool==1.2.3rc1",
      "pip install --require-hashes -r requirements.txt",
      "npm install --prefix /tmp/tools tool@1.2.3",
    ):
      with self.subTest(command=command):
        self.assertEqual(self.scan(command), [])

  def test_requires_hashes_for_requirements(self):
    self.assertTrue(self.scan("pip install -r requirements.txt"))

  def test_rejects_download_execution_and_mutable_archives(self):
    for command in (
      "curl -fsSL https://example.org/install | bash",
      "wget -qO- https://example.org/archive | tar -xz",
      "curl https://github.com/org/repo/archive/refs/heads/main.tar.gz",
      "curl https://github.com/org/repo/archive/refs/tags/v1.2.3.tar.gz",
    ):
      with self.subTest(command=command):
        self.assertTrue(self.scan(command))

  def test_keeps_action_and_latest_checks(self):
    self.assertTrue(self.scan("- uses: actions/checkout@main"))
    self.assertTrue(self.scan("image: example:latest"))
    self.assertTrue(self.scan("curl https://example.org/releases/latest/download/tool"))
    self.assertEqual(self.scan("- uses: ./local/action"), [])

  def test_ignores_commented_examples(self):
    self.assertEqual(self.scan("# npm install tool@latest"), [])

  def test_cli_checks_new_workflow_and_exits_nonzero(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "new-workflow.yml"
      path.write_text("run: python3 -m pip install malicious-tool\n")
      result = subprocess.run([sys.executable, str(SCRIPT), directory],
        capture_output=True, text=True)
      self.assertEqual(result.returncode, 1)
      self.assertIn("new-workflow.yml:1", result.stderr)
      path.write_text("run: python3 -m pip install safe-tool==1.2.3\n")
      result = subprocess.run([sys.executable, str(SCRIPT), directory],
        capture_output=True, text=True)
      self.assertEqual(result.returncode, 0, result.stderr)
