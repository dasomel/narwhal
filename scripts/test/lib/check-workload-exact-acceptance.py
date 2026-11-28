#!/usr/bin/env python3
"""R423 (lane B): exact workload exceptions and technical rationale."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    'acceptance': ROOT / 'portal/src/data/compliance-acceptance-rules.json',
    'i18n': ROOT / 'portal/src/lib/i18n.ts',
    'docs': ROOT / 'docs/common/compliance-hardening.md',
    'regression': ROOT / 'scripts/test/regression-check-kakao.sh',
    'renderer': ROOT / 'scripts/ops/render-cis-benchmark.py',
}


def check(values):
    errors = []
    rules = json.loads(values['acceptance'])['configAudit']
    expected = {
        'cfg:security-system:CronJob:cis-bench-974178c0082e:accept': ['KSV010', 'KSV012', 'KSV020', 'KSV021', 'KSV022', 'KSV023'],
        'cfg:chaos-testing:ReplicaSet:chaos-controller-manager:accept': ['KSV001', 'KSV003', 'KSV004'],
        'cfg:database:Pod:narwhal-db-1:accept': ['KSV020', 'KSV021'],
        'cfg:database:Pod:narwhal-db-1:ksv036-accept': ['KSV036'],
    }
    for rid, checks in expected.items():
        matched = [rule for rule in rules if rule.get('id') == rid]
        if len(matched) != 1 or matched[0].get('checks') != checks:
            errors.append(f'{rid}: exact target/check set mismatch')
    for key in ('kubeBenchHostAudit', 'chaosManagerChartLimits', 'cnpgPostgresUid26'):
        if values['i18n'].count(f'"compliance.acceptedReason.{key}":') != 2:
            errors.append(f'{key}: expected bilingual reasons')
    if "'securityContext': {'runAsUser': 0, 'allowPrivilegeEscalation': False," not in values['renderer']:
        errors.append('kube-bench stays root (non-root scans failed live, D-CIS-BENCH-ROOT)')
    for text in ('CronJob/cis-bench-974178c0082e', 'ReplicaSet/chaos-controller-manager', 'Pod/narwhal-db-1 | KSV020, KSV021', 'Pod/narwhal-db-1 | KSV036'):
        if text not in values['docs']:
            errors.append(f'compliance table missing {text}')
    if 'check R423 ' not in values['regression'] or 'check-workload-exact-acceptance.py --mutation-verify' not in values['regression']:
        errors.append('R423 lane-B regression registration missing')
    return errors


def main():
    current = {key: path.read_text() for key, path in FILES.items()}
    errors = check(current)
    if errors:
        print('FAIL: ' + '; '.join(errors))
        return 1
    if '--mutation-verify' in sys.argv:
        for old, new in [('cis-bench-974178c0082e', 'cis-bench-*'), ('chaos-controller-manager', 'chaos-*'), ('narwhal-db-1', 'narwhal-db-*')]:
            changed = dict(current)
            changed['acceptance'] = changed['acceptance'].replace(old, new, 1)
            if not check(changed):
                print(f'FAIL: accepted widened selector {old}')
                return 1
        print('PASS: R423 lane-B exact workload acceptance (3 mutations rejected)')
    else:
        print('PASS: R423 lane-B exact workload acceptance')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
