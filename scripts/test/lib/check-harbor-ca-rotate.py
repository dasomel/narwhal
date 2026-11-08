#!/usr/bin/env python3
"""R317: 08-6 restarts harbor-core only when narwhal-ca-cert really changed (#551, D-HARBOR-CA-ROTATE).

Runs the real CA-distribution block of 08-6-tls-routes.sh against a stub kubectl whose devtools
Secret state is a file, so the before/after comparison, a first install, a re-run, a missing
Deployment and a failed restart are executed, not grepped. A mutation (restart unconditionally)
must fail the same scenarios.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (ROOT / 'scripts/cluster/08-6-tls-routes.sh').read_text()
BLOCK = SCRIPT[SCRIPT.index('CA_CERT=$(kubectl get secret narwhal-wildcard-tls'):
               SCRIPT.index('\necho "=== TLS Routes and APISIX Routes Installation Complete')]
GUARD = re.compile(r'if \[ -n "\$\{CA_BEFORE\}" \] && \[ "\$\{CA_BEFORE\}" != "\$\{CA_AFTER\}" \] \\\n\s+&& ')


def run(block, before, new, harbor=True, restart_fails=False):
  """before: stored devtools ca.crt data ('' = no Secret); new: the wildcard CA data."""
  with tempfile.TemporaryDirectory() as d:
    state, log = Path(d) / 'devtools-ca', Path(d) / 'log'
    state.write_text(before)
    log.write_text('')
    stub = f'''set -euo pipefail
kubectl() {{
  echo "$*" >> {log}
  case "$*" in
    "get secret narwhal-wildcard-tls -n platform-system"*) printf '%s' '{new}' ;;
    "get secret narwhal-ca-cert -n devtools"*) [ -s {state} ] && command cat {state} || true ;;
    "get deployment harbor-core -n devtools"*) {'true' if harbor else 'false'} ;;
    "rollout restart deployment/harbor-core -n devtools") {'false' if restart_fails else 'true'} ;;
    *) true ;;
  esac
}}
base64() {{ command cat; }}
K8S_CA=k8s
'''
    # The real apply of the devtools Secret is replaced by writing the new CA to the state file.
    needle = 'kubectl apply -f -\n  done'
    assert needle in block
    wrapped = stub + block.replace(
      needle, f'kubectl apply -f -\n    if [ "${{ns}}" = devtools ]; then printf "%s" \'{new}\' > {state}; fi\n  done')
    r = subprocess.run(['bash', '-c', wrapped], text=True, capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=30)
    return r.returncode, log.read_text(), r.stdout + r.stderr


def scenarios(block):
  def restarted(log):
    return 'rollout restart deployment/harbor-core -n devtools' in log
  rc, log, _ = run(block, 'OLD', 'NEW')
  yield 'changed CA restarts harbor-core and waits for the rollout', \
    rc == 0 and restarted(log) and 'rollout status deployment/harbor-core -n devtools' in log
  rc, log, _ = run(block, 'SAME', 'SAME')
  yield 're-run with an unchanged CA does not restart harbor-core', rc == 0 and not restarted(log)
  rc, log, _ = run(block, '', 'NEW')
  yield 'first install (no previous Secret) does not restart harbor-core', rc == 0 and not restarted(log)
  rc, log, _ = run(block, 'OLD', 'NEW', harbor=False)
  yield 'changed CA without a harbor-core Deployment is a no-op', rc == 0 and not restarted(log)
  rc, _, out = run(block, 'OLD', 'NEW', restart_fails=True)
  yield 'a failed restart warns and does not abort 08-6', \
    rc == 0 and 'WARN: harbor-core restart did not finish' in out


count = 0
for name, ok in scenarios(BLOCK):
  assert ok, name
  count += 1
  print(f'PASS {name}')
assert 'D-HARBOR-CA-ROTATE' in SCRIPT and 'kubectl -n devtools rollout restart deployment/harbor-core' in \
  (ROOT / 'docs/vagrant/operations.md').read_text(), 'procedure not documented in operations.md'
count += 1
print('PASS procedure documented in operations.md')

# #551 item 3: the D-ARGOCD-NO-DEFAULT comment must point at the g-lines where they actually are (above it).
acm = (ROOT / 'gitops/charts/narwhal-platform/templates/argocd-config.yaml').read_text()
assert 'Only the g-lines above grant access.' in acm and 'g-lines below' not in acm, 'argocd-config comment direction'
assert acm.index('g, guest, role:none') < acm.index('Only the g-lines above grant access.') < acm.index('policy.default: ""')
count += 1
print('PASS argocd-config comment says the g-lines are above')

mutated = GUARD.sub('if ', BLOCK)
assert mutated != BLOCK, 'mutation did not apply'
failed = [name for name, ok in scenarios(mutated) if not ok]
assert failed, 'mutation (unconditional restart) was not caught'
count += 1
print(f'PASS mutation: unconditional restart caught by {len(failed)} scenario(s)')
print(f'{count} passed, 0 failed')
