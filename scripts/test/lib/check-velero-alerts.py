#!/usr/bin/env python3
"""R294: Velero scrape path and alerts (#456); optional promtool behavior checks."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def read(path):
    return yaml.safe_load((ROOT / path).read_text())


def check():
    app = read('gitops/charts/narwhal-apps/templates/velero.yaml')
    assert app['spec']['source']['targetRevision'] == '12.0.3'
    values = app['spec']['source']['helm']['valuesObject']
    assert values['metrics']['enabled'] is True
    assert values['metrics']['serviceMonitor'] == {
        'enabled': True, 'autodetect': False,
        'additionalLabels': {'release': 'prometheus-stack'}}
    # D-velero-scrape: the PeerAuthentication sits with the other mesh policies (kubeconform has
    # no Istio schemas for gitops/resources); the NetworkPolicy stays in network-policies.yaml.
    policies = list(yaml.safe_load_all((ROOT / 'gitops/resources/network-policies.yaml').read_text()))
    nets = [p for p in policies if p and p['metadata']['name'] == 'velero-metrics']
    assert [p['kind'] for p in nets] == ['NetworkPolicy']
    mesh = (ROOT / 'gitops/charts/narwhal-platform/templates/istio-ambient-policies.yaml').read_text()
    peers = [p for p in yaml.safe_load_all(mesh) if p and p['metadata']['name'] == 'velero-metrics']
    assert len(peers) == 1
    peer, net = peers[0], nets[0]
    selector = {'matchLabels': {'app.kubernetes.io/name': 'velero', 'app.kubernetes.io/instance': 'velero',
                                 'name': 'velero'}}
    assert peer['metadata']['namespace'] == net['metadata']['namespace'] == 'storage'
    assert peer['kind'] == 'PeerAuthentication'
    assert peer['spec'] == {'selector': selector, 'mtls': {'mode': 'STRICT'},
                            'portLevelMtls': {8085: {'mode': 'PERMISSIVE'}}}
    assert net['kind'] == 'NetworkPolicy'
    assert net['spec'] == {
        'podSelector': selector, 'policyTypes': ['Ingress'], 'ingress': [{
            'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'monitoring'}},
                      'podSelector': {'matchLabels': {
                          'app.kubernetes.io/name': 'prometheus',
                          'prometheus': 'prometheus-stack-kube-prom-prometheus'}}}],
            'ports': [{'protocol': 'TCP', 'port': 8085}, {'protocol': 'TCP', 'port': 15008}]}]}
    resource = read('gitops/resources/prometheus-alerts.yaml')
    assert resource['kind'] == 'PrometheusRule'
    assert resource['metadata']['namespace'] == 'monitoring'
    assert resource['metadata']['labels']['release'] == 'prometheus-stack'
    rules = next(g['rules'] for g in resource['spec']['groups'] if g['name'] == 'velero-backups')
    by_name = {r['alert']: r for r in rules}
    assert set(by_name) == {'VeleroBackupStale', 'VeleroBackupNeverSucceeded', 'VeleroBackupFailed',
                            'VeleroBackupPartiallyFailed', 'VeleroBackupValidationFailed', 'VeleroMetricsUnavailable'}
    for suffix, metric in [('Failed', 'failure'), ('PartiallyFailed', 'partial_failure'),
                           ('ValidationFailed', 'validation_failure')]:
        rule = by_name['VeleroBackup' + suffix]
        assert rule['expr'] == f'sum by (schedule) (increase(velero_backup_{metric}_total{{namespace="storage"}}[1h])) > 0'
        assert rule['for'] == '5m' and rule['labels']['severity'] == 'warning'
    assert '26 * 3600' in by_name['VeleroBackupStale']['expr']
    assert 'unless on (schedule)' in by_name['VeleroBackupNeverSucceeded']['expr']
    assert by_name['VeleroBackupNeverSucceeded']['for'] == '26h'
    for rule in rules:
        assert rule['annotations']['runbook_url'].endswith('#velero-backup-alerts')
    print('PASS: R294 monitor, restricted scrape path, 6 alert definitions')
    return rules


def behavior(rules):
    if not shutil.which('promtool'):
        print('SKIP: promtool unavailable; R294 static checks passed')
        return
    cases = []

    def case(name, series, at, alert, firing=False, schedule='daily-full'):
        labels = {'severity': next(r['labels']['severity'] for r in rules if r['alert'] == alert)}
        if schedule:
            labels['schedule'] = schedule
        cases.append({'name': name, 'interval': '1m', 'input_series': series,
                      'alert_rule_test': [{'eval_time': at, 'alertname': alert,
                                           'exp_alerts': [{'exp_labels': labels}] if firing else []}]})

    def metric(name, values):
        return {'series': f'velero_backup_{name}{{namespace="storage",schedule="daily-full"}}', 'values': values}

    success = metric('last_successful_timestamp', '60+0x1620')
    case('fresh success', [success], '25h', 'VeleroBackupStale')
    case('stuck after success', [success], '26h7m', 'VeleroBackupStale', True)
    attempts = metric('attempt_total', '0+0x1620')
    case('never succeeded grace', [attempts], '25h59m', 'VeleroBackupNeverSucceeded')
    case('never succeeded absent gauge', [attempts], '26h', 'VeleroBackupNeverSucceeded', True)
    case('never succeeded zero gauge', [attempts, metric('last_successful_timestamp', '0+0x1620')],
         '26h', 'VeleroBackupNeverSucceeded', True)
    case('success clears missing', [attempts, success], '26h', 'VeleroBackupNeverSucceeded')
    for suffix, counter in [('Failed', 'failure'), ('PartiallyFailed', 'partial_failure'),
                             ('ValidationFailed', 'validation_failure')]:
        case(counter, [metric(counter + '_total', '0+0x5 1+0x10')],
             '12m', 'VeleroBackup' + suffix, True)
        case(counter + ' reset only', [metric(counter + '_total', '4+0x5 0+0x10')],
             '12m', 'VeleroBackup' + suffix)
    case('scrape absent', [], '16m', 'VeleroMetricsUnavailable', True, '')
    # absent(up) preserves the equality-match labels, unlike max(up)==0.
    cases[-1]['alert_rule_test'][0]['exp_alerts'][0]['exp_labels'].update(namespace='storage', service='velero')
    case('scrape down', [{'series': 'up{namespace="storage",service="velero"}', 'values': '0+0x20'}],
         '16m', 'VeleroMetricsUnavailable', True, '')
    case('scrape healthy', [{'series': 'up{namespace="storage",service="velero"}', 'values': '1+0x20'}],
         '16m', 'VeleroMetricsUnavailable', False, '')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        # Test real expressions/for/labels, without duplicating user-facing annotation text.
        bare = [{k: v for k, v in r.items() if k != 'annotations'} for r in rules]
        (path / 'rules.yaml').write_text(yaml.safe_dump({'groups': [{'name': 'velero', 'rules': bare}]}))
        (path / 'tests.yaml').write_text(yaml.safe_dump({
            'rule_files': ['rules.yaml'], 'evaluation_interval': '1m', 'tests': cases}))
        subprocess.run(['promtool', 'check', 'rules', str(path / 'rules.yaml')], check=True)
        subprocess.run(['promtool', 'test', 'rules', str(path / 'tests.yaml')], check=True)
    print(f'PASS: {len(cases)} promtool behavior cases')


if __name__ == '__main__':
    behavior(check())
