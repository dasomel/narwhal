#!/usr/bin/env python3
"""R346: readiness pre-flight retries and diagnoses kubectl failures."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/test/show-credentials.sh'


def check(src):
    assert 'for attempt in 1 2 3' in src, 'readiness probe must have three bounded attempts'
    assert '2>"${READY_ERR}"' in src, 'probe stderr must be captured for diagnosis'
    assert 'x509' in src and 'certificate signed by unknown authority' in src, \
        'stale kubeconfig CA branch missing'
    assert 'is not ready or too slow' in src and '/readyz?verbose' in src, \
        'API-not-ready branch missing'
    unreachable = src[src.index('*"connection refused"*'):src.index(')', src.index('*"connection refused"*'))]
    assert 'context deadline' not in unreachable and 'Client.Timeout' not in unreachable, \
        'a post-connect timeout (etcd stall) must not be classified as an unreachable VM'
    assert 'ubuntu@<bastion>' in src, 'kakao bastion tunnel hint missing'


def main():
    src = SCRIPT.read_text()
    check(src)
    mutations = {
        'retry-removed': src.replace('for attempt in 1 2 3; do', 'for attempt in 1; do'),
        'stderr-hidden': src.replace('2>"${READY_ERR}"', '2>/dev/null'),
        'x509-removed': src.replace('certificate signed by unknown authority', 'certificate error'),
        'not-ready-removed': src.replace('is not ready or too slow', 'API error'),
        'deadline-as-unreachable': src.replace('*"connection refused"*|', '*"connection refused"*|*"context deadline exceeded"*|', 1),
        'kakao-hint-removed': src.replace('ubuntu@<bastion>', 'the bastion'),
    }
    for name, mutated in mutations.items():
        try:
            check(mutated)
        except AssertionError:
            continue
        raise SystemExit(f'mutation not caught: {name}')
    print('ok: readiness retry and diagnostics present; 6 mutations caught')


if __name__ == '__main__':
    main()
