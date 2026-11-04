#!/usr/bin/env python3
"""R293: offline contracts and shell behavior for issue #505 item 6."""
from pathlib import Path
import re
import subprocess
import sys

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
# Keep these contracts independently evaluable against origin/main: one failure
# must not mask the other stale checks.
def sso_contracts(sso_text, registry_text, clients_text):
  redirect = re.search(r'for protected_app in (.*?); do', sso_text)[1].split()
  member_pos = clients_text.find('/projects/1/members')
  mode_pos = clients_text.find('"${HARBOR_AUTH_MODE}" != "oidc_auth"')
  return [
    ('Gitea uses installed credential', 'gitea-admin:gitea-admin' not in sso_text and
     "get secret gitea-admin -n devtools -o jsonpath='{.data.admin-password}'" in sso_text),
    ('Gitea checks both teams', 'for team in Developers Viewers; do' in sso_text and
     '/orgs/narwhal/teams' in sso_text),
    ('Headlamp uses API gate instead of redirect', 'headlamp' not in redirect and
     '/clusters/main/api/v1/namespaces' in sso_text and '401|403) pass "Headlamp:' in sso_text),
    ('Harbor members follow verified auth mode', '/projects/1/members' not in registry_text and
     0 < mode_pos < member_pos and clients_text.index('-X PUT') < mode_pos),
    ('Harbor member HTTP errors fail', member_pos >= 0 and
     "HTTP_CODE=${MEMBER_RESPONSE##*$'\\n'}" in clients_text and
     '201|409)' in clients_text and 'exit 1 ;;' in clients_text and
     '|| true' not in block(clients_text, 'for group_role in', '\n      done')),
  ]

registry_path = 'scripts/cluster/08-5-registry.sh'
clients_path = 'scripts/cluster/11-3-keycloak-clients.sh'
registry_text = (ROOT / registry_path).read_text()
clients_text = (ROOT / clients_path).read_text()
for name, valid in sso_contracts(sso, registry_text, clients_text):
  check(valid, name)

# Optional baseline proof uses local Git objects only, never the cluster/network.
if '--prove-origin-main' in sys.argv:
  def baseline(path):
    return subprocess.check_output(['git', 'show', f'origin/main:{path}'], cwd=ROOT, text=True)
  for name, valid in sso_contracts(baseline('scripts/test/test-sso.sh'),
                                   baseline(registry_path), baseline(clients_path)):
    check(not valid, f'origin/main rejected: {name}')

gitea_helper = 'gitea_api() {' + block(sso, 'gitea_api() {', '\n}\n') + '\n}\n'
for available in [True, False]:
  out = run(gitea_helper + '\nif gitea_api /orgs/narwhal; then echo PASS; else echo FAIL; fi',
            '''GITEA_POD=gitea
kubectl() {
  if [ "$1" = get ]; then ''' + ('printf c2VudGluZWw=' if available else 'return 1') + ''';
  else [[ "$*" == *'gitea-admin:sentinel'* ]]; fi
}''')
  check(out.strip() == ('PASS' if available else 'FAIL'), f'Gitea SSO credential available={available}')
teams = 'GITEA_TEAMS=' + block(sso, '    GITEA_TEAMS=', '\n\n')
for answer, passes in [('[{"name":"Developers"},{"name":"Viewers"}]', 2),
                       ('[{"name":"Developers"}]', 1), ('[]', 0), ('invalid', 0)]:
  out = run(teams, f"gitea_api() {{ echo '{answer}'; }}")
  check(out.count('PASS') == passes and out.count('FAIL') == 2 - passes,
        f'Gitea teams response={answer}')
# D-GATEWAY-LIVE-LOGIN (#519): the gateway group check covers all four hosts and the four
# cases, never passes when the login form is unparseable, and cleans its cookie jars. The
# admin/view result must come from the app URL: a wrong password ends on the Keycloak page with
# HTTP 200, which a status-only check would pass (review of #536).
check('for gw_host in hubble prometheus alertmanager nfs-quota; do' in sso and
      'NOFORM) fail "${gw_host}: admin' in sso and 'NOFORM) fail "${gw_host}: view' in sso and
      'X-Userinfo: ${GW_FORGED}' in sso and 'trap \'rm -rf "${GW_TMP:-}"\' EXIT' in sso and
      '"403 ${gw_url}"*) pass "${gw_host}: view' in sso and
      '"2"??" ${gw_url}"*) pass "${gw_host}: admin' in sso and
      "%{http_code} %{url_effective}' --data-binary" in sso and
      '2??|3??) pass "${gw_host}: admin' not in sso, 'gateway admin-group live check shape')
# D-ARGOCD-LIVE-RBAC: the guest/viewer ArgoCD live login must fail on NOFORM or a missing
# session (empty list alone proves nothing), fail on guest apps > 0, and the viewer must be
# denied sync.
check('ArgoCD ${argo_user}: login form not found, cannot verify' in sso and
      'no ArgoCD session after Keycloak login' in sso and
      '/api/v1/session/userinfo' in sso and
      'fail "ArgoCD guest: application list returned ${ARGO_COUNT} apps (matrix: deny)"' in sso and
      'fail "ArgoCD view: sync expected 403' in sso and
      'for argo_user in guest view; do' in sso, 'ArgoCD live login check shape')
check('for h_user in admin dev; do' in sso and 'admin_role_in_auth=true, sysadmin-only /users HTTP 200' in sso and
      'Grafana admin: server admin and org Admin' in sso and
      'Keycloak console ${k_user}: no token from the console login, cannot verify' in sso and
      'Keycloak console ${k_user}: login form not found, cannot verify' in sso, 'admin top-role live check shape')
login = 'gateway_login() {' + block(sso, 'gateway_login() {', '\n}\n') + '\n}\n'
for page, expected in [('<html>no form</html>', 'NOFORM'), ('<form action="x"><input name="username"></form>', 'NOFORM')]:
  out = run(login + '\ngateway_login /dev/null u p https://x/',
            f"curl() {{ printf '%s' '{page}'; }}")
  check(out.strip() == expected, f'gateway_login unparseable form -> {expected}')

# D-VELERO-UI-ADMIN-ONLY: velero-ui falls back to `manage all` for everyone when no policy loads, so
# the chart must mount a policy with exactly one valid group line for cluster-admin and no wildcard
# (`*`) or user line, the gateway must close the unguarded bulk download, and the live check must
# cover the four users, fail on an empty token and create nothing.
vu_app = (ROOT / 'gitops/charts/narwhal-apps/templates/velero-ui.yaml').read_text()
vu_routes = (ROOT / 'gitops/charts/narwhal-platform/templates/apisix-routes.yaml').read_text()
vu_pol = re.search(r'policies:\n\s+enabled: true\n\s+value: \|\n((?: {14}\S[^\n]*\n)+)', vu_app)
vu_lines = [l.strip() for l in vu_pol.group(1).splitlines() if l.strip() and not l.strip().startswith('#')] if vu_pol else []
check(vu_lines == ['g,cluster-admin,manage,all'], 'velero-ui policy is exactly g,cluster-admin,manage,all')
check('name: velero-ui-block-bulk-download' in vu_routes and '"/api/backups/download"' in vu_routes,
      'velero-ui bulk download closed at the gateway')
check('for vu_user in admin view dev guest; do' in sso and
      'fail "velero-ui: ${vu_user} login/token exchange failed, cannot verify"' in sso and
      'vu_expect "${VU_CREATE}" 400' in sso and 'vu_expect "${VU_DELETE}" 403' in sso and
      'vu_expect "${VU_LIST}" 403' in sso, 'velero-ui live check shape')

headlamp = 'HEADLAMP_API_CODE=' + block(sso, '  HEADLAMP_API_CODE=', '\n\n')
for code in ['401', '403', '200', '302', '500', '000']:
  out = run(headlamp, f'DOMAIN=test.internal\ncurl() {{ echo {code}; }}')
  check(out.startswith('PASS' if code in ['401', '403'] else 'FAIL'), f'Headlamp API HTTP {code}')

# Execute the actual provisioning block with shell functions replacing all I/O.
# Exercise re-runs and failure before either POST; no real credentials are used.
harbor_start = clients_text.index('      HTTP_CODE=$(curl -sk --max-time 30')
harbor_setup = clients_text[harbor_start:clients_text.index('\n    else', harbor_start)]
# D-HARBOR-OIDC-TRUST: the PUT must enable oidc_verify_cert, and only after Harbor itself
# pinged the issuer with verification on; a failed ping stops before the PUT and the members.
check('\\"oidc_verify_cert\\": true' in harbor_setup and 'oidc_verify_cert\\": false' not in harbor_setup,
      'Harbor OIDC PUT enables oidc_verify_cert')
for ping in ['500', '000']:
  setup = f'''set -euo pipefail
exec 3>&1
HARBOR_API=http://unused
HARBOR_ADMIN_PASS=sentinel
HARBOR_SECRET=client-secret
ISSUER_URL=http://unused
curl() {{
  case "$*" in
    *'/system/oidc/ping'*) printf '{ping}' ;;
    *) echo UNEXPECTED >&3; printf '200' ;;
  esac
}}
'''
  result = subprocess.run(['bash', '-c', setup + harbor_setup], text=True, capture_output=True)
  check(result.returncode != 0 and 'UNEXPECTED' not in result.stdout and 'does not trust' in result.stderr,
        f'Harbor OIDC ping={ping} fails before PUT and members')
for put, mode, member, expected in [('200', 'oidc_auth', '201', True),
                                    ('200', 'oidc_auth', '409', True),
                                    ('500', 'oidc_auth', '201', False),
                                    ('200', 'db_auth', '201', False),
                                    ('200', 'oidc_auth', '400', False),
                                    ('200', 'oidc_auth', '401', False),
                                    ('200', 'oidc_auth', '500', False),
                                    ('200', 'oidc_auth', '000', False)]:
  setup = f'''set -euo pipefail
exec 3>&1
HARBOR_API=http://unused
HARBOR_ADMIN_PASS=sentinel
HARBOR_SECRET=client-secret
ISSUER_URL=http://unused
curl() {{
  case "$*" in
    *'/system/oidc/ping'*) printf '200' ;;
    *'-X PUT'*) printf '{put}' ;;
    *'-X POST'*) echo POST >&3; printf 'sentinel client-secret error body\\n{member}';
      [ '{member}' != 000 ] ;;
    *) printf '{{"auth_mode":{{"value":"{mode}"}}}}' ;;
  esac
}}
'''
  result = subprocess.run(['bash', '-c', setup + harbor_setup], text=True, capture_output=True)
  out = result.stdout + result.stderr
  check((result.returncode == 0) == expected and 'sentinel' not in out and 'client-secret' not in out,
        f'Harbor PUT={put} mode={mode} member={member}')
  if put != '200' or mode != 'oidc_auth':
    check('POST' not in out,
          'Harbor rejects config failure before members')
  elif not expected:
    check(f'HTTP {member}' in out and ('error body' in out or 'Transport failure' in out),
          f'Harbor reports member failure {member}')
  else:
    check(out.count('POST') == 2 and 'developer group' in out and 'viewer group' in out,
          'Harbor registers both groups')
print(f'{count} passed, 0 failed')
