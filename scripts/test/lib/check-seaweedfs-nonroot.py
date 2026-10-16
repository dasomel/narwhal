#!/usr/bin/env python3
"""R267: SeaweedFS master/filer keep state on NFS, so they run as the image's non-root `seaweed` user
(uid/gid 1000) with emptyDir logs, in the GitOps chart AND the 08-4 helm install; the align script's static
map and chart uid agree, defaults to dry run and never writes /etc/exports."""
import re
import sys

chart = open('gitops/charts/narwhal-apps/templates/seaweedfs.yaml').read()
sh = open('scripts/cluster/08-4-storage.sh').read()
align = open('scripts/cluster/nfs-root-squash-align.sh').read()

blocks = {
    'master': re.search(r'\n        master:\n(.*?)\n        volume:', chart, re.S),
    'filer': re.search(r'\n        filer:\n(.*?)\n        s3:', chart, re.S),
}
for comp, m in blocks.items():
    assert m, f'{comp} block not found'
    b = m.group(1)
    psc = re.search(r'podSecurityContext:\n((?:\s+.*\n)+?)\s+(?:logs|containerSecurityContext|s3):', b + '\n          logs:')
    assert psc, f'{comp} podSecurityContext'
    for want in ('enabled: true', 'runAsUser: 1000', 'runAsGroup: 1000', 'fsGroup: 1000', 'fsGroupChangePolicy: OnRootMismatch'):
        assert want in psc.group(1), f'{comp} chart {want}'
    assert re.search(r'logs:\n\s+type: emptyDir', b), f'{comp} logs must be emptyDir (hostPath is root-owned)'
    for key in ('enabled=true', 'runAsUser=1000', 'runAsGroup=1000', 'fsGroup=1000', 'fsGroupChangePolicy=OnRootMismatch'):
        assert f'--set {comp}.podSecurityContext.{key}' in sh, f'08-4 {comp} {key}'
    assert f'--set {comp}.logs.type=emptyDir' in sh, f'08-4 {comp} logs'

for pvc in ('storage/data-filer-seaweedfs-filer-0=1000:1000', 'storage/data-storage-seaweedfs-master-0=1000:1000'):
    assert f'"{pvc}"' in align, f'static map {pvc}'

assert 'APPLY=false' in align and '--apply) APPLY=true' in align, 'dry run must be the default'
code = [l for l in align.splitlines() if not l.lstrip().startswith('#')]
assert not any(re.search(r'(exportfs|/etc/exports.*(tee|sed -i|>>?)|(tee|sed -i|>>?).*/etc/exports)', l) and 'grep' not in l for l in code), 'must not edit exports'
assert 'no_root_squash' not in '\n'.join(code), 'script must not manipulate squash options'
sys.exit(0)
