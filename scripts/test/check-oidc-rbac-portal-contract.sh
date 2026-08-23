#!/usr/bin/env bash
set -euo pipefail

#=========================================
# OIDC RBAC <-> Portal ALLOWED_GROUPS contract check
#=========================================
# Cross-repo seam: gitops/resources/rbac-policies.yaml binds ClusterRoles to OIDC
# group subjects named "oidc:<X>"; narwhal-portal's src/lib/auth.ts gates login
# through a bare-name ALLOWED_GROUPS set. The two lists must describe the same
# groups (portal's "guest" fallback excepted) or a group added on one side
# silently has no effect on the other — an RBAC binding nothing can log in as,
# or a portal role that grants a UI a user's token can never actually present.
#
# Usage: scripts/test/check-oidc-rbac-portal-contract.sh

cd "$(dirname "$0")/../.."

RBAC_FILE="${RBAC_FILE:-gitops/resources/rbac-policies.yaml}"
PORTAL_DIR="${PORTAL_DIR:-../narwhal-portal}"
AUTH_FILE="${PORTAL_DIR}/src/lib/auth.ts"

# The portal repo is a sibling checkout, not a submodule of this one — it may not
# exist in every environment this runs in (e.g. a narwhal-only CI job). This is
# structured as an `if` block, not `[ -d ... ] && ...`: under `set -e` the latter
# aborts the whole script the instant the directory is absent, never reaching the
# skip message below. Same hazard the 07-save-binaries.sh chmod fix hit.
if [ ! -d "${PORTAL_DIR}" ]; then
  echo "SKIP: narwhal-portal sibling checkout not found at ${PORTAL_DIR} -- nothing to compare"
  exit 0
fi

if [ ! -f "${AUTH_FILE}" ]; then
  echo "SKIP: ${AUTH_FILE} not found -- nothing to compare"
  exit 0
fi

# RBAC side: bare names of every "oidc:<X>" Group subject bound by a
# ClusterRoleBinding, deduped. yq per this repo's convention (never sed for YAML).
rbac_groups="$(
  yq eval-all \
    'select(.kind == "ClusterRoleBinding") | .subjects[]? | select(.kind == "Group" and (.name | test("^oidc:"))) | .name' \
    "${RBAC_FILE}" \
    | grep -v '^---$' \
    | sed 's/^oidc://' \
    | sort -u
)"

# Portal side: string literals inside the `const ALLOWED_GROUPS = new Set([...])`
# block only -- the awk range keeps this from matching an unrelated string
# literal elsewhere in auth.ts. TypeScript, so a scoped grep/sed extraction is
# fine here (no TS parser available in this repo).
portal_groups="$(
  awk '/^const ALLOWED_GROUPS/,/^\]\)/' "${AUTH_FILE}" \
    | grep -oE '"[a-zA-Z0-9_-]+"' \
    | tr -d '"' \
    | sort -u
)"

if [ -z "${rbac_groups}" ]; then
  echo "FAIL: no oidc: Group subjects found in ${RBAC_FILE}" >&2
  exit 1
fi
if [ -z "${portal_groups}" ]; then
  echo "FAIL: no ALLOWED_GROUPS entries found in ${AUTH_FILE}" >&2
  exit 1
fi

fail=0

echo "RBAC (oidc:<X>) -> Portal ALLOWED_GROUPS:"
while IFS= read -r g; do
  [ -z "${g}" ] && continue
  if printf '%s\n' "${portal_groups}" | grep -qxF "${g}"; then
    echo "  PASS  oidc:${g} -> ${g}"
  else
    echo "  FAIL  oidc:${g} -> ${g} missing from portal ALLOWED_GROUPS"
    fail=1
  fi
done <<< "${rbac_groups}"

echo "Portal ALLOWED_GROUPS -> RBAC (oidc:<X>) [guest exempt]:"
while IFS= read -r p; do
  [ -z "${p}" ] && continue
  if [ "${p}" = "guest" ]; then
    echo "  PASS  ${p} (portal-only fallback role, no RBAC binding required)"
    continue
  fi
  if printf '%s\n' "${rbac_groups}" | grep -qxF "${p}"; then
    echo "  PASS  ${p} -> oidc:${p}"
  else
    echo "  FAIL  ${p} -> no oidc:${p} RBAC binding found (orphaned portal group)"
    fail=1
  fi
done <<< "${portal_groups}"

if [ "${fail}" -eq 0 ]; then
  echo "PASS: OIDC RBAC groups and portal ALLOWED_GROUPS match"
  exit 0
else
  echo "FAIL: OIDC RBAC <-> portal ALLOWED_GROUPS drift detected"
  exit 1
fi
