#!/bin/bash
# R257b: scripts/cluster/rotate-apisix-admin-key.sh against fake kubectl/helm/curl/python3 state.
# Happy path: Secret -> restart -> IC -> portal -> verify, old key 401 / new 200, portal re-synced.
# Failure path: verification fails after the Secret changed -> previous Secret restored, APISIX
# restarted, IC re-applied, old key verified again, exit non-zero. Run from the repo root.
set -euo pipefail
repo=$(pwd)
w=$(mktemp -d); trap 'rm -rf "$w"' EXIT
mkdir -p "$w/bin" "$w/st"
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
cat > "$w/bin/kubectl" <<'FAKE'
#!/bin/bash
st="${FAKE_ST}"
echo "kubectl $*" >> "${st}/calls"
args="$*"
case "$1 $2" in
  "get secret")
    case "$3" in
      apisix-admin-key)
        f="${7#jsonpath=\{.data.}"; f="${f%\}}"
        [ -f "${st}/s_${f}" ] && printf '%s' "$(cat "${st}/s_${f}")" | base64 | tr -d '\n'
        exit 0 ;;
      narwhal-portal-secrets) exit 0 ;;
    esac ;;
  "get configmap") cat "${st}/cm"; exit 0 ;;
  "create secret")
    for a in "$@"; do case "$a" in --from-file=key=*) k="${a#--from-file=key=}" ;; --from-file=viewer=*) v="${a#--from-file=viewer=}" ;; esac; done
    # a literal key in argv would be a leak
    case "${args}" in *from-literal*) echo "from-literal used" >> "${st}/leak" ;; esac
    printf 'SECRET\n%s\n%s\n' "$(cat "$k")" "$(cat "$v")"; exit 0 ;;
  "create configmap")
    for a in "$@"; do case "$a" in --from-file=config.yaml=*) c="${a#--from-file=config.yaml=}" ;; esac; done
    { echo CM; cat "$c"; }; exit 0 ;;
  "apply -f")
    IFS= read -r first; rest=$(cat)
    if [ "$first" = SECRET ]; then printf '%s' "$(echo "$rest" | sed -n 1p)" > "${st}/s_key"; printf '%s' "$(echo "$rest" | sed -n 2p)" > "${st}/s_viewer"
    else printf '%s\n' "$rest" > "${st}/cm"; fi
    exit 0 ;;
  "port-forward "*) exec sleep 30 ;;
  "logs "*) exit 0 ;;
esac
case "$1" in
  patch)
    case "${args}" in *narwhal-portal-secrets*) cp "${args##*--patch-file=}" "${st}/portal_patch" ;; esac; exit 0 ;;
  rollout|wait) exit 0 ;;
esac
exit 0
FAKE
cat > "$w/bin/helm" <<'FAKE'
#!/bin/bash
echo "helm $*" >> "${FAKE_ST}/calls"
exit 0
FAKE
cat > "$w/bin/curl" <<'FAKE'
#!/bin/bash
key=$(sed -n 's/.*X-API-KEY: \(.*\)"$/\1/p')
st="${FAKE_ST}"
if [ -n "${FAKE_BREAK:-}" ] && [ "$(cat "${st}/s_key")" != oldadmin ]; then printf 500; exit 0; fi
# keys valid for APISIX = what the Secret holds (restart already happened in the script)
if [ "$key" = "$(cat "${st}/s_key")" ] || [ "$key" = "$(cat "${st}/s_viewer")" ]; then printf 200; else printf 401; fi
FAKE
cat > "$w/bin/python3" <<'FAKE'
#!/bin/bash
echo 40123
FAKE
chmod +x "$w/bin"/*
# scripts dir shim: lib-charts needs chart(); lib.sh is the real one
mkdir -p "$w/scripts/common"
cp "$repo/scripts/common/lib.sh" "$w/scripts/common/lib.sh"
printf 'chart() { echo "/charts/$1.tgz"; }\n' > "$w/scripts/common/lib-charts.sh"
G=$(command -v grep)
# argv recorder: every external tool the script (or a fake) runs is logged to the calls file
# with its full argv, then delegated to the real binary, so a key in ANY argv is caught.
for tool in grep awk cat base64 tr sed openssl mv seq sleep mktemp rm cp kill; do
  real=$(command -v "$tool")
  printf '#!/bin/bash\necho "%s $*" >> "${FAKE_ST}/calls"\nexec %s "$@"\n' "$tool" "$real" > "$w/bin/$tool"
  chmod +x "$w/bin/$tool"
done
export PATH="$w/bin:$PATH" FAKE_ST="$w/st" NARWHAL_SCRIPTS_DIR="$w/scripts" KUBECONFIG=/dev/null

reset() {
  rm -f "$w/st"/*; : > "$w/st/calls"
  printf oldadmin > "$w/st/s_key"; printf oldviewer > "$w/st/s_viewer"
  printf 'admin_key:\n  - key: oldadmin\n    name: admin\n  - key: oldviewer\n    name: viewer\n' > "$w/st/cm"
}
fail() { echo "FAIL: $*" >&2; exit 1; }

reset
bash scripts/cluster/rotate-apisix-admin-key.sh > "$w/out" 2>&1 || { cat "$w/out" >&2; fail "happy path exited non-zero"; }
[[ "$(cat "$w/st/s_key")" =~ ^[0-9a-f]{32}$ ]] || fail "Secret admin key not rotated"
[ "$(cat "$w/st/s_key")" != oldadmin ] || fail "admin key unchanged"
"$G" -q oldadmin "$w/st/cm" && fail "configmap literal still holds the old key"
"$G" -q "$(cat "$w/st/s_key")" "$w/st/cm" || fail "configmap literal not updated to the new key"
"$G" -q 'HTTP 401' "$w/out" || fail "old key 401 not verified"
[ ! -f "$w/st/leak" ] || fail "key passed via --from-literal"
"$G" -q "$(b64 "$(cat "$w/st/s_key")")" "$w/st/portal_patch" || fail "portal secret not re-synced"
"$G" -q 'rollout restart deployment/narwhal-portal' "$w/st/calls" || fail "portal not restarted"
# order: secret apply, apisix restart, helm (IC), portal patch
ln_apply=$("$G" -n 'apply -f' "$w/st/calls" | head -1 | cut -d: -f1)
ln_apx=$("$G" -n 'rollout restart deployment/apisix ' "$w/st/calls" | head -1 | cut -d: -f1)
ln_helm=$("$G" -n '^helm upgrade' "$w/st/calls" | head -1 | cut -d: -f1)
[ "$ln_apply" -lt "$ln_apx" ] && [ "$ln_apx" -lt "$ln_helm" ] || fail "order must be Secret, APISIX restart, IC"
for secret in oldadmin oldviewer "$(cat "$w/st/s_key")" "$(cat "$w/st/s_viewer")"; do
  "$G" -Fq -- "$secret" "$w/st/calls" && fail "key value appears in a command argv"
done

reset
if FAKE_BREAK=1 bash scripts/cluster/rotate-apisix-admin-key.sh > "$w/out" 2>&1; then fail "broken verification must exit non-zero"; fi
"$G" -q 'ROLLBACK COMPLETE' "$w/out" || { cat "$w/out" >&2; fail "rollback did not complete"; }
[ "$(cat "$w/st/s_key")" = oldadmin ] && [ "$(cat "$w/st/s_viewer")" = oldviewer ] || fail "previous Secret not restored"
[ "$("$G" -c '^helm upgrade' "$w/st/calls")" -ge 2 ] || fail "IC not re-applied on rollback"
[ "$("$G" -c 'rollout restart deployment/apisix ' "$w/st/calls")" -ge 2 ] || fail "APISIX not restarted on rollback"
"$G" -q oldadmin "$w/st/cm" || fail "configmap not restored"

# #399 case: Secret holds env placeholders -> the backup falls back to the configmap literal,
# so a failed rotation still rolls back cleanly (no false INCOMPLETE).
reset
printf '%s' '${{APISIX_ADMIN_KEY}}' > "$w/st/s_key"; printf '%s' '${{APISIX_VIEWER_KEY}}' > "$w/st/s_viewer"
if FAKE_BREAK=1 bash scripts/cluster/rotate-apisix-admin-key.sh > "$w/out" 2>&1; then fail "placeholder case must exit non-zero when verification breaks"; fi
"$G" -q 'ROLLBACK COMPLETE' "$w/out" || { cat "$w/out" >&2; fail "placeholder Secret: rollback not complete"; }
