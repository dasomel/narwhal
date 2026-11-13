#!/bin/bash
set -euo pipefail

# =============================================================================
# push-security-db.sh — Push the PROMOTED security-db artifacts into the cluster's
#                        internal registry (narwhal#48)
#
# WHY HARBOR, NOT registry.airgap.local (AIRGAP_REGISTRY): AIRGAP_REGISTRY is a
# host-level bootstrap registry that containerd on each node is pointed at via
# /etc/containerd/certs.d hosts.toml (06-configure-mirrors.sh) — that mirror only
# intercepts CRI/containerd image pulls (kubelet pulling pod images). Trivy's DB
# fetch is a plain HTTP call the trivy BINARY makes for itself from inside the scan
# Job's network namespace; it never goes through containerd, so it never sees that
# mirror. It needs an endpoint reachable over the ordinary pod network instead — a
# real Kubernetes Service. Harbor already runs one: `harbor.devtools.svc.cluster.local`
# on port 80, which is where Trivy PULLS from anonymously (confirmed against gitops/charts/narwhal-platform/templates/apisix-routes.yaml,
# the ApisixUpstream backing the public harbor.<domain> route). This script PUSHES to
# the external https URL instead (D-SECDB-PUSH-ENDPOINT below). The pull registry is the
# one gitops/resources/trivy-operator.yaml's dbRegistry/javaDbRegistry/
# policiesBundle.registry now point at.
#
# WHY A PUBLIC PROJECT, NOT A ROBOT-ACCOUNT SECRET: this repo's rules (docs/common/agent-operational-rules.md) forbid
# hardcoding a password/token in any script or manifest, and the trivy-operator
# Helm chart (0.27.0) has no existingSecret option for dbRepository credentials —
# only plaintext dbRepositoryUsername/dbRepositoryPassword values fields, which
# would put a credential in git. A Harbor project marked public allows anonymous
# pull with no credential at all; scoped to a network with no path to the public
# internet in the first place (that is the entire premise of this issue), an
# unauthenticated *read-only pull* of a vulnerability DB inside the cluster's own
# network is an acceptable trade against embedding a secret in gitops/. Pushing
# still requires the Harbor admin credential, which this script reads the same way
# scripts/cluster/08-5-registry.sh already does (K8s Secret, never a literal).
#
# PROJECT CREATION: the harbor-secrets Secret + `trivy-db` public project bootstrap
# is scripts/cluster/08-5-registry.sh's job (it already owns Harbor's initial state).
# This script assumes that project already exists and fails with a clear message if
# the push is rejected for a missing project, rather than trying to create it itself
# with a second, redundant credential path.
#
# USAGE:
#   push-security-db.sh [--bundle <dir>] [--registry harbor.<domain>] [--project trivy-db]
#                        [--ca-cert <ca.crt>] [--kube-context <ctx>]
#
# Run this from wherever harbor.<domain> resolves and is reachable over https (a workstation
# on the cluster DNS, or master-1). Local Vagrant: `--ca-cert` = the narwhal-ca-cert Secret's
# ca.crt, `--kube-context narwhal`. A plain-HTTP port-forward does NOT work (see D-SECDB-PUSH-ENDPOINT).
#
# VERIFIED (2026-08-25): fetch -> promote -> push -> pull round-tripped end to end
# against a throwaway local `docker run registry:2` (skopeo copy oci:... docker://...,
# an earlier plain-HTTP, unauthenticated variant of this script) — the pulled-back manifest was byte-identical
# to the original ghcr.io/aquasecurity/trivy-db:2 manifest (same config + layer digests).
# Since then (2026-11-04) the push is authenticated and https (admin credential in a 0600
# authfile, optional --ca-cert) and was run against the real Harbor on the local Vagrant
# cluster. The Kakao Cloud cluster is destroyed, so that path is unverified live.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../00-config.sh
source "${SCRIPT_DIR}/../00-config.sh"

BUNDLE="${AIRGAP_BUNDLE_DIR}"
# D-SECDB-PUSH-ENDPOINT: the push endpoint must be Harbor's EXTERNAL URL (harbor.<domain>,
# https + the Narwhal CA), not the in-cluster Service. Harbor's registry token realm is built
# from its externalURL (https://harbor.<domain>/service/token), so a plain-HTTP push to the
# Service or to a port-forward gets "http: server gave HTTP response to HTTPS client" the
# moment skopeo follows the realm. (Trivy still PULLS from the in-cluster Service,
# anonymously, via the public project — only this push needs the token flow.)
SECDB_REGISTRY="${AIRGAP_SECURITY_DB_REGISTRY:-harbor.${DOMAIN}}"
SECDB_PROJECT="${AIRGAP_SECURITY_DB_PROJECT:-trivy-db}"
# Optional: CA bundle (PEM) that signed the Harbor cert. Needed on a workstation that does
# not already trust the Narwhal root CA (the equivalent of Docker's certs.d/<host>/ca.crt).
SECDB_CA_CERT="${AIRGAP_SECURITY_DB_CA_CERT:-}"
# Optional: kubectl context used to read the Harbor admin password from the harbor-secrets
# Secret when HARBOR_ADMIN_PASSWORD is not exported. The password is never printed or put on
# a command line (it goes to a 0600 authfile via --password-stdin).
SECDB_KUBE_CONTEXT="${AIRGAP_SECURITY_DB_KUBE_CONTEXT:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)   BUNDLE="$2"; shift 2 ;;
    --registry) SECDB_REGISTRY="$2"; shift 2 ;;
    --project)  SECDB_PROJECT="$2"; shift 2 ;;
    --ca-cert)  SECDB_CA_CERT="$2"; shift 2 ;;
    --kube-context) SECDB_KUBE_CONTEXT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

PROMOTED_DIR="${BUNDLE}/security-db/promoted"
MANIFEST="${PROMOTED_DIR}/manifest.json"

if [[ ! -f "${MANIFEST}" ]]; then
  echo "ERROR: ${MANIFEST} not found. Run fetch-security-db.sh then promote-security-db.sh first." >&2
  exit 1
fi

if ! command -v skopeo >/dev/null 2>&1; then
  echo "ERROR: skopeo not installed" >&2
  exit 1
fi

# Refuse to push a promoted set that has gone stale between promotion and push time.
if ! python3 "${SCRIPT_DIR}/check-security-db-freshness.py" "${MANIFEST}"; then
  echo "ERROR: promoted security-db manifest fails the freshness SLO — re-fetch and re-promote." >&2
  exit 1
fi

# Keep the credential out of xtrace (`bash -x` prints expanded assignments and arguments);
# the previous xtrace state is restored right after the login.
case "$-" in *x*) XTRACE_WAS_ON=1; set +x ;; *) XTRACE_WAS_ON=0 ;; esac
# Push credential: env first, else the same harbor-secrets Secret 08-5-registry.sh reads.
HARBOR_PW="${HARBOR_ADMIN_PASSWORD:-}"
if [[ -z "${HARBOR_PW}" ]] && command -v kubectl >/dev/null 2>&1; then
  HARBOR_PW="$(kubectl ${SECDB_KUBE_CONTEXT:+--context "${SECDB_KUBE_CONTEXT}"} get secret harbor-secrets -n devtools \
    -o jsonpath='{.data.HARBOR_ADMIN_PASSWORD}' 2>/dev/null | base64 -d 2>/dev/null || true)"
fi
if [[ -z "${HARBOR_PW}" ]]; then
  [[ "${XTRACE_WAS_ON}" -eq 1 ]] && set -x
  echo "ERROR: no Harbor admin credential. Export HARBOR_ADMIN_PASSWORD or make kubectl able to read" >&2
  echo "       secret devtools/harbor-secrets (--kube-context). Anonymous push is not possible:" >&2
  echo "       a public Harbor project only allows anonymous PULL." >&2
  exit 1
fi
AUTHFILE="$(mktemp)"; CERTDIR="$(mktemp -d)"
trap 'rm -rf "${AUTHFILE}" "${CERTDIR}"' EXIT
chmod 600 "${AUTHFILE}"
echo "{}" > "${AUTHFILE}"   # skopeo login rejects a zero-byte authfile
LOGIN_CERT=(); COPY_CERT=()   # empty-array safe expansions below (macOS bash 3.2 + set -u)
if [[ -n "${SECDB_CA_CERT}" ]]; then
  [[ -f "${SECDB_CA_CERT}" ]] || { echo "ERROR: --ca-cert ${SECDB_CA_CERT} not found" >&2; exit 1; }
  # Both `skopeo login --cert-dir` and `skopeo copy --dest-cert-dir` read *.crt straight from the
  # directory given; a certs.d parent (<dir>/<host>/ca.crt) is not searched (skopeo 1.24.1, 2026-11-14:
  # the copy failed "certificate is not trusted" once the CA was not also in the OS trust store).
  mkdir -p "${CERTDIR}/${SECDB_REGISTRY}"
  cp "${SECDB_CA_CERT}" "${CERTDIR}/${SECDB_REGISTRY}/ca.crt"
  LOGIN_CERT=(--cert-dir "${CERTDIR}/${SECDB_REGISTRY}"); COPY_CERT=(--dest-cert-dir "${CERTDIR}/${SECDB_REGISTRY}")
fi
if ! printf '%s' "${HARBOR_PW}" | skopeo login ${LOGIN_CERT[@]+"${LOGIN_CERT[@]}"} \
     --authfile "${AUTHFILE}" -u admin --password-stdin "${SECDB_REGISTRY}" >/dev/null 2>&1; then
  [[ "${XTRACE_WAS_ON}" -eq 1 ]] && set -x
  echo "ERROR: Harbor login to ${SECDB_REGISTRY} failed (bad credential, untrusted CA -> pass --ca-cert, or unreachable)." >&2
  exit 1
fi
unset HARBOR_PW
[[ "${XTRACE_WAS_ON}" -eq 1 ]] && set -x

ok=0; fail=0
while IFS= read -r name; do
  [[ -z "${name}" ]] && continue
  src="${PROMOTED_DIR}/${name}"
  [[ -d "${src}" ]] || { echo "[skip] ${name} (no OCI layout under ${src})"; continue; }

  ref="$(python3 -c "
import json
doc = json.load(open('${MANIFEST}'))
for a in doc['artifacts']:
    if a['name'] == '${name}':
        print(a['source_ref']); break
")"
  # Preserve the upstream org/repo path under the internal project, e.g.
  # ghcr.io/aquasecurity/trivy-db:2 -> harbor.../trivy-db/aquasecurity/trivy-db:2
  # — matches dbRepository=trivy-db/aquasecurity/trivy-db in trivy-operator.yaml.
  repo_path="${ref%:*}"
  repo_path="${repo_path#*/}"   # strip the leading registry host
  tag="${ref##*:}"
  dst="${SECDB_REGISTRY}/${SECDB_PROJECT}/${repo_path}:${tag}"

  echo "[push] ${name} -> ${dst}"
  if skopeo copy --authfile "${AUTHFILE}" ${COPY_CERT[@]+"${COPY_CERT[@]}"} --retry-times 3 --quiet "oci:${src}:${tag}" "docker://${dst}"; then
    ok=$((ok + 1))
  else
    echo "[FAIL] ${name} -> ${dst} (check the '${SECDB_PROJECT}' Harbor project exists - scripts/cluster/08-5-registry.sh - and that the push endpoint/CA are right; a very large blob can also trip Harbor's liveness probe on a small VM: re-run, the push is idempotent)" >&2
    fail=$((fail + 1))
  fi
done < <(python3 -c "
import json
doc = json.load(open('${MANIFEST}'))
for a in doc['artifacts']:
    print(a['name'])
")

echo ""
echo "Pushed: ${ok} | Failed: ${fail}"
[[ ${fail} -eq 0 ]]
