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
#                        [--ca-cert <ca.crt>] [--kube-context <ctx>] [--allow-downgrade]
#
# Run this from wherever harbor.<domain> resolves and is reachable over https (a workstation
# on the cluster DNS, or master-1). Local Vagrant: `--ca-cert` = the narwhal-ca-cert Secret's (namespace devtools)
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
# Optional: allow overwriting a newer mirror DB with an older bundle DB (escape hatch for
# intentional rollback via promote-security-db.sh --rollback).
ALLOW_DOWNGRADE="${AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)   BUNDLE="$2"; shift 2 ;;
    --registry) SECDB_REGISTRY="$2"; shift 2 ;;
    --project)  SECDB_PROJECT="$2"; shift 2 ;;
    --ca-cert)  SECDB_CA_CERT="$2"; shift 2 ;;
    --kube-context) SECDB_KUBE_CONTEXT="$2"; shift 2 ;;
    --allow-downgrade) ALLOW_DOWNGRADE=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

case "${ALLOW_DOWNGRADE}" in
  1|true|TRUE|yes|YES) ALLOW_DOWNGRADE=1 ;;
  0|false|FALSE|no|NO|"") ALLOW_DOWNGRADE=0 ;;
  *) echo "ERROR: invalid AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE=${ALLOW_DOWNGRADE} (expected 0 or 1)" >&2; exit 1 ;;
esac

CA_TMPDIR=""
AUTHFILE=""
CERTDIR=""

cleanup() {
  local rc=$?
  # Best effort: a failing rm must not skip the other paths or change the exit code.
  set +e
  [[ -n "${CA_TMPDIR:-}" ]] && rm -rf "${CA_TMPDIR}" 2>/dev/null
  [[ -n "${AUTHFILE:-}" ]] && rm -rf "${AUTHFILE}" 2>/dev/null
  [[ -n "${CERTDIR:-}" ]] && rm -rf "${CERTDIR}" 2>/dev/null
  exit "${rc}"
}
trap cleanup EXIT

# Validate --ca-cert early before any expensive network or cluster operations.
validate_ca_cert() {
  local cert="$1"
  if [[ ! -f "${cert}" ]]; then
    echo "ERROR: --ca-cert ${cert} not found" >&2
    exit 1
  fi
  if [[ ! -r "${cert}" ]]; then
    echo "ERROR: --ca-cert ${cert} is not readable" >&2
    exit 1
  fi
  if [[ ! -s "${cert}" ]]; then
    echo "ERROR: --ca-cert ${cert} is empty" >&2
    exit 1
  fi
  if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl is required to validate --ca-cert" >&2
    exit 1
  fi

  CA_TMPDIR="$(mktemp -d "${TMPDIR:-/tmp}/secdb-ca.XXXXXX")"

  awk -v dir="${CA_TMPDIR}" '
    /-----BEGIN CERTIFICATE-----/ {
      if (in_cert && out != "") {
        close(out)
      }
      in_cert = 1
      count++
      out = sprintf("%s/cert-%04d.crt", dir, count)
    }
    in_cert {
      sub(/\r$/, "")
      print > out
      if (/-----END CERTIFICATE-----/) {
        close(out)
        in_cert = 0
      }
    }
  ' "${cert}"

  local cert_blocks=()
  for b in "${CA_TMPDIR}"/cert-*.crt; do
    [[ -f "${b}" ]] && cert_blocks+=("${b}")
  done

  if [[ ${#cert_blocks[@]} -eq 0 ]]; then
    echo "ERROR: --ca-cert ${cert} is not a valid PEM certificate" >&2
    exit 1
  fi

  local valid_count=0
  local invalid_count=0

  for b in "${cert_blocks[@]}"; do
    if ! openssl x509 -in "${b}" -noout >/dev/null 2>&1; then
      echo "ERROR: --ca-cert ${cert} is not a valid PEM certificate" >&2
      exit 1
    fi

    local is_valid=1
    if ! openssl x509 -in "${b}" -noout -checkend 0 >/dev/null 2>&1; then
      is_valid=0
    fi

    if [[ ${is_valid} -eq 1 ]]; then
      local nb
      nb="$(openssl x509 -in "${b}" -noout -startdate 2>/dev/null | cut -d= -f2-)"
      if ! python3 -c "
import sys, datetime
raw = sys.argv[1].strip()
cleaned = ' '.join(raw.split())
t = datetime.datetime.strptime(cleaned, '%b %d %H:%M:%S %Y GMT').replace(tzinfo=datetime.timezone.utc)
sys.exit(0 if t <= datetime.datetime.now(datetime.timezone.utc) else 1)
" "${nb}" >/dev/null 2>&1; then
        is_valid=0
      fi
    fi

    if [[ ${is_valid} -eq 1 ]]; then
      valid_count=$((valid_count + 1))
    else
      invalid_count=$((invalid_count + 1))
    fi
  done

  if [[ ${valid_count} -eq 0 ]]; then
    echo "ERROR: --ca-cert ${cert} has no valid certificate (has expired or is not yet valid)" >&2
    exit 1
  fi

  if [[ ${invalid_count} -gt 0 ]]; then
    echo "WARN: --ca-cert ${cert} contains ${invalid_count} expired or not-yet-valid certificate(s)" >&2
  fi
}

if [[ -n "${SECDB_CA_CERT}" ]]; then
  validate_ca_cert "${SECDB_CA_CERT}"
fi

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
AUTHFILE="$(mktemp "${TMPDIR:-/tmp}/secdb-auth.XXXXXX")"; CERTDIR="$(mktemp -d "${TMPDIR:-/tmp}/secdb-cert.XXXXXX")"
chmod 600 "${AUTHFILE}"
echo "{}" > "${AUTHFILE}"   # skopeo login rejects a zero-byte authfile
LOGIN_CERT=(); COPY_CERT=()   # empty-array safe expansions below (macOS bash 3.2 + set -u)
if [[ -n "${SECDB_CA_CERT}" ]]; then
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

# D-SECDB-NO-DOWNGRADE (#658): refuse to overwrite a fresher DB in Harbor with an older
# airgap bundle DB (e.g. a manual push after trivy-db-refresh already updated the mirror,
# which caused trivy-server to revert to an expired DB and hot-download 2 GB/h).
# Compare OCI manifest `annotations["org.opencontainers.image.created"]` (full RFC 3339
# with arbitrary-length fractional seconds and Z/[+-]HH:MM offsets) and raw manifest sha256
# via `skopeo inspect --raw` instead of pulling 140MB-1GB layers to read metadata.json
# (measured against live Harbor and ghcr.io trivy-db:2 / trivy-java-db:1 / trivy-checks:1).
# Equal `created` skips only when the raw manifest digest also matches; equal `created` with
# a different digest is not a downgrade and is pushed. To shrink the TOCTOU race with
# trivy-db-refresh, each queued artifact is re-inspected immediately before its `skopeo copy`
# and refused if the mirror moved ahead of the bundle. Accepted residual risk: OCI registries
# offer no atomic compare-and-swap on tag writes, so a seconds-wide TOCTOU window remains
# between the pre-copy re-inspect and `skopeo copy` manifest commit. Reason: avoiding a custom
# registry lock keeps the script self-contained against standard OCI/Harbor APIs. Cost: fails
# closed if a present manifest lacks a parseable RFC 3339 `org.opencontainers.image.created`,
# assumes that annotation reflects DB freshness, and a concurrent refresh winning inside the
# final seconds-wide window can still land until the next refresh or manual push. Escape hatch:
# --allow-downgrade or AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE=1 (for intentional rollback via
# promote-security-db.sh --rollback).
secdb_manifest_py() {
  python3 - "$@" <<'PYEOF'
from __future__ import annotations

import datetime
import decimal
import hashlib
import json
import re
import sys

_RFC3339_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?([Zz]|([+-])(\d{2}):(\d{2}))\Z"
)


def parse_rfc3339(raw: str) -> tuple[datetime.datetime, decimal.Decimal]:
    m = _RFC3339_RE.match(raw)
    if not m:
        raise ValueError(f"invalid RFC 3339 timestamp: {raw!r}")
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    frac_str = m.group(7)
    tz_str = m.group(8)
    if tz_str in ("Z", "z"):
        tz = datetime.timezone.utc
    else:
        sign = 1 if m.group(9) == "+" else -1
        off_h = int(m.group(10))
        off_m = int(m.group(11))
        if off_h > 23 or off_m > 59:
            raise ValueError(f"invalid RFC 3339 offset: {raw!r}")
        tz = datetime.timezone(sign * datetime.timedelta(hours=off_h, minutes=off_m))
    dt = datetime.datetime(year, month, day, hour, minute, second, tzinfo=tz)
    dt_utc = dt.astimezone(datetime.timezone.utc)
    frac = decimal.Decimal(f"0.{frac_str}") if frac_str else decimal.Decimal(0)
    return (dt_utc, frac)


mode = sys.argv[1]
if mode == "inspect":
    with open(sys.argv[2], "rb") as f:
        raw_bytes = f.read()
    digest = "sha256:" + hashlib.sha256(raw_bytes).hexdigest()
    try:
        doc = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        sys.exit(2)
    anns = doc.get("annotations")
    created = anns.get("org.opencontainers.image.created") if isinstance(anns, dict) else None
    if not isinstance(created, str) or not created:
        sys.exit(3)
    try:
        parse_rfc3339(created)
    except ValueError:
        sys.exit(3)
    print(f"{digest}\t{created}")
elif mode == "compare":
    b = parse_rfc3339(sys.argv[2])
    m = parse_rfc3339(sys.argv[3])
    if b < m:
        print("older")
    elif b == m:
        print("equal")
    else:
        print("newer")
else:
    sys.exit(4)
PYEOF
}

is_mirror_absent_err() {
  local err_file="$1"
  grep -qiE 'manifest unknown|manifest_unknown|name_unknown|tag_unknown|not_found|repository [^[:space:]]+ not found|manifest [^[:space:]]+ not found|unknown: .*not found' "${err_file}"
}

INSPECT_ERR="${CERTDIR}/inspect.err"
RAW_MANIFEST="${CERTDIR}/inspect.raw"
MIRROR_ACTION="fail"

check_mirror_freshness() {
  local name="$1" dst="$2" bundle_created="$3" bundle_digest="$4" phase="$5"
  local mirror_rc=0 mirror_meta="" mirror_digest="" mirror_created="" cmp_result=""
  MIRROR_ACTION="fail"

  skopeo inspect --authfile "${AUTHFILE}" ${LOGIN_CERT[@]+"${LOGIN_CERT[@]}"} --raw "docker://${dst}" >"${RAW_MANIFEST}" 2>"${INSPECT_ERR}" || mirror_rc=$?
  if [[ "${mirror_rc}" -ne 0 ]]; then
    if is_mirror_absent_err "${INSPECT_ERR}"; then
      if [[ "${phase}" == "preflight" ]]; then
        echo "[check] ${name}: mirror ${dst} is empty/missing (bundle created=${bundle_created}) -> will push"
      fi
      MIRROR_ACTION="push"
    else
      echo "[FAIL] ${name}: cannot inspect mirror ${dst} during ${phase} (mirror unreachable or unreadable; refusing to guess): $(tr '\n' ' ' < "${INSPECT_ERR}")" >&2
      MIRROR_ACTION="fail"
    fi
    return 0
  fi

  if ! mirror_meta="$(secdb_manifest_py inspect "${RAW_MANIFEST}")"; then
    if [[ "${ALLOW_DOWNGRADE}" -eq 1 ]]; then
      echo "[warn] ${name}: mirror ${dst} lacks valid org.opencontainers.image.created; overriding per --allow-downgrade" >&2
      MIRROR_ACTION="push"
    else
      echo "[FAIL] ${name}: mirror ${dst} lacks a valid org.opencontainers.image.created annotation during ${phase} (cannot verify freshness; pass --allow-downgrade to override)" >&2
      MIRROR_ACTION="fail"
    fi
    return 0
  fi

  IFS=$'\t' read -r mirror_digest mirror_created <<< "${mirror_meta}"
  cmp_result="$(secdb_manifest_py compare "${bundle_created}" "${mirror_created}")"
  case "${cmp_result}" in
    older)
      if [[ "${ALLOW_DOWNGRADE}" -eq 1 ]]; then
        echo "[warn] ${name}: bundle (${bundle_created}) is OLDER than mirror (${mirror_created}); overriding per --allow-downgrade" >&2
        MIRROR_ACTION="push"
      else
        echo "[FAIL] ${name}: refusing to downgrade ${dst} (${phase}) — bundle (${bundle_created}) is older than mirror (${mirror_created}). Re-fetch/promote a newer bundle or pass --allow-downgrade (AIRGAP_SECURITY_DB_ALLOW_DOWNGRADE=1) to override." >&2
        MIRROR_ACTION="fail"
      fi
      ;;
    equal)
      if [[ "${bundle_digest}" == "${mirror_digest}" ]]; then
        echo "[skip] ${name}: mirror ${dst} already at ${bundle_created} (${bundle_digest}, equal freshness)"
        MIRROR_ACTION="skip"
      else
        if [[ "${phase}" == "preflight" ]]; then
          echo "[check] ${name}: bundle and mirror share ${bundle_created} but manifest digests differ (${bundle_digest} != ${mirror_digest}) -> will push"
        fi
        MIRROR_ACTION="push"
      fi
      ;;
    newer)
      if [[ "${phase}" == "preflight" ]]; then
        echo "[check] ${name}: bundle (${bundle_created}) is newer than mirror (${mirror_created}) -> will push"
      fi
      MIRROR_ACTION="push"
      ;;
  esac
}

push_names=()
push_srcs=()
push_tags=()
push_dsts=()
push_createds=()
push_digests=()
skipped=0
guard_fail=0

while IFS=$'\t' read -r name ref; do
  [[ -z "${name}" ]] && continue
  src="${PROMOTED_DIR}/${name}"
  if [[ ! -d "${src}" ]]; then
    echo "[skip] ${name} (no OCI layout under ${src})"
    skipped=$((skipped + 1))
    continue
  fi

  # Preserve the upstream org/repo path under the internal project, e.g.
  # ghcr.io/aquasecurity/trivy-db:2 -> harbor.../trivy-db/aquasecurity/trivy-db:2
  # — matches dbRepository=trivy-db/aquasecurity/trivy-db in trivy-operator.yaml.
  repo_path="${ref%:*}"
  repo_path="${repo_path#*/}"   # strip the leading registry host
  tag="${ref##*:}"
  dst="${SECDB_REGISTRY}/${SECDB_PROJECT}/${repo_path}:${tag}"

  if ! skopeo inspect --authfile "${AUTHFILE}" --raw "oci:${src}:${tag}" >"${RAW_MANIFEST}" 2>"${INSPECT_ERR}"; then
    echo "[FAIL] ${name}: cannot inspect bundle OCI manifest oci:${src}:${tag}: $(tr '\n' ' ' < "${INSPECT_ERR}")" >&2
    guard_fail=$((guard_fail + 1))
    continue
  fi
  if ! bundle_meta="$(secdb_manifest_py inspect "${RAW_MANIFEST}")"; then
    echo "[FAIL] ${name}: bundle OCI manifest oci:${src}:${tag} lacks a valid org.opencontainers.image.created annotation" >&2
    guard_fail=$((guard_fail + 1))
    continue
  fi
  IFS=$'\t' read -r bundle_digest bundle_created <<< "${bundle_meta}"

  check_mirror_freshness "${name}" "${dst}" "${bundle_created}" "${bundle_digest}" "preflight"
  case "${MIRROR_ACTION}" in
    skip)
      skipped=$((skipped + 1))
      continue
      ;;
    fail)
      guard_fail=$((guard_fail + 1))
      continue
      ;;
    push)
      ;;
  esac

  push_names+=("${name}")
  push_srcs+=("${src}")
  push_tags+=("${tag}")
  push_dsts+=("${dst}")
  push_createds+=("${bundle_created}")
  push_digests+=("${bundle_digest}")
done < <(python3 - "${MANIFEST}" <<'PYEOF'
import json, sys
doc = json.load(open(sys.argv[1]))
for a in doc["artifacts"]:
    print(f"{a['name']}\t{a['source_ref']}")
PYEOF
)

if [[ "${guard_fail}" -gt 0 ]]; then
  echo "" >&2
  echo "ERROR: freshness guard aborted push (${guard_fail} artifact(s) failed preflight; nothing was pushed)." >&2
  exit 1
fi

ok=0; fail=0
idx=0
while [[ "${idx}" -lt "${#push_names[@]}" ]]; do
  name="${push_names[${idx}]}"
  src="${push_srcs[${idx}]}"
  tag="${push_tags[${idx}]}"
  dst="${push_dsts[${idx}]}"
  bundle_created="${push_createds[${idx}]}"
  bundle_digest="${push_digests[${idx}]}"
  idx=$((idx + 1))

  check_mirror_freshness "${name}" "${dst}" "${bundle_created}" "${bundle_digest}" "pre-copy"
  case "${MIRROR_ACTION}" in
    skip)
      skipped=$((skipped + 1))
      continue
      ;;
    fail)
      echo "ERROR: pre-copy freshness guard aborted push at ${name} (stopping remaining copies)." >&2
      fail=$((fail + 1))
      break
      ;;
    push)
      ;;
  esac

  echo "[push] ${name} -> ${dst}"
  if skopeo copy --authfile "${AUTHFILE}" ${COPY_CERT[@]+"${COPY_CERT[@]}"} --retry-times 3 --quiet "oci:${src}:${tag}" "docker://${dst}"; then
    ok=$((ok + 1))
  else
    echo "[FAIL] ${name} -> ${dst} (check the '${SECDB_PROJECT}' Harbor project exists - scripts/cluster/08-5-registry.sh - and that the push endpoint/CA are right; a very large blob can also trip Harbor's liveness probe on a small VM: re-run, the push is idempotent)" >&2
    fail=$((fail + 1))
  fi
done

echo ""
echo "Pushed: ${ok} | Skipped: ${skipped} | Failed: ${fail}"
[[ ${fail} -eq 0 ]]
