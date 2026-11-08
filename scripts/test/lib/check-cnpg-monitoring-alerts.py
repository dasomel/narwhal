#!/usr/bin/env python3
"""R326 (D-CNPG-MON, D-CNPG-MON-INGRESS, D-CNPG-HEALTH-ALERT, D-CNPG-WAL-ARCHIVE-ALERT, #600, PR #601, PR #602).

Validates:
1. CNPG metrics scraping enabled in gitops/resources/narwhal-db.yaml and scripts/cluster/07-cnpg.sh.
2. kube-prometheus-stack selects PodMonitors across all namespaces (podMonitorSelectorNilUsesHelmValues: false)
   in gitops/charts/narwhal-apps/templates/prometheus-stack.yaml and scripts/cluster/08-2-monitoring.sh.
3. CNPG alerts in gitops/resources/prometheus-alerts.yaml:
   - CNPGClusterNotHealthy evaluated on cnpg_collector_up == 0 without absent()
   - CNPGMetricsUnavailable alerting on absent() for 15m window
   - CNPGWALArchivingFailing alerting on nonzero .ready backlog for 30m (exact expression and window)
   - Decisions and official CNPG 1.29 docs URL recorded in comments
4. NetworkPolicy database-prometheus-ingress in gitops/resources/database-ingress-policy.yaml:
   - Prometheus ingress allow from namespace monitoring (app.kubernetes.io/name=prometheus)
   - Scoped strictly to TCP 9187 (+ ambient HBONE 15008 per R200)
   - D-CNPG-MON-INGRESS comment recorded
5. promtool behavior cases for all alerts (healthy, collector down, absent metrics, pending backlog, sustained backlog, backlog cleared).
6. Mutation verification catching regressions across all 6 files.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = [
    'gitops/resources/narwhal-db.yaml',
    'scripts/cluster/07-cnpg.sh',
    'gitops/charts/narwhal-apps/templates/prometheus-stack.yaml',
    'scripts/cluster/08-2-monitoring.sh',
    'gitops/resources/prometheus-alerts.yaml',
    'gitops/resources/database-ingress-policy.yaml',
]

DOCS_URL = 'https://cloudnative-pg.io/documentation/1.29/monitoring/'


def static(root):
    # 1. narwhal-db.yaml
    db_text = (root / FILES[0]).read_text()
    assert 'D-CNPG-MON' in db_text, 'D-CNPG-MON comment missing in narwhal-db.yaml'
    db_docs = [d for d in yaml.safe_load_all(db_text) if d and d.get('kind') == 'Cluster']
    assert len(db_docs) == 1, 'Cluster document not found in narwhal-db.yaml'
    assert db_docs[0]['spec']['monitoring']['enablePodMonitor'] is True, (
        'enablePodMonitor must be true in narwhal-db.yaml'
    )

    # 2. 07-cnpg.sh
    cnpg_sh = (root / FILES[1]).read_text()
    assert 'enablePodMonitor: true' in cnpg_sh, 'enablePodMonitor: true missing in 07-cnpg.sh'
    assert 'D-CNPG-MON' in cnpg_sh, 'D-CNPG-MON comment missing in 07-cnpg.sh'

    # 3. prometheus-stack.yaml
    prom_stack_text = (root / FILES[2]).read_text()
    assert 'D-CNPG-MON' in prom_stack_text, 'D-CNPG-MON comment missing in prometheus-stack.yaml'
    prom_stack_yaml = yaml.safe_load(prom_stack_text)
    values = prom_stack_yaml['spec']['source']['helm']['valuesObject']
    pspec = values['prometheus']['prometheusSpec']
    assert pspec['podMonitorSelectorNilUsesHelmValues'] is False, (
        'podMonitorSelectorNilUsesHelmValues must be false in prometheus-stack.yaml'
    )
    assert pspec['podMonitorSelector'] == {}, 'podMonitorSelector must be empty dict {}'
    assert pspec['podMonitorNamespaceSelector'] == {}, 'podMonitorNamespaceSelector must be empty dict {}'

    # 4. 08-2-monitoring.sh
    mon_sh = (root / FILES[3]).read_text()
    assert '--set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false' in mon_sh, (
        'podMonitorSelectorNilUsesHelmValues=false missing in 08-2-monitoring.sh'
    )

    # 5. prometheus-alerts.yaml
    alerts_text = (root / FILES[4]).read_text()
    assert 'D-CNPG-HEALTH-ALERT' in alerts_text, 'D-CNPG-HEALTH-ALERT comment missing in prometheus-alerts.yaml'
    assert 'D-CNPG-WAL-ARCHIVE-ALERT' in alerts_text, 'D-CNPG-WAL-ARCHIVE-ALERT comment missing in prometheus-alerts.yaml'
    assert alerts_text.count(DOCS_URL) == 3, (
        f'CNPG docs URL {DOCS_URL} must appear 3 times in prometheus-alerts.yaml (found {alerts_text.count(DOCS_URL)})'
    )

    alerts_yaml = yaml.safe_load(alerts_text)
    db_group = next((g for g in alerts_yaml['spec']['groups'] if g['name'] == 'database'), None)
    assert db_group is not None, 'database alert group missing in prometheus-alerts.yaml'

    by_alert = {r['alert']: r for r in db_group['rules'] if 'alert' in r}
    assert 'CNPGClusterNotHealthy' in by_alert, 'CNPGClusterNotHealthy alert missing'
    assert 'CNPGMetricsUnavailable' in by_alert, 'CNPGMetricsUnavailable alert missing'
    assert 'CNPGWALArchivingFailing' in by_alert, 'CNPGWALArchivingFailing alert missing'

    health = by_alert['CNPGClusterNotHealthy']
    h_expr = ' '.join(health['expr'].split())
    assert h_expr == 'cnpg_collector_up{namespace="database"} == 0', f'unexpected health expr: {h_expr}'
    assert 'absent(' not in health['expr'], 'absent() must not be in CNPGClusterNotHealthy expr'
    assert health['for'] == '5m'
    assert health['labels']['severity'] == 'critical'

    unavail = by_alert['CNPGMetricsUnavailable']
    u_expr = ' '.join(unavail['expr'].split())
    assert u_expr == 'absent(cnpg_collector_up{namespace="database"})', f'unexpected unavail expr: {u_expr}'
    assert unavail['for'] == '15m'
    assert unavail['labels']['severity'] == 'warning'

    wal = by_alert['CNPGWALArchivingFailing']
    w_expr = ' '.join(wal['expr'].split())
    # D2: CI has no promtool. Pin the entire predicate so selector, operator,
    # threshold and extra branches cannot silently drift. Cost: formatting is
    # normalized only; escape hatch: update this contract with behavior cases.
    assert w_expr == (
        'cnpg_collector_pg_wal_archive_status{namespace="database",value="ready"} > 0'
    ), f'unexpected WAL backlog expr: {w_expr}'
    assert wal['for'] == '30m'
    assert wal['labels']['severity'] == 'critical'

    # 6. database-ingress-policy.yaml (P1, #600, PR #602)
    netpol_text = (root / FILES[5]).read_text()
    assert 'D-CNPG-MON-INGRESS' in netpol_text, 'D-CNPG-MON-INGRESS comment missing in database-ingress-policy.yaml'
    netpol_docs = [d for d in yaml.safe_load_all(netpol_text) if isinstance(d, dict) and d.get('kind') == 'NetworkPolicy']
    db_netpol = next((p for p in netpol_docs if p.get('metadata', {}).get('name') == 'database-default-deny-ingress'), None)
    assert db_netpol is not None, 'database-default-deny-ingress NetworkPolicy not found'
    assert db_netpol.get('metadata', {}).get('namespace') == 'database', (
        'database-default-deny-ingress must be in database namespace'
    )

    policies = [p for p in netpol_docs if p.get('metadata', {}).get('name') == 'database-prometheus-ingress']
    assert len(policies) == 1, 'expected exactly one database-prometheus-ingress policy'
    policy = policies[0]
    assert policy['metadata'].get('namespace') == 'database'
    spec = policy.get('spec', {})
    assert spec.get('podSelector') == {'matchLabels': {'cnpg.io/cluster': 'narwhal-db'}}, (
        'Prometheus ingress must target narwhal-db pods only'
    )
    assert spec.get('policyTypes') == ['Ingress']
    rules = spec.get('ingress', [])
    assert len(rules) == 1, 'expected exactly one Prometheus ingress rule'
    rule = rules[0]
    assert rule.get('from') == [{
        'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'monitoring'}},
        'podSelector': {'matchLabels': {
            'app.kubernetes.io/name': 'prometheus',
            'prometheus': 'prometheus-stack-kube-prom-prometheus',
        }},
    }], 'Prometheus namespace and pod selectors must be combined in one peer'
    assert rule.get('ports') == [
        {'protocol': 'TCP', 'port': 9187},
        {'protocol': 'TCP', 'port': 15008},
    ], 'Prometheus ingress must allow only TCP 9187 and ambient HBONE 15008'
    # D-CNPG-MON-SCOPE: NetworkPolicies add permissions; a broad duplicate would
    # bypass the target selector. Cost: pin this baseline; escape: review new peers.
    assert not any(
        peer.get('namespaceSelector', {}).get('matchLabels', {}).get('kubernetes.io/metadata.name') == 'monitoring'
        for r in db_netpol.get('spec', {}).get('ingress', [])
        for peer in r.get('from', [])
    ), 'baseline policy must not also grant namespace-wide Prometheus ingress'

    return alerts_yaml['spec']


def behavior(rule_spec):
    if not shutil.which('promtool'):
        print('SKIP: promtool unavailable; R326 behavior cases not run')
        return

    # Strip annotations to allow promtool alert test matching on exp_labels cleanly
    stripped_rules = []
    for g in rule_spec['groups']:
        if g['name'] == 'database':
            stripped_rules.append({
                'name': g['name'],
                'rules': [{k: v for k, v in r.items() if k != 'annotations'} for r in g['rules']]
            })

    test_cases = [
        {
            'name': 'healthy cnpg collector',
            'interval': '1m',
            'input_series': [
                {
                    'series': 'cnpg_collector_up{namespace="database",pod="narwhal-db-1"}',
                    'values': '1+0x30'
                }
            ],
            'alert_rule_test': [
                {'eval_time': '20m', 'alertname': 'CNPGClusterNotHealthy', 'exp_alerts': []},
                {'eval_time': '20m', 'alertname': 'CNPGMetricsUnavailable', 'exp_alerts': []}
            ]
        },
        {
            'name': 'unhealthy cnpg collector (value 0)',
            'interval': '1m',
            'input_series': [
                {
                    'series': 'cnpg_collector_up{namespace="database",pod="narwhal-db-1"}',
                    'values': '0+0x30'
                }
            ],
            'alert_rule_test': [
                {
                    'eval_time': '10m',
                    'alertname': 'CNPGClusterNotHealthy',
                    'exp_alerts': [
                        {'exp_labels': {'namespace': 'database', 'pod': 'narwhal-db-1', 'severity': 'critical'}}
                    ]
                },
                {'eval_time': '20m', 'alertname': 'CNPGMetricsUnavailable', 'exp_alerts': []}
            ]
        },
        {
            'name': 'absent cnpg collector',
            'interval': '1m',
            'input_series': [],
            'alert_rule_test': [
                {'eval_time': '10m', 'alertname': 'CNPGClusterNotHealthy', 'exp_alerts': []},
                {
                    'eval_time': '20m',
                    'alertname': 'CNPGMetricsUnavailable',
                    'exp_alerts': [
                        {'exp_labels': {'namespace': 'database', 'severity': 'warning'}}
                    ]
                }
            ]
        },
    ]
    # Sustained backlog detects a stuck single segment as well as growth.
    for name, values, eval_time, firing, namespace, value in [
        ('no backlog', '0+0x45', '40m', False, 'database', 'ready'),
        ('backlog pending', '1+0x45', '29m', False, 'database', 'ready'),
        ('single stuck WAL', '1+0x45', '30m', True, 'database', 'ready'),
        ('growing backlog', '1+1x45', '35m', True, 'database', 'ready'),
        ('backlog cleared', '1+0x20 0+0x24', '40m', False, 'database', 'ready'),
        ('brief backlog resets timer', '1+0x20 0 1+0x23', '40m', False, 'database', 'ready'),
        ('archived WAL only', '10+0x45', '40m', False, 'database', 'done'),
        ('other namespace', '1+0x45', '40m', False, 'other', 'ready'),
        ('missing backlog metric', None, '40m', False, 'database', 'ready'),
    ]:
        labels = {'namespace': namespace, 'pod': 'narwhal-db-1', 'value': value}
        series_labels = ','.join(f'{k}="{v}"' for k, v in labels.items())
        test_cases.append({
            'name': name,
            'interval': '1m',
            'input_series': [] if values is None else [{
                'series': f'cnpg_collector_pg_wal_archive_status{{{series_labels}}}',
                'values': values,
            }],
            'alert_rule_test': [{
                'eval_time': eval_time,
                'alertname': 'CNPGWALArchivingFailing',
                'exp_alerts': [{'exp_labels': {**labels, 'severity': 'critical'}}] if firing else [],
            }],
        })

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / 'rules.yaml').write_text(yaml.safe_dump({'groups': stripped_rules}))
        (t / 'tests.yaml').write_text(yaml.safe_dump({
            'rule_files': ['rules.yaml'],
            'evaluation_interval': '1m',
            'tests': test_cases
        }))
        subprocess.run(['promtool', 'check', 'rules', str(t / 'rules.yaml')], check=True, capture_output=True)
        subprocess.run(['promtool', 'test', 'rules', str(t / 'tests.yaml')], check=True, capture_output=True)

    print(f'PASS: {len(test_cases)} promtool behavior cases')


def check(root):
    behavior(static(root))


MUTATIONS = [
    (
        'narwhal-db enablePodMonitor: false',
        FILES[0],
        'enablePodMonitor: true',
        'enablePodMonitor: false',
    ),
    (
        '07-cnpg.sh enablePodMonitor: false',
        FILES[1],
        'enablePodMonitor: true',
        'enablePodMonitor: false',
    ),
    (
        'prometheus-stack podMonitorSelectorNilUsesHelmValues: true',
        FILES[2],
        'podMonitorSelectorNilUsesHelmValues: false',
        'podMonitorSelectorNilUsesHelmValues: true',
    ),
    (
        '08-2-monitoring.sh missing podMonitorSelectorNilUsesHelmValues',
        FILES[3],
        '--set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false',
        '--set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=true',
    ),
    (
        'CNPGClusterNotHealthy reintroduces absent()',
        FILES[4],
        'cnpg_collector_up{namespace="database"} == 0',
        'cnpg_collector_up{namespace="database"} == 0 or absent(cnpg_collector_up{namespace="database"})',
    ),
    *[
        (f'WAL predicate: {name}', FILES[4],
         'cnpg_collector_pg_wal_archive_status{namespace="database",value="ready"} > 0', expr)
        for name, expr in [
            ('unavailable SQL metric', 'cnpg_pg_stat_archiver_failed_count{namespace="database"} > 0'),
            ('wrong metric', 'cnpg_collector_pg_wal{namespace="database",value="ready"} > 0'),
            ('wrong namespace', 'cnpg_collector_pg_wal_archive_status{namespace="other",value="ready"} > 0'),
            ('missing namespace', 'cnpg_collector_pg_wal_archive_status{value="ready"} > 0'),
            ('done files', 'cnpg_collector_pg_wal_archive_status{namespace="database",value="done"} > 0'),
            ('zero fires', 'cnpg_collector_pg_wal_archive_status{namespace="database",value="ready"} >= 0'),
            ('single segment ignored', 'cnpg_collector_pg_wal_archive_status{namespace="database",value="ready"} > 1'),
            ('extra branch', 'cnpg_collector_pg_wal_archive_status{namespace="database",value="ready"} > 0 or vector(1)'),
        ]
    ],
    (
        'WAL window 30m -> 3m', FILES[4],
        'value="ready"} > 0\n          for: 30m',
        'value="ready"} > 0\n          for: 3m',
    ),
    (
        'WAL severity critical -> warning', FILES[4],
        'for: 30m\n          labels:\n            severity: critical',
        'for: 30m\n          labels:\n            severity: warning',
    ),
    (
        'missing official docs URL',
        FILES[4],
        DOCS_URL,
        'https://example.com/docs',
    ),
    (
        'CNPGMetricsUnavailable window 15m -> 1m',
        FILES[4],
        'alert: CNPGMetricsUnavailable\n          expr: |\n            absent(cnpg_collector_up{namespace="database"})\n          for: 15m',
        'alert: CNPGMetricsUnavailable\n          expr: |\n            absent(cnpg_collector_up{namespace="database"})\n          for: 1m',
    ),
    (
        'CNPGClusterNotHealthy severity critical -> warning',
        FILES[4],
        'labels:\n            severity: critical\n          annotations:\n            summary: "CNPG 인스턴스 비정상',
        'labels:\n            severity: warning\n          annotations:\n            summary: "CNPG 인스턴스 비정상',
    ),
    (
        'database-ingress-policy missing D-CNPG-MON-INGRESS',
        FILES[5],
        '# D-CNPG-MON-INGRESS',
        '# D-MUTATED',
    ),
    (
        'database-ingress-policy wrong port 9187 -> 9188',
        FILES[5],
        'port: 9187',
        'port: 9188',
    ),
    (
        'database-ingress-policy wrong namespace monitoring -> devtools',
        FILES[5],
        'kubernetes.io/metadata.name: monitoring',
        'kubernetes.io/metadata.name: devtools',
    ),
    (
        'database-ingress-policy wrong pod label prometheus -> alertmanager',
        FILES[5],
        'app.kubernetes.io/name: prometheus',
        'app.kubernetes.io/name: alertmanager',
    ),
    (
        'database-ingress-policy widened ports with 5432',
        FILES[5],
        'port: 9187\n        - protocol: TCP\n          port: 15008',
        'port: 9187\n        - protocol: TCP\n          port: 5432\n        - protocol: TCP\n          port: 15008',
    ),
    (
        'database-ingress-policy target widened to all database pods',
        FILES[5],
        'podSelector:\n    matchLabels:\n      cnpg.io/cluster: narwhal-db',
        'podSelector: {}',
    ),
    (
        'database-ingress-policy Prometheus instance selector removed',
        FILES[5],
        '              prometheus: prometheus-stack-kube-prom-prometheus\n',
        '',
    ),
    (
        'database-ingress-policy namespace and pod peers split (OR)',
        FILES[5],
        'kubernetes.io/metadata.name: monitoring\n          podSelector:',
        'kubernetes.io/metadata.name: monitoring\n        - podSelector:',
    ),
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
    print('PASS: R326 CNPG metrics scraping and WAL archiving alerts')
    if '--mutation-verify' in sys.argv:
        mutation_verify()
