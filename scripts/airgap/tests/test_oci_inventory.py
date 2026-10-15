import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_oci_layout import GATE, IMG, OCI_INDEX, VERIFIER, Layout

OTHER = 'docker.io/x/z:2'


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.b = pathlib.Path(self._tmp.name)
        self.inv = self.b / 'images.digests.json'

    def saved(self, images=(IMG,)):
        """Lay out + save-time inventory, the way 02 does it."""
        shutil.rmtree(self.b / 'oci', ignore_errors=True)
        for img in images:
            lay = Layout(self.b, img)
            lay.index(lay.image()[0])
        (self.b / 'manifest.txt').write_text('\n'.join(images) + '\n')
        r = self.run_py('--images', str(self.b / 'manifest.txt'), '--inventory-out', str(self.inv),
                        '--platform', 'linux/arm64', '--tool', 'skopeo version 9.9')
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_py(self, *extra):
        return subprocess.run([sys.executable, '-I', str(VERIFIER), '--bundle', str(self.b), *extra],
                              capture_output=True, text=True)

    def verify(self, images=(IMG,)):
        (self.b / 'list.txt').write_text('\n'.join(images) + '\n')
        return self.run_py('--images', str(self.b / 'list.txt'), '--manifest', str(self.b / 'manifest.txt'),
                           '--inventory', str(self.inv))

    def rewrite(self, fn):
        doc = json.loads(self.inv.read_text())
        fn(doc)
        self.inv.write_text(json.dumps(doc))

    def assertRejected(self, needle, images=(IMG,)):
        r = self.verify(images)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(needle, r.stderr)
        self.assertNotIn('Traceback', r.stderr)

    def test_writer_is_deterministic_sorted_and_verifies(self):
        self.saved((OTHER, IMG))
        first = self.inv.read_bytes()
        doc = json.loads(first)
        self.assertEqual([e['image'] for e in doc['images']], [IMG, OTHER])
        e = doc['images'][0]
        self.assertEqual((e['layout'], e['platforms'], e['collapsed_to'], doc['tool']),
                         ('oci/docker.io_x_y_1', 'single-platform', 'linux/arm64', 'skopeo version 9.9'))
        self.saved((IMG, OTHER))
        self.assertEqual(self.inv.read_bytes(), first)
        r = self.verify((IMG, OTHER))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('saved-digest inventory', r.stderr)

    def test_absent_inventory_fails_closed_with_regeneration_hint(self):
        self.saved()
        self.inv.unlink()
        self.assertRejected('re-saved with 02-save-images.sh')

    def test_symlinked_inventory_is_rejected(self):
        self.saved()
        real = self.b / 'real.json'
        real.write_bytes(self.inv.read_bytes())
        self.inv.unlink()
        self.inv.symlink_to(real)
        self.assertRejected('inventory missing or not a regular file')

    def test_malformed_inventories_are_rejected(self):
        self.saved((IMG, OTHER))
        good = self.inv.read_text()

        def entry(**kw):
            return lambda d: d['images'][0].update(kw)
        cases = [
            ('{not json', 'not valid JSON'),
            ('[]', 'exactly schema, tool, images'),
            (None, 'unknown-field', lambda d: d.update(extra=1), 'exactly schema, tool, images'),
            (None, 'schema', lambda d: d.update(schema='v0'), 'is not narwhal-airgap-image-digests/v1'),
            (None, 'tool', lambda d: d.update(tool=' '), 'tool must be'),
            (None, 'images', lambda d: d.update(images={}), 'not a list'),
            (None, 'entry-field', entry(extra=1), 'exactly'),
            (None, 'digest', entry(digest='sha256:' + 'A' * 64), 'malformed digest'),
            (None, 'layout', entry(layout='oci/elsewhere'), 'is not oci/docker.io_x_y_1'),
            (None, 'platforms', entry(platforms='all'), 'unknown platforms'),
            (None, 'collapsed', entry(collapsed_to=None), 'is not <os>/<arch>'),
            (None, 'collapsed-bad', entry(collapsed_to='x'), 'is not <os>/<arch>'),
            (None, 'unsorted', lambda d: d['images'].reverse(), 'out of sorted order'),
            (None, 'duplicate', lambda d: d['images'].append(dict(d['images'][-1])), 'out of sorted order'),
        ]
        for case in cases:
            with self.subTest(case=case[1] if case[0] is None else case[0][:12]):
                if case[0] is None:
                    self.inv.write_text(good)
                    self.rewrite(case[2])
                    needle = case[3]
                else:
                    self.inv.write_text(case[0])
                    needle = case[1]
                self.assertRejected(needle, (IMG, OTHER))
        self.inv.write_text(good.replace('"digest"', '"digest": "x", "digest"', 1))
        self.assertRejected('not valid JSON', (IMG, OTHER))

    def test_collapsed_to_must_be_null_for_preserved_index(self):
        self.saved()
        self.rewrite(lambda d: d['images'][0].update(platforms='full-index-preserved'))
        self.assertRejected('collapsed_to must be null')

    def test_image_without_inventory_entry_is_rejected(self):
        self.saved()
        lay = Layout(self.b, OTHER)
        lay.index(lay.image()[0])
        (self.b / 'manifest.txt').write_text(IMG + '\n' + OTHER + '\n')
        self.assertRejected(f'{OTHER}: no entry in the digest inventory')
        # images.txt-only ref (not in manifest.txt) is rejected the same way
        (self.b / 'manifest.txt').write_text(IMG + '\n')
        self.assertRejected(f'{OTHER}: no entry in the digest inventory', (IMG, OTHER))

    def test_inventoried_image_outside_the_lists_is_still_verified(self):
        self.saved((IMG, OTHER))
        (self.b / 'manifest.txt').write_text(IMG + '\n')
        self.assertEqual(self.verify().returncode, 0)
        lay = Layout.__new__(Layout)
        lay.dir = self.b / 'oci' / 'docker.io_x_z_2'
        blob = next((lay.dir / 'blobs' / 'sha256').iterdir())
        blob.write_bytes(b'x' * blob.stat().st_size)
        self.assertRejected('hashes to sha256')

    def test_two_images_sharing_one_layout_directory_are_rejected(self):
        self.saved()
        twin = 'docker.io_x/y:1'  # same oci/ directory name as IMG after normalization
        self.rewrite(lambda d: d['images'].append(dict(d['images'][0], image=twin)))
        self.assertRejected('same layout directory')

    def test_unlisted_layout_and_stray_entries_are_rejected(self):
        self.saved()
        Layout(self.b, OTHER)
        self.assertRejected('oci/docker.io_x_z_2: layout not recorded')
        (self.b / 'oci' / 'docker.io_x_z_2').rename(self.b / 'elsewhere')
        (self.b / 'oci' / 'docker.io_x_z_2').symlink_to(self.b / 'elsewhere')
        self.assertRejected('not a real directory')
        (self.b / 'oci' / 'docker.io_x_z_2').unlink()
        (self.b / 'oci' / 'README').write_text('x')
        self.assertRejected('oci/README: not a real directory')

    def swap_in(self, build):
        """Replace the saved layout with another, internally consistent one."""
        with tempfile.TemporaryDirectory() as scratch:
            lay = Layout(pathlib.Path(scratch), IMG)
            top = build(lay)
            lay.index(top)
            shutil.rmtree(self.b / 'oci' / 'docker.io_x_y_1')
            shutil.copytree(lay.dir, self.b / 'oci' / 'docker.io_x_y_1')
        return top

    def test_digest_mismatch_with_self_consistent_swapped_layout_is_rejected(self):
        self.saved()

        def build(lay):
            config = lay.put(b'{"os":"linux","other":1}', 'application/vnd.oci.image.config.v1+json')
            layer = lay.put(b'other-layer', 'application/vnd.oci.image.layer.v1.tar')
            doc = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
                   'config': config, 'layers': [layer]}
            return lay.put(json.dumps(doc).encode(), 'application/vnd.oci.image.manifest.v1+json')
        self.swap_in(build)
        self.assertEqual(self.run_py('--images', str(self.b / 'manifest.txt')).returncode, 0)  # self-consistent
        self.assertRejected('!= saved sha256:')

    def test_platform_mode_change_is_rejected(self):
        self.saved()

        def build(lay):
            child = lay.image()[0]
            doc = {'schemaVersion': 2, 'mediaType': OCI_INDEX, 'manifests': [child]}
            return lay.put(json.dumps(doc).encode(), OCI_INDEX)
        top = self.swap_in(build)
        self.rewrite(lambda d: d['images'][0].update(digest=top['digest']))  # digest matches; mode does not
        self.assertRejected('layout is full-index-preserved but saved as single-platform')

    def test_writer_refuses_bad_args_and_broken_layout(self):
        self.saved()
        self.assertEqual(self.run_py('--images', str(self.b / 'manifest.txt'),
                                     '--inventory-out', str(self.inv)).returncode, 2)
        self.assertEqual(self.run_py('--images', str(self.b / 'manifest.txt'), '--inventory', str(self.inv),
                                     '--inventory-out', str(self.inv), '--platform', 'linux/arm64').returncode, 2)
        self.inv.unlink()
        (self.b / 'oci' / 'docker.io_x_y_1' / 'index.json').write_text('{}')
        r = self.run_py('--images', str(self.b / 'manifest.txt'), '--inventory-out', str(self.inv),
                        '--platform', 'linux/arm64')
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.inv.exists())

    def test_gate_rejects_missing_inventory_and_accepts_saved_bundle(self):
        self.saved()
        (self.b / 'list.txt').write_text(IMG + '\n')
        (self.b / 'digests.tsv').write_text(f'{IMG}\tsha256:{"b" * 64}\t2026-10-15T00:00:00Z\tcrane\n')
        cmd = ['bash', str(GATE), '--list', str(self.b / 'list.txt'), '--bundle', str(self.b),
               '--digests', str(self.b / 'digests.tsv')]
        ok = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.inv.unlink()
        bad = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertIn('INCOMPLETE', bad.stderr)
        self.assertIn('re-saved with 02-save-images.sh', bad.stderr)


if __name__ == '__main__':
    unittest.main()
