#!/usr/bin/env python3
"""R345: Gitea OAuth setup waits for the patched Deployment rollout."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def check(source):
    patch = source.index('kubectl patch deployment gitea')
    ready_wait = source.index('kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea')
    pod_lookup = source.index('GITEA_POD=$(kubectl get pod -n devtools')
    rollout_wait = source.index('kubectl rollout status deployment/gitea -n devtools --timeout=')
    running_selector = source.index('--field-selector=status.phase=Running')
    assert patch < rollout_wait < ready_wait < pod_lookup, 'rollout status must precede pod readiness and lookup'
    assert running_selector < pod_lookup, 'pod lookup must exclude non-Running pods'


def main():
    source = (ROOT / 'scripts/cluster/12-gitea.sh').read_text()
    check(source)
    mutated = source.replace(
        'kubectl rollout status deployment/gitea -n devtools --timeout=600s || true\n', '', 1
    )
    try:
        check(mutated)
    except (AssertionError, ValueError):
        print('ok: rollout ordering and Running pod selector; removal mutation caught')
        return
    raise AssertionError('mutation survived: removed rollout status wait')


if __name__ == '__main__':
    main()
