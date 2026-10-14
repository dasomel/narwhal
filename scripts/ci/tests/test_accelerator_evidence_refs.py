import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "test/lib/check-accelerator-profiles.py"
spec = importlib.util.spec_from_file_location("accelerator_profiles", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HardwareEvidenceReferencesTests(unittest.TestCase):
  def setUp(self):
    self.schema = json.loads(module.SCHEMA.read_text())
    self.profile = json.loads((module.PROFILE_DIR / "nvidia-linux.json").read_text())

  def validate(self, refs):
    profile = copy.deepcopy(self.profile)
    profile["capabilities"]["partitioning.hardware"].update(
      status="supported", evidenceKind="hardware", evidenceRefs=refs)
    module.validate(profile, {profile["backend"]}, set(profile["capabilities"]), self.schema)

  def test_rejects_missing_blank_and_malformed_references(self):
    for refs in ([], [""], [" "], ["\n\t"], ["local.json", ""], [None], "local.json"):
      with self.subTest(refs=refs), self.assertRaises(ValueError):
        self.validate(refs)

  def test_accepts_nonempty_offline_reference_without_network(self):
    self.validate(["evidence/gpu-conformance.json#partitioning.hardware"])
    # Reference presence does not assert that the report exists or is trustworthy.
    self.validate(["urn:narwhal:offline-evidence:sha256:sample"])

  def test_directory_gate_rejects_supported_hardware_without_reference(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory) / "profiles"
      shutil.copytree(module.PROFILE_DIR, root)
      profile = copy.deepcopy(self.profile)
      profile["capabilities"]["partitioning.hardware"].update(
        status="supported", evidenceKind="hardware", evidenceRefs=[])
      (root / "nvidia-linux.json").write_text(json.dumps(profile))
      with self.assertRaisesRegex(ValueError, "requires nonempty evidenceRefs"):
        module.check_profiles(root)
