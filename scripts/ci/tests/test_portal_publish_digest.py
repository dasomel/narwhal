import re
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/portal-docker-publish.yml"


class PublishDigestPropagation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = yaml.safe_load(WORKFLOW.read_text())
        cls.merge = cls.wf["jobs"]["merge"]
        cls.sign = cls.wf["jobs"]["sign"]
        cls.sign_text = "\n".join(str(s.get("run", "")) + str(s.get("env", "")) for s in cls.sign["steps"])

    def test_sign_references_only_declared_merge_outputs(self):
        used = set(re.findall(r"needs\.merge\.outputs\.(\w+)", WORKFLOW.read_text()))
        self.assertTrue(used, "sign job must read a merge output")
        self.assertLessEqual(used, set(self.merge["outputs"]))

    def test_merge_output_points_at_step_that_writes_it(self):
        for name, expr in self.merge["outputs"].items():
            m = re.fullmatch(r"\$\{\{ steps\.([\w-]+)\.outputs\.(\w+) \}\}", expr)
            self.assertIsNotNone(m, expr)
            step = next(s for s in self.merge["steps"] if s.get("id") == m.group(1))
            self.assertIn(f'echo "{m.group(2)}=', step["run"])

    def test_merge_does_not_read_build_push_metadata_key_from_imagetools(self):
        # `imagetools create --metadata-file` has no `containerimage.digest` key (v1.1.0: `@null`).
        for step in self.merge["steps"]:
            self.assertNotIn("containerimage.digest", step.get("run", ""))

    def test_digest_is_validated_in_merge_and_before_cosign(self):
        pattern = "sha256:[0-9a-f]{64}"
        merge_run = next(s for s in self.merge["steps"] if s.get("id") == "create-manifest")["run"]
        self.assertIn(pattern, merge_run)
        names = [s.get("name") for s in self.sign["steps"]]
        validate = names.index("Validate image digest")
        cosign = names.index("Sign image digest")
        self.assertLess(validate, cosign)
        self.assertIn(pattern, self.sign["steps"][validate]["run"])


if __name__ == "__main__":
    unittest.main()
