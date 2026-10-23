#!/usr/bin/env python3
"""R280: emptyDir-only policy and schedule wiring (#422)."""

import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def check(app):
    values = app['spec']['source']['helm']['valuesObject']
    policies = [obj for obj in values.get('extraObjects', [])
                if obj.get('metadata', {}).get('name') == 'velero-volume-policy']
    assert len(policies) == 1, 'exactly one velero-volume-policy required'
    policy = policies[0]
    assert policy['apiVersion'] == 'v1' and policy['kind'] == 'ConfigMap'
    assert policy['metadata']['namespace'] == app['spec']['destination']['namespace'] == 'storage'
    assert len(policy['data']) == 1, 'exactly one policy document required'
    assert yaml.safe_load(next(iter(policy['data'].values()))) == {
        'version': 'v1',
        'volumePolicies': [{'conditions': {'volumeTypes': ['emptyDir']},
                            'action': {'type': 'skip'}}],
    }, 'policy must skip only emptyDir, without includeExcludePolicy'
    schedules = values['schedules']
    assert set(schedules) == {'daily-full', 'daily-databases', 'daily-gitea',
                              'daily-harbor', 'daily-openbao'}, 'five schedules required'
    for name, schedule in schedules.items():
        template = schedule['template']
        assert template.get('resourcePolicy') == {
            'kind': 'configmap', 'name': 'velero-volume-policy'}, name
        assert template.get('defaultVolumesToFsBackup') is True, name


if __name__ == '__main__':
    try:
        check(yaml.safe_load((ROOT / 'gitops/charts/narwhal-apps/templates/velero.yaml').read_text()))
    except (AssertionError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f'FAIL: Velero volume policy: {exc}', file=sys.stderr)
        sys.exit(1)
    print('PASS: emptyDir-only skip policy and all five schedule references')
