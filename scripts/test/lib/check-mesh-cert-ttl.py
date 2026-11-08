#!/usr/bin/env python3
"""R324 (D-MESH-CERT-TTL, D-MESH-CERT-ALERT, D-CNPG-POD-READY, #600): mesh workload certs survive host sleep.

Pins (1) the 168h workload cert TTL on every client that chooses it (ztunnel env.SECRET_TTL, waypoint
meshConfig.defaultConfig.proxyMetadata.SECRET_TTL) and on istiod (DEFAULT/MAX_WORKLOAD_CERT_TTL), in BOTH the
gitops Applications and the 09-istio-ambient.sh heredocs, (2) the waypoint cert-expiry and CNPG instance
readiness alerts including promtool behavior cases, (3) the host-sleep runbook, the troubleshooting entry and the
warn-only up.sh check. --mutation-verify re-runs everything against copies with one defect each.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
ISTIOD = 'gitops/charts/narwhal-apps/templates/istiod.yaml'
ZTUNNEL = 'gitops/charts/narwhal-apps/templates/ztunnel.yaml'
SCRIPT = 'scripts/cluster/09-istio-ambient.sh'
ALERTS = 'gitops/resources/prometheus-alerts.yaml'
OPS = 'docs/vagrant/operations.md'
TROUBLE = 'docs/common/troubleshooting.md'
UP = 'scripts/up.sh'
FILES = [ISTIOD, ZTUNNEL, SCRIPT, ALERTS, OPS, TROUBLE, UP]
MIN_TTL = 168 * 3600  # 7d: survives a weekend plus margin; refresh at ~50% leaves >=84h of life
HEADING = '## 14. 호스트 슬립과 메시 인증서 (D-MESH-CERT-TTL)'
ANCHOR = '#14-호스트-슬립과-메시-인증서-d-mesh-cert-ttl'


def seconds(value):
    total = 0
    for num, unit in re.findall(r'(\d+)([hms])', str(value)):
        total += int(num) * {'h': 3600, 'm': 60, 's': 1}[unit]
    assert total > 0, f'not a duration: {value!r}'
    return total


def heredoc(text, target):
    m = re.search(r"cat > /tmp/" + target + r" << 'EOF'\n(.*?)\nEOF\n", text, re.S)
    assert m, f'heredoc {target} missing'
    return yaml.safe_load(m.group(1))


def ttl_values(istiod, ztunnel):
    return {
        'istiod DEFAULT': istiod['env']['DEFAULT_WORKLOAD_CERT_TTL'],
        'istiod MAX': istiod['env']['MAX_WORKLOAD_CERT_TTL'],
        'waypoint SECRET_TTL': istiod['meshConfig']['defaultConfig']['proxyMetadata']['SECRET_TTL'],
        'ztunnel SECRET_TTL': ztunnel['env']['SECRET_TTL'],
    }


def check_ttl(label, istiod, ztunnel):
    vals = ttl_values(istiod, ztunnel)
    secs = {k: seconds(v) for k, v in vals.items()}
    for key in ('istiod DEFAULT', 'waypoint SECRET_TTL', 'ztunnel SECRET_TTL'):
        assert secs[key] >= MIN_TTL, f'{label}: {key}={vals[key]} is below 168h'
    assert secs['istiod MAX'] >= max(secs['waypoint SECRET_TTL'], secs['ztunnel SECRET_TTL']), \
        f'{label}: MAX_WORKLOAD_CERT_TTL below a requested TTL (istiod would refuse the CSR)'
    # the ambient profile's own proxyMetadata must survive a deep merge; here: SECRET_TTL is not alone-clobbering
    return vals


def static(root):
    gi = yaml.safe_load((root / ISTIOD).read_text())['spec']['source']['helm']['valuesObject']
    gz = yaml.safe_load((root / ZTUNNEL).read_text())['spec']['source']['helm']['valuesObject']
    script = (root / SCRIPT).read_text()
    si, sz = heredoc(script, 'istiod-values.yaml'), heredoc(script, 'ztunnel-values.yaml')
    g, s = check_ttl('gitops', gi, gz), check_ttl('script', si, sz)
    assert g == s, f'gitops and 09-istio-ambient.sh disagree: {g} vs {s}'
    assert gi['profile'] == 'ambient' and si['profile'] == 'ambient'
    assert 'D-MESH-CERT-TTL' in (root / ISTIOD).read_text() and 'options.go:76' in (root / ISTIOD).read_text(), \
        'D-id with source citation missing'

    rules = yaml.safe_load((root / ALERTS).read_text())['spec']['groups']
    by = {r['alert']: r for grp in rules for r in grp['rules'] if 'alert' in r}
    wp = by['IstioWaypointCertExpiringSoon']
    e = ' '.join(wp['expr'].split())
    assert e == 'istio_agent_cert_expiry_seconds{job="istio-system/istio-waypoints"} < 24 * 3600', e
    assert wp['labels']['severity'] == 'critical' and wp['for'] == '5m'
    cn = by['CNPGInstanceNotReady']
    e = ' '.join(cn['expr'].split())
    assert e == 'kube_pod_status_ready{namespace="database",condition="true",pod=~".+-[0-9]+"} == 0', e
    assert cn['labels']['severity'] == 'critical' and cn['for'] == '10m'

    ops, trouble, up = (root / OPS).read_text(), (root / TROUBLE).read_text(), (root / UP).read_text()
    assert ops.count(HEADING + '\n') == 1, 'runbook heading'
    sec = ops.split(HEADING + '\n', 1)[1].split('\n## ', 1)[0]
    for needle in ('caffeinate -dimsu', 'pmset -a sleep 0', 'rollout restart daemonset/ztunnel',
                   'rollout restart deployment/waypoint', 'gateway.networking.k8s.io/gateway-name=waypoint',
                   '--context narwhal'):
        assert needle in sec, f'runbook lacks {needle}'
    assert sec.index('daemonset/ztunnel') < sec.index('deployment/waypoint'), 'restart order: ztunnel first'
    assert 'operations.md' + ANCHOR in trouble, 'troubleshooting link to the runbook anchor'
    assert '### 10.2 ' in trouble
    block = up.split('D-MESH-CERT-TTL', 1)[1].split('\nfi\n', 1)[0]
    assert 'pmset -g' in block and 'pgrep -x caffeinate' in block and 'say "WARN' in block
    assert not re.search(r'\bexit \d', block), 'up.sh host-sleep check must be warn-only'
    return wp, cn


def behavior(wp, cn):
    if not shutil.which('promtool'):
        print('SKIP: promtool unavailable; R324 behavior cases not run')
        return

    def wcase(name, value, firing):
        lab = {'container': 'istio-proxy', 'job': 'istio-system/istio-waypoints', 'namespace': 'monitoring',
               'pod': 'waypoint-x', 'resource_name': 'default'}
        sel = ','.join(f'{k}="{v}"' for k, v in lab.items())
        exp = [{'exp_labels': {**{k: lab[k] for k in ('container', 'job', 'namespace', 'pod', 'resource_name')},
                               'severity': 'critical'}}] if firing else []
        return {'name': name, 'interval': '1m',
                'input_series': [{'series': f'istio_agent_cert_expiry_seconds{{{sel}}}', 'values': f'{value}x30'}],
                'alert_rule_test': [{'eval_time': '20m', 'alertname': 'IstioWaypointCertExpiringSoon', 'exp_alerts': exp}]}

    def ccase(name, pod, ready, firing):
        exp = [{'exp_labels': {'namespace': 'database', 'condition': 'true', 'pod': pod, 'severity': 'critical'}}] \
            if firing else []
        return {'name': name, 'interval': '1m',
                'input_series': [{'series': 'kube_pod_status_ready{namespace="database",condition="true",'
                                            f'pod="{pod}"}}', 'values': f'{ready}x30'}],
                'alert_rule_test': [{'eval_time': '20m', 'alertname': 'CNPGInstanceNotReady', 'exp_alerts': exp}]}

    cases = [
        wcase('fresh 168h cert does not fire', 600000, False),
        wcase('healthy minimum (84h left) does not fire', 302400, False),
        wcase('22h left fires', 79200, True),
        wcase('already expired (negative) fires', -3600, True),
        ccase('instance NotReady fires', 'narwhal-db-2', 0, True),
        ccase('instance Ready does not fire', 'narwhal-db-2', 1, False),
        ccase('pooler pod name is not an instance', 'narwhal-db-pooler-rw-5b44f8765f-vpnfb', 0, False),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'rules.yaml').write_text(yaml.safe_dump({'groups': [{'name': 'g', 'rules': [
            {k: v for k, v in r.items() if k != 'annotations'} for r in (wp, cn)]}]}))
        (t / 'tests.yaml').write_text(yaml.safe_dump({
            'rule_files': ['rules.yaml'], 'evaluation_interval': '1m', 'tests': [
                {k: v for k, v in c.items()} for c in cases]}))
        subprocess.run(['promtool', 'check', 'rules', str(t / 'rules.yaml')], check=True)
        subprocess.run(['promtool', 'test', 'rules', str(t / 'tests.yaml')], check=True)
    print(f'PASS: {len(cases)} promtool behavior cases')


def check(root):
    behavior(*static(root))


MUTATIONS = [
    ('istiod DEFAULT 168h -> 24h', ISTIOD, 'DEFAULT_WORKLOAD_CERT_TTL: 168h', 'DEFAULT_WORKLOAD_CERT_TTL: 24h'),
    ('istiod MAX 720h -> 100h (below requested)', ISTIOD, 'MAX_WORKLOAD_CERT_TTL: 720h', 'MAX_WORKLOAD_CERT_TTL: 100h'),
    ('waypoint SECRET_TTL dropped (gitops)', ISTIOD, '              SECRET_TTL: 168h\n', '              X_SECRET_TTL: 168h\n'),
    ('ztunnel SECRET_TTL 168h -> 24h (gitops)', ZTUNNEL, 'SECRET_TTL: 168h', 'SECRET_TTL: 24h'),
    ('script ztunnel TTL drifts from gitops', SCRIPT, 'env:\n  SECRET_TTL: 168h\n', 'env:\n  SECRET_TTL: 240h\n'),
    ('script istiod waypoint TTL 168h -> 24h', SCRIPT, '      SECRET_TTL: 168h\n', '      SECRET_TTL: 24h\n'),
    ('source citation removed', ISTIOD, 'options.go:76', 'options.go:0'),
    ('waypoint alert threshold 24h -> 1h', ALERTS, '< 24 * 3600', '< 1 * 3600'),
    ('waypoint alert job selector dropped', ALERTS,
     'istio_agent_cert_expiry_seconds{job="istio-system/istio-waypoints"}', 'istio_agent_cert_expiry_seconds'),
    ('CNPG alert regex matches poolers', ALERTS, 'pod=~".+-[0-9]+"', 'pod=~".+"'),
    ('CNPG alert for 10m -> 3h', ALERTS, 'for: 10m\n          labels:\n            severity: critical\n          annotations:\n'
     '            summary: "CNPG 인스턴스', 'for: 3h\n          labels:\n            severity: critical\n          annotations:\n'
     '            summary: "CNPG 인스턴스'),
    ('runbook caffeinate removed', OPS, 'caffeinate -dimsu', 'caffeinate-removed'),
    ('runbook restarts waypoint before ztunnel', OPS, 'rollout restart daemonset/ztunnel',
     'rollout restart deployment/waypoint-first'),
    ('troubleshooting link broken', TROUBLE, ANCHOR, '#nope'),
    ('up.sh check made fatal', UP, 'say "WARN: host sleep is enabled', 'exit 1; say "WARN: host sleep is enabled'),
    ('up.sh caffeinate exemption dropped', UP, "&& ! pgrep -x caffeinate >/dev/null 2>&1", ''),
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
            except (AssertionError, KeyError, TypeError, subprocess.CalledProcessError):
                print(f'caught: {name}')
                continue
            raise SystemExit(f'MUTATION SURVIVED: {name}')
    print(f'PASS: {len(MUTATIONS)} mutations rejected')


if __name__ == '__main__':
    check(ROOT)
    print('PASS: R324 mesh cert TTL, alerts, runbook and up.sh warning')
    if '--mutation-verify' in sys.argv:
        mutation_verify()
