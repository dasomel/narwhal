#!/usr/bin/env python3
"""R334: notification ownership/least privilege, with isolated negative fixtures."""
import copy
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
NAME = 'narwhal-notification-channels'
LABEL = {'narwhal.io/notification-channels': 'true'}
NS = {'matchLabels': {'kubernetes.io/metadata.name': 'monitoring'}}


def load():
    apps = list(yaml.safe_load_all(subprocess.check_output(
        ['helm', 'template', 'test', str(ROOT / 'gitops/charts/narwhal-apps')], text=True)))
    platform = list(yaml.safe_load_all(subprocess.check_output(
        ['helm', 'template', 'test', str(ROOT / 'gitops/charts/narwhal-platform')], text=True)))
    return {
        'values': next(x for x in apps if x['metadata']['name'] == 'prometheus-stack')['spec']['source']['helm']['valuesObject'],
        'app': next(x for x in apps if x['metadata']['name'] == 'notification-channels'),
        'objects': list(yaml.safe_load_all((ROOT / 'gitops/resources/notification-channels.yaml').read_text())),
        'role': next(x for x in platform if x['kind'] == 'Role' and x['metadata']['name'] == 'narwhal-portal-notification-channels'),
        'binding': next(x for x in platform if x['kind'] == 'RoleBinding' and x['metadata']['name'] == 'narwhal-portal-notification-channels'),
        'script': (ROOT / 'scripts/cluster/08-2-monitoring.sh').read_text(),
    }


def check(d):
    a = d['values']['alertmanager']['alertmanagerSpec']
    assert a['alertmanagerConfigSelector'] == {'matchLabels': LABEL}
    assert a['alertmanagerConfigNamespaceSelector'] == NS
    assert a['alertmanagerConfigMatcherStrategy'] == {'type': 'None'}
    etcd = d['values']['kubeEtcd']
    assert etcd['enabled'] is True
    assert etcd['service'] == {'port': 2381, 'targetPort': 2381, 'selector': {'component': 'etcd'}}
    assert etcd['serviceMonitor']['scheme'] == 'http'
    assert etcd['serviceMonitor']['bearerTokenFile'] == ''
    assert '--set-string kubeEtcd.serviceMonitor.bearerTokenFile= ' in d['script']
    assert len(d['objects']) == 2
    for obj in d['objects']:
        assert obj['metadata'] == {'name': NAME, 'namespace': 'monitoring', 'labels': LABEL}
    secret, config = d['objects']
    assert secret['kind'] == 'Secret' and not secret.get('data') and not secret.get('stringData')
    assert config['kind'] == 'AlertmanagerConfig'
    assert config['spec']['route']['receiver'] == 'null'
    assert not config['spec']['route'].get('routes')
    assert config['spec']['receivers'] == [{'name': 'null'}]
    role = d['role']
    assert role['metadata']['namespace'] == 'monitoring'
    assert role['rules'] == [
        {'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': [NAME], 'verbs': ['get', 'update', 'patch']},
        {'apiGroups': ['monitoring.coreos.com'], 'resources': ['alertmanagerconfigs'], 'resourceNames': [NAME], 'verbs': ['get', 'update', 'patch']},
    ]
    b = d['binding']
    assert b['metadata']['namespace'] == 'monitoring'
    assert b['roleRef'] == {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': role['metadata']['name']}
    assert b['subjects'] == [{'kind': 'ServiceAccount', 'name': 'narwhal-portal', 'namespace': 'devtools'}]
    app = d['app']['spec']
    assert app['source']['directory']['include'] == 'notification-channels.yaml'
    assert app['ignoreDifferences'] == [
        {'group': '', 'kind': 'Secret', 'name': NAME, 'namespace': 'monitoring', 'jsonPointers': ['/data']},
        {'group': 'monitoring.coreos.com', 'kind': 'AlertmanagerConfig', 'name': NAME, 'namespace': 'monitoring', 'jsonPointers': ['/spec']},
    ]
    assert 'RespectIgnoreDifferences=true' in app['syncPolicy']['syncOptions']
    for key, value in [('kubeEtcd.enabled', 'true'), ('kubeEtcd.service.port', '2381'),
                       ('kubeEtcd.service.targetPort', '2381'), ('kubeEtcd.service.selector.component', 'etcd'),
                       ('kubeEtcd.serviceMonitor.scheme', 'http'),
                       ('alertmanager.alertmanagerSpec.alertmanagerConfigMatcherStrategy.type', 'None')]:
        assert f'--set {key}={value} ' in d['script']
    for key, value in [('alertmanagerConfigSelector', {'matchLabels': LABEL}), ('alertmanagerConfigNamespaceSelector', NS)]:
        match = re.search(r"--set-json 'alertmanager.alertmanagerSpec\." + key + r"=(.*?)'", d['script'])
        assert match and yaml.safe_load(match[1]) == value


def mutations(d):
    for key in ['alertmanagerConfigSelector', 'alertmanagerConfigNamespaceSelector']:
        m = copy.deepcopy(d)
        m['values']['alertmanager']['alertmanagerSpec'][key] = {}
        yield key, m
    m = copy.deepcopy(d)
    m['values']['alertmanager']['alertmanagerSpec']['alertmanagerConfigMatcherStrategy'] = {'type': 'OnNamespace'}
    yield 'namespace matcher', m
    for i in range(2):
        for key, value in [('resourceNames', ['*']), ('resourceNames', []),
                           ('verbs', ['get', 'update', 'patch', 'create']),
                           ('verbs', ['get', 'update', 'patch', 'delete']),
                           ('verbs', ['get', 'update', 'patch', 'list'])]:
            m = copy.deepcopy(d)
            m['role']['rules'][i][key] = value
            yield f'RBAC {i} {key} {value}', m
        for key in ['name', 'namespace']:
            m = copy.deepcopy(d)
            del m['app']['spec']['ignoreDifferences'][i][key]
            yield f'ignore scope {i} {key}', m
        m = copy.deepcopy(d)
        m['app']['spec']['ignoreDifferences'][i]['jsonPointers'] = ['/metadata']
        yield f'ignore pointer {i}', m
        m = copy.deepcopy(d)
        m['objects'][i]['metadata']['labels'] = {}
        yield f'object label {i}', m
    m = copy.deepcopy(d)
    m['app']['spec']['syncPolicy']['syncOptions'].remove('RespectIgnoreDifferences=true')
    yield 'sync overwrite', m
    m = copy.deepcopy(d)
    m['script'] = m['script'].replace('Strategy.type=None', 'Strategy.type=OnNamespace')
    yield 'script drift', m


if __name__ == '__main__':
    data = load()
    check(data)
    print('R334 PASS: selectors, disabled objects, scoped RBAC, Argo ownership, install parity')
    if '--mutation-verify' in sys.argv:
        count = 0
        for name, mutated in mutations(data):
            try:
                check(mutated)
            except (AssertionError, KeyError):
                count += 1
            else:
                raise AssertionError(f'escaped mutation: {name}')
        print(f'R334 PASS: {count} mutations rejected')
