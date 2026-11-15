#!/usr/bin/env python3
"""R345: Gitea OAuth setup waits for the patched Deployment rollout."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def check(source):
    patch = source.index('kubectl patch deployment gitea')
    ready_wait = source.index('kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=gitea')
    pod_lookup = source.index('GITEA_POD=$(kubectl get pod -n devtools')
    rollout_wait = source.index('kubectl rollout status deployment/gitea -n devtools --timeout=')
    assert patch < rollout_wait < ready_wait < pod_lookup, 'rollout status must precede pod readiness and lookup'
    lookup = source[pod_lookup:source.index('\n\nif [ -n "${GITEA_POD}" ]', pod_lookup)]
    assert '--field-selector=status.phase=Running' in lookup, 'pod lookup must exclude non-Running pods'
    assert "awk 'NF == 1 { print $1; exit }'" in lookup, 'pod lookup must skip terminating pods and print only the name'


def main():
    source = (ROOT / 'scripts/cluster/12-gitea.sh').read_text()
    check(source)
    mutations = {
        'removed rollout status wait': source.replace(
            'kubectl rollout status deployment/gitea -n devtools --timeout=600s || true\n', '', 1
        ),
        'removed Running selector from GITEA_POD lookup': source.replace(
            "  --field-selector=status.phase=Running \\\n  -o jsonpath='{range .items[*]}{.metadata.name}{\" \"}{.metadata.deletionTimestamp}{\"\\n\"}{end}'",
            "  -o jsonpath='{range .items[*]}{.metadata.name}{\" \"}{.metadata.deletionTimestamp}{\"\\n\"}{end}'",
            1,
        ),
        'accepted terminating pod in awk filter': source.replace(
            "awk 'NF == 1 { print $1; exit }'", "awk 'NF >= 1 { print $1; exit }'", 1
        ),
    }
    for description, mutated in mutations.items():
        try:
            check(mutated)
        except (AssertionError, ValueError):
            continue
        raise AssertionError(f'mutation survived: {description}')
    print('ok: rollout ordering, Running selector, and terminating-pod filter; all 3 mutations caught')


if __name__ == '__main__':
    main()
