#!/usr/bin/env bash
set -euo pipefail

# preflight-keycloak-oidc-tls.sh
# Validates TLS verification and CA trust chain preconditions for Keycloak OIDC consumers:
# - ArgoCD (argocd-config.yaml, 13-argocd.sh, 11-3-keycloak-clients.sh)
# - APISIX Gateway (apisix.yaml, apisix-routes.yaml)
#
# Narwhal #141 (APISIX Gitea OIDC TLS verification)
# Narwhal #147 (ArgoCD Keycloak TLS verification)

ARGOCD_MANIFEST="${ARGOCD_MANIFEST:-gitops/charts/narwhal-platform/templates/argocd-config.yaml}"
APISIX_MANIFEST="${APISIX_MANIFEST:-gitops/charts/narwhal-apps/templates/apisix.yaml}"
ROUTES_MANIFEST="${ROUTES_MANIFEST:-gitops/charts/narwhal-platform/templates/apisix-routes.yaml}"
CLIENTS_SCRIPT="${CLIENTS_SCRIPT:-scripts/cluster/11-3-keycloak-clients.sh}"
ARGOCD_SCRIPT="${ARGOCD_SCRIPT:-scripts/cluster/13-argocd.sh}"
LIVE_MODE=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --argocd-manifest) ARGOCD_MANIFEST="$2"; shift 2 ;;
    --apisix-manifest) APISIX_MANIFEST="$2"; shift 2 ;;
    --routes-manifest) ROUTES_MANIFEST="$2"; shift 2 ;;
    --clients-script)  CLIENTS_SCRIPT="$2"; shift 2 ;;
    --argocd-script)   ARGOCD_SCRIPT="$2"; shift 2 ;;
    --live)            LIVE_MODE=true; shift ;;
    --help|-h)
      echo "Usage: $0 [--argocd-manifest <file>] [--apisix-manifest <file>] [--routes-manifest <file>] [--clients-script <file>] [--argocd-script <file>] [--live]"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

FAILURES=0

pass() {
  echo "PASS: $1"
}

fail() {
  echo "FAIL: $1" >&2
  FAILURES=$((FAILURES + 1))
}

echo "=== Keycloak OIDC TLS Verification Preflight ==="

# 1. ArgoCD GitOps desired state
if [ ! -f "${ARGOCD_MANIFEST}" ]; then
  fail "ArgoCD manifest not found: ${ARGOCD_MANIFEST}"
else
  if grep -rqE "(insecureSkipVerify|oidc\.tls\.insecure\.skip\.verify)[[:space:]]*[:=][[:space:]]*['\"]?true['\"]?" "${ARGOCD_MANIFEST}"; then
    fail "ArgoCD manifest (${ARGOCD_MANIFEST}) sets insecureSkipVerify: true"
  else
    pass "ArgoCD manifest (${ARGOCD_MANIFEST}) enforces TLS verification (no insecureSkipVerify)"
  fi

  if grep -Fq 'rootCA: $oidc.keycloak.rootCA' "${ARGOCD_MANIFEST}"; then
    pass "ArgoCD manifest (${ARGOCD_MANIFEST}) configures rootCA: \$oidc.keycloak.rootCA"
  else
    fail "ArgoCD manifest (${ARGOCD_MANIFEST}) missing rootCA: \$oidc.keycloak.rootCA"
  fi

  if grep -qE 'issuer:[[:space:]]*https://keycloak\.' "${ARGOCD_MANIFEST}"; then
    pass "ArgoCD manifest (${ARGOCD_MANIFEST}) uses HTTPS for Keycloak issuer"
  else
    fail "ArgoCD manifest (${ARGOCD_MANIFEST}) does not use HTTPS for Keycloak issuer"
  fi
fi

# 2. APISIX Gateway desired state
if [ ! -f "${APISIX_MANIFEST}" ]; then
  fail "APISIX manifest not found: ${APISIX_MANIFEST}"
else
  if grep -q 'name:[[:space:]]*narwhal-root-ca' "${APISIX_MANIFEST}" && grep -q 'mountPath:[[:space:]]*/usr/local/apisix/conf/cert/narwhal' "${APISIX_MANIFEST}"; then
    pass "APISIX manifest (${APISIX_MANIFEST}) mounts narwhal-root-ca Secret volume"
  else
    fail "APISIX manifest (${APISIX_MANIFEST}) missing narwhal-root-ca volume mount"
  fi

  if grep -qE 'ssl_trusted_certificate:[[:space:]]*/usr/local/apisix/conf/cert/narwhal/tls\.crt' "${APISIX_MANIFEST}"; then
    pass "APISIX manifest (${APISIX_MANIFEST}) sets ssl_trusted_certificate to /usr/local/apisix/conf/cert/narwhal/tls.crt"
  else
    fail "APISIX manifest (${APISIX_MANIFEST}) missing or invalid ssl_trusted_certificate"
  fi
fi

# 3. APISIX Routes desired state
if [ ! -f "${ROUTES_MANIFEST}" ]; then
  fail "Routes manifest not found: ${ROUTES_MANIFEST}"
else
  if grep -qE 'ssl_verify:[[:space:]]*false' "${ROUTES_MANIFEST}"; then
    fail "APISIX routes (${ROUTES_MANIFEST}) contain ssl_verify: false bypass"
  else
    pass "APISIX routes (${ROUTES_MANIFEST}) do not bypass TLS verification (no ssl_verify: false)"
  fi

  if grep -qE 'discovery:[[:space:]]*"?https://keycloak\.' "${ROUTES_MANIFEST}"; then
    pass "APISIX routes (${ROUTES_MANIFEST}) specify HTTPS Keycloak discovery endpoint"
  else
    fail "APISIX routes (${ROUTES_MANIFEST}) missing HTTPS Keycloak discovery endpoint"
  fi
fi

# 4. Provisioning scripts
if [ -f "${ARGOCD_SCRIPT}" ]; then
  if grep -rqE "(insecureSkipVerify|oidc\.tls\.insecure\.skip\.verify)[[:space:]]*[:=][[:space:]]*['\"]?true['\"]?" "${ARGOCD_SCRIPT}"; then
    fail "ArgoCD bootstrap script (${ARGOCD_SCRIPT}) contains insecureSkipVerify: true"
  else
    pass "ArgoCD bootstrap script (${ARGOCD_SCRIPT}) enforces TLS verification"
  fi

  if grep -Fq 'rootCA: \$oidc.keycloak.rootCA' "${ARGOCD_SCRIPT}" && grep -Fq 'oidc.keycloak.rootCA' "${ARGOCD_SCRIPT}"; then
    pass "ArgoCD bootstrap script (${ARGOCD_SCRIPT}) populates and references rootCA"
  else
    fail "ArgoCD bootstrap script (${ARGOCD_SCRIPT}) missing rootCA population or reference"
  fi
fi

if [ -f "${CLIENTS_SCRIPT}" ]; then
  if grep -rqE "(insecureSkipVerify|oidc\.tls\.insecure\.skip\.verify)[[:space:]]*[:=][[:space:]]*['\"]?true['\"]?" "${CLIENTS_SCRIPT}"; then
    fail "Keycloak clients script (${CLIENTS_SCRIPT}) contains insecureSkipVerify: true"
  else
    pass "Keycloak clients script (${CLIENTS_SCRIPT}) does not bypass TLS verification"
  fi

  if grep -Fq 'rootCA: \$oidc.keycloak.rootCA' "${CLIENTS_SCRIPT}"; then
    pass "Keycloak clients script (${CLIENTS_SCRIPT}) preserves rootCA in argocd-cm"
  else
    fail "Keycloak clients script (${CLIENTS_SCRIPT}) missing rootCA in argocd-cm patch"
  fi
fi

# 5. Live cluster verification (if --live specified)
if [ "${LIVE_MODE}" = "true" ]; then
  echo "--- Live Cluster Checks ---"
  if ! kubectl get nodes >/dev/null 2>&1; then
    fail "Live cluster unreachable via current kubectl context"
  else
    if kubectl get secret narwhal-root-ca-secret -n platform-system >/dev/null 2>&1; then
      pass "Live cluster: narwhal-root-ca-secret exists in platform-system"
    else
      fail "Live cluster: narwhal-root-ca-secret missing in platform-system"
    fi

    ARGOCD_SECRET_CA=$(kubectl get secret argocd-secret -n devtools -o jsonpath='{.data.oidc\.keycloak\.rootCA}' 2>/dev/null || echo "")
    if [ -n "${ARGOCD_SECRET_CA}" ]; then
      pass "Live cluster: argocd-secret contains oidc.keycloak.rootCA"
    else
      fail "Live cluster: argocd-secret missing oidc.keycloak.rootCA"
    fi
  fi
fi

if [ "${FAILURES}" -ne 0 ]; then
  echo "ERROR: Keycloak OIDC TLS verification preflight failed with ${FAILURES} error(s)." >&2
  exit 1
fi

echo "Keycloak OIDC TLS verification preflight passed (0 failures)."
exit 0
