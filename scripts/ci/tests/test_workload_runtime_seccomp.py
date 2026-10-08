import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "test/lib/check-workload-runtime-posture.py"
spec = importlib.util.spec_from_file_location("workload_runtime", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SeccompOverrideTests(unittest.TestCase):
  def test_container_override_takes_precedence(self):
    pod = {"seccompProfile": {"type": "RuntimeDefault"}}
    self.assertEqual(module.seccomp_gap(pod, {"seccompProfile": {"type": "Unconfined"}}),
      "unconfinedSeccomp")
    for value in ({}, None):
      self.assertEqual(module.seccomp_gap(pod, {"seccompProfile": value}), "missingSeccomp")
    self.assertIsNone(module.seccomp_gap(pod, {}))
    self.assertIsNone(module.seccomp_gap({"seccompProfile": {"type": "Unconfined"}},
      {"seccompProfile": {"type": "RuntimeDefault"}}))

  def test_localhost_profile_requires_safe_relative_path(self):
    for path in (None, "", " ", "/absolute.json", "../escape.json", "a/../b", "a//b", "./profile"):
      with self.subTest(path=path):
        self.assertEqual(module.seccomp_gap({}, {"seccompProfile": {
          "type": "Localhost", "localhostProfile": path}}), "invalidSeccomp")
    self.assertIsNone(module.seccomp_gap({}, {"seccompProfile": {
      "type": "Localhost", "localhostProfile": "operator/profile.json"}}))
    self.assertEqual(module.seccomp_gap({}, {"seccompProfile": True}), "invalidSeccomp")
    self.assertIsNone(module.seccomp_gap({}, {"seccompProfile": module.TEXT_PROFILE_PRESENT}))
    self.assertEqual(module.seccomp_gap({}, {"seccompProfile": {"type": "Typo"}}),
      "invalidSeccomp")

  def test_cli_rejects_unconfined_init_app_and_ephemeral_containers(self):
    for section in ("containers", "initContainers", "ephemeralContainers"):
      with self.subTest(section=section), tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "gitops").mkdir()
        profile = root / module.PROFILE
        profile.parent.mkdir(parents=True)
        profile.write_text("RuntimeClass 확인하지 못했다\n")
        manifest = root / "gitops/pod.yaml"
        manifest.write_text("""apiVersion: v1
kind: Pod
metadata:
  name: override
  namespace: regression
spec:
  securityContext:
    seccompProfile:
      type: RuntimeDefault
  SECTION:
    - name: unsafe
      image: example:v1
      securityContext:
        seccompProfile:
          type: Unconfined
""".replace("SECTION", section))
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", directory],
          capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("regression:unconfinedSeccomp=1", result.stderr)
        manifest.write_text(manifest.read_text().replace("seccompProfile:\n          type: Unconfined", "seccompProfile: true"))
        result = subprocess.run([sys.executable, str(SCRIPT), "--root", directory],
          capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("regression:invalidSeccomp=1", result.stderr)
