#!/bin/bash
# R250: behaviour of resolve_s3_credentials (scripts/common/lib.sh) against a fake kubectl.
# Run from the repo root. Scenario is chosen with FAKE_SCENARIO; secret values never printed.
set -euo pipefail

k=$(mktemp -d); trap 'rm -rf "$k"' EXIT
cat > "$k/kubectl" <<'FAKE'
#!/bin/bash
# args: get secret <name> -n <ns> -o jsonpath={.data.<key>}
name="$3"; key="${6#jsonpath=\{.data.}"; key="${key%\}}"
case "${FAKE_SCENARIO}" in
  missing) echo "kubectl: command not found" >&2; exit 127 ;;
  notfound_nomsg) echo "bash: kubectl: command not found" >&2; exit 1 ;;
  warn_ok)
    echo "Warning: stderr noise that must not reach the value" >&2
    case "${key}" in access-key) printf 'a2V5' ;; secret-key) printf 'c2VjcmV0' ;; esac
    exit 0 ;;
  notfound) echo "Error from server (NotFound): secrets \"${name}\" not found" >&2; exit 1 ;;
  apierror) echo "Unable to connect to the server: net/http: TLS handshake timeout" >&2; exit 1 ;;
  cnpg_only)
    if [ "${name}" = cnpg-s3-credentials ]; then
      case "${key}" in ACCESS_KEY_ID) printf 'a2V5' ;; ACCESS_SECRET_KEY) printf 'c2VjcmV0' ;; esac
      exit 0
    fi
    echo "Error from server (NotFound): secrets \"${name}\" not found" >&2; exit 1 ;;
esac
FAKE
chmod +x "$k/kubectl"
export PATH="$k:$PATH"
# shellcheck source=/dev/null
source scripts/common/lib.sh

run() { unset S3_ACCESS_KEY S3_SECRET_KEY; export FAKE_SCENARIO="$1"; shift; resolve_s3_credentials "$@" 2>/dev/null; }
cnpg=(cnpg-s3-credentials database ACCESS_KEY_ID ACCESS_SECRET_KEY)

# NotFound -> default allowed
run notfound; [ "$S3_ACCESS_KEY" = admin ] && [ "$S3_SECRET_KEY" = admin ]
run notfound "${cnpg[@]}"; [ "$S3_ACCESS_KEY" = admin ] && [ "$S3_SECRET_KEY" = admin ]
# any other kubectl error -> fail closed
if run apierror; then echo "apierror must fail" >&2; exit 1; fi
if run apierror "${cnpg[@]}"; then echo "apierror (cnpg) must fail" >&2; exit 1; fi
# kubectl missing (rc 127, "command not found") or any non-server "not found" -> fail closed
if run missing; then echo "rc127 must fail" >&2; exit 1; fi
if run notfound_nomsg; then echo "shell not-found text must fail" >&2; exit 1; fi
# stderr warning on a successful read must not corrupt the value
run warn_ok; [ "$S3_ACCESS_KEY" = key ] && [ "$S3_SECRET_KEY" = secret ]
# existing cnpg secret (velero absent) is preserved, not defaulted
run cnpg_only "${cnpg[@]}"; [ "$S3_ACCESS_KEY" = key ] && [ "$S3_SECRET_KEY" = secret ]
# env override wins; empty secret key still ends non-empty
export FAKE_SCENARIO=notfound; unset S3_ACCESS_KEY S3_SECRET_KEY
S3_ACCESS_KEY=ak S3_SECRET_KEY=""; resolve_s3_credentials 2>/dev/null
[ "$S3_ACCESS_KEY" = ak ] && [ -n "$S3_SECRET_KEY" ]
