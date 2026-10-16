#!/bin/bash
# R255: real_key/cm_key/secret_key (scripts/cluster/08-1-networking.sh) against a fake kubectl.
# Run from the repo root. Key values are fixtures; nothing real is read or printed.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
# args: get secret apisix-admin-key -n ns -o jsonpath={.data.<k>} | get configmap apisix -n ns -o jsonpath=...
case "$2" in
  secret)
    f="${7#jsonpath=\{.data.}"; f="${f%\}}"
    case "${FAKE_SECRET}:${f}" in
      both:key) printf 'c2VjcmV0YWRtaW4=' ;;           # secretadmin
      both:viewer) printf 'c2VjcmV0dmlld2Vy' ;;        # secretviewer
      placeholder:key) printf '%s' "$(printf '%s' '${{APISIX_ADMIN_KEY}}' | base64)" ;;
      placeholder:viewer) printf '%s' "$(printf '%s' '${{APISIX_VIEWER_KEY}}' | base64)" ;;
      viewer_only:viewer) printf 'c2VjcmV0dmlld2Vy' ;;
    esac
    [ "${FAKE_SECRET}" = none ] && { echo 'Error from server (NotFound): secrets "apisix-admin-key" not found' >&2; exit 1; }
    exit 0 ;;
  configmap)
    if [ "${FAKE_CM}" = literal ]; then
      cat <<'Y'
deployment:
  admin:
    admin_key:
      - key: cmadminkey
        name: admin
        role: admin
      - key: "cmviewerkey"
        name: "viewer"
        role: viewer
Y
    else
      cat <<'Y'
deployment:
  admin:
    admin_key:
      - key: ${{APISIX_ADMIN_KEY}}
        name: admin
        role: admin
      - key: ${{APISIX_VIEWER_KEY}}
        name: viewer
        role: viewer
Y
    fi ;;
esac
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH"

# Pull the helper definitions out of the real script so the test cannot drift from it.
defs=$(sed -n '/^secret_key() {/,/^real_key() {/p' scripts/cluster/08-1-networking.sh)
[ -n "${defs}" ]
eval "${defs}"
resolve() { # <FAKE_SECRET> <FAKE_CM> <role: key|viewer> <cm role: admin|viewer>
  export FAKE_SECRET="$1" FAKE_CM="$2"
  real_key "$(secret_key "$3")" || real_key "$(cm_key "$4")" || true
}
eq() { [ "$1" = "$2" ] || { echo "expected '$2' got '$1' ($3)" >&2; exit 1; }; }

eq "$(resolve both literal key admin)" secretadmin "secret wins over configmap"
eq "$(resolve both literal viewer viewer)" secretviewer "secret viewer wins"
eq "$(resolve placeholder literal key admin)" cmadminkey "placeholder secret falls back to configmap (unquoted name)"
eq "$(resolve none literal viewer viewer)" cmviewerkey "quoted key/name parsed"
eq "$(resolve viewer_only literal key admin)" cmadminkey "secret lacking key falls back to configmap admin"
eq "$(resolve none placeholder key admin)" "" "configmap placeholders are never keys"
eq "$(resolve none placeholder viewer viewer)" "" "configmap placeholder viewer is never a key"
# the script must abort when either key is empty (no public default admin key)
grep -q 'for _role in admin viewer' scripts/cluster/08-1-networking.sh
! grep -q 'edd1c9f034335f136f87ad84b625c8f1' scripts/cluster/08-1-networking.sh
