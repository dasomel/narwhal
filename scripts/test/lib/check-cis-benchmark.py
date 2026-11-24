#!/usr/bin/env python3
"""R401: CIS evidence delivery/security contract, with destructive in-memory mutations."""
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def check(docs, app, bootstrap, recorder):
  assert app['spec']['source']['directory']['include'] == 'cis-benchmark.yaml'
  assert app['spec']['source']['path'] == 'resources'
  assert app['spec']['syncPolicy']['automated'] == {'prune': True, 'selfHeal': True}
  assert 'RespectIgnoreDifferences=true' in app['spec']['syncPolicy']['syncOptions']
  assert 'kubectl apply -f "${CIS_MANIFEST}"' in bootstrap and 'cis-benchmark.yaml' in bootstrap
  assert '--nodes "${CIS_NODES_FILE}" --output-root "$PWD"' in bootstrap
  assert bootstrap.index('--nodes') < bootstrap.index('git push')
  assert 'WARN: CIS generation failed' in bootstrap and 'WARN: CIS resource seed failed' in bootstrap
  jobs = [d for d in docs if d['kind'] == 'CronJob']
  roles = {d['metadata']['name']: d for d in docs if d['kind'] == 'Role'}
  sas = {d['metadata']['name']: d for d in docs if d['kind'] == 'ServiceAccount'}
  cms = {d['metadata']['name']: d for d in docs if d['kind'] == 'ConfigMap'}
  assert jobs and len(roles) == len(jobs) == len(sas)
  hosts = set()
  for job in jobs:
    assert job['metadata']['namespace'] == 'security-system'
    assert job['spec']['concurrencyPolicy'] == 'Forbid'
    assert job['spec']['schedule'] == '17 2 * * *'
    js = job['spec']['jobTemplate']['spec']
    assert js['backoffLimit'] == 0 and js['activeDeadlineSeconds'] == 900
    pod = js['template']['spec']
    terms = pod['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    host = terms[0]['matchFields'][0]['values'][0]
    assert all(t['matchFields'] == [{'key': 'metadata.name', 'operator': 'In', 'values': [host]}] for t in terms)
    assert host not in hosts
    hosts.add(host)
    assert pod['automountServiceAccountToken'] is False and pod['hostPID'] is True
    assert pod['restartPolicy'] == 'Never'
    begin, bench = pod['initContainers']
    assert begin['name'] == 'record-start' and begin['args'] == ['begin']
    assert bench['name'] == 'kube-bench'
    assert bench['image'] == 'docker.io/aquasec/kube-bench:v0.16.0@sha256:75506f222d1eb6ce2a751a5533bdc0a3b54c898e2e49e7751d0ee22cfb862679'
    command = bench['command'][2]
    assert '--benchmark cis-1.23' in command and '--json > /work/raw.json' in command
    affinity = pod['affinity']['nodeAffinity']['requiredDuringSchedulingIgnoredDuringExecution']['nodeSelectorTerms']
    cp = any(e['operator'] == 'Exists' for t in affinity for e in t['matchExpressions'])
    assert '--targets ' + ('master,etcd,controlplane,node,policies' if cp else 'node') + ' ' in command
    assert '/work/exit-code' in command
    assert bench['securityContext'] == {'runAsUser': 0, 'allowPrivilegeEscalation': False,
      'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL'], 'add': ['DAC_READ_SEARCH']}}
    mounts = {v['name']: v for v in bench['volumeMounts']}
    assert 'auth' not in mounts and 'tmp' not in mounts
    hostpaths = [v for v in pod['volumes'] if 'hostPath' in v]
    expected_paths = {'/var/lib/kubelet', '/etc/systemd', '/lib/systemd', '/etc/kubernetes', '/etc/cni/net.d', '/usr/bin'}
    if cp:
      expected_paths.add('/var/lib/etcd')
    assert {v['hostPath']['path'] for v in hostpaths} == expected_paths
    assert all(mounts[v['name']]['readOnly'] is True for v in hostpaths)
    sa = pod['serviceAccountName']
    assert sas[sa]['automountServiceAccountToken'] is False
    rules = roles[sa]['rules']
    assert len(rules) == 1
    rule = rules[0]
    assert rule['apiGroups'] == [''] and rule['resources'] == ['configmaps']
    assert set(rule['verbs']) == {'get', 'update', 'patch'}
    assert len(rule['resourceNames']) == 1
    record = rule['resourceNames'][0]
    assert record in cms
    assert app['spec']['ignoreDifferences'] == [{'group': '', 'kind': 'ConfigMap',
      'namespace': 'security-system', 'jqPathExpressions': [
        'select(.metadata.labels["narwhal.io/compliance-tool"] == "kube-bench") | .data']}]
    main = pod['containers'][0]
    assert main['image'] == begin['image'] == 'docker.io/alpine/k8s:1.31.4@sha256:9c4976d47656d78cf53a92b0203fc54ac45eae18a2b45001ac221c27da4c8036'
    for container in (begin, main):
      sc = container['securityContext']
      assert sc['runAsNonRoot'] is True and type(sc['runAsUser']) is int and sc['runAsUser'] > 0
      assert sc['readOnlyRootFilesystem'] is True and sc['allowPrivilegeEscalation'] is False
      assert sc['capabilities'] == {'drop': ['ALL']}
      assert any(m['name'] == 'auth' for m in container['volumeMounts'])
      assert any(m['name'] == 'tmp' and m['mountPath'] == '/tmp' for m in container['volumeMounts'])
      assert not any(m['name'] in mounts and m['name'] != 'work' for m in container['volumeMounts'])
      assert any(e == {'name': 'NODE_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'spec.nodeName'}}}
                 for e in container['env'])
    assert all(e['key'] != 'node-role.kubernetes.io/master' for t in terms for e in t['matchExpressions'])
  assert '/work/meta.json' not in recorder and '/work/record.json' not in recorder
  assert '/tmp/run-meta.json' in recorder and 'scanner version mismatch' in recorder
  assert 'status:"error",reason:"scan not completed"' in recorder
  assert 'status:"error",reason:"scanner failed, invalid output or record too large"' in recorder
  assert 'publish\nexit 1' in recorder
  assert '"$(cat /work/exit-code 2>/dev/null || echo missing)" == 0' in recorder


def main():
  spec = importlib.util.spec_from_file_location('renderer', ROOT / 'scripts/ops/render-cis-benchmark.py')
  renderer = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(renderer)
  app = yaml.safe_load((ROOT / 'gitops/charts/narwhal-apps/templates/cis-benchmark.yaml').read_text())
  bootstrap = (ROOT / 'scripts/cluster/14-gitops-bootstrap.sh').read_text()
  recorder = (ROOT / 'scripts/ops/cis-benchmark-recorder.sh').read_text()
  subprocess.run(['python3', 'scripts/ops/render-cis-benchmark.py', '--check'], cwd=ROOT, check=True)
  subprocess.run(['python3', 'scripts/test/lib/test-cis-benchmark-record.py'], cwd=ROOT, check=True)
  subprocess.run(['python3', 'scripts/test/lib/test-cis-benchmark-recorder.py'], cwd=ROOT, check=True)
  single = [{'name': 'narwhal-master-1', 'roles': ['control-plane']}]
  ha = [{'name': f'narwhal-master-{i}', 'roles': ['control-plane']} for i in range(1, 4)]
  ha += [{'name': f'narwhal-worker-{i}', 'roles': ['worker']} for i in range(1, 4)]
  for nodes in (single, ha, [{'name': 'renamed-control', 'roles': ['control-plane']},
                           {'name': 'renamed-worker', 'roles': ['worker']}]):
    docs = list(yaml.safe_load_all(renderer.render(nodes)))
    check(docs, app, bootstrap, recorder)
    assert len([d for d in docs if d['kind'] == 'CronJob']) == len(nodes)
    with tempfile.TemporaryDirectory() as directory:
      base = Path(directory)
      measured = base / 'nodes.json'
      measured.write_text(json.dumps({'items': [{'metadata': {'name': n['name'], 'labels':
        {'node-role.kubernetes.io/control-plane': ''} if n['roles'] == ['control-plane'] else {}}}
        for n in nodes]}))
      assert renderer.imported(measured) == sorted(nodes, key=lambda n: n['name'])
      (base / 'resources').mkdir()
      (base / 'charts/narwhal-apps/templates').mkdir(parents=True)
      command = ['python3', str(ROOT / 'scripts/ops/render-cis-benchmark.py'),
                 '--nodes', str(measured), '--output-root', str(base)]
      subprocess.run(command, check=True, capture_output=True)
      before = (base / 'resources/cis-benchmark.yaml').read_text()
      subprocess.run(command, check=True, capture_output=True)
      assert (base / 'resources/cis-benchmark.yaml').read_text() == before
      check(list(yaml.safe_load_all(before)), app, bootstrap, recorder)
      missing = subprocess.run(['python3', str(ROOT / 'scripts/ops/render-cis-benchmark.py'),
        '--check-nodes', str(measured)], capture_output=True, text=True)
      assert missing.returncode == 1 and all(n['name'] in missing.stdout for n in nodes)
      records = base / 'records.json'
      coverage = ['python3', str(ROOT / 'scripts/ops/render-cis-benchmark.py'),
        '--check-nodes', str(measured), '--records', str(records)]
      for status, hours, expected in (('complete', 0, 0), ('error', 0, 1), ('complete', 27, 1)):
        records.write_text(json.dumps({'items': [{'data': {'record.json': json.dumps({
          'node': n['name'], 'status': status, 'finishedAt':
          (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()})}} for n in nodes]}))
        assert subprocess.run(coverage, capture_output=True).returncode == expected
  # Execute only the isolated CIS bootstrap blocks with failing clients; Phase 2 must continue.
  generation = bootstrap[bootstrap.index('if ! CIS_NODES_FILE='):bootstrap.index('# D-FALCO-045')]
  seed = bootstrap[bootstrap.index('if ! kubectl apply -f "${CIS_MANIFEST}"'):bootstrap.index('# D-PSA-LABELS-14')]
  for fail in ('kubectl', 'python3'):
    clients = 'kubectl() { return ' + ('1' if fail == 'kubectl' else '0') + '; }; python3() { return 1; }; '
    result = subprocess.run(['bash', '-c', 'set -euo pipefail; ' + clients + generation + seed +
      '\nprintf "CONTINUED\\n"'], text=True, capture_output=True)
    assert result.returncode == 0 and 'CONTINUED' in result.stdout and 'WARN: CIS generation failed' in result.stderr
  docs = list(yaml.safe_load_all(renderer.render(single)))
  assert any(d['metadata']['name'] == 'cis-bench-974178c0082e' for d in docs)
  mutations = []
  if '--mutation-verify' in sys.argv:
    def mutate_doc(kind, fn):
      altered = copy.deepcopy(docs)
      fn(next(d for d in altered if d['kind'] == kind))
      mutations.append((altered, app, bootstrap, recorder))
    def mutate_pod(fn):
      mutate_doc('CronJob', lambda j: fn(j['spec']['jobTemplate']['spec']['template']['spec']))
    mutate_doc('CronJob', lambda j: j['spec'].update(schedule='* * * * *'))
    mutate_doc('Role', lambda r: r['rules'][0].update(verbs=['get', 'create', 'patch']))
    mutate_doc('Role', lambda r: r['rules'][0].pop('resourceNames'))
    mutate_doc('ServiceAccount', lambda s: s.update(automountServiceAccountToken=True))
    mutate_pod(lambda p: p.update(automountServiceAccountToken=True))
    mutate_pod(lambda p: p['initContainers'][1].update(image='docker.io/aquasec/kube-bench:v0.16.0'))
    mutate_pod(lambda p: p['containers'][0].update(image='docker.io/alpine/k8s:1.31.4'))
    mutate_pod(lambda p: p['initContainers'][1]['command'].__setitem__(2, 'kube-bench run --json'))
    mutate_pod(lambda p: p['initContainers'][1]['securityContext']['capabilities'].update(add=['SYS_ADMIN']))
    mutate_pod(lambda p: p['initContainers'][1]['securityContext']['capabilities'].update(drop=[]))
    mutate_pod(lambda p: p['initContainers'][1]['volumeMounts'][0].update(readOnly=False))
    mutate_pod(lambda p: p['initContainers'][1]['volumeMounts'].append({'name': 'auth', 'mountPath': '/auth'}))
    mutate_pod(lambda p: p['initContainers'][1]['volumeMounts'].append({'name': 'tmp', 'mountPath': '/metadata'}))
    for group, index in (('containers', 0), ('initContainers', 0)):
      for field, value in (('readOnlyRootFilesystem', False), ('runAsUser', 0),
                           ('runAsNonRoot', False), ('allowPrivilegeEscalation', True),
                           ('capabilities', {'drop': []})):
        mutate_pod(lambda p, g=group, i=index, f=field, v=value: p[g][i]['securityContext'].update({f: v}))
    mutate_pod(lambda p: p.update(hostPID=False))
    mutate_pod(lambda p: p['initContainers'].reverse())
    mutations += [(docs, app, '', recorder), (docs, app, bootstrap, recorder.replace('status:"error"', 'status:"complete"'))]
    for field in ('ignoreDifferences', 'source'):
      a = copy.deepcopy(app)
      a['spec'][field] = [] if field == 'ignoreDifferences' else {'path': 'absent'}
      mutations.append((docs, a, bootstrap, recorder))
    a = copy.deepcopy(app)
    a['spec']['syncPolicy']['syncOptions'].remove('RespectIgnoreDifferences=true')
    mutations.append((docs, a, bootstrap, recorder))
    for i, mutation in enumerate(mutations):
      try:
        check(*mutation)
      except (AssertionError, KeyError, ValueError):
        continue
      raise AssertionError(f'mutation {i} escaped')
  print(f'PASS: R401 CIS security/delivery + single/HA 3+3/renamed topology; {len(mutations)} mutations rejected')


if __name__ == '__main__':
  main()
