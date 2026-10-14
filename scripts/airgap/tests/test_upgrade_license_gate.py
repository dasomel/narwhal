import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

LIB = pathlib.Path(__file__).resolve().parents[1] / 'lib'


class UpgradeLicenseGateCliTests(unittest.TestCase):
    def run_verifier(self, license_value):
        manifest = json.loads((LIB / 'upgrade-bundle-v1.1.0.json').read_text())
        manifest['artifacts'][0]['license'] = license_value
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'manifest.json'
            path.write_text(json.dumps(manifest))
            return subprocess.run([sys.executable, str(LIB / 'verify-upgrade-bundle.py'), '--manifest', str(path)],
                                  capture_output=True, text=True)

    def test_approved_license_passes(self):
        for value in ['Apache-2.0', 'MIT', 'curl', 'AGPL-3.0']:
            with self.subTest(license=value):
                result = self.run_verifier(value)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unreviewed_license_fails_closed(self):
        for value in ['UNKNOWN', 'NOASSERTION', 'LicenseRef-Custom', 'MIT OR Apache-2.0']:
            with self.subTest(license=value):
                result = self.run_verifier(value)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('needs review', result.stderr)

    def test_forbidden_and_blank_licenses_remain_rejected(self):
        for value in ['SSPLv1', 'BUSL-1.1', '', '   ']:
            with self.subTest(license=value):
                result = self.run_verifier(value)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Traceback', result.stderr)

    def test_malformed_licenses_fail_with_diagnostic(self):
        for value in [None, 123, [], {}]:
            with self.subTest(license=value):
                result = self.run_verifier(value)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Traceback', result.stderr)

    def test_real_promotion_rejects_unknown_before_changing_previous_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = pathlib.Path(tmp)
            root = bundle / 'upgrade-bundle'
            staged = root / 'staged'
            promoted = root / 'promoted'
            staged.mkdir(parents=True)
            promoted.mkdir()
            sentinel = promoted / 'existing-evidence.txt'
            sentinel.write_text('existing approved bundle')
            manifest = json.loads((LIB / 'upgrade-bundle-v1.1.0.json').read_text())
            manifest['artifacts'][0]['license'] = 'UNKNOWN'
            (staged / 'upgrade-bundle.json').write_text(json.dumps(manifest))
            command = ['bash', str(LIB / 'promote-upgrade-bundle.sh'), '--bundle', str(bundle),
                       '--approved-by', 'local-regression-fixture']
            denied = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(denied.returncode, 0, denied.stdout)
            self.assertIn('needs review', denied.stderr)
            self.assertEqual(sentinel.read_text(), 'existing approved bundle')
            self.assertFalse((root / 'promoted-previous').exists())
            self.assertFalse((root / 'promotion-log.jsonl').exists())
            # The same real promotion entrypoint retains its approved path.
            manifest['artifacts'][0]['license'] = 'Apache-2.0'
            (staged / 'upgrade-bundle.json').write_text(json.dumps(manifest))
            allowed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            evidence = json.loads((promoted / 'upgrade-bundle.json').read_text())
            self.assertEqual(evidence['promotion_stage'], 'production')
            self.assertEqual((root / 'promoted-previous/existing-evidence.txt').read_text(),
                             'existing approved bundle')
            log = json.loads((root / 'promotion-log.jsonl').read_text())
            self.assertEqual(log['action'], 'promote')
            self.assertEqual(log['approved_by'], 'local-regression-fixture')


if __name__ == '__main__':
    unittest.main()
