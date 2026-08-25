#!/bin/bash
set -euo pipefail

# =============================================================================
# promote-security-db.sh — dev -> verified -> promoted gate for security-db
#                           artifacts fetched by fetch-security-db.sh (narwhal#48)
#
# WHY THIS EXISTS: AC "개발->검증->운영 DB promotion 단계와 승인 기록" and "DB
# rollback 및 이전 검증된 DB 재적용" — fetch-security-db.sh only stages a fresh
# download; nothing decided it is trustworthy or made it "the DB the cluster
# serves". This is that decision, recorded as an append-only log, with the
# previously-promoted set kept around for a one-step rollback — the same
# keep-the-old-one-until-the-new-one-is-proven shape as any release promotion,
# applied to a database instead of a container image.
#
# staged/   <- fetch-security-db.sh writes here (dev)
# promoted/ <- what push-security-db.sh reads (verified/prod)
# promoted-previous/ <- one rollback step
# promotion-log.jsonl <- append-only: every promote/rollback, who approved it, when
#
# USAGE:
#   promote-security-db.sh --approved-by <name> [--bundle <dir>] [--slo-days N]
#   promote-security-db.sh --rollback --approved-by <name> [--bundle <dir>]
#
# Refuses to promote a staged/ manifest that already fails the freshness SLO
# (check-security-db-freshness.py) — promoting something already stale defeats the
# point of a promotion gate.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../00-config.sh
source "${SCRIPT_DIR}/../00-config.sh"

BUNDLE="${AIRGAP_BUNDLE_DIR}"
SLO_DAYS="7"
APPROVED_BY=""
ROLLBACK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)       BUNDLE="$2"; shift 2 ;;
    --slo-days)     SLO_DAYS="$2"; shift 2 ;;
    --approved-by)  APPROVED_BY="$2"; shift 2 ;;
    --rollback)     ROLLBACK=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${APPROVED_BY}" ]]; then
  echo "ERROR: --approved-by <name> is required — a promotion/rollback with no recorded" >&2
  echo "       approver defeats the point of an approval log." >&2
  exit 1
fi

SECDB_DIR="${BUNDLE}/security-db"
LOG_FILE="${SECDB_DIR}/promotion-log.jsonl"
mkdir -p "${SECDB_DIR}"

log_entry() {
  # $1=action $2=digests-json-or-null
  python3 - "$1" "$2" "${APPROVED_BY}" "${LOG_FILE}" <<'PYEOF'
import json, sys, datetime
action, digests_json, approved_by, log_file = sys.argv[1:5]
entry = {
    "action": action,
    "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "approved_by": approved_by,
    "digests": json.loads(digests_json) if digests_json != "null" else None,
}
with open(log_file, "a") as f:
    f.write(json.dumps(entry, sort_keys=True) + "\n")
PYEOF
}

digests_of() {
  # $1=manifest.json path -> {"name": "digest", ...} as compact JSON, or "null"
  local manifest="$1"
  [[ -f "${manifest}" ]] || { echo "null"; return; }
  python3 - "${manifest}" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    doc = json.load(f)
print(json.dumps({a["name"]: a.get("digest") for a in doc.get("artifacts", [])}))
PYEOF
}

if [[ "${ROLLBACK}" -eq 1 ]]; then
  if [[ ! -d "${SECDB_DIR}/promoted-previous" ]]; then
    echo "ERROR: no promoted-previous/ to roll back to." >&2
    exit 1
  fi
  # Swap: current promoted/ <-> promoted-previous/, so a second rollback undoes the first.
  tmp="${SECDB_DIR}/.promoted-swap-tmp"
  rm -rf "${tmp}"
  mv "${SECDB_DIR}/promoted" "${tmp}"
  mv "${SECDB_DIR}/promoted-previous" "${SECDB_DIR}/promoted"
  mv "${tmp}" "${SECDB_DIR}/promoted-previous"

  digests="$(digests_of "${SECDB_DIR}/promoted/manifest.json")"
  log_entry "rollback" "${digests}"
  echo "Rolled back. promoted/ now matches the prior promoted-previous/."
  echo "Digests: ${digests}"
  exit 0
fi

STAGED_MANIFEST="${SECDB_DIR}/staged/manifest.json"
if [[ ! -f "${STAGED_MANIFEST}" ]]; then
  echo "ERROR: ${STAGED_MANIFEST} not found — run fetch-security-db.sh first." >&2
  exit 1
fi

if ! python3 "${SCRIPT_DIR}/check-security-db-freshness.py" "${STAGED_MANIFEST}" --slo-days "${SLO_DAYS}"; then
  echo "ERROR: staged security-db manifest already fails the freshness SLO — re-run" >&2
  echo "       fetch-security-db.sh before promoting." >&2
  exit 1
fi

if [[ -d "${SECDB_DIR}/promoted" ]]; then
  rm -rf "${SECDB_DIR}/promoted-previous"
  mv "${SECDB_DIR}/promoted" "${SECDB_DIR}/promoted-previous"
fi
cp -r "${SECDB_DIR}/staged" "${SECDB_DIR}/promoted"

digests="$(digests_of "${SECDB_DIR}/promoted/manifest.json")"
log_entry "promote" "${digests}"
echo "Promoted staged/ -> promoted/."
echo "Digests: ${digests}"
echo "Log: ${LOG_FILE}"
