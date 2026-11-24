#!/usr/bin/env python3
"""Render GitOps-owned per-node Jobs/RBAC; no cluster writes or discovery RBAC.

On node changes: kubectl get nodes -o json > /tmp/nodes.json
  python3 scripts/ops/render-cis-benchmark.py --import-nodes /tmp/nodes.json
  python3 scripts/ops/render-cis-benchmark.py
Review inventory + generated diff together, then deliver through GitOps.
"""
import argparse
import hashlib
import json
from pathlib import Path

try:
  import yaml
except ImportError:
  yaml = None

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / 'scripts/ops'
OUTPUT = ROOT / 'gitops/resources/cis-benchmark.yaml'
BENCH = 'docker.io/aquasec/kube-bench:v0.16.0@sha256:75506f222d1eb6ce2a751a5533bdc0a3b54c898e2e49e7751d0ee22cfb862679'
RECORDER = 'docker.io/alpine/k8s:1.31.4@sha256:9c4976d47656d78cf53a92b0203fc54ac45eae18a2b45001ac221c27da4c8036'
HOSTS = {'var-lib-etcd': '/var/lib/etcd', 'var-lib-kubelet': '/var/lib/kubelet',
         'etc-systemd': '/etc/systemd', 'lib-systemd': '/lib/systemd',
         'etc-kubernetes': '/etc/kubernetes', 'etc-cni': '/etc/cni/net.d', 'usr-bin': '/usr/bin'}


class Dumper(yaml.SafeDumper if yaml else object):
  def increase_indent(self, flow=False, indentless=False):
    return super().increase_indent(flow, False)


def string(dumper, value):
  return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|' if '\n' in value else ('"' if value == 'cis-benchmark.yaml' else None))


if yaml:
  Dumper.add_representer(str, string)


def resource(kind, name, **fields):
  api = {'CronJob': 'batch/v1', 'Role': 'rbac.authorization.k8s.io/v1',
         'RoleBinding': 'rbac.authorization.k8s.io/v1'}.get(kind, 'v1')
  return {'apiVersion': api, 'kind': kind,
          'metadata': {'name': name, 'namespace': 'security-system'}, **fields}


def imported(path):
  nodes = json.loads(Path(path).read_text())['items']
  return sorted([{'name': n['metadata']['name'], 'roles':
                 ['control-plane'] if any(
                   label in n['metadata'].get('labels', {}) for label in
                   ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master'))
                 else ['worker']} for n in nodes], key=lambda n: n['name'])


def render(nodes):
  if not nodes or len({n['name'] for n in nodes}) != len(nodes):
    raise ValueError('node inventory empty or duplicated')
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
        'kube-bench run --benchmark cis-1.23 --targets ' + targets +
        ' --json > /work/raw.json 2> /work/stderr; rc=$?; printf "%s\\n" "$rc" > /work/exit-code; exit 0'],
      'securityContext': {'runAsUser': 0, 'allowPrivilegeEscalation': False,
        'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL'], 'add': ['DAC_READ_SEARCH']}},
      'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
      'volumeMounts': [{'name': k, 'mountPath': '/usr/local/mount-from-host/bin' if k == 'usr-bin' else v,
                       'readOnly': True} for k, v in hosts.items()] + [{'name': 'work', 'mountPath': '/work'}]}
    # D1: one explicit node name per CronJob guarantees coverage unlike role affinity.
    # Cost: GitOps inventory update on topology changes; escape hatch: regenerate
    # from `kubectl get nodes -o json` with --import-nodes, then review the diff.
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
    role_labels = ['node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master']
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
  return '# Generated by scripts/ops/render-cis-benchmark.py; edit sources, then regenerate.\n' + yaml.dump_all(
    docs, Dumper=Dumper, sort_keys=False, width=160, explicit_start=True)


def render_app(nodes):
  names = ['cis-bench-' + hashlib.sha256(n['name'].encode()).hexdigest()[:12] + '-latest' for n in nodes]
  app = {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
    'metadata': {'name': 'cis-benchmark', 'namespace': 'devtools',
      'annotations': {'argocd.argoproj.io/sync-wave': '6'},
      'finalizers': ['resources-finalizer.argocd.argoproj.io']},
    'spec': {'project': 'platform',
      'source': {'repoURL': '{{ .Values.repoURL }}', 'targetRevision': 'HEAD', 'path': 'resources',
                 'directory': {'include': 'cis-benchmark.yaml'}},
      'destination': {'server': 'https://kubernetes.default.svc', 'namespace': 'security-system'},
      'ignoreDifferences': [{'group': '', 'kind': 'ConfigMap', 'name': name,
                            'namespace': 'security-system', 'jsonPointers': ['/data']} for name in names],
      'syncPolicy': {'automated': {'prune': True, 'selfHeal': True},
                     'syncOptions': ['ServerSideApply=true', 'RespectIgnoreDifferences=true']}}}
  return ('# Generated by scripts/ops/render-cis-benchmark.py.\n'
          '# D2: /data only, named per-node records; same convention as notification-channels.\n'
          '# Cost: evidence data drift intentional; escape hatch: remove ignore + RespectIgnoreDifferences.\n'
          + yaml.dump(app, Dumper=Dumper, sort_keys=False, width=160))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--check', action='store_true')
  parser.add_argument('--import-nodes')
  parser.add_argument('--check-nodes', help='compare measured NodeList with GitOps inventory')
  args = parser.parse_args()
  inventory = OPS / 'cis-benchmark-nodes.json'
  if args.import_nodes:
    inventory.write_text(json.dumps(imported(args.import_nodes), indent=2) + '\n')
  nodes = json.loads(inventory.read_text())
  if args.check_nodes:
    assert imported(args.check_nodes) == sorted(nodes, key=lambda n: n['name']), 'node inventory drift'
  if args.check_nodes and not args.check:
    print('PASS: measured NodeList matches GitOps inventory')
    return
  output = render(nodes)
  app_path = ROOT / 'gitops/charts/narwhal-apps/templates/cis-benchmark.yaml'
  app = render_app(nodes)
  if args.check:
    assert OUTPUT.read_text() == output, 'generated CIS resource drift; regenerate'
    assert app_path.read_text() == app, 'generated CIS Application drift; regenerate'
  else:
    OUTPUT.write_text(output)
    app_path.write_text(app)
  print('PASS: CIS node inventory and generated resources')


if __name__ == '__main__':
  main()
