#!/usr/bin/env python3
"""R350: DB direct-host migration, operator budget and ready-endpoint gate."""
import copy
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FQDN = 'narwhal-db-rw.database.svc.cluster.local'
CNPG = 'scripts/cluster/07-cnpg.sh'
REGISTRY = 'scripts/cluster/08-5-registry.sh'
GITEA = 'scripts/cluster/12-gitea.sh'
HARBOR = 'gitops/charts/narwhal-apps/templates/harbor.yaml'
DB = 'gitops/resources/narwhal-db.yaml'
VERIFY = 'scripts/test/verify-cluster.sh'
POLICY = 'gitops/resources/database-ingress-policy.yaml'
EGRESS = 'gitops/resources/network-policies.yaml'
PATHS = (CNPG, REGISTRY, GITEA, HARBOR, DB, VERIFY, POLICY, EGRESS)
BUDGET = {'requests.cpu': '50m', 'requests.memory': '128Mi',
          'limits.cpu': '300m', 'limits.memory': '256Mi'}


def validate(files):
  for path in (CNPG, REGISTRY, DB):
    assert not re.search(r'^\s*type:\s*ExternalName\s*$', files[path], re.M), path
  assert f'database.external.host={FQDN} \\\n' in files[REGISTRY]
  assert f'gitea.config.database.HOST={FQDN}:5432 \\\n' in files[GITEA]
  values = yaml.safe_load(files[HARBOR].replace('{{ .Values.baseDomain }}', 'example.test'))
  assert values['spec']['source']['helm']['valuesObject']['database']['external']['host'] == FQDN
  cluster = next(d for d in yaml.safe_load_all(files[DB]) if d['kind'] == 'Cluster')
  assert cluster['metadata']['name'] == 'narwhal-db'
  command = files[CNPG].split('retry helm upgrade --install cnpg ', 1)[1].split('\n\n', 1)[0]
  for key, value in BUDGET.items():
    assert f'--set resources.{key}={value} \\\n' in command, key
  policy = next(yaml.safe_load_all(files[POLICY]))
  for namespace, labels in [('iam', {'app.kubernetes.io/instance': 'keycloak'}),
                            ('devtools', {'app': 'harbor'}),
                            ('devtools', {'app.kubernetes.io/name': 'gitea'})]:
    assert any(peer.get('namespaceSelector', {}).get('matchLabels') ==
               {'kubernetes.io/metadata.name': namespace} and
               peer.get('podSelector', {}).get('matchLabels') == labels and
               {5432, 15008} <= {p['port'] for p in rule['ports']}
               for rule in policy['spec']['ingress'] for peer in rule['from'])
  policies = list(yaml.safe_load_all(files[EGRESS]))
  for ns in ('iam', 'devtools'):
    assert any(p['metadata']['namespace'] == ns and
               p['spec'].get('egress') == [{}] for p in policies)
  assert 'ExternalName svc' not in files[VERIFY]
  block = files[VERIFY].split('  # Consumers connect directly', 1)[1].split('  echo ""', 1)[0].split('\n', 1)[1]
  # Execute the actual verification gate; a Service without ready addresses must fail.
  for kind, endpoints, expected in [('ClusterIP', '10.0.0.2', 'PASS'),
                                    ('ClusterIP', '', 'FAIL'),
                                    ('ExternalName', '10.0.0.2', 'FAIL'),
                                    ('', '', 'FAIL')]:
    stub = f'''kubectl() {{
      case "$*" in
        'get svc narwhal-db-rw -n database -o jsonpath='*) printf '%s' '{kind}' ;;
        'get endpointslice -n database -l kubernetes.io/service-name=narwhal-db-rw -o jsonpath='*) printf '%s' '{endpoints}' ;;
        *) return 1 ;;
      esac
    }}
    pass() {{ echo PASS; }}
    fail() {{ echo FAIL; }}
    '''
    result = subprocess.run(['bash', '-eu', '-c', stub + block], text=True,
                            capture_output=True, check=True)
    assert result.stdout.strip() == expected, (kind, endpoints, result.stdout)


def main():
  files = {p: (ROOT / p).read_text() for p in PATHS}
  validate(files)
  print('R350 PASS: direct hosts, no alias creation, budget, network path; 4 endpoint-gate cases')
  if '--mutation-verify' in sys.argv:
    mutations = [(REGISTRY, FQDN, 'harbor-db-rw'),
                 (GITEA, FQDN, 'gitea-db-rw'),
                 (HARBOR, FQDN, 'harbor-db-rw'),
                 (DB, 'kind: Cluster', 'type: ExternalName\nkind: Cluster'),
                 (CNPG, '  --set resources.requests.cpu=50m \\\n', ''),
                 (POLICY, 'app: harbor', 'app: wrong'),
                 (EGRESS, '  egress:\n    - {}', '  egress: []'),
                 (VERIFY, '&& [ -n "${DB_ENDPOINTS}" ]', '')]
    for path, old, new in mutations:
      changed = copy.copy(files)
      assert old in changed[path], (path, old)
      changed[path] = changed[path].replace(old, new)
      try:
        validate(changed)
      except AssertionError:
        continue
      raise AssertionError(f'mutation escaped: {path}: {old}')
    print(f'R350 PASS: {len(mutations)} mutations rejected')


if __name__ == '__main__':
  main()
