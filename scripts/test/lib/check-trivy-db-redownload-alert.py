#!/usr/bin/env python3
"""R316 (D-SECDB-STALE, #585): TrivyServerDbRedownloading alert and the DB refresh runbook.

Pins (1) the alert (metric, container selector, 3h window, 6e9 threshold, severity, runbook
anchor that resolves to a real heading), (2) the runbook's three refresh commands in order and
(3) promtool behavior cases when available: a stale mirror (one 3.94e9 download per hour) fires,
a healthy single download and idle scans do not. --mutation-verify re-runs the checks against
copies with one defect each and requires every defect to be rejected.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = ['gitops/resources/prometheus-alerts.yaml', 'scripts/airgap/README.md']
ALERT = 'TrivyServerDbRedownloading'
HEADING = '### 보안 DB 갱신 절차와 낡은 미러 경보 (D-SECDB-STALE)'
DOWNLOAD = 3940000000  # one trivy-db download in cAdvisor container_fs_writes_bytes_total


def slug(heading):
    text = heading.lstrip('#').strip().lower()
    return re.sub(r'\s', '-', re.sub(r'[^\w\s-]', '', text))


def static(root):
    rules = yaml.safe_load((root / FILES[0]).read_text())
    found = [r for g in rules['spec']['groups'] for r in g['rules'] if r.get('alert') == ALERT]
    assert len(found) == 1, 'alert missing'
    rule = found[0]
    expr = ' '.join(rule['expr'].split())
    assert 'container_fs_writes_bytes_total{namespace="security-system",container="trivy-server"}[3h]' in expr, expr
    assert expr.endswith('> 6e9'), expr
    assert expr.startswith('max by (pod) (increase('), 'per-device series must not be summed: ' + expr
    assert rule['labels']['severity'] == 'warning' and rule['for'] == '10m'
    docs = (root / FILES[1]).read_text()
    assert docs.count(HEADING + '\n') == 1, 'runbook heading'
    url = rule['annotations']['runbook_url']
    assert url.endswith('/scripts/airgap/README.md#' + slug(HEADING)), url
    sec = docs.split(HEADING + '\n', 1)[1].split('\n### ', 1)[0]
    pos = [sec.find(k) for k in ('fetch-security-db.sh', 'promote-security-db.sh --bundle <bundle-dir> --approved-by',
                                 'push-security-db.sh')]
    assert all(p >= 0 for p in pos) and pos == sorted(pos), pos
    assert '--ca-cert' in sec and '--kube-context narwhal' in sec
    return rule


def behavior(rule):
    if not shutil.which('promtool'):
        print('SKIP: promtool unavailable; R316 behavior cases not run')
        return

    def series(pod, downloads_at_min, minutes=360, device='/dev/dm-0'):
        vals, total = [], 0
        for m in range(minutes):
            if m in downloads_at_min:
                total += DOWNLOAD
            vals.append(str(total))
        return {'series': 'container_fs_writes_bytes_total{namespace="security-system",'
                          f'container="trivy-server",pod="{pod}",device="{device}"}}', 'values': ' '.join(vals)}

    def case(name, s, firing):
        exp = [{'exp_labels': {'pod': 'trivy-server-0', 'severity': 'warning'}}] if firing else []
        return {'name': name, 'interval': '1m', 'input_series': s if isinstance(s, list) else [s],
                'alert_rule_test': [{'eval_time': '5h', 'alertname': ALERT, 'exp_alerts': exp}]}

    cases = [
        case('stale mirror: hourly download', series('trivy-server-0', set(range(5, 360, 60))), True),
        case('healthy: one download at start', series('trivy-server-0', {5}), False),
        # live cAdvisor exposes the same counter for dm-0 and the nvme0n1 under it
        case('healthy: one download, two device series', [series('trivy-server-0', {5}),
             series('trivy-server-0', {5}, device='/dev/nvme0n1')], False),
        case('stale mirror, two device series', [series('trivy-server-0', set(range(5, 360, 60))),
             series('trivy-server-0', set(range(5, 360, 60)), device='/dev/nvme0n1')], True),
        case('idle scans, nothing written', series('trivy-server-0', set()), False),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'rules.yaml').write_text(yaml.safe_dump({'groups': [{'name': 'g', 'rules': [
            {k: v for k, v in rule.items() if k != 'annotations'}]}]}))
        (t / 'tests.yaml').write_text(yaml.safe_dump({
            'rule_files': ['rules.yaml'], 'evaluation_interval': '1m', 'tests': cases}))
        subprocess.run(['promtool', 'check', 'rules', str(t / 'rules.yaml')], check=True)
        subprocess.run(['promtool', 'test', 'rules', str(t / 'tests.yaml')], check=True)
    print(f'PASS: {len(cases)} promtool behavior cases')


def check(root):
    behavior(static(root))


MUTATIONS = [
    ('max by (pod) -> sum by (pod)', FILES[0], 'max by (pod) (increase(', 'sum by (pod) (increase('),
    ('threshold 6e9 -> 60e9', FILES[0], '[3h])) > 6e9', '[3h])) > 60e9'),
    ('window 3h -> 5m', FILES[0], 'container="trivy-server"}[3h]', 'container="trivy-server"}[5m]'),
    ('container selector dropped', FILES[0], ',container="trivy-server"}[3h]', '}[3h]'),
    ('runbook anchor broken', FILES[0], '#보안-db-갱신-절차와-낡은-미러-경보-d-secdb-stale', '#refresh'),
    ('runbook push step removed', FILES[1], 'push-security-db.sh --bundle <bundle-dir>',
     'push-removed.sh --bundle <bundle-dir>'),
    ('runbook cluster context dropped', FILES[1], '--kube-context narwhal', '--kube-context <ctx>'),
]


def mutation_verify():
    for name, rel, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            for f in FILES:
                (t / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / f, t / f)
            text = (t / rel).read_text()
            assert text.count(old) >= 1, f'mutation target missing: {name}'
            (t / rel).write_text(text.replace(old, new, 1))
            try:
                check(t)
            except (AssertionError, subprocess.CalledProcessError):
                print(f'caught: {name}')
                continue
            raise SystemExit(f'MUTATION SURVIVED: {name}')
    print(f'PASS: {len(MUTATIONS)} mutations rejected')


if __name__ == '__main__':
    check(ROOT)
    print('PASS: R316 alert, runbook anchor and refresh order')
    if '--mutation-verify' in sys.argv:
        mutation_verify()
