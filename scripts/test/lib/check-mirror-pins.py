#!/usr/bin/env python3
"""R295: scripts/test/check-mirror-pins.py (narwhal#468) against a local stub registry.

Proves PRESENT / MISSING / DIGEST-MISMATCH / optional WARN, the HEAD-only read-only
contract, the Accept headers, and the image-ref -> /v2/<repo>/manifests/<tag> mapping
for docker.io library and org images, ghcr.io, gcr.io and registry.k8s.io. The mapping
is also compared with the case statement of 05-load-images.sh for every images.txt
entry, and four broken mappings must fail this harness (--mutation-verify).
"""
import http.server
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/test/check-mirror-pins.py'
LOAD = (ROOT / 'scripts/airgap/05-load-images.sh').read_text()
D = {n: 'sha256:' + n * 64 for n in 'abcdef'}
count = 0


def check(condition, name):
  global count
  assert condition, name
  count += 1
  print(f'PASS {name}')


# ref -> (repo path the registry must serve, tag, digest the stub answers with or None=404)
CASES = {
  'busybox:1.28': ('docker.io/library/busybox', '1.28', D['a']),
  'apache/apisix:3.15.0-ubuntu': ('docker.io/apache/apisix', '3.15.0-ubuntu', D['b']),
  'docker.io/grafana/loki:3.7.3': ('docker.io/grafana/loki', '3.7.3', D['c']),
  'ghcr.io/dasomel/goharbor/nginx-photon:v2.15.1': ('ghcr.io/dasomel/goharbor/nginx-photon', 'v2.15.1', D['d']),
  'registry.k8s.io/sig-storage/csi-resizer:v2.0.0': ('registry.k8s.io/sig-storage/csi-resizer', 'v2.0.0', D['e']),
  'gcr.io/kaniko-project/executor:v1.24.0@sha256:' + 'f' * 64: ('gcr.io/kaniko-project/executor', 'v1.24.0', None),
  'quay.io/metallb/controller:v0.16.1': ('quay.io/metallb/controller', 'v0.16.1', None),
  'docker.io/valkey/valkey:8-alpine': ('docker.io/valkey/valkey', '8-alpine', D['a']),  # stub serves a, tsv pins b
}
TSV = {'busybox:1.28': D['a'], 'docker.io/valkey/valkey:8-alpine': D['b']}


class Registry(http.server.BaseHTTPRequestHandler):
  seen = []

  def log_message(self, *a):
    pass

  def _serve(self, body):
    Registry.seen.append((self.command, self.path, self.headers.get('Accept', ''), self.headers.get('Authorization')))
    m = re.fullmatch(r'/v2/(.+)/manifests/(.+)', self.path)
    digest = None
    if m:
      for repo, tag, dg in CASES.values():
        if (repo, tag) == (m[1], m[2]):
          digest = dg
    if not digest:
      self.send_response(404)
      self.end_headers()
      return
    self.send_response(200)
    self.send_header('Docker-Content-Digest', digest)
    self.end_headers()

  def do_HEAD(self):
    self._serve(False)

  def do_GET(self):
    self._serve(True)


def run(script, *extra, images=None, mirror=None):
  with tempfile.TemporaryDirectory() as tmp:
    imgs = Path(tmp, 'images.txt')
    imgs.write_text('# comment\n\n' + '\n'.join(images or CASES) + '\n')
    tsv = Path(tmp, 'digests.tsv')
    tsv.write_text('# header\n' + ''.join(f'{k}\t{v}\t2026-10-31T00:00:00Z\tcrane digest\n' for k, v in TSV.items()))
    return subprocess.run([sys.executable, str(script), '--images', str(imgs), '--digests', str(tsv),
                           '--mirror', mirror, '--timeout', '5', *extra], text=True, capture_output=True)


def harness(script, parity=True):
  srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Registry)
  threading.Thread(target=srv.serve_forever, daemon=True).start()
  mirror = f'http://127.0.0.1:{srv.server_address[1]}'
  Registry.seen.clear()
  try:
    r = run(script, mirror=mirror)
    out = r.stdout
    check(r.returncode == 1, 'exit 1 while images are missing or mismatched')
    check(re.search(r'^PRESENT busybox:1\.28 ', out, re.M), 'docker.io library image PRESENT')
    check(re.search(r'^PRESENT apache/apisix:3\.15\.0-ubuntu ', out, re.M), 'docker.io org image PRESENT')
    check(re.search(r'^PRESENT docker\.io/grafana/loki:3\.7\.3 ', out, re.M), 'qualified docker.io image PRESENT')
    check(re.search(r'^PRESENT ghcr\.io/dasomel/goharbor/nginx-photon', out, re.M), 'ghcr.io nested path PRESENT')
    check(re.search(r'^PRESENT registry\.k8s\.io/sig-storage/csi-resizer', out, re.M), 'registry.k8s.io PRESENT')
    check(re.search(r'^MISSING quay\.io/metallb/controller:v0\.16\.1 ', out, re.M), 'absent image MISSING')
    check(re.search(r'^DIGEST-MISMATCH docker\.io/valkey/valkey:8-alpine ', out, re.M), 'tag at another digest than the tsv pin is DIGEST-MISMATCH')
    check(re.search(r'^WARN gcr\.io/kaniko-project/executor:v1\.24\.0@\S+ \(optional\) MISSING', out, re.M), 'optional kaniko absence is WARN')
    check('1 missing, 1 digest-mismatch' in out and '1 warn' in out, 'summary counts')
    paths = {p for _, p, _, _ in Registry.seen}
    for repo, tag, _ in CASES.values():
      check(f'/v2/{repo}/manifests/{tag}' in paths, f'requested /v2/{repo}/manifests/{tag}')
    check({m for m, _, _, _ in Registry.seen} <= {'HEAD'}, 'only HEAD requests (registry has no write path)')
    check(all(a is None for *_, a in Registry.seen), 'no Authorization header sent')
    check(all(all(t in acc for t in ('image.index.v1', 'image.manifest.v1', 'manifest.list.v2', 'manifest.v2'))
              for _, _, acc, _ in Registry.seen), 'Accept covers OCI index/manifest and docker list/v2')
    # all present + optional missing + tsv in agreement -> exit 0; pinned scope ignores the tag-only tsv pin
    good = [c for c in CASES if 'quay.io' not in c and 'valkey' not in c]
    check(run(script, images=good, mirror=mirror).returncode == 0, 'exit 0 when only an optional image is missing')
    r = run(script, '--digest-scope', 'pinned', images=[c for c in CASES if 'quay.io' not in c], mirror=mirror)
    check(r.returncode == 0 and 'DIGEST-MISMATCH' not in r.stdout, '--digest-scope pinned compares only @sha256 refs')
    dead = run(script, images=['busybox:1.28'], mirror='http://127.0.0.1:1')
    check(dead.returncode == 1 and 'ERROR busybox:1.28' in dead.stdout, 'unreachable mirror is ERROR with non-zero exit')
    cred = run(script, images=['busybox:1.28'], mirror='http://user:pw@127.0.0.1:1')
    check(cred.returncode == 2 and 'pw' not in cred.stdout + cred.stderr, 'credentials in the mirror URL are refused, not echoed')
    if parity:
      listing = subprocess.run([sys.executable, str(script), '--list-paths', '--images', str(ROOT / 'scripts/airgap/images.txt')], text=True, capture_output=True, check=True).stdout
      case = re.search(r'(case "\$\{img\}" in.*?esac)', LOAD, re.S)[1]
      rows = [l.split('\t') for l in listing.splitlines()]
      refs = '\n'.join(r[0] for r in rows) + '\n'
      sh = subprocess.run(['bash', '-c', 'while read -r img; do ' + case + '\n push_ref="${qualified%@sha256:*}"; echo "${push_ref}"; done'],
                          input=refs, text=True, capture_output=True, check=True).stdout.splitlines()
      mine = [f'{r[1]}:{r[2]}' for r in rows]  # every images.txt entry carries a tag
      check(len(rows) > 80 and sh == mine, f'mapping equals 05-load-images.sh push path for all {len(rows)} images.txt entries')
  finally:
    srv.shutdown()


def mutations():
  src = SCRIPT.read_text()
  muts = {
    'bare images lose docker.io/library': ("return 'docker.io/library/' + img", "return img"),
    'org images lose docker.io': ("return 'docker.io/' + img", "return img"),
    'digest pin kept in tag': ("q, digest = q.split('@', 1)", "digest = None"),
    'wrong /v2 root': ("f'{base}/v2/{repo}/manifests/{tag}'", "f'{base}/{repo}/v2/manifests/{tag}'"),
  }
  for name, (old, new) in muts.items():
    assert old in src, f'mutation anchor gone: {name}'
    with tempfile.TemporaryDirectory() as tmp:
      bad = Path(tmp, 'check-mirror-pins.py')
      bad.write_text(src.replace(old, new))
      try:
        harness(bad)
      except AssertionError:
        check(True, f'broken mapping caught: {name}')
        continue
    raise SystemExit(f'FAIL mutation survived: {name}')


if __name__ == '__main__':
  harness(SCRIPT)
  mutations()
  print(f'{count} checks passed')
