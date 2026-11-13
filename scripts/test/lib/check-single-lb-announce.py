#!/usr/bin/env python3
"""R344: execute single/HA convergence blocks offline with a recording kubectl."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LABEL = 'node.kubernetes.io/exclude-from-external-load-balancers-'
GUARD = 'if [ "${NARWHAL_TOPOLOGY:-ha}" = "single" ]; then'
PATHS = {
    '02-init-cluster.sh': ('# D-SINGLE-LB-ANNOUNCE:', '# Cloud uses'),
    '06-phase2-start.sh': ('# D-SINGLE-NODE:', '# Phase 2 scripts'),
    'cluster-heal.sh': ('# D-SINGLE-LB-ANNOUNCE:', '#========================================='),
}
STUB = r'''
kubectl() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$@"
}
log() { :; }
'''


def check(code):
    for topology in ('single', 'ha'):
        env = dict(os.environ, NARWHAL_TOPOLOGY=topology)
        # Run twice: label removal must remain safe when provisioning/healing repeats.
        proc = subprocess.run(['bash', '-ec', STUB + code + '\n' + code],
                              env=env, capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0, proc.stderr
        calls = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith('[')]
        labels = [c for c in calls if c[0] == 'label']
        expected = [['label', 'nodes', '--all', LABEL]] * 2 if topology == 'single' else []
        assert labels == expected, (topology, labels)


def main():
    mutations = 0
    for name, (start, end) in PATHS.items():
        source = (ROOT / 'scripts/cluster' / name).read_text()
        assert 'source /home/vagrant/scripts/common/lib-topology.sh' in source, name
        begin = source.index(start)
        code = source[begin:source.index(end, begin)]
        check(code)
        for old, new in [(f'kubectl label nodes --all {LABEL}', 'true'),
                         (GUARD, 'if true; then')]:
            assert old in code, (name, old)
            try:
                check(code.replace(old, new))
            except AssertionError:
                mutations += 1
            else:
                raise AssertionError(f'{name}: mutation survived: {old}')
    print(f'OK: init/Phase 2/heal single-only label removal, repeated runs; {mutations} mutations caught')


if __name__ == '__main__':
    main()
