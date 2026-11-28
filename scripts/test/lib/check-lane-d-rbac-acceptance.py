#!/usr/bin/env python3
"""R424: Lane D six exact RBAC acceptances; role and check sets must not drift."""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
# D-RBAC-LANE-D: exact role/check matches are accepted because measured rules grant
# controller Secret or wildcard capabilities; cost is retaining that cluster-wide reach.
# Escape hatch: remove a rule only after the pinned namespace mode renders and live
# reconciliation proves platform resources remain functional.
TARGETS = {
    'trivy-operator': {'KSV041'},
    'apisix-ingress-controller-clusterrole': {'KSV041'},
        'cnpg-cloudnative-pg': {'KSV041'},
                'nfs-external-provisioner-role': {'KSV041'},
    'prometheus-stack-kube-prom-operator': {'KSV041', 'KSV045'},
    'istiod-clusterrole-istio-system': {'KSV041', 'KSV046', 'KSV049', 'KSV114'},
}

def check(texts):
    errors=[]
    data=json.loads(texts['data'])
    by_name={r['name']:r for r in data['rbac'] if r['name'] in TARGETS}
    for name, checks in TARGETS.items():
        rule=by_name.get(name)
        if rule is None:
            errors.append(f'missing exact ClusterRole rule {name}')
            continue
        if (rule.get('scope'),rule.get('namespace'),rule.get('kind')) != ('cluster','','ClusterRole'):
            errors.append(f'{name}: wrong scope/kind')
        if set(rule.get('checks',[])) != checks:
            errors.append(f'{name}: check set differs')
        if rule.get('class') != 'accept' or rule.get('reviewedAt') != '2026-11-28':
            errors.append(f'{name}: acceptance metadata differs')
        key=rule.get('reasonKey','')
        if texts['i18n'].count(f'"{key}"') != 2:
            errors.append(f'{name}: bilingual reason missing')
        if f'ClusterRole/{name}' not in texts['doc']:
            errors.append(f'{name}: docs row missing')
    if len(by_name) != len(TARGETS): errors.append('unexpected duplicate target role')
    if '**52 RBAC rules**' not in texts['doc']: errors.append('RBAC summary count mismatch')
    for name in ('cilium-operator','k8s-monitoring-alloy-operator','keycloakcontroller-cluster-role','keycloakrealmimportcontroller-cluster-role'):
        if any(r['name']==name for r in data['rbac']): errors.append(f'{name}: rejected acceptance remains')
        if f'ClusterRole/{name}' in texts['doc']: errors.append(f'{name}: rejected docs row remains')
        reason_keys={
            'cilium-operator':'compliance.acceptedReason.ciliumOperatorClusterSecrets',
            'k8s-monitoring-alloy-operator':'compliance.acceptedReason.alloyOperatorClusterSecrets',
            'keycloakcontroller-cluster-role':'compliance.acceptedReason.keycloakOperatorClusterSecrets',
            'keycloakrealmimportcontroller-cluster-role':'compliance.acceptedReason.keycloakRealmImportClusterSecrets',
        }
        if reason_keys[name] in texts['i18n']: errors.append(f'{name}: rejected bilingual reason remains')
    if 'R424' not in texts['reg'] or 'check-lane-d-rbac-acceptance.py --mutation-verify' not in texts['reg']:
        errors.append('R424 registration missing')
    return errors

def main():
    texts={'data':(ROOT/'portal/src/data/compliance-acceptance-rules.json').read_text(),'i18n':(ROOT/'portal/src/lib/i18n.ts').read_text(),'doc':(ROOT/'docs/common/compliance-hardening.md').read_text(),'reg':(ROOT/'scripts/test/regression-check-kakao.sh').read_text()}
    errors=check(texts)
    if errors:
        print('\n'.join(errors),file=sys.stderr); return 1
    mutations=[]
    for name in TARGETS:
        changed=copy.deepcopy(json.loads(texts['data']))
        changed['rbac']=[r for r in changed['rbac'] if r['name']!=name]
        mutations.append((f'remove {name}',changed))
        changed=copy.deepcopy(json.loads(texts['data']))
        next(r for r in changed['rbac'] if r['name']==name)['checks'].append('KSV056')
        mutations.append((f'add check {name}',changed))
    if '--mutation-verify' in sys.argv:
        for label,changed in mutations:
            if not check(dict(texts,data=json.dumps(changed))):
                print(f'FAIL: mutation not rejected: {label}',file=sys.stderr); return 1
        print(f'PASS: R424 lane D six exact RBAC acceptances ({len(mutations)} mutations rejected)')
    else: print('PASS: R424 lane D six exact RBAC acceptances')
    return 0
if __name__=='__main__': raise SystemExit(main())
