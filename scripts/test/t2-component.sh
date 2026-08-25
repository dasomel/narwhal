#!/usr/bin/env bash
set -euo pipefail

# T2 dispatches one platform component at a time.  Adapters own the component
# contract; this wrapper keeps their CLI and lifecycle boundary consistent.
usage() {
  cat <<'EOF'
Usage: scripts/test/t2-component.sh <component> [--mode render|runtime|all] [--template <chart-path>]

Components:
  keycloak  Offline desired-state rendering plus an optional local-container OIDC contract.

The render stage is an offline desired-state contract, not a live-cluster test.
The runtime stage never pulls images and fails when its required local image is absent.
EOF
}

if [ $# -lt 1 ]; then
  usage >&2
  exit 2
fi

COMPONENT="$1"
shift
MODE="all"
TEMPLATE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode)
      MODE="${2:?--mode requires render, runtime, or all}"
      shift 2
      ;;
    --template)
      TEMPLATE="${2:?--template requires a chart path}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${MODE}" in render|runtime|all) ;; *) echo "ERROR: invalid mode: ${MODE}" >&2; exit 2 ;; esac
case "${COMPONENT}" in
  keycloak)
    args=(--mode "${MODE}")
    if [ -n "${TEMPLATE}" ]; then args+=(--template "${TEMPLATE}"); fi
    exec "$(dirname "$0")/t2/keycloak.sh" "${args[@]}"
    ;;
  *) echo "ERROR: unregistered T2 component: ${COMPONENT}" >&2; usage >&2; exit 2 ;;
esac
