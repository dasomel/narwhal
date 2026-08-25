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
# on port 80 (confirmed against gitops/charts/narwhal-platform/templates/apisix-routes.yaml,
# the ApisixUpstream backing the public harbor.<domain> route — same Service, just
# reached from inside the cluster instead of through APISIX/ingress). That is the
# registry gitops/resources/trivy-operator.yaml's dbRegistry/javaDbRegistry/
# policiesBundle.registry now point at.
#
# WHY A PUBLIC PROJECT, NOT A ROBOT-ACCOUNT SECRET: this repo's CLAUDE.md forbids
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
#   push-security-db.sh [--bundle <dir>] [--registry harbor.devtools.svc.cluster.local]
#                        [--project trivy-db]
#
# Run this from wherever harbor.devtools.svc.cluster.local resolves and is reachable
# — inside the cluster network (e.g. master-1, or `kubectl port-forward` to a local
# port and pass --registry localhost:<port>).
#
# VERIFIED (2026-08-25): fetch -> promote -> push -> pull round-tripped end to end
# against a throwaway local `docker run registry:2` (skopeo copy oci:... docker://...,
# same TLS-disabled path this script uses) — the pulled-back manifest was byte-identical
# to the original ghcr.io/aquasecurity/trivy-db:2 manifest (same config + layer digests).
# NOT verified against the real Harbor endpoint or from inside the cluster network — the
# Kakao Cloud cluster this script targets is currently destroyed (see narwhal#48 issue
# comment), so harbor.devtools.svc.cluster.local reachability and the `trivy-db` project's
# existence/public visibility are unverified live.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../00-config.sh
source "${SCRIPT_DIR}/../00-config.sh"

BUNDLE="${AIRGAP_BUNDLE_DIR}"
SECDB_REGISTRY="${AIRGAP_SECURITY_DB_REGISTRY:-harbor.devtools.svc.cluster.local}"
SECDB_PROJECT="${AIRGAP_SECURITY_DB_PROJECT:-trivy-db}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)   BUNDLE="$2"; shift 2 ;;
    --registry) SECDB_REGISTRY="$2"; shift 2 ;;
    --project)  SECDB_PROJECT="$2"; shift 2 ;;
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

TLS_OPT="--dest-tls-verify=false"

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
  if skopeo copy ${TLS_OPT} --retry-times 3 --quiet "oci:${src}:${tag}" "docker://${dst}"; then
    ok=$((ok + 1))
  else
    echo "[FAIL] ${name} -> ${dst} (is the '${SECDB_PROJECT}' Harbor project created and public? see scripts/cluster/08-5-registry.sh)" >&2
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
