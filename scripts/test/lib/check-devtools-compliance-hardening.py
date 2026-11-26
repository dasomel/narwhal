#!/usr/bin/env python3
"""R364: DevTools compliance hardening (Lane 7b).

Validates:
- headlamp: readOnlyRootFilesystem on main & ca-bundle, ca-bundle resources, bootstrap parity in 08-3-security.sh
- kubernetes-dashboard: kong.resources requests/limits
- chaos-mesh: chaosDaemon & controllerManager cpu limits
- harbor: redis.internal, portal, and nginx resources requests/limits
- gitea: main container resources and initContainers limit flags in 12-gitea.sh
- argocd: repo-server, redis, dex-server initContainer resources in 13-argocd.sh
"""
import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

HEADLAMP = 'gitops/charts/narwhal-apps/templates/headlamp.yaml'
HEADLAMP_SCRIPT = 'scripts/cluster/08-3-security.sh'
K8S_DASHBOARD = 'gitops/charts/narwhal-apps/templates/kubernetes-dashboard.yaml'
CHAOS_MESH = 'gitops/charts/narwhal-apps/templates/chaos-mesh.yaml'
HARBOR = 'gitops/charts/narwhal-apps/templates/harbor.yaml'
GITEA = 'scripts/cluster/12-gitea.sh'
ARGOCD = 'scripts/cluster/13-argocd.sh'

PATHS = (HEADLAMP, HEADLAMP_SCRIPT, K8S_DASHBOARD, CHAOS_MESH, HARBOR, GITEA, ARGOCD)


def parse_helm_app(content: str) -> dict:
  cleaned = re.sub(r'\{\{.*?\}\}', 'placeholder', content)
  return yaml.safe_load(cleaned)


def validate(files: dict[str, str]) -> None:
  # 1. Headlamp
  hl_app = parse_helm_app(files[HEADLAMP])
  hl_vals = hl_app['spec']['source']['helm']['valuesObject']
  assert hl_vals.get('securityContext', {}).get('readOnlyRootFilesystem') is True, (
      'headlamp missing securityContext.readOnlyRootFilesystem: true'
  )
  hl_init = hl_vals.get('initContainers', [])
  assert hl_init and hl_init[0].get('name') == 'ca-bundle', 'headlamp ca-bundle initContainer missing'
  assert hl_init[0].get('securityContext', {}).get('readOnlyRootFilesystem') is True, (
      'headlamp ca-bundle missing readOnlyRootFilesystem: true'
  )
  assert hl_init[0].get('resources') == {
      'requests': {'cpu': '10m', 'memory': '16Mi'},
      'limits': {'cpu': '100m', 'memory': '64Mi'},
  }, 'headlamp ca-bundle resources mismatch'

  # 2. Headlamp bootstrap script parity
  hl_script = files[HEADLAMP_SCRIPT]
  assert hl_script.count('readOnlyRootFilesystem: true') >= 2, (
      '08-3-security.sh missing readOnlyRootFilesystem: true on main or initContainer'
  )
  assert 'memory: 512Mi' in hl_script, '08-3-security.sh missing headlamp memory limit'
  assert 'requests:\n        cpu: 10m\n        memory: 16Mi' in hl_script, (
      '08-3-security.sh ca-bundle requests mismatch'
  )
  assert 'limits:\n        cpu: 100m\n        memory: 64Mi' in hl_script, (
      '08-3-security.sh ca-bundle limits mismatch'
  )

  # 3. Kubernetes Dashboard
  kd_app = parse_helm_app(files[K8S_DASHBOARD])
  kd_vals = kd_app['spec']['source']['helm']['valuesObject']
  kong_res = kd_vals.get('kong', {}).get('resources', {})
  assert kong_res == {
      'requests': {'cpu': '50m', 'memory': '128Mi'},
      'limits': {'cpu': '250m', 'memory': '256Mi'},
  }, f'kubernetes-dashboard kong.resources mismatch: {kong_res}'

  # 4. Chaos Mesh
  cm_app = parse_helm_app(files[CHAOS_MESH])
  cm_vals = next(source['helm']['valuesObject'] for source in cm_app['spec']['sources']
                 if source.get('chart') == 'chaos-mesh')
  daemon_lim = cm_vals.get('chaosDaemon', {}).get('resources', {}).get('limits', {})
  assert daemon_lim.get('cpu') == '500m' and daemon_lim.get('memory') == '256Mi', (
      f'chaosDaemon limits mismatch: {daemon_lim}'
  )
  cm_lim = cm_vals.get('controllerManager', {}).get('resources', {}).get('limits', {})
  assert cm_lim.get('cpu') == '500m' and cm_lim.get('memory') == '256Mi', (
      f'controllerManager limits mismatch: {cm_lim}'
  )

  # 5. Harbor
  hb_app = parse_helm_app(files[HARBOR])
  hb_vals = hb_app['spec']['source']['helm']['valuesObject']
  redis_res = hb_vals.get('redis', {}).get('internal', {}).get('resources', {})
  assert redis_res == {
      'requests': {'cpu': '50m', 'memory': '64Mi'},
      'limits': {'cpu': '250m', 'memory': '256Mi'},
  }, f'harbor redis.internal resources mismatch: {redis_res}'
  portal_res = hb_vals.get('portal', {}).get('resources', {})
  assert portal_res == {
      'requests': {'cpu': '25m', 'memory': '32Mi'},
      'limits': {'cpu': '100m', 'memory': '64Mi'},
  }, f'harbor portal resources mismatch: {portal_res}'
  nginx_res = hb_vals.get('nginx', {}).get('resources', {})
  assert nginx_res == {
      'requests': {'cpu': '50m', 'memory': '64Mi'},
      'limits': {'cpu': '200m', 'memory': '128Mi'},
  }, f'harbor nginx resources mismatch: {nginx_res}'

  # 6. Gitea
  gitea_script = files[GITEA]
  for flag in [
      '--set resources.requests.cpu=100m',
      '--set resources.requests.memory=256Mi',
      '--set resources.limits.cpu=500m',
      '--set resources.limits.memory=1Gi',
      '--set initContainers.resources.limits.cpu=200m',
      '--set initContainers.resources.limits.memory=256Mi',
  ]:
    assert flag in gitea_script, f'12-gitea.sh missing {flag}'

  # 7. ArgoCD
  argocd_script = files[ARGOCD]
  assert 'patch_init_resources()' in argocd_script, '13-argocd.sh missing patch_init_resources function'
  # D-ARGOCD-COPYUTIL-OOM: the two inits that copy the ~207MB argocd binary need 256Mi (64Mi
  # OOM-killed cp and left a truncated binary that `cp -n` never replaced); redis's init does not copy.
  for workload, mem_lim in (('argocd-repo-server', '256Mi'), ('argocd-redis', '64Mi'), ('argocd-dex-server', '256Mi')):
    pattern = rf'patch_init_resources\s+deployment\s+{workload}\s+0\s+10m\s+32Mi\s+100m\s+{mem_lim}\s*$'
    assert re.search(pattern, argocd_script, re.M), f'13-argocd.sh missing {workload} initContainer patch ({mem_lim})'


def main() -> None:
  files = {p: (ROOT / p).read_text() for p in PATHS}
  validate(files)
  print('R364 PASS: DevTools compliance hardening validated across 7 components')

  if '--mutation-verify' in sys.argv:
    mutations = [
        (HEADLAMP, 'readOnlyRootFilesystem: true', 'readOnlyRootFilesystem: false'),
        (HEADLAMP, 'cpu: 10m', 'cpu: 50m'),
        (HEADLAMP_SCRIPT, 'readOnlyRootFilesystem: true', 'readOnlyRootFilesystem: false'),
        (K8S_DASHBOARD, 'cpu: 250m', 'cpu: 100m'),
        (CHAOS_MESH, 'cpu: 500m', 'cpu: 100m'),
        (HARBOR, 'cpu: 250m', 'cpu: 100m'),
        (HARBOR, 'cpu: 200m', 'cpu: 50m'),
        (GITEA, '--set resources.limits.memory=1Gi', '--set resources.limits.memory=512Mi'),
        (ARGOCD, 'patch_init_resources deployment argocd-dex-server', '# patch_init_resources'),
        (ARGOCD, 'argocd-dex-server  0 10m 32Mi 100m 256Mi', 'argocd-dex-server  0 10m 32Mi 100m 64Mi'),
        (ARGOCD, 'argocd-repo-server 0 10m 32Mi 100m 256Mi', 'argocd-repo-server 0 10m 32Mi 100m 64Mi'),
    ]
    for path, old, new in mutations:
      changed = copy.copy(files)
      assert old in changed[path], f'Target string {old!r} not found in {path}'
      changed[path] = changed[path].replace(old, new, 1)
      try:
        validate(changed)
      except AssertionError:
        continue
      raise AssertionError(f'mutation escaped: {path}: {old}')
    print(f'R364 PASS: {len(mutations)} mutations rejected')


if __name__ == '__main__':
  main()
