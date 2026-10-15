import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

AIRGAP = pathlib.Path(__file__).resolve().parents[1]
VERIFIER = AIRGAP / 'lib' / 'verify-oci-layout.py'
GATE = AIRGAP / '09-verify-bundle-completeness.sh'
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
OCI_INDEX = 'application/vnd.oci.image.index.v1+json'
IMG = 'docker.io/x/y:1'


class Layout:
    """Builds a minimal OCI layout like `skopeo copy ... oci:` writes (verified offline)."""

    def __init__(self, bundle, img=IMG):
        self.dir = bundle / 'oci' / img.replace('/', '_').replace(':', '_')
        (self.dir / 'blobs' / 'sha256').mkdir(parents=True)
        (self.dir / 'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')

    def put(self, data, media):
        hexd = hashlib.sha256(data).hexdigest()
        (self.dir / 'blobs' / 'sha256' / hexd).write_bytes(data)
        return {'mediaType': media, 'digest': 'sha256:' + hexd, 'size': len(data)}

    def image(self, subject=False):
        config = self.put(b'{"os":"linux"}', 'application/vnd.oci.image.config.v1+json')
        layer = self.put(b'layer-bytes', 'application/vnd.oci.image.layer.v1.tar')
        doc = {'schemaVersion': 2, 'mediaType': OCI_MANIFEST, 'config': config, 'layers': [layer]}
        if subject:
            doc['subject'] = config
        return self.put(json.dumps(doc).encode(), OCI_MANIFEST), config, layer

    def index(self, top, name='latest'):
        top = dict(top, annotations={'org.opencontainers.image.ref.name': name})
        (self.dir / 'index.json').write_text(json.dumps({'schemaVersion': 2, 'manifests': [top]}))

    def blob(self, desc):
        return self.dir / 'blobs' / 'sha256' / desc['digest'].split(':')[1]


class OciLayoutTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bundle = pathlib.Path(self._tmp.name)

    def run_verifier(self, images=(IMG,), report=False):
        lst = self.bundle / 'list.txt'
        lst.write_text('# c\n' + '\n'.join(images) + '\n')
        cmd = [sys.executable, '-I', str(VERIFIER), '--bundle', str(self.bundle), '--images', str(lst)]
        if report:
            cmd += ['--report-out', str(self.bundle / 'report.json')]
        return subprocess.run(cmd, capture_output=True, text=True)

    def good(self, **kw):
        lay = Layout(self.bundle)
        manifest, config, layer = lay.image(**kw)
        lay.index(manifest)
        return lay, manifest, config, layer

    def assertRejected(self, needle):
        r = self.run_verifier()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(needle, r.stderr)
        self.assertNotIn('Traceback', r.stderr)

    def test_normal_layout_passes_and_report_states_referrer_loss(self):
        self.good()
        r = self.run_verifier(report=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('referrers (SBOM/signature/attestation) preserved for 0 of 1', r.stderr)
        row = json.loads((self.bundle / 'report.json').read_text())['images'][0]
        self.assertEqual(row['platforms'], 'single-platform (--override-arch)')
        self.assertIs(row['referrers_preserved'], False)

    def test_pinned_digest_must_match_layout_digest(self):
        lay, manifest, _, _ = self.good()
        pinned = IMG + '@' + manifest['digest']
        lay.dir.rename(lay.dir.parent / pinned.replace('/', '_').replace(':', '_'))
        self.assertEqual(self.run_verifier((pinned,)).returncode, 0)
        wrong = IMG + '@sha256:' + 'a' * 64
        (lay.dir.parent / pinned.replace('/', '_').replace(':', '_')).rename(
            lay.dir.parent / wrong.replace('/', '_').replace(':', '_'))
        r = self.run_verifier((wrong,))
        self.assertEqual(r.returncode, 1)
        self.assertIn('layout top-level digest is', r.stderr)

    def test_index_children_walked_and_referrers_counted(self):
        lay = Layout(self.bundle)
        child, _, _ = lay.image(subject=True)
        top = lay.put(json.dumps({'schemaVersion': 2, 'mediaType': OCI_INDEX, 'manifests': [child]}).encode(), OCI_INDEX)
        lay.index(top)
        self.assertEqual(self.run_verifier(report=True).returncode, 0)
        row = json.loads((self.bundle / 'report.json').read_text())['images'][0]
        self.assertEqual(row['platforms'], 'full-index-preserved')
        self.assertEqual(row['referrers_in_layout'], 1)

    def test_corrupted_blob_same_size_is_rejected(self):
        lay, manifest, config, _ = self.good()
        lay.blob(config).write_bytes(b'{"os":"darwi"}')
        self.assertRejected('hashes to sha256')

    def test_missing_and_truncated_blobs_are_rejected(self):
        lay, manifest, config, layer = self.good()
        lay.blob(layer).write_bytes(b'short')
        self.assertRejected('size 5 != descriptor 11')
        lay.blob(layer).unlink()
        self.assertRejected('missing')
        lay.blob(manifest).unlink()
        self.assertRejected('missing')

    def test_symlinked_blob_is_rejected(self):
        lay, _, config, _ = self.good()
        blob = lay.blob(config)
        target = self.bundle / 'elsewhere'
        target.write_bytes(blob.read_bytes())
        blob.unlink()
        blob.symlink_to(target)
        self.assertRejected('missing')

    def test_absent_layout_pieces_are_rejected(self):
        lay, _, _, _ = self.good()
        (lay.dir / 'oci-layout').unlink()
        self.assertRejected('oci-layout missing')
        r = self.run_verifier(('docker.io/never/saved:1',))
        self.assertEqual(r.returncode, 1)
        self.assertIn('oci-layout missing', r.stderr)

    def test_malformed_index_and_descriptors_are_rejected(self):
        lay, manifest, _, _ = self.good()
        for text, needle in [
            ('{not json', 'not valid JSON'),
            ('[]', 'lists no manifests'),
            ('{"manifests": []}', 'lists no manifests'),
            (json.dumps({'manifests': [dict(manifest, digest='sha256:' + 'A' * 64)]}), 'malformed digest'),
            (json.dumps({'manifests': [dict(manifest, digest=manifest['digest'] + '\n')]}), 'malformed digest'),
            (json.dumps({'manifests': [dict(manifest, size=True)]}), 'malformed size'),
            (json.dumps({'manifests': [dict(manifest, mediaType='text/plain')]}), 'unsupported mediaType'),
            (json.dumps({'manifests': ['x']}), 'unsupported mediaType None'),
            (json.dumps({'manifests': [manifest, manifest]}), 'candidate entries'),
        ]:
            with self.subTest(needle=needle, text=text[:30]):
                (lay.dir / 'index.json').write_text(text)
                self.assertRejected(needle)

    def test_unreadable_image_list_is_usage_error(self):
        r = subprocess.run([sys.executable, '-I', str(VERIFIER), '--bundle', str(self.bundle),
                            '--images', str(self.bundle / 'nope.txt')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class CompletenessGateTests(unittest.TestCase):
    def run_gate(self, corrupt):
        with tempfile.TemporaryDirectory() as tmp:
            b = pathlib.Path(tmp)
            lay = Layout(b)
            manifest, config, _ = lay.image()
            lay.index(manifest)
            (b / 'list.txt').write_text(IMG + '\n')
            (b / 'manifest.txt').write_text(IMG + '\n')
            subprocess.run([sys.executable, '-I', str(VERIFIER), '--bundle', str(b), '--images', str(b / 'list.txt'),
                            '--inventory-out', str(b / 'images.digests.json'), '--platform', 'linux/arm64'],
                           check=True, capture_output=True)
            if corrupt:
                lay.blob(config).write_bytes(b'{"os":"darwi"}')
            (b / 'digests.tsv').write_text(f'{IMG}\tsha256:{"b" * 64}\t2026-10-15T00:00:00Z\tcrane\n')
            return subprocess.run(
                ['bash', str(GATE), '--list', str(b / 'list.txt'), '--bundle', str(b),
                 '--digests', str(b / 'digests.tsv')], capture_output=True, text=True)

    def test_gate_accepts_intact_and_rejects_corrupted_layout(self):
        ok = self.run_gate(corrupt=False)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn('Bundle complete', ok.stderr)
        bad = self.run_gate(corrupt=True)
        self.assertEqual(bad.returncode, 1, bad.stderr)
        self.assertIn('INCOMPLETE', bad.stderr)


if __name__ == '__main__':
    unittest.main()
