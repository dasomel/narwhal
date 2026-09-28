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
PORTAL_DIR="${NARWHAL_PORTAL_DIR:-${PORTAL_DIR:-../narwhal-portal}}"
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

# Production Gitea calls require a configured HTTPS endpoint and API token.
# Owner/repo/base branch have safe portal defaults; admin passwords are not runtime inputs.
PORTAL_CONFIG_FILE="${PORTAL_DIR}/src/lib/config.ts"
PORTAL_GITEA_FILE="${PORTAL_DIR}/src/lib/gitea.ts"
PORTAL_DEPLOYMENT="${PORTAL_DEPLOYMENT_FILE:-gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml}"
PORTAL_GITOPS_BOOTSTRAP="${PORTAL_GITOPS_BOOTSTRAP_FILE:-scripts/cluster/14-gitops-bootstrap.sh}"
PORTAL_BINDINGS="${PORTAL_BINDINGS_FILE:-scripts/cluster/13-2-narwhal-portal-bindings.sh}"
if [ -f "${PORTAL_CONFIG_FILE}" ] && [ -f "${PORTAL_GITEA_FILE}" ]; then
  for configured_var in GITEA_URL GITEA_OWNER GITEA_REPO GITEA_TOKEN GITEA_BASE_BRANCH; do
    if ! grep -q "\"${configured_var}\":" "${PORTAL_GITOPS_BOOTSTRAP}" \
      && ! grep -q -- "--from-literal=${configured_var}=" "${PORTAL_BINDINGS}" \
      && ! grep -q "name: ${configured_var}" "${PORTAL_DEPLOYMENT}"; then
      echo "FAIL: ${configured_var} is not provided by bootstrap, secret creation, or Deployment env" >&2
      exit 1
    fi
  done
  if ! grep -A3 '          envFrom:' "${PORTAL_DEPLOYMENT}" \
    | grep -q 'name: narwhal-portal-secrets'; then
    echo "FAIL: portal Deployment does not import narwhal-portal-secrets through envFrom" >&2
    exit 1
  fi

  for required_var in GITEA_URL GITEA_TOKEN; do
    case "${required_var}" in
      GITEA_URL)
        if ! grep -q '"GITEA_URL", process.env.GITEA_URL' "${PORTAL_CONFIG_FILE}"; then
          echo "FAIL: portal production requirement for GITEA_URL was not found" >&2
          exit 1
        fi
        provided=0
        # 14 patches the portal's GITEA_URL from PORTAL_GITEA_URL (https; R220 checks the scheme).
        grep -q '"GITEA_URL": "${PORTAL_GITEA_URL}"' "${PORTAL_GITOPS_BOOTSTRAP}" \
          && grep -A3 '          envFrom:' "${PORTAL_DEPLOYMENT}" \
            | grep -q 'name: narwhal-portal-secrets' && provided=1
        ;;
      GITEA_TOKEN)
        if ! grep -q 'GITEA_TOKEN is not configured' "${PORTAL_GITEA_FILE}"; then
          echo "FAIL: portal production requirement for GITEA_TOKEN was not found" >&2
          exit 1
        fi
        provided=0
        grep -q '"GITEA_TOKEN": "${PORTAL_GIT_TOKEN}"' "${PORTAL_GITOPS_BOOTSTRAP}" \
          && grep -A3 '          envFrom:' "${PORTAL_DEPLOYMENT}" \
            | grep -q 'name: narwhal-portal-secrets' && provided=1
        ;;
    esac
    if [ "${provided}" -ne 1 ]; then
      echo "FAIL: production-required ${required_var} is not provided by Narwhal" >&2
      exit 1
    fi
  done
  echo "PASS: production-required Gitea env is provided (GITEA_URL, GITEA_TOKEN)"
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

# Portal side: string literals inside the `[export ]const ALLOWED_GROUPS[: T] = new Set([...])`
# block only -- the awk range keeps this from matching an unrelated string
# literal elsewhere in auth.ts. TypeScript, so a scoped grep/sed extraction is
# fine here (no TS parser available in this repo).
portal_groups="$(
  awk '/^(export )?const ALLOWED_GROUPS/,/^\]\)/' "${AUTH_FILE}" \
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
