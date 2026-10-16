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

    def _step(self, name):
        return next(s for s in self.sign["steps"] if s.get("name") == name)

    def test_sign_digest_makes_signing_required(self):
        gate = self._step("Determine whether signing is mandatory")
        self.assertIn("-n \"$SIGN_DIGEST\"", gate["run"])
        self.assertIn("inputs.sign_digest", gate["env"]["SIGN_DIGEST"])

    def test_sign_digest_reaches_shell_only_via_env(self):
        for job in (self.merge, self.sign):
            for step in job["steps"]:
                self.assertNotIn("inputs.sign_digest", str(step.get("run", "")))
                self.assertNotIn("needs.merge.outputs", str(step.get("run", "")))

    def test_existence_check_precedes_cosign(self):
        names = [s.get("name") for s in self.sign["steps"]]
        exists = names.index("Verify digest exists in the image repository")
        self.assertLess(exists, names.index("Sign image digest"))
        self.assertIn('imagetools inspect "${IMAGE}@${DIGEST}"', self.sign["steps"][exists]["run"])

    def test_merge_digest_comes_from_create_descriptor_not_a_tag_lookup(self):
        run = next(s for s in self.merge["steps"] if s.get("id") == "create-manifest")["run"]
        self.assertIn("containerimage.descriptor", run)
        self.assertNotIn("tags[0]", run)

    def test_arch_check_inspects_the_created_digest_not_a_mutable_tag(self):
        step = next(s for s in self.merge["steps"] if s.get("name") == "Verify both architectures are present")
        self.assertIn("steps.create-manifest.outputs.digest", step["env"]["DIGEST"])
        self.assertIn('ref="${IMAGE}@${DIGEST}"', step["run"])
        self.assertNotIn("steps.meta.outputs.version", step["run"])


if __name__ == "__main__":
    unittest.main()
