#!/bin/bash
set -euo pipefail

# validate-keycloak-clients.sh
# Validates Keycloak client security posture for Narwhal IDP (#148, #149):
# 1. Zero unexpected clients with directAccessGrantsEnabled=true (ROPC)
# 2. Zero unexpected clients with wildcard or relative webOrigins (* or +)
#
# Usage:
#   bash validate-keycloak-clients.sh [realm]
#   bash validate-keycloak-clients.sh --input <clients-json-file> [realm]

REALM="narwhal"
INPUT_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input|-i)
      INPUT_FILE="$2"
      shift 2
      ;;
    --realm|-r)
      REALM="$2"
      shift 2
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      REALM="$1"
      shift
      ;;
  esac
done

if [[ -n "${INPUT_FILE}" ]]; then
  if [[ ! -f "${INPUT_FILE}" ]]; then
    echo "ERROR: Input file not found: ${INPUT_FILE}" >&2
    exit 1
  fi
  CLIENTS_JSON="$(cat "${INPUT_FILE}")"
else
  export KUBECONFIG="${KUBECONFIG:-/home/vagrant/.kube/config-local}"
  CLIENTS_JSON="$(kubectl exec -n iam keycloak-0 -c keycloak -- \
    /opt/keycloak/bin/kcadm.sh get clients -r "${REALM}" 2>/dev/null || true)"
fi

python3 - "${REALM}" "${CLIENTS_JSON}" <<'PYEOF'
import json
import os
import sys

realm = sys.argv[1]
raw_json = sys.argv[2] if len(sys.argv) > 2 else ""

if not raw_json.strip():
    print(f"ERROR: No client data retrieved for realm '{realm}'", file=sys.stderr)
    sys.exit(1)

try:
    clients = json.loads(raw_json)
except Exception as exc:
    print(f"ERROR: Failed to parse Keycloak clients JSON: {exc}", file=sys.stderr)
    sys.exit(1)

# Documented exceptions for Keycloak built-in/system clients:
# - admin-cli: Keycloak internal CLI client (defaults to directAccessGrantsEnabled=true)
# - account-console: Keycloak internal account console (defaults to webOrigins=["+"])
# Zero production application clients are permitted in either exception set.
ropc_env = os.environ.get("KEYCLOAK_ROPC_EXCEPTIONS", "admin-cli")
origin_env = os.environ.get("KEYCLOAK_ORIGIN_EXCEPTIONS", "account-console")
ROPC_EXCEPTIONS = set(filter(None, [x.strip() for x in ropc_env.split(",")]))
ORIGIN_EXCEPTIONS = set(filter(None, [x.strip() for x in origin_env.split(",")]))

errors = []
inventory = []

for c in sorted(clients, key=lambda x: x.get("clientId", "")):
    cid = c.get("clientId", "<unknown>")
    ropc = c.get("directAccessGrantsEnabled", False)
    origins = c.get("webOrigins") or []
    public_client = c.get("publicClient", False)
    service_accounts = c.get("serviceAccountsEnabled", False)
    bearer_only = c.get("bearerOnly", False)

    if bearer_only:
        client_type = "bearer-only"
    elif service_accounts:
        client_type = "service-account"
    elif public_client:
        client_type = "public"
    else:
        client_type = "confidential"

    # Check ROPC / Direct Access Grants (#149)
    if ropc and cid not in ROPC_EXCEPTIONS:
        errors.append(f"Client '{cid}' has directAccessGrantsEnabled=true (unexpected ROPC client)")

    # Check wildcard or relative webOrigins (* or +) (#148)
    wildcards = [o for o in origins if any(ch in o for ch in ("*", "+"))]
    if wildcards and cid not in ORIGIN_EXCEPTIONS:
        errors.append(f"Client '{cid}' has unexpected wildcard/relative webOrigins: {wildcards}")

    inventory.append((cid, client_type, "ENABLED" if ropc else "disabled", origins))

print(f"=== Keycloak Client Security Inventory (realm: {realm}) ===")
for cid, ctype, ropc_status, origins in inventory:
    origins_str = ", ".join(origins) if origins else "(none)"
    print(f"  {cid:24s} [{ctype:15s}] ROPC: {ropc_status:8s} webOrigins: {origins_str}")

if errors:
    print("\nERROR: Keycloak client security validation FAILED:", file=sys.stderr)
    for err in errors:
        print(f"  - {err}", file=sys.stderr)
    sys.exit(1)

print(f"\nKeycloak client security validation passed ({len(clients)} clients): zero unexpected ROPC clients, zero unexpected wildcard origins")
PYEOF
