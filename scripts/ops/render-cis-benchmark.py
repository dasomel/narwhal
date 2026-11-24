#!/usr/bin/env python3
"""Render GitOps-owned per-node Jobs/RBAC; no cluster writes or discovery RBAC.

Bootstrap renders from a measured NodeList into the staged Gitea tree.
No committed cluster inventory; without --nodes only the shared script template is rendered.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / 'scripts/ops'
BENCH = 'docker.io/aquasec/kube-bench:v0.16.0@sha256:75506f222d1eb6ce2a751a5533bdc0a3b54c898e2e49e7751d0ee22cfb862679'
RECORDER = 'docker.io/alpine/k8s:1.31.4@sha256:9c4976d47656d78cf53a92b0203fc54ac45eae18a2b45001ac221c27da4c8036'
HOSTS = {'var-lib-etcd': '/var/lib/etcd', 'var-lib-kubelet': '/var/lib/kubelet',
         'etc-systemd': '/etc/systemd', 'lib-systemd': '/lib/systemd',
         'etc-kubernetes': '/etc/kubernetes', 'etc-cni': '/etc/cni/net.d', 'usr-bin': '/usr/bin'}


def emit(value, indent=0):
  """D6: limited deterministic YAML, stdlib only on VMs without PyYAML.

  Cost: only JSON-shaped values; escape hatch: extend with round-trip tests.
  """
  pad = ' ' * indent
  if isinstance(value, (dict, list)) and not value:
    return pad + ('{}' if isinstance(value, dict) else '[]') + '\n'
  if isinstance(value, dict):
    return ''.join(pad + scalar(k, key=True) + ':' + field(v, indent) for k, v in value.items())
  if isinstance(value, list):
    return ''.join(pad + '-' + field(v, indent) for v in value)
  raise TypeError('expected mapping or sequence')


def scalar(value, key=False):
  """D10: plain strings only when YAML 1.1/1.2 agree on their type.

  Cost: conservative quoting for ambiguous tokens; escape hatch: JSON quoting.
  """
  if not isinstance(value, str):
    if key:
      raise TypeError('mapping keys must be strings')
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
  if key and '\n' in value:
    raise ValueError('multiline mapping keys are unsupported')
  ambiguous = (not value or value != value.strip() or
    value.lower() in {'true', 'false', 'null', 'yes', 'no', 'on', 'off', '~', 'y', 'n'} or
    value[0] in '-?:,[]{}#&*!|>\'"%@`' or
    re.fullmatch(r'[+]?(?:[0-9][0-9_]*(?::[0-9_]+)*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9_]+(?:[eE][+-]?[0-9]+)?|0[xX][0-9a-fA-F_]+|0[oObB][0-9_]+|\.(?:inf|nan))', value, re.I) or
    re.match(r'^[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:[Tt ]|$)', value) or ': ' in value or value.endswith(':') or
    ' #' in value or any(ord(c) < 32 or ord(c) == 127 for c in value) or
    any(c in value for c in '\u0085\u2028\u2029'))
  return json.dumps(value, ensure_ascii=True) if ambiguous else value


def field(value, indent):
  if isinstance(value, (dict, list)) and value:
    return '\n' + emit(value, indent + 2)
  if isinstance(value, str) and '\n' in value:
    if any((ord(c) < 32 and c != '\n') or c in '\x7f\u0085\u2028\u2029' for c in value):
      raise ValueError('unsupported control character in block scalar')
    # Explicit indentation preserves leading spaces; keep chomping preserves all LFs.
    trailing = len(value) - len(value.rstrip('\n'))
    style = '|2' + ('-' if trailing == 0 else '+' if trailing > 1 or not value.strip('\n') else '')
    lines = value.split('\n')
    if trailing:
      lines.pop()
    return ' ' + style + '\n' + ''.join(
      ' ' * (indent + 2) + line + '\n' if line else '\n' for line in lines)
  return ' ' + scalar(value) + '\n'


def resource(kind, name, **fields):
  api = {'CronJob': 'batch/v1', 'Role': 'rbac.authorization.k8s.io/v1',
         'RoleBinding': 'rbac.authorization.k8s.io/v1'}.get(kind, 'v1')
  return {'apiVersion': api, 'kind': kind,
          'metadata': {'name': name, 'namespace': 'security-system',
            'annotations': {'argocd.argoproj.io/sync-options': 'Prune=false,Delete=false'}}, **fields}


def imported(path):
  nodes = json.loads(Path(path).read_text())['items']
  return sorted([{'name': n['metadata']['name'], 'roles':
                 ['control-plane'] if 'node-role.kubernetes.io/control-plane' in
                 n['metadata'].get('labels', {})
                 else ['worker']} for n in nodes], key=lambda n: n['name'])


def render(nodes):
  if len({n['name'] for n in nodes}) != len(nodes):
    raise ValueError('duplicate node names')
  docs = [resource('ConfigMap', 'cis-benchmark-scripts', data={
    'record.jq': (OPS / 'cis-benchmark-record.jq').read_text(),
    'recorder.sh': (OPS / 'cis-benchmark-recorder.sh').read_text()})]
  for node in nodes:
    name = 'cis-bench-' + hashlib.sha256(node['name'].encode()).hexdigest()[:12]
    record = name + '-latest'
    cp = 'control-plane' in node['roles']
    hosts = {k: v for k, v in HOSTS.items() if cp or k != 'var-lib-etcd'}
    if node['roles'] != (['control-plane'] if cp else ['worker']):
      raise ValueError('unsupported node roles')
    cm = resource('ConfigMap', record, data={})
    cm['metadata']['labels'] = {'narwhal.io/compliance-tool': 'kube-bench',
                                'narwhal.io/benchmark-node': node['name']}
    docs += [cm, resource('ServiceAccount', name, automountServiceAccountToken=False),
             resource('Role', name, rules=[{'apiGroups': [''], 'resources': ['configmaps'],
                      'resourceNames': [record], 'verbs': ['get', 'update', 'patch']}]),
             resource('RoleBinding', name,
                      roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name},
                      subjects=[{'kind': 'ServiceAccount', 'name': name, 'namespace': 'security-system'}])]
    env = [{'name': k, 'value': v} for k, v in {
      'NODE_ROLES': ','.join(node['roles']), 'RECORD_NAME': record, 'BENCH_IMAGE': BENCH}.items()]
    env += [{'name': 'NODE_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'spec.nodeName'}}},
            {'name': 'POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}}]
    recorder = {'name': 'recorder', 'image': RECORDER,
      'command': ['/bin/bash', '/scripts/recorder.sh'], 'env': env,
      'securityContext': {'runAsUser': 1000, 'runAsNonRoot': True,
        'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
      'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '200m', 'memory': '128Mi'}},
      'volumeMounts': [{'name': 'work', 'mountPath': '/work'}, {'name': 'tmp', 'mountPath': '/tmp'},
        {'name': 'scripts', 'mountPath': '/scripts', 'readOnly': True},
        {'name': 'auth', 'mountPath': '/auth', 'readOnly': True}]}
    begin = {**recorder, 'name': 'record-start', 'args': ['begin']}
    targets = 'master,etcd,controlplane,node,policies' if cp else 'node'
    benchmark = {'name': 'kube-bench', 'image': BENCH,
      'command': ['/bin/sh', '-c',
        'kube-bench version > /work/version 2> /work/version-stderr; kube-bench run --benchmark cis-1.23 --targets ' + targets +
        ' --json > /work/raw.json 2> /work/stderr; rc=$?; printf "%s\\n" "$rc" > /work/exit-code; exit 0'],
      'securityContext': {'runAsUser': 0, 'allowPrivilegeEscalation': False,
        'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL'], 'add': ['DAC_READ_SEARCH']}},
      'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
      'volumeMounts': [{'name': k, 'mountPath': '/usr/local/mount-from-host/bin' if k == 'usr-bin' else v,
                       'readOnly': True} for k, v in hosts.items()] + [{'name': 'work', 'mountPath': '/work'}]}
    # D1: one explicit node name per CronJob guarantees coverage unlike role affinity.
    # Cost: explicit regeneration on node changes; escape hatch: re-render from live
    # NodeList and push through GitOps; no discovery/create permission in workloads.
    spec = {'serviceAccountName': name, 'automountServiceAccountToken': False, 'hostPID': True,
      'restartPolicy': 'Never',
      'tolerations': [{'operator': 'Exists'}],
      'securityContext': {'fsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
      'initContainers': [begin, benchmark], 'containers': [recorder],
      'volumes': [{'name': k, 'hostPath': {'path': v}} for k, v in hosts.items()] + [
        {'name': 'work', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}},
        {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '4Mi'}},
        {'name': 'scripts', 'configMap': {'name': 'cis-benchmark-scripts'}},
        {'name': 'auth', 'projected': {'sources': [
          {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 3600}},
          {'configMap': {'name': 'kube-root-ca.crt', 'items': [{'key': 'ca.crt', 'path': 'ca.crt'}]}}]}}]}
    role_labels = ['node-role.kubernetes.io/control-plane']
    spec['affinity'] = {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
      'nodeSelectorTerms': ([{'matchExpressions': [{'key': label, 'operator': 'Exists'}]}
                             for label in role_labels] if cp else
                           [{'matchExpressions': [{'key': label, 'operator': 'DoesNotExist'}
                                                  for label in role_labels]}])}}}
    for term in spec['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']:
      term['matchFields'] = [{'key': 'metadata.name', 'operator': 'In', 'values': [node['name']]}]
    docs += [resource('CronJob', name, spec={'schedule': '17 2 * * *', 'timeZone': 'Etc/UTC',
      'concurrencyPolicy': 'Forbid', 'startingDeadlineSeconds': 3600,
      'successfulJobsHistoryLimit': 1, 'failedJobsHistoryLimit': 2,
      'jobTemplate': {'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 900,
        'ttlSecondsAfterFinished': 86400, 'template': {
          'metadata': {'labels': {'app.kubernetes.io/name': 'cis-benchmark', 'istio.io/dataplane-mode': 'none'}},
          'spec': spec}}}})]
  # D7: retain records and their execution identities on inventory loss/application deletion.
  # Cost: stale nodes need manual cleanup; escape hatch: documented named kubectl delete.
  return ('# Generated by scripts/ops/render-cis-benchmark.py; edit sources, then regenerate.\n'
          '# narwhal.io/generated-per-cluster: true\n'
          + '---\n'.join(emit(doc) for doc in docs))


def render_app(nodes):
  app = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
    'metadata': {'name': 'cis-benchmark', 'namespace': 'devtools',
      'annotations': {'argocd.argoproj.io/sync-wave': '6'},
      'finalizers': ['resources-finalizer.argocd.argoproj.io']},
    'spec': {'project': 'platform',
      'source': {'repoURL': '{{ .Values.repoURL }}', 'targetRevision': 'HEAD', 'path': 'resources',
                 'directory': {'include': 'cis-benchmark.yaml'}},
      'destination': {'server': 'https://kubernetes.default.svc', 'namespace': 'security-system'},
      'ignoreDifferences': [{'group': '', 'kind': 'ConfigMap', 'namespace': 'security-system',
        'jqPathExpressions': ['select(.metadata.labels["narwhal.io/compliance-tool"] == "kube-bench") | .data']}],
      'syncPolicy': {'automated': {'prune': False, 'selfHeal': True},
                     'syncOptions': ['ServerSideApply=true', 'RespectIgnoreDifferences=true']}}}
  return ('# Generated by scripts/ops/render-cis-benchmark.py.\n'
          '# D2: /data only on labeled record ConfigMaps; script data stays reconciled.\n'
          '# Cost: evidence data drift intentional; escape hatch: remove ignore + RespectIgnoreDifferences.\n'
          + emit(app))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--check', action='store_true')
  parser.add_argument('--nodes', help='measured kubectl get nodes -o json')
  parser.add_argument('--output-root', type=Path, default=ROOT / 'gitops')
  parser.add_argument('--check-nodes', help='report live nodes without a complete, recent record')
  parser.add_argument('--records', help='kubectl get configmaps -n security-system -o json')
  args = parser.parse_args()
  if args.check_nodes:
    from datetime import datetime, timezone
    nodes = imported(args.check_nodes)
    records = json.loads(Path(args.records).read_text())['items'] if args.records else []
    covered = set()
    for cm in records:
      try:
        record = json.loads(cm.get('data', {}).get('record.json', '{}'))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['finishedAt'].replace('Z', '+00:00'))).total_seconds()
        if record['status'] == 'complete' and 0 <= age <= 93600:
          covered.add(record['node'])
      except (KeyError, ValueError, TypeError):
        continue
    missing = [n['name'] for n in nodes if n['name'] not in covered]
    live = {n['name'] for n in nodes}
    stale = sorted({cm.get('metadata', {}).get('labels', {}).get('narwhal.io/benchmark-node')
                    for cm in records
                    if cm.get('metadata', {}).get('labels', {}).get('narwhal.io/compliance-tool') == 'kube-bench'} - live - {None})
    if stale:
      print('WARN: stale CIS objects for removed nodes (manual cleanup required): ' + ', '.join(stale))
    if missing:
      print('WARN: CIS nodes without a complete record within 26h: ' + ', '.join(missing))
      raise SystemExit(1)
    if stale:
      raise SystemExit(1)
    print('PASS: all live nodes have recent complete CIS records')
    return
  nodes = imported(args.nodes) if args.nodes else []
  if args.nodes and not nodes:
    raise ValueError('measured NodeList is empty')
  output = render(nodes)
  app_path = args.output_root / 'charts/narwhal-apps/templates/cis-benchmark.yaml'
  output_path = args.output_root / 'resources/cis-benchmark.yaml'
  app = render_app(nodes)
  if args.check:
    assert output_path.read_text() == output, 'generated CIS resource drift; regenerate'
    assert app_path.read_text() == app, 'generated CIS Application drift; regenerate'
  else:
    output_path.write_text(output)
    app_path.write_text(app)
  print(f'PASS: CIS templates/resources rendered (nodes={len(nodes)})')


if __name__ == '__main__':
  main()
