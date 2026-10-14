import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "test/lib/check-static-pod-hostpaths.py"
spec = importlib.util.spec_from_file_location("static_hostpaths", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def workload(kind, section="containers", read_only=False):
  pod = {"volumes": [{"name": "host", "hostPath": {"path": "/etc/kubernetes/manifests"}}],
    section: [{"name": "write", "volumeMounts": [{"name": "host", "mountPath": "/host",
      "readOnly": read_only}]}]}
  if kind == "Pod":
    spec = pod
  elif kind == "CronJob":
    spec = {"jobTemplate": {"spec": {"template": {"spec": pod}}}}
  else:
    spec = {"template": {"spec": pod}}
  return {"apiVersion": "batch/v1" if kind in ("CronJob", "Job") else "v1",
    "kind": kind, "metadata": {"name": "unsafe", "namespace": "regression"}, "spec": spec}


class HostpathWorkloadShapeTests(unittest.TestCase):
  def test_inventory_covers_cronjob_and_existing_controller_shapes(self):
    for kind in ("Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"):
      with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "workload.yaml"
        path.write_text(yaml.safe_dump(workload(kind)))
        self.assertEqual(module.hostpath_mounts(path), [{"path": "/etc/kubernetes/manifests",
          "namespace": "regression", "readOnly": False}])

  def test_ephemeral_container_mount_readonly_is_inventoried(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "pod.yaml"
      path.write_text(yaml.safe_dump(workload("Pod", "ephemeralContainers", True)))
      self.assertEqual(module.hostpath_mounts(path)[0]["readOnly"], True)

  def test_actual_cli_rejects_cronjob_unapproved_manifest_mount(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      script = root / "scripts/test/lib/check-static-pod-hostpaths.py"
      script.parent.mkdir(parents=True)
      shutil.copy2(SCRIPT, script)
      for relative in ("scripts/airgap/04-bootstrap-registry-k8s.yaml",
        "scripts/cluster/harden-node-files.sh", "scripts/cluster/02-init-cluster.sh",
        "scripts/cluster/02-join-control-plane.sh"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(module.ROOT / relative, target)
      (root / "gitops").mkdir()
      (root / "gitops/unsafe-cronjob.yaml").write_text(yaml.safe_dump(workload("CronJob")))
      result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
      self.assertNotEqual(result.returncode, 0)
      self.assertIn("unapproved hostPath '/etc/kubernetes/manifests'", result.stderr)
