#!/usr/bin/env python3
"""R293: offline contracts and shell behavior for issue #505 item 6."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
verify = (ROOT / 'scripts/test/verify-cluster.sh').read_text()
sso = (ROOT / 'scripts/test/test-sso.sh').read_text()
regression = (ROOT / 'scripts/test/regression-check-kakao.sh').read_text()
install = (ROOT / 'scripts/cluster/09-istio-ambient.sh').read_text()
count = 0


def run(body, setup=''):
  result = subprocess.run(['bash', '-c', '''set -euo pipefail
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; }
warn() { echo "WARN $*"; }
skip() { echo "SKIP $*"; }
''' + setup + '\n' + body], text=True, capture_output=True)
  assert result.returncode == 0, result.stderr
  return result.stdout


def check(condition, name):
  global count
  assert condition, name
  count += 1
  print(f'PASS {name}')


def block(text, start, end):
  return text.split(start, 1)[1].split(end, 1)[0]


def names(text):
  return re.search(r'AMBIENT_NAMESPACES=\((.*?)\)', text, re.S)[1].split()


tls = 'CERT_DOMAINS=' + block(verify, '  CERT_DOMAINS=', '\n\n')
for answer, expected in [('*.test.internal', 'PASS'), ('x.test.internal', 'FAIL'),
                         ('*.testXinternal', 'FAIL'), ('*.${DOMAIN}', 'FAIL')]:
  out = run(tls, f'DOMAIN=test.internal\nkubectl() {{ echo \'{answer}\'; }}')
  check(out.startswith(expected), f'literal wildcard {answer}')

check(names(verify) == names(install), 'ambient list matches installer')
# D-DEV-OPTOUT: the namespace label is owned by gitops/resources/dev-namespace.yaml;
# derive dev's mode from it instead of hard-coding, and fail if the lists disagree.
dev_doc = [d for d in (ROOT / 'gitops/resources/dev-namespace.yaml').read_text().split('\n---')
           if re.search(r'^kind: Namespace$', d, re.M) and re.search(r'^  name: dev$', d, re.M)][0]
dev_mode = re.search(r'^\s+istio\.io/dataplane-mode:\s*"?(\w+)', dev_doc, re.M)[1]
gitops_ambient = set()
for f in (ROOT / 'gitops').rglob('*.yaml'):
  for d in re.split(r'^---$', f.read_text(), flags=re.M):
    if re.search(r'^kind: Namespace$', d, re.M) and re.search(r'^    istio\.io/dataplane-mode:\s*"?ambient', d, re.M):
      gitops_ambient.add(re.search(r'^  name:\s*(\S+)', d, re.M)[1])
check(('dev' in names(install)) == (dev_mode == 'ambient') == ('dev' in names(verify)),
      f'dev enrollment agrees with dev-namespace.yaml (mode={dev_mode})')
check(gitops_ambient <= set(names(install)), 'gitops ambient namespaces are enrolled by the installer')
ambient = 'AMBIENT_NAMESPACES=' + block(verify, '  AMBIENT_NAMESPACES=', '\n\n')
for missing in ['', 'platform-system']:
  out = run(ambient, f'''kubectl() {{
  if [ "$3" = "{missing}" ]; then return 1; fi
  echo ambient
}}''')
  check(out.count('PASS') == (4 if missing else 5) and
        out.count('FAIL') == (1 if missing else 0), f'ambient missing={missing or "none"}')
devcheck = 'DEV_MODE=' + block(verify, '  DEV_MODE=', '\n\n')
for answer, expected in [('ambient', 'FAIL'), ('none', 'PASS'), ('', 'PASS')]:
  out = run(devcheck, f"kubectl() {{ echo '{answer}'; }}")
  check(out.startswith(expected), f'dev not ambient answer={answer or "unlabeled"}')

# D-LIVE-ABORT: a `$(... | base64 -d)` assignment under set -e/pipefail ends the
# run silently when the Secret is missing; require a guard on each, plus the
# ERR-trap sentinel as backstop.
bare = [l for l in sso.splitlines() if re.search(r'=\$\(.*\| *base64 -d', l) and '||' not in l]
check(not bare, 'test-sso.sh has no unguarded kubectl|base64 assignment')
check(re.search(r"^trap '.*ABORTED at line.*' ERR$", sso, re.M) and re.search(r'^set -E$', sso, re.M),
      'test-sso.sh has an ABORTED sentinel ERR trap')
check('get secret oidc ' not in sso and
      "get secret headlamp-oidc-secret -n devtools -o jsonpath='{.data.OIDC_ISSUER_URL}'" in sso,
      'Headlamp issuer read from headlamp-oidc-secret')

gitea = 'GITEA_PW=' + block(verify, '  GITEA_PW=', '\n\n')
for available in [True, False]:
  out = run(gitea, '''DOMAIN=test.internal
kubectl() { ''' + ('printf c2VudGluZWw=' if available else 'return 1') + '''; }
curl() {
  [[ "$*" == *'gitea-admin:sentinel'* ]] || return 1
  echo 200
}''')
  check(out.startswith('PASS' if available else 'FAIL') and 'sentinel' not in out,
        f'private Gitea credential available={available}')
check("get secret gitea-admin -n devtools -o jsonpath='{.data.admin-password}'" in gitea,
      'Gitea installed Secret')

harbor = 'harbor_api() {' + block(sso, 'harbor_api() {', '\n}\n') + '\n}\n'
for available in [True, False]:
  out = run(harbor + '\nif harbor_api https://harbor.test/api; then echo PASS; else echo FAIL; fi',
            '''kubectl() { ''' + ('printf c2VudGluZWw=' if available else 'return 1') + '''; }
curl() { [[ "$*" == *'admin:sentinel'* ]]; }''')
  check(out.strip() == ('PASS' if available else 'FAIL'), f'Harbor credential available={available}')
check("get secret harbor-secrets -n devtools -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}'" in harbor
      and 'Harbor12345' not in sso and sso.count('$(harbor_api ') == 2, 'Harbor API credential wiring')
clients = re.search(r'for client_name in (.*?); do', sso)[1].split()
check('oauth2-proxy' not in clients and 'headlamp' in clients, 'retired Keycloak client removed')
creds = (ROOT / 'scripts/test/show-credentials.sh').read_text()
for name, text in [('test-sso.sh', sso), ('show-credentials.sh', creds)]:
  live = [l for l in text.splitlines() if 'oauth2-proxy' in l.lower() and not l.lstrip().startswith('#')
          and not re.search(r'\s#.*oauth2-proxy', l, re.I)]
  check(not live, f'{name} has no live oauth2-proxy check')
# D-519: Guard only retired SSO Middleware checks; unrelated Traefik artifacts
# stay out of scope. Revisit these names if Middleware resources are reintroduced.
for retired in ['forwardauth-oauth2', 'auth-signin', 'middlewares.traefik']:
  live = [l for l in sso.splitlines() if retired in l.lower() and not l.lstrip().startswith('#')
          and not re.search(r'\s#.*' + re.escape(retired), l, re.I)]
  check(not live, f'test-sso.sh has no live {retired} check')
probe = block(regression, 'local probe="regression-probe-$$"', '--command -- sleep')
check('--labels=app.kubernetes.io/name=apisix-ingress-controller,istio.io/dataplane-mode=none' in probe,
      'probe bypasses ambient with controller identity')
image = re.search(r'--image=(\S+)', probe)[1]
check(image in (ROOT / 'scripts/airgap/images.txt').read_text().splitlines() and
      any(l.startswith(image + '\t') for l in (ROOT / 'scripts/airgap/lib/image-digests.tsv').read_text().splitlines()),
      'probe image mirrored and digest-pinned')

# Execute each provider branch with no external commands allowed. Cloud skips
# must neither call kubectl/check_ready nor report PASS; Vagrant must still probe.
starts = ['  KVIP_PODS=', '  check_ready "deployment" "platform-system" "metallb-controller"',
          '  # APISIX gateway LoadBalancer IP', '  # dnsmasq service']
for start in starts:
  pos = verify.index(start)
  begin = verify.rfind('  if [ "${PROVIDER}" = "vagrant" ]; then', 0, pos)
  end = verify.index('\n  fi\n\n', pos) + len('\n  fi')
  guarded = verify[begin:end]
  out = run(guarded, '''PROVIDER=kakao
kubectl() { echo UNEXPECTED; }
check_ready() { echo UNEXPECTED; }
''')
  check('SKIP ' in out and all(line.startswith('SKIP ') for line in out.splitlines()),
        f'cloud skips {start.strip()}')
check('PROVIDER="${PROVIDER:-vagrant}"' in verify and
      '[ "${PROVIDER}" = "local" ] && PROVIDER="vagrant"' in verify and
      'echo "  SKIP: ${SKIP_COUNT}"' in verify, 'provider convention and SKIP accounting')
print(f'{count} passed, 0 failed')
