import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

LIB = pathlib.Path(__file__).resolve().parents[1] / 'lib'
NOW = '2026-10-15T00:00:00Z'  # fixed clock: replay must not depend on today's date


class UpgradeFreshnessTests(unittest.TestCase):
    def run_verifier(self, mutate=None, extra=(), without_site=True):
        manifest = json.loads((LIB / 'upgrade-bundle-v1.1.0.json').read_text())
        if mutate:
            mutate(manifest)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'manifest.json'
            path.write_text(json.dumps(manifest))
            command = [sys.executable] + (['-S'] if without_site else [])
            command += [str(LIB / 'verify-upgrade-bundle.py'), '--manifest', str(path), '--now', NOW, *extra]
            return subprocess.run(command, capture_output=True, text=True)

    def assert_rejected(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_real_manifests_pass_and_replay_identically(self):
        first = self.run_verifier(extra=['--max-age-days', '90'])
        second = self.run_verifier(extra=['--max-age-days', '90'])
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual((first.stdout, first.stderr), (second.stdout, second.stderr))

    def test_stale_bundle_rejected_only_when_ceiling_set(self):
        self.assert_rejected(self.run_verifier(extra=['--max-age-days', '30']))
        self.assertEqual(self.run_verifier().returncode, 0)

    def test_future_created_at_rejected(self):
        self.assert_rejected(self.run_verifier(lambda m: m.update(created_at='2026-10-16T00:00:00Z')))

    def test_malformed_created_at_rejected(self):
        for value in ['yesterday', '2026-08-25', '2026-08-25T00:00:00', '2026-13-45T00:00:00Z', 7, None, '']:
            with self.subTest(value=value):
                self.assert_rejected(self.run_verifier(lambda m, v=value: m.update(created_at=v)))

    def test_absent_created_at_rejected(self):
        self.assert_rejected(self.run_verifier(lambda m: m.pop('created_at')))

    def test_missing_or_blank_producer_rejected(self):
        self.assert_rejected(self.run_verifier(lambda m: m.pop('provenance')))
        self.assert_rejected(self.run_verifier(lambda m: m['provenance'].update(source_repo='  ')))
        self.assert_rejected(self.run_verifier(lambda m: m['provenance'].pop('author')))

    def test_bad_cli_clock_is_an_error(self):
        result = self.run_verifier(extra=['--now', 'soon'])
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
