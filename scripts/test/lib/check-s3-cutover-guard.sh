#!/bin/bash
# R274: guard_s3_auth_cutover (scripts/common/lib.sh, D-s3ops-4, #414) against a fake kubectl.
# Run from the repo root. The guard is read-only: every case also asserts no mutating call.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
export ST="$k/store"; mkdir -p "$ST"
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
# get deployment seaweedfs-s3 ... (DEP selects the answer) | get secret <name> -n <ns> -o name
echo "$*" >> "${ST}/calls"
[ "$1" = get ] || exit 0
if [ "$2" = deployment ]; then
  case "${DEP:-missing}" in
    missing) echo 'Error from server (NotFound): deployments.apps "seaweedfs-s3" not found' >&2; exit 1 ;;
    apierror) echo 'Unable to connect to the server: net/http: TLS handshake timeout' >&2; exit 1 ;;
    forbidden) echo 'Error from server (Forbidden): deployments.apps "seaweedfs-s3" is forbidden' >&2; exit 1 ;;
    garbage) exit 0 ;;
    anon) printf 's3\t["weed"]\t["s3","-port=8333"]' ;;
    auth_command) printf 's3\t["weed","s3","-config=/etc/sw/seaweedfs_s3_config"]\t' ;;
    auth_args) printf 's3\t\t["s3","-config=/etc/sw/seaweedfs_s3_config"]' ;;
    empty_config) printf 's3\t["weed","s3","-config="]\t' ;;
  esac
  exit 0
fi
if [ "$2" = secret ]; then
  [ "${FAKE_SECRET_ERR:-}" = "$3" ] && { echo 'Error from server (Forbidden): secrets is forbidden' >&2; exit 1; }
  [ -f "${ST}/$5__$3" ] && { echo "secret/$3"; exit 0; }
  echo "Error from server (NotFound): secrets \"$3\" not found" >&2; exit 1
fi
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH"
# shellcheck source=/dev/null
source scripts/common/lib.sh

fail() { echo "FAIL: $*" >&2; exit 1; }
MSG='ERROR: existing SeaweedFS S3 has no -config; Loki/Tempo credentials are missing. Follow docs/runbooks/seaweedfs-s3-auth.md before enabling auth. Approved immediate cutover only: NARWHAL_ALLOW_S3_AUTH_CUTOVER=true.'
secrets() { rm -f "$ST"/monitoring__*; for s in "$@"; do touch "$ST/monitoring__$s"; done; }
run() { : > "$ST/calls"; RC=0; guard_s3_auth_cutover >"$k/out" 2>&1 || RC=$?; OUT=$(<"$k/out"); }
# no write ever: every recorded kubectl call is a `get`
readonly_calls() { ! grep -v '^get ' "$ST/calls" | grep -q .; }
allow() { run; [ "$RC" -eq 0 ] || fail "$1 must be allowed (rc=$RC: $OUT)"; readonly_calls || fail "$1 wrote"; }
reject() { run; [ "$RC" -ne 0 ] || fail "$1 must abort"; readonly_calls || fail "$1 wrote before aborting"; }

export DEP=missing; secrets
allow "clean install (no Deployment)"
export DEP=auth_command; secrets
allow "authenticated server (-config in command), Secrets absent"
export DEP=auth_args
allow "authenticated server (-config in args)"
export DEP=anon; secrets loki-s3-credentials tempo-s3-credentials
allow "anonymous server, both Secrets present (staged cutover)"
secrets tempo-s3-credentials
reject "anonymous server, loki Secret missing"; [ "$OUT" = "$MSG" ] || fail "message differs: $OUT"
secrets loki-s3-credentials
reject "anonymous server, tempo Secret missing"; [ "$OUT" = "$MSG" ] || fail "message differs: $OUT"
secrets
reject "anonymous server, both Secrets missing"
export DEP=empty_config
reject "empty -config= counts as anonymous"
export DEP=anon
NARWHAL_ALLOW_S3_AUTH_CUTOVER=true allow "override true on the transition abort"
NARWHAL_ALLOW_S3_AUTH_CUTOVER=yes reject "override must be exactly true"
NARWHAL_ALLOW_S3_AUTH_CUTOVER=true FAKE_SECRET_ERR=loki-s3-credentials reject "Secret read error is not bypassed by the override"
export NARWHAL_ALLOW_S3_AUTH_CUTOVER=true
for d in apierror forbidden garbage; do DEP=$d reject "Deployment read $d is not bypassed by the override"; done
unset NARWHAL_ALLOW_S3_AUTH_CUTOVER
for d in apierror forbidden garbage; do DEP=$d reject "Deployment read $d fails closed"; done
export DEP=anon; secrets tempo-s3-credentials
FAKE_SECRET_ERR=loki-s3-credentials reject "Secret read error fails closed"
echo "OK"
