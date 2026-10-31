#!/usr/bin/env python3
"""R298: platform *Down alerts watch workload availability and fire when the workload is gone; optional promtool cases."""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
PATH = 'gitops/resources/prometheus-alerts.yaml'
# alert -> (metric, label, namespace, workload); names verified from the pinned charts / Keycloak CR (see trace).
ALERTS = {
    'KeycloakDown': ('kube_statefulset_status_replicas_ready', 'statefulset', 'iam', 'keycloak'),
    'ArgoCDDown': ('kube_deployment_status_replicas_available', 'deployment', 'devtools', 'argocd-server'),
    'HarborDown': ('kube_deployment_status_replicas_available', 'deployment', 'devtools', 'harbor-core'),
    'GiteaDown': ('kube_deployment_status_replicas_available', 'deployment', 'devtools', 'gitea'),
    'ApisixDown': ('kube_deployment_status_replicas_available', 'deployment', 'platform-system', 'apisix'),
}


def check():
    text = (ROOT / PATH).read_text()
    # D-DOWN-WORKLOAD: kube-state-metrics has an empty metricLabelsAllowlist, so no label_* series exist.
    assert 'traefik' not in text.lower(), 'Traefik was replaced by APISIX; no traefik alert or selector may remain'
    resource = yaml.safe_load(text)
    rules = {r['alert']: r for g in resource['spec']['groups'] for r in g['rules'] if 'alert' in r}
    assert 'TraefikDown' not in rules
    for name, rule in rules.items():
        if name.endswith('Down'):
            assert 'kube_pod_labels' not in rule['expr'] and not re.search(r'label_\w+=', rule['expr']), \
                f'{name}: kube_pod_labels label_* joins never match (empty metricLabelsAllowlist)'
    for name, (metric, label, ns, workload) in ALERTS.items():
        rule = rules[name]
        sel = f'{metric}{{namespace="{ns}",{label}="{workload}"}}'
        assert rule['expr'].split() == f'{sel} == 0 or absent({sel})'.split(), name
        assert rule['for'] == '5m' and rule['labels']['severity'] == 'critical', name
    print(f'PASS: R298 {len(ALERTS)} Down alerts use workload availability + absent(), no label joins, no Traefik')
    return rules


def behavior(rules):
    if not shutil.which('promtool'):
        print('SKIP: promtool unavailable; R298 static checks passed')
        return
    cases = []
    for name, (metric, label, ns, workload) in ALERTS.items():
        def series(value, namespace=ns):
            return {'series': f'{metric}{{namespace="{namespace}",{label}="{workload}"}}', 'values': f'{value}+0x30'}

        def case(desc, input_series, firing):
            exp = [{'exp_labels': {'severity': 'critical', 'namespace': ns, label: workload}}] if firing else []
            cases.append({'name': f'{name}: {desc}', 'interval': '1m', 'input_series': input_series,
                          'alert_rule_test': [{'eval_time': '20m', 'alertname': name, 'exp_alerts': exp}]})

        case('available', [series(1)], False)
        case('zero available', [series(0)], True)
        case('series absent', [], True)
        case('same name in another namespace', [series(1, 'other')], True)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        bare = [{k: v for k, v in rules[n].items() if k != 'annotations'} for n in ALERTS]
        (path / 'rules.yaml').write_text(yaml.safe_dump({'groups': [{'name': 'down', 'rules': bare}]}))
        (path / 'tests.yaml').write_text(yaml.safe_dump({
            'rule_files': ['rules.yaml'], 'evaluation_interval': '1m', 'tests': cases}))
        subprocess.run(['promtool', 'check', 'rules', str(path / 'rules.yaml')], check=True)
        subprocess.run(['promtool', 'test', 'rules', str(path / 'tests.yaml')], check=True)
    print(f'PASS: {len(cases)} promtool behavior cases')


if __name__ == '__main__':
    behavior(check())
