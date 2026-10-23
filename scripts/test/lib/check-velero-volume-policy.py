#!/usr/bin/env python3
"""R280: emptyDir-only policy parity across GitOps and 08-4 install values (#422)."""

import argparse
import copy
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
CHART = Path('gitops/charts/narwhal-apps/templates/velero.yaml')
INSTALL = Path('scripts/cluster/08-4-storage.sh')
MARKER = 'cat > /tmp/velero-values.yaml << EOF\n'
ERRORS = (AssertionError, KeyError, TypeError, ValueError, yaml.YAMLError)


def check_values(values, namespace):
    policies = [obj for obj in values.get('extraObjects', [])
                if obj.get('metadata', {}).get('name') == 'velero-volume-policy']
    assert len(policies) == 1, 'exactly one velero-volume-policy required'
    policy = policies[0]
    assert policy['apiVersion'] == 'v1' and policy['kind'] == 'ConfigMap'
    assert policy['metadata']['namespace'] == namespace == 'storage'
    assert len(policy['data']) == 1, 'exactly one policy document required'
    document = next(iter(policy['data'].values()))
    assert yaml.safe_load(document) == {
        'version': 'v1',
        'volumePolicies': [{'conditions': {'volumeTypes': ['emptyDir']},
                            'action': {'type': 'skip'}}],
    }, 'policy must skip only emptyDir, without includeExcludePolicy'
    assert values['configuration']['defaultVolumesToFsBackup'] is True, 'server fs-backup default'
    schedules = values['schedules']
    assert set(schedules) == {'daily-full', 'daily-databases', 'daily-gitea',
                              'daily-harbor', 'daily-openbao'}, 'five schedules required'
    for name, schedule in schedules.items():
        template = schedule['template']
        assert template.get('resourcePolicy') == {
            'kind': 'configmap', 'name': 'velero-volume-policy'}, name
        assert template.get('defaultVolumesToFsBackup') is True, name
    return document


def install_parts(text):
    assert text.count(MARKER) == 1, 'exactly one 08-4 Velero values heredoc required'
    prefix, rest = text.split(MARKER, 1)
    body, suffix = rest.split('\nEOF\n', 1)
    return prefix, body, suffix


def check(root):
    app = yaml.safe_load((root / CHART).read_text())
    _, body, _ = install_parts((root / INSTALL).read_text())
    chart_policy = check_values(app['spec']['source']['helm']['valuesObject'],
                                app['spec']['destination']['namespace'])
    install_policy = check_values(yaml.safe_load(body), 'storage')
    assert chart_policy == install_policy, 'GitOps and 08-4 policy documents must be identical'


def mutate(values, mutation):
    policy = next(obj for obj in values['extraObjects']
                  if obj['metadata']['name'] == 'velero-volume-policy')
    key = next(iter(policy['data']))
    document = yaml.safe_load(policy['data'][key])
    if mutation == 'action fs-backup':
        document['volumePolicies'][0]['action']['type'] = 'fs-backup'
    elif mutation == 'includeExcludePolicy':
        document['includeExcludePolicy'] = {'includedResources': ['pods']}
    elif mutation == 'second volumeType':
        document['volumePolicies'][0]['conditions']['volumeTypes'].append('persistentVolumeClaim')
    elif mutation == 'ConfigMap name':
        policy['metadata']['name'] = 'wrong-volume-policy'
    elif mutation == 'server fs-backup false':
        values['configuration']['defaultVolumesToFsBackup'] = False
    elif mutation.startswith('resourcePolicy removed: '):
        del values['schedules'][mutation.split(': ', 1)[1]]['template']['resourcePolicy']
    elif mutation.startswith('schedule fs-backup false: '):
        values['schedules'][mutation.split(': ', 1)[1]]['template']['defaultVolumesToFsBackup'] = False
    elif mutation == 'document parity':
        policy['data'][key] += '# semantic match but document drift\n'
        return
    else:
        raise ValueError(f'unknown mutation: {mutation}')
    if mutation in {'action fs-backup', 'includeExcludePolicy', 'second volumeType'}:
        policy['data'][key] = yaml.safe_dump(document, sort_keys=False)


def mutation_verify(root):
    sources = {path: (root / path).read_text() for path in (CHART, INSTALL)}
    app = yaml.safe_load(sources[CHART])
    prefix, body, suffix = install_parts(sources[INSTALL])
    install = yaml.safe_load(body)
    mutations = ['action fs-backup', 'includeExcludePolicy', 'second volumeType',
                 'ConfigMap name', 'server fs-backup false', 'document parity']
    for name in install['schedules']:
        mutations.extend([f'resourcePolicy removed: {name}', f'schedule fs-backup false: {name}'])
    rejected = 0
    # D1: mutate real files in a disposable tree so extraction is exercised too.
    # Cost: two small file copies per case; escape hatch: extend fixtures if paths change.
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp)
        for path in sources:
            (fixture / path).parent.mkdir(parents=True, exist_ok=True)
        for path in sources:
            for mutation in mutations:
                for original, text in sources.items():
                    (fixture / original).write_text(text)
                if path == CHART:
                    changed = copy.deepcopy(app)
                    mutate(changed['spec']['source']['helm']['valuesObject'], mutation)
                    text = yaml.safe_dump(changed, sort_keys=False)
                else:
                    changed = copy.deepcopy(install)
                    mutate(changed, mutation)
                    text = prefix + MARKER + yaml.safe_dump(changed, sort_keys=False) + 'EOF\n' + suffix
                (fixture / path).write_text(text)
                try:
                    check(fixture)
                except ERRORS:
                    rejected += 1
                    print(f'REJECTED: {path.name}: {mutation}')
                else:
                    raise AssertionError(f'mutation survived: {path}: {mutation}')
        for path, text in sources.items():
            (fixture / path).write_text(text)
        check(fixture)
    print(f'PASS: mutations rejected {rejected}/{2 * len(mutations)}; clean fixture accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--mutation-verify', action='store_true')
    args = parser.parse_args()
    try:
        check(args.root)
        if args.mutation_verify:
            mutation_verify(args.root)
    except ERRORS as exc:
        print(f'FAIL: Velero volume policy: {exc}', file=sys.stderr)
        return 1
    print('PASS: identical emptyDir-only policies; GitOps + 08-4 install, 10 schedule references')
    return 0


if __name__ == '__main__':
    sys.exit(main())
