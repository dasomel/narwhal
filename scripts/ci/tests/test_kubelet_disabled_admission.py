import copy
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "test/lib/check-kubelet-authz-baseline.py"
spec = importlib.util.spec_from_file_location("kubelet_baseline", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DisabledAdmissionTests(unittest.TestCase):
  def test_disabled_noderestriction_fails_in_list_and_map_forms(self):
    source = module.INIT.read_text()
    kubelet, cluster = module.parse_kubeadm_documents(source)
    for args in (
      [{"name": "enable-admission-plugins", "value": "NodeRestriction"},
       {"name": "disable-admission-plugins", "value": "NodeRestriction"}],
      {"enable-admission-plugins": "NodeRestriction",
       "disable-admission-plugins": "NodeRestriction"},
      {"disable-admission-plugins": "NodeRestriction"},
    ):
      with self.subTest(args=args):
        mutated = copy.deepcopy(cluster)
        mutated["apiServer"]["extraArgs"] = args
        result = module.checks(kubelet, mutated)
        self.assertFalse(result["apiserver NodeRestriction admission plugin is enabled"])

  def test_disabling_other_plugin_does_not_disable_noderestriction(self):
    kubelet, cluster = module.parse_kubeadm_documents(module.INIT.read_text())
    cluster["apiServer"]["extraArgs"] = {"disable-admission-plugins": "AlwaysAdmit"}
    self.assertTrue(module.checks(kubelet, cluster)[
      "apiserver NodeRestriction admission plugin is enabled"])

  def test_text_fallback_retains_disabled_flag(self):
    source = "apiServer:\n  extraArgs:\n    disable-admission-plugins: NodeRestriction\n"
    self.assertEqual(module.apiserver_args(source)["disable-admission-plugins"], "NodeRestriction")
    self.assertFalse(module.checks("", source)[
      "apiserver NodeRestriction admission plugin is enabled"])

  def test_actual_cli_rejects_disabled_noderestriction(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      script = root / "scripts/test/lib/check-kubelet-authz-baseline.py"
      script.parent.mkdir(parents=True)
      shutil.copy2(SCRIPT, script)
      init = root / "scripts/cluster/02-init-cluster.sh"
      init.parent.mkdir(parents=True)
      text = module.INIT.read_text()
      # Preserve all real kubeadm documents and shell splice substitutions.
      text = text.replace('    - name: enable-admission-plugins',
        '    - name: disable-admission-plugins\n      value: "NodeRestriction"\n    - name: enable-admission-plugins', 1)
      self.assertIn("disable-admission-plugins", text)
      init.write_text(text)
      result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
      self.assertEqual(result.returncode, 1)
      self.assertIn("FAIL apiserver NodeRestriction admission plugin is enabled", result.stdout)
