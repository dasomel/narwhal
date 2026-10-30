#!/usr/bin/env python3
"""Read-only check that the airgap mirror holds every pinned image (narwhal#468).

A pin bump in images.txt / image-digests.tsv does not reach the bastion registry by
itself; nodes have no upstream egress, so a reschedule of a pod whose new pin is
missing from the mirror ends in ImagePullBackOff. Run this against the bastion
registry BEFORE the pin goes live and again after the copy:

  MIRROR=http://172.17.0.191:5000 python3 scripts/test/check-mirror-pins.py

Per image: PRESENT, MISSING (404), DIGEST-MISMATCH (tag resolves to another digest
than the pin) or ERROR (timeout/unexpected status/no digest header). WARN replaces
MISSING/MISMATCH/ERROR for optional images. Exit 1 on any non-optional failure.

Path mapping (D-MIRROR-PATH) mirrors scripts/airgap/05-load-images.sh: the ref is
fully qualified (bare `busybox` -> docker.io/library/busybox, `org/name` ->
docker.io/org/name), the @sha256 pin is dropped, and the rest is the repository path
under the registry root, served at /v2/<qualified-repo>/manifests/<tag>. This is also
the path 06-configure-mirrors.sh gives containerd (override_path /v2/<upstream>).

Strictly read-only: HEAD only (GET only when the registry sends no digest header),
no credentials sent or accepted in the URL, every request has a timeout.
"""
import argparse
import hashlib
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCEPT = ', '.join([
  'application/vnd.oci.image.index.v1+json',
  'application/vnd.oci.image.manifest.v1+json',
  'application/vnd.docker.distribution.manifest.list.v2+json',
  'application/vnd.docker.distribution.manifest.v2+json',
])
# D-MIRROR-OPTIONAL: images.txt has no optional marker; kaniko is an optional dev tool
# (developer-kaniko-builds.md), so its absence is a warning, not a failed gate.
DEFAULT_OPTIONAL = [r'^gcr\.io/kaniko-project/']


def qualify(img):
  """Same case statement as 05-load-images.sh."""
  if re.match(r'^(.*\..*/|.*:.*/|localhost/)', img):
    return img
  if '/' in img:
    return 'docker.io/' + img
  return 'docker.io/library/' + img


def split_ref(img):
  """-> (repo path under the mirror, tag, inline digest or None)."""
  q = qualify(img)
  digest = None
  if '@sha256:' in q:
    q, digest = q.split('@', 1)
  tail = q.rsplit('/', 1)[-1]
  if ':' in tail:
    repo, tag = q.rsplit(':', 1)
  else:
    repo, tag = q, 'latest'
  return repo, tag, digest


def read_images(path):
  return [l.strip() for l in Path(path).read_text().splitlines() if l.strip() and not l.lstrip().startswith('#')]


def read_digests(path):
  table = {}
  for line in Path(path).read_text().splitlines():
    if not line.strip() or line.startswith('#'):
      continue
    cols = line.split('\t')
    if len(cols) >= 2 and re.fullmatch(r'sha256:[0-9a-f]{64}', cols[1]):
      table[cols[0]] = cols[1]
  return table


def probe(base, repo, tag, timeout, ctx):
  """-> (status, digest or None); status is an int or an error string."""
  url = f'{base}/v2/{repo}/manifests/{tag}'
  for method in ('HEAD', 'GET'):
    req = urllib.request.Request(url, method=method, headers={'Accept': ACCEPT})
    try:
      with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        digest = resp.headers.get('Docker-Content-Digest')
        if digest or method == 'GET':
          if not digest and method == 'GET':
            digest = 'sha256:' + hashlib.sha256(resp.read()).hexdigest()
          return resp.status, digest
    except urllib.error.HTTPError as e:
      return e.code, None
    except (urllib.error.URLError, OSError) as e:
      return f'{type(e).__name__}', None
  return 200, None


def main():
  ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
  ap.add_argument('--mirror', default=os.environ.get('MIRROR'), help='registry base URL (env MIRROR), e.g. http://172.17.0.191:5000')
  ap.add_argument('--images', default=os.environ.get('IMAGES_TXT', str(ROOT / 'scripts/airgap/images.txt')))
  ap.add_argument('--digests', default=os.environ.get('IMAGE_DIGESTS_TSV', str(ROOT / 'scripts/airgap/lib/image-digests.tsv')))
  ap.add_argument('--timeout', type=float, default=10, help='seconds per request')
  ap.add_argument('--optional', action='append', default=None, help='regex of optional images (WARN, not FAIL); default kaniko')
  ap.add_argument('--digest-scope', choices=('all', 'pinned'), default='all',
                  help='pinned: compare the digest only for @sha256 refs. A mirror filled by 05-load-images.sh holds '
                       're-serialized ordinary images whose digest differs from the tsv pin; a mirror filled with '
                       'skopeo copy --preserve-digests matches for every ref.')
  ap.add_argument('--insecure', action='store_true', help='skip TLS verification for an https mirror (self-signed bastion)')
  ap.add_argument('--list-paths', action='store_true', help='print ref, repo path and tag without any request')
  args = ap.parse_args()

  images = read_images(args.images)
  if args.list_paths:
    for img in images:
      repo, tag, _ = split_ref(img)
      print(f'{img}\t{repo}\t{tag}')
    return 0
  if not args.mirror:
    print('ERROR: pass --mirror URL or set MIRROR (e.g. http://172.17.0.191:5000)', file=sys.stderr)
    return 2
  parsed = urllib.parse.urlsplit(args.mirror)
  if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
    print('ERROR: --mirror must be http(s)://host[:port] without credentials', file=sys.stderr)
    return 2
  base = args.mirror.rstrip('/')
  ctx = None
  if args.insecure and parsed.scheme == 'https':
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
  optional = [re.compile(p) for p in (args.optional if args.optional is not None else DEFAULT_OPTIONAL)]
  table = read_digests(args.digests)

  counts = {'PRESENT': 0, 'MISSING': 0, 'DIGEST-MISMATCH': 0, 'ERROR': 0, 'WARN': 0}
  for img in images:
    repo, tag, inline = split_ref(img)
    expected = inline or table.get(img)
    if args.digest_scope == 'pinned' and not inline:
      expected = None
    status, digest = probe(base, repo, tag, args.timeout, ctx)
    if status == 200 and digest and (not expected or digest == expected):
      verdict, detail = 'PRESENT', digest
    elif status == 200 and digest:
      verdict, detail = 'DIGEST-MISMATCH', f'mirror {digest} != pin {expected}'
    elif status == 404:
      verdict, detail = 'MISSING', f'{repo}:{tag}'
    else:
      verdict, detail = 'ERROR', f'{repo}:{tag} status={status}'
    if verdict != 'PRESENT' and any(p.search(qualify(img)) for p in optional):
      print(f'WARN {img} (optional) {verdict} {detail}')
      counts['WARN'] += 1
      continue
    print(f'{verdict} {img} {detail}')
    counts[verdict] += 1
  print(f"mirror {parsed.scheme}://{parsed.netloc}: {counts['PRESENT']} present, {counts['MISSING']} missing, "
        f"{counts['DIGEST-MISMATCH']} digest-mismatch, {counts['ERROR']} error, {counts['WARN']} warn (optional)")
  return 1 if counts['MISSING'] or counts['DIGEST-MISMATCH'] or counts['ERROR'] else 0


if __name__ == '__main__':
  sys.exit(main())
