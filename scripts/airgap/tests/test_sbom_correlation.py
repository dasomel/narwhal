import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / 'lib/correlate-sbom-vulnerabilities.py'
SPEC = importlib.util.spec_from_file_location('correlation', MODULE_PATH)
correlation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(correlation)


class CorrelationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.component = {
            'type': 'container', 'name': 'registry.test/team/app', 'version': '1.2',
            'hashes': [{'alg': 'SHA-256', 'content': 'a' * 64}],
        }
        self.finding = {
            'artifact_name': 'registry.test/team/app:1.2',
            'target': 'registry.test/team/app:1.2 (debian 12)',
            'pkg_name': 'curl', 'installed_version': '8.1',
        }

    def test_exact_image_identity(self):
        self.assertTrue(correlation.match_vuln_to_component(self.finding, self.component))

    def test_missing_artifact_name_does_not_match_every_target(self):
        self.finding.update(artifact_name='', target='registry.test/team/other:1.2 (debian 12)')
        self.assertFalse(correlation.match_vuln_to_component(self.finding, self.component))

    def test_image_prefix_collision(self):
        self.finding.update(artifact_name='registry.test/team/app-extra:1.2', target='app-extra')
        self.assertFalse(correlation.match_vuln_to_component(self.finding, self.component))

    def test_other_image_version(self):
        self.finding.update(artifact_name='registry.test/team/app:1.3', target='registry.test/team/app:1.3 (debian 12)')
        self.assertFalse(correlation.match_vuln_to_component(self.finding, self.component))

    def test_conflicting_digest_overrides_matching_name(self):
        self.finding['repo_digests'] = ['registry.test/team/app@sha256:' + 'b' * 64]
        self.assertFalse(correlation.match_vuln_to_component(self.finding, self.component))

    def test_exact_digest_supports_mirrored_image(self):
        self.finding.update(artifact_name='mirror.test/app:1.2', target='mirror.test/app:1.2')
        self.finding['repo_digests'] = ['mirror.test/app@sha256:' + 'a' * 64]
        self.assertTrue(correlation.match_vuln_to_component(self.finding, self.component))

    def test_digest_prefix_does_not_match(self):
        self.finding.update(artifact_name='other', target='other', image_id='sha256:' + 'a' * 65)
        self.assertFalse(correlation.match_vuln_to_component(self.finding, self.component))

    def test_image_configuration_id_is_not_manifest_digest(self):
        self.finding['image_id'] = 'sha256:' + 'b' * 64
        self.assertTrue(correlation.match_vuln_to_component(self.finding, self.component))

    def test_package_version_is_required(self):
        self.component = {'type': 'library', 'name': 'curl', 'version': '8.2'}
        self.assertFalse(correlation.match_vuln_to_component(self.finding, self.component))
        self.component['version'] = '8.1'
        self.assertTrue(correlation.match_vuln_to_component(self.finding, self.component))

    def test_empty_component_identity_never_matches(self):
        self.assertFalse(correlation.match_vuln_to_component(self.finding, {}))


if __name__ == '__main__':
    unittest.main()
