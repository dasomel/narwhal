import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
LIB = REPO / 'scripts/airgap/lib'


class UpgradeArtifactIdentityCliTests(unittest.TestCase):
    def metadata_verifier(self, name):
        manifest = json.loads((LIB / 'upgrade-bundle-v1.1.0.json').read_text())
        manifest['artifacts'][1]['name'] = name
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'manifest.json'
            path.write_text(json.dumps(manifest))
            return subprocess.run([sys.executable, '-S', str(LIB / 'verify-upgrade-bundle.py'), '--manifest', str(path)],
                                  capture_output=True, text=True)

    def test_metadata_rejects_duplicate_name_across_types(self):
        result = self.metadata_verifier('cert-manager-controller')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('duplicate artifact name', result.stderr)

    def test_metadata_rejects_missing_and_malformed_identity_cleanly(self):
        for name in ['', '   ', None, [], {}]:
            with self.subTest(name=name):
                result = self.metadata_verifier(name)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Traceback', result.stderr)

    def test_metadata_retains_unique_names(self):
        result = self.metadata_verifier('cert-manager-chart')
        self.assertEqual(result.returncode, 0, result.stderr)

    def content_verifier(self, names):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            artifacts = []
            for index, name in enumerate(names):
                content = f'payload-{index}'.encode()
                path = f'artifact-{index}.pkg'
                (root / path).write_bytes(content)
                artifacts.append({'name': name, 'component': 'istio', 'version': '1.30.1',
                                  'artifact_type': 'operator_package', 'path': path,
                                  'digest': 'sha256:' + hashlib.sha256(content).hexdigest()})
            (root / 'manifest.json').write_text(json.dumps({'target_version': '1.35.7', 'artifacts': artifacts}))
            return subprocess.run([sys.executable, str(REPO / 'scripts/verify/upgrade-bundle-check.py'), '--root', str(root)],
                                  capture_output=True, text=True)

    def test_content_rejects_duplicate_name_even_when_files_differ(self):
        result = self.content_verifier(['same', 'same'])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('duplicate artifact name', result.stdout)

    def test_content_retains_distinct_names(self):
        result = self.content_verifier(['first', 'second'])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_content_rejects_malformed_names_cleanly(self):
        for name in [None, '', [], {}]:
            with self.subTest(name=name):
                result = self.content_verifier(['first', name])
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
