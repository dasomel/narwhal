import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'lib/diff-upgrade-bundle.py'
SPEC = importlib.util.spec_from_file_location('upgrade_diff', MODULE_PATH)
upgrade_diff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade_diff)


def artifact(name, dependencies=None):
    return {'name': name, 'version': '1.0', 'digest': 'sha256:' + 'a' * 64,
            'artifact_type': 'image', 'license': 'Apache-2.0', 'source_ref': name,
            'dependencies': dependencies or []}


class CandidateDependencyTests(unittest.TestCase):
    def test_removed_dependency_is_missing_in_candidate_snapshot(self):
        current = {'artifacts': [artifact('app', ['database']), artifact('database')]}
        candidate = {'artifacts': [artifact('app', ['database'])]}
        diff = upgrade_diff.generate_diff(current, candidate)
        self.assertEqual(diff['missing_dependencies'], [{'artifact': 'app', 'missing_dependency': 'database'}])
        self.assertEqual(diff['counts']['removed'], 1)

    def test_candidate_dependency_is_available_without_current_artifact(self):
        diff = upgrade_diff.generate_diff({'artifacts': []},
            {'artifacts': [artifact('app', ['database']), artifact('database')]})
        self.assertEqual(diff['missing_dependencies'], [])

    def test_cli_json_and_text_report_removed_dependency(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = pathlib.Path(tmp)
            current = directory / 'current.json'
            candidate = directory / 'candidate.json'
            current.write_text(json.dumps({'artifacts': [artifact('app', ['database']), artifact('database')]}))
            candidate.write_text(json.dumps({'artifacts': [artifact('app', ['database'])]}))
            command = [sys.executable, str(MODULE_PATH), '--current', str(current), '--new', str(candidate)]
            result = subprocess.run(command + ['--json'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['counts']['missing_dependencies'], 1)
            text = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("app requires missing artifact: 'database'", text.stdout)


if __name__ == '__main__':
    unittest.main()
