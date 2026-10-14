import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'lib/diff-upgrade-bundle.py'
SPEC = importlib.util.spec_from_file_location('upgrade_license_diff', MODULE_PATH)
upgrade_diff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade_diff)


def artifact(license_id):
    return {'name': 'app', 'version': '1.0', 'digest': 'sha256:' + 'a' * 64,
            'artifact_type': 'image', 'license': license_id, 'source_ref': 'app'}


class UpgradeLicenseCliTests(unittest.TestCase):
    def test_license_only_change_is_updated(self):
        diff = upgrade_diff.generate_diff({'artifacts': [artifact('Apache-2.0')]},
            {'artifacts': [artifact('SSPLv1')]})
        self.assertEqual(diff['counts']['updated'], 1)
        self.assertEqual(diff['counts']['unchanged'], 0)
        self.assertEqual(diff['updated'][0]['old_license'], 'Apache-2.0')
        self.assertEqual(diff['updated'][0]['new_license'], 'SSPLv1')
        self.assertEqual(diff['updated'][0]['license'], 'SSPLv1')

    def test_same_license_keeps_unchanged_semantics(self):
        diff = upgrade_diff.generate_diff({'artifacts': [artifact('Apache-2.0')]},
            {'artifacts': [artifact('Apache-2.0')]})
        self.assertEqual(diff['counts']['updated'], 0)
        self.assertEqual(diff['counts']['unchanged'], 1)

    def test_cli_json_and_text_export_license_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            current = directory / 'current.json'
            candidate = directory / 'candidate.json'
            current.write_text(json.dumps({'artifacts': [artifact('Apache-2.0')]}))
            candidate.write_text(json.dumps({'artifacts': [artifact('SSPLv1')]}))
            command = [sys.executable, str(MODULE_PATH), '--current', str(current), '--new', str(candidate)]
            result = subprocess.run(command + ['--json'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['updated'][0]['new_license'], 'SSPLv1')
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('license Apache-2.0 -> SSPLv1', result.stdout)


if __name__ == '__main__':
    unittest.main()
