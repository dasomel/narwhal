#!/bin/bash
# R255/R256: resolve_apisix_keys / apisix_key_usable (scripts/common/lib.sh) against a fake
# kubectl. Run from the repo root. Key values are fixtures; nothing real is read or printed.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
# The upstream defaults, assembled so no complete literal sits in the tree.
DEF_ADMIN="edd1c9f0""34335f136f87ad84b625c8f1"
DEF_VIEWER="4054f7cf""07e344346cd3f287985e76a2"
export DEF_ADMIN DEF_VIEWER
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
b64() { printf '%s' "$1" | base64 | tr -d '\n'; }
case "$2" in
  secret)
    [ "${FAKE_SECRET}" = error ] && { echo 'Unable to connect to the server: timeout' >&2; exit 1; }
    [ "${FAKE_SECRET}" = none ] && { echo 'Error from server (NotFound): secrets "apisix-admin-key" not found' >&2; exit 1; }
    f="${7#jsonpath=\{.data.}"; f="${f%\}}"
    case "${FAKE_SECRET}:${f}" in
      both:key) b64 secretadmin ;;
      both:viewer) b64 secretviewer ;;
      default:key) b64 "${DEF_ADMIN}" ;;
      default:viewer) b64 secretviewer ;;
      defviewer:key) b64 secretadmin ;;
      defviewer:viewer) b64 "${DEF_VIEWER}" ;;
      placeholder:key) b64 '${{APISIX_ADMIN_KEY}}' ;;
      placeholder:viewer) b64 '${{APISIX_VIEWER_KEY}}' ;;
      viewer_only:viewer) b64 secretviewer ;;
    esac
    exit 0 ;;
  configmap)
    case "${FAKE_CM}" in
      literal) printf 'admin_key:\n  - key: cmadminkey\n    name: admin\n    role: admin\n  - key: "cmviewerkey"\n    name: "viewer"\n    role: viewer\n' ;;
      default) printf 'admin_key:\n  - key: %s\n    name: admin\n    role: admin\n  - key: %s\n    name: viewer\n    role: viewer\n' "${DEF_ADMIN}" "${DEF_VIEWER}" ;;
      placeholder) printf 'admin_key:\n  - key: ${{APISIX_ADMIN_KEY}}\n    name: admin\n    role: admin\n  - key: ${{APISIX_VIEWER_KEY}}\n    name: viewer\n    role: viewer\n' ;;
    esac ;;
esac
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH"

# shellcheck source=scripts/common/lib.sh
source scripts/common/lib.sh
run() { # <FAKE_SECRET> <FAKE_CM>: sets A V G in this shell
  export FAKE_SECRET="$1" FAKE_CM="$2"
  resolve_apisix_keys; A="${APISIX_ADMIN_KEY}"; V="${APISIX_VIEWER_KEY}"; G="${APISIX_KEYS_GENERATED}"
}
eq() { [ "$1" = "$2" ] || { echo "expected '$2' got '$1' ($3)" >&2; exit 1; }; }
hex32() { [[ "$1" =~ ^[0-9a-f]{32}$ ]] || { echo "not 32 hex: ($2)" >&2; exit 1; }; }

# apisix_key_usable: defaults and placeholders are never usable
apisix_key_usable "${DEF_ADMIN}" && { echo "default admin key accepted" >&2; exit 1; }
apisix_key_usable "${DEF_VIEWER}" && { echo "default viewer key accepted" >&2; exit 1; }
apisix_key_usable '' && { echo "empty accepted" >&2; exit 1; }
apisix_key_usable '${{APISIX_ADMIN_KEY}}' && { echo "placeholder accepted" >&2; exit 1; }
apisix_key_usable abc123 || { echo "random key rejected" >&2; exit 1; }

run both literal;       eq "$A $V $G" "secretadmin secretviewer false" "existing random keys are preserved"
run placeholder literal; eq "$A $V $G" "cmadminkey cmviewerkey false" "placeholder Secret falls back to literal configmap (#399 case)"
run both default;       eq "$A $V $G" "secretadmin secretviewer false" "secret wins over configmap"
run none literal;       eq "$A $V $G" "cmadminkey cmviewerkey false" "non-default bootstrap configmap keys adopted"
run viewer_only literal; eq "$A $V $G" "cmadminkey secretviewer false" "per-role fallback"
run default placeholder; eq "$V" secretviewer "viewer kept"; [ "$A" != "${DEF_ADMIN}" ] && hex32 "$A" "default admin replaced"; eq "$G" true "default admin flagged generated"
run defviewer placeholder; eq "$A" secretadmin "admin kept"; [ "$V" != "${DEF_VIEWER}" ] && hex32 "$V" "default viewer replaced"
run none default;       [ "$A" != "${DEF_ADMIN}" ] && [ "$V" != "${DEF_VIEWER}" ] && hex32 "$A" cmA && hex32 "$V" cmV; eq "$G" true "configmap defaults never reused"
run placeholder placeholder; hex32 "$A" phA; hex32 "$V" phV; eq "$G" true "placeholders alone => fresh keys"
run none placeholder;   hex32 "$A" freshA; [ "$A" != "$V" ] || { echo "admin == viewer" >&2; exit 1; }
export FAKE_SECRET=error FAKE_CM=literal
if (resolve_apisix_keys) 2>/dev/null; then echo "Secret read error must fail closed" >&2; exit 1; fi
