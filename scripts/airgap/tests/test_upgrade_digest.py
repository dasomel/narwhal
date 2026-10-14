import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

LIB = pathlib.Path(__file__).resolve().parents[1] / 'lib'


class UpgradeDigestCliTests(unittest.TestCase):
    def run_verifier(self, digest, without_site=False):
        manifest = json.loads((LIB / 'upgrade-bundle-v1.1.0.json').read_text())
        manifest['artifacts'][0]['digest'] = digest
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'manifest.json'
            path.write_text(json.dumps(manifest))
            command = [sys.executable]
            if without_site:
                command.append('-S')
            command += [str(LIB / 'verify-upgrade-bundle.py'), '--manifest', str(path)]
            return subprocess.run(command, capture_output=True, text=True)

    def test_correct_algorithm_widths_pass(self):
        for prefix, width in [('sha256:', 64), ('sha512:', 128)]:
            for without_site in [False, True]:
                with self.subTest(prefix=prefix, without_site=without_site):
                    result = self.run_verifier(prefix + 'a' * width, without_site)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_algorithm_widths_fail_with_or_without_jsonschema(self):
        for prefix, width in [('sha256:', 65), ('sha256:', 128), ('sha512:', 64), ('sha512:', 127)]:
            for without_site in [False, True]:
                with self.subTest(prefix=prefix, width=width, without_site=without_site):
                    result = self.run_verifier(prefix + 'a' * width, without_site)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertNotIn('Traceback', result.stderr)

    def test_malformed_digests_fail_cleanly(self):
        for value in [None, [], 'md5:' + 'a' * 32, 'sha256:' + 'g' * 64, 'sha256:' + 'a' * 64 + '\n']:
            with self.subTest(value=value):
                result = self.run_verifier(value, True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
