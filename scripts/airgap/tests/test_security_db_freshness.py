import datetime
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

CHECKER = pathlib.Path(__file__).resolve().parents[1] / 'lib/check-security-db-freshness.py'


class FreshnessCliTests(unittest.TestCase):
    def manifest(self, days=0):
        timestamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        return {'artifacts': [{'name': 'trivy-db', 'fetched_at': timestamp.strftime('%Y-%m-%dT%H:%M:%SZ'), 'digest': 'sha256:' + 'a' * 64}]}

    def run_checker(self, doc, *flags):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = pathlib.Path(tmp) / 'manifest.json'
            manifest.write_text(json.dumps(doc))
            return subprocess.run([sys.executable, str(CHECKER), str(manifest), *flags], capture_output=True, text=True)

    def assert_fails_cleanly(self, result):
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_fresh_manifest_passes(self):
        result = self.run_checker(self.manifest())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_future_timestamp_cannot_extend_freshness(self):
        self.assert_fails_cleanly(self.run_checker(self.manifest(-30)))

    def test_nan_infinite_negative_and_zero_slo_fail_cleanly(self):
        for value in ['nan', 'inf', '-1', '0', '1e300']:
            with self.subTest(value=value):
                self.assert_fails_cleanly(self.run_checker(self.manifest(), '--slo-days', value))

    def test_malformed_shapes_fail_cleanly(self):
        for doc in [[], None, {'artifacts': [None]}, {'artifacts': [{'fetched_at': 123}]}]:
            with self.subTest(doc=doc):
                self.assert_fails_cleanly(self.run_checker(doc))

    def test_stale_and_missing_digest_fail(self):
        self.assert_fails_cleanly(self.run_checker(self.manifest(30)))
        doc = self.manifest()
        del doc['artifacts'][0]['digest']
        self.assert_fails_cleanly(self.run_checker(doc))


if __name__ == '__main__':
    unittest.main()
