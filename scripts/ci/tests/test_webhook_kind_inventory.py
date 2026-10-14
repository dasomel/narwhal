import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "test/lib/check-admission-webhook-settings.py"
spec = importlib.util.spec_from_file_location("webhook_settings", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

GOOD = """apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata:
  name: baseline
webhooks:
  - name: baseline.example.org
    failurePolicy: Fail
    timeoutSeconds: 5
"""


class WebhookKindInventoryTests(unittest.TestCase):
  def test_actual_cli_rejects_quoted_or_commented_unsafe_webhook(self):
    for kind in ('"ValidatingWebhookConfiguration"', "'MutatingWebhookConfiguration'",
      'ValidatingWebhookConfiguration # production'):
      with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        good = root / "good.yaml"
        good.write_text(GOOD)
        bad = root / "unsafe.yaml"
        bad.write_text(GOOD.replace("kind: ValidatingWebhookConfiguration", "kind: " + kind)
          .replace("timeoutSeconds: 5", "timeoutSeconds: 99"))
        result = subprocess.run([sys.executable, str(SCRIPT), str(good), str(bad)],
          capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("outside 1..30", result.stdout)
        self.assertIn("configurations=2, entries=2", result.stdout)

  def test_accepts_valid_quoted_and_commented_webhook(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      path = root / "quoted.yaml"
      path.write_text(GOOD.replace("kind: ValidatingWebhookConfiguration",
        'kind: "ValidatingWebhookConfiguration" # validated'))
      errors, warnings, configs, entries = module.check_paths([path])
      self.assertEqual(errors, [])
      self.assertEqual((configs, entries), (1, 1))

  def test_template_inventory_retains_quoted_kind(self):
    text = GOOD.replace("kind: ValidatingWebhookConfiguration",
      'kind: "ValidatingWebhookConfiguration" # rendered')
    errors, warnings, configs, entries = module.check_template(text, "templates/test.yaml")
    self.assertEqual(errors, [])
    self.assertEqual((configs, entries), (1, 1))
