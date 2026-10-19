#!/bin/bash
# R274: guard_s3_auth_cutover (scripts/common/lib.sh, D-s3ops-4, #414) against a fake kubectl.
# Run from the repo root. The guard is read-only: every case also asserts no mutating call.
# The signal is "the live Loki/Tempo StatefulSet references its S3 Secret via envFrom", never
# "the Secret exists" (07's ensure_s3_identities creates both Secrets before 08-4 runs).
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
export ST="$k/store"; mkdir -p "$ST"
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
# DEP selects the seaweedfs-s3 Deployment answer, SS_LOKI/SS_TEMPO the StatefulSet answer
echo "$*" >> "${ST}/calls"
case "$1 ${2:-}" in
  "get deployment")
    # honour the jsonpath: reading containers[0] only sees the first line
    if [[ "$*" != *'containers[*]'* ]]; then DEP_FIRST=1; fi
    case "${DEP:-missing}" in
      missing) echo 'Error from server (NotFound): deployments.apps "seaweedfs-s3" not found' >&2; exit 1 ;;
      apierror) echo 'Unable to connect to the server: net/http: TLS handshake timeout' >&2; exit 1 ;;
      forbidden) echo 'Error from server (Forbidden): deployments.apps "seaweedfs-s3" is forbidden' >&2; exit 1 ;;
      garbage) exit 0 ;;
      anon) printf 's3\t["weed"]\t["s3","-port=8333"]\n' ;;
      anon_two) printf 's3\t["weed"]\t["s3"]\nmetrics\t\t["-port=9"]\n' ;;
      auth_command) printf 's3\t["weed","s3","-config=/etc/sw/seaweedfs_s3_config"]\t\n' ;;
      auth_args) printf 's3\t\t["s3","-config=/etc/sw/seaweedfs_s3_config"]\n' ;;
      auth_second) printf 'metrics\t\t["-port=9"]\ns3\t\t["s3","-config=/etc/sw/seaweedfs_s3_config"]\n' ;;
      empty_config) printf 's3\t["weed","s3","-config="]\t\n' ;;
    esac | { if [ -n "${DEP_FIRST:-}" ]; then head -n1; else cat; fi; }
    exit "${PIPESTATUS[0]}" ;;
  "get statefulset")
    var="SS_$(echo "$3" | tr a-z A-Z)"; mode="${!var:-missing}"
    case "$mode" in
      missing) echo "Error from server (NotFound): statefulsets.apps \"$3\" not found" >&2; exit 1 ;;
      error) echo 'Error from server (Forbidden): statefulsets.apps is forbidden' >&2; exit 1 ;;
      consuming) printf '%s-s3-credentials\n' "$3" ;;
      second) printf 'other\n%s-s3-credentials\n' "$3" ;;
      bare) ;;
      decoy) printf 'x%s-s3-credentials\n%s-s3-credentials-old\n' "$3" "$3" ;;
    esac
    exit 0 ;;
  "get secret")
    [ "${FAKE_SECRET_ERR:-}" = "$3" ] && { echo 'Error from server (Forbidden): secrets is forbidden' >&2; exit 1; }
    [ -f "${ST}/secret__$5__$3" ] && { [ "${7:-}" = name ] && echo "secret/$3"; exit 0; }
    echo "Error from server (NotFound): secrets \"$3\" not found" >&2; exit 1 ;;
  "apply -f")
    [ "$3" = "-" ] && { cat >/dev/null; exit 0; }
    ns=$(awk '/^  namespace:/{print $2}' "$3"); name=$(awk '/^  name:/{print $2; exit}' "$3")
    touch "${ST}/secret__${ns}__${name}" ;;
esac
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH"
# shellcheck source=/dev/null
source scripts/common/lib.sh

fail() { echo "FAIL: $*" >&2; exit 1; }
MSG='ERROR: existing SeaweedFS S3 has no -config; Loki/Tempo do not consume their S3 credential Secrets yet. Follow docs/runbooks/seaweedfs-s3-auth.md before enabling auth. Approved immediate cutover only: NARWHAL_ALLOW_S3_AUTH_CUTOVER=true.'
run() { : > "$ST/calls"; RC=0; guard_s3_auth_cutover >"$k/out" 2>&1 || RC=$?; OUT=$(<"$k/out"); }
# no write ever: every recorded kubectl call is a `get`
readonly_calls() { ! grep -v '^get ' "$ST/calls" | grep -q .; }
allow() { run; [ "$RC" -eq 0 ] || fail "$1 must be allowed (rc=$RC: $OUT)"; readonly_calls || fail "$1 wrote"; }
reject() { run; [ "$RC" -ne 0 ] || fail "$1 must abort"; readonly_calls || fail "$1 wrote before aborting"; }
reject_msg() { reject "$1"; [ "$OUT" = "$MSG" ] || fail "$1: message differs: $OUT"; }

export SS_LOKI=bare SS_TEMPO=bare
DEP=missing allow "clean install (no Deployment)"
for d in auth_command auth_args auth_second; do DEP=$d allow "authenticated server ($d), StatefulSets not consuming"; done
export DEP=anon

# --- full Phase 2 re-run on an anonymous cluster: 07 creates both Secrets, 08-4 must STILL abort
ensure_s3_identities >/dev/null 2>&1 || fail "07-style ensure_s3_identities failed"
[ -f "$ST/secret__monitoring__loki-s3-credentials" ] && [ -f "$ST/secret__monitoring__tempo-s3-credentials" ] || fail "07 did not create the Loki/Tempo Secrets (test is vacuous)"
reject_msg "Phase 2 re-run: Secrets exist but the StatefulSets do not reference them"
# staged state (runbook step 2 done): both StatefulSets reference their Secret
SS_LOKI=consuming SS_TEMPO=consuming allow "staged cutover: both consume"
SS_LOKI=second SS_TEMPO=consuming allow "staged cutover: Secret is not the first envFrom"
SS_LOKI=missing SS_TEMPO=missing allow "Loki and Tempo not installed"
SS_LOKI=missing SS_TEMPO=consuming allow "Loki not installed, Tempo consumes"
SS_LOKI=consuming SS_TEMPO=bare reject_msg "Loki consumes, Tempo does not"
SS_LOKI=bare SS_TEMPO=consuming reject_msg "Tempo consumes, Loki does not"
SS_LOKI=missing SS_TEMPO=bare reject_msg "Loki not installed, Tempo does not consume"
SS_LOKI=decoy SS_TEMPO=consuming reject_msg "similarly named Secret references are not the client Secret"
DEP=anon_two reject_msg "anonymous server with two containers"
DEP=empty_config reject_msg "empty -config= counts as anonymous"

# --- override bypasses ONLY the transition abort
NARWHAL_ALLOW_S3_AUTH_CUTOVER=true allow "override true on the transition abort"
case "$OUT" in *"do not consume"*) ;; *) fail "override must warn which clients do not consume: $OUT" ;; esac
NARWHAL_ALLOW_S3_AUTH_CUTOVER=yes reject "override must be exactly true"
export NARWHAL_ALLOW_S3_AUTH_CUTOVER=true
SS_LOKI=error reject "StatefulSet read error is not bypassed by the override"
SS_TEMPO=error reject "Tempo StatefulSet read error is not bypassed by the override"
for d in apierror forbidden garbage; do DEP=$d reject "Deployment read $d is not bypassed by the override"; done
unset NARWHAL_ALLOW_S3_AUTH_CUTOVER
SS_LOKI=error reject "StatefulSet read error fails closed"
for d in apierror forbidden garbage; do DEP=$d reject "Deployment read $d fails closed"; done
echo "OK"
