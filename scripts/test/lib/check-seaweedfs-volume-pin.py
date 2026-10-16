#!/usr/bin/env python3
"""R265: the SeaweedFS volume server keeps data on a node-local hostPath, so it must be pinned."""
import re
import sys

LABEL = 'narwhal.io/seaweedfs-volume'

chart = open('gitops/charts/narwhal-apps/templates/seaweedfs.yaml').read()
m = re.search(r'\n        volume:\n(.*?)\n        filer:', chart, re.S)
assert m, 'volume block not found'
assert re.search(r'nodeSelector: \|\n\s+' + re.escape(LABEL) + r': "true"', m.group(1)), 'chart volume nodeSelector'

s = open('scripts/cluster/08-4-storage.sh').read()
assert f'volume.nodeSelector={LABEL}: "true"' in s, '08-4 helm flag'
assert f'-l {LABEL}=true -o name' in s and 'LABELED_NODES=' in s, '08-4 must skip labelling when a node already carries the label'
label = f'label "${{SW_NODE}}" {LABEL}=true --overwrite'
assert label in s, '08-4 label command'
assert s.index(label) < s.index('helm upgrade --install seaweedfs'), 'label must precede the install'

v = open('scripts/verify/nfs-nonroot-write-check.sh').read()
lib = open('scripts/verify/lib-nfs-squash.sh').read()
assert 'lib-nfs-squash.sh' in v
for needle in ('no_root_squash', 'expected 750'):
    assert needle in lib, f'squash-aware root mode check: {needle}'
sys.exit(0)
