#!/bin/bash
set -euo pipefail

# =============================================================================
# promote-upgrade-bundle.sh — dev -> staged -> production promotion gate for
#                              air-gap upgrade bundles (narwhal#45)
#
# WHY THIS EXISTS:
#   Matches narwhal#48's promote-security-db.sh pattern for general air-gap
#   upgrade bundles. An incoming staged bundle must pass schema & policy validation
#   before being promoted to production. The promotion records an append-only
#   audit log (promotion-log.jsonl) with operator identity (--approved-by) and
#   keeps promoted-previous/ for safe 1-step rollback.
#
# USAGE:
#   promote-upgrade-bundle.sh --approved-by <name> [--bundle <dir>]
#   promote-upgrade-bundle.sh --rollback --approved-by <name> [--bundle <dir>]
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../00-config.sh
source "${SCRIPT_DIR}/../00-config.sh"

BUNDLE="${AIRGAP_BUNDLE_DIR}"
APPROVED_BY=""
ROLLBACK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)       BUNDLE="$2"; shift 2 ;;
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

BUNDLE_ROOT="${BUNDLE}/upgrade-bundle"
LOG_FILE="${BUNDLE_ROOT}/promotion-log.jsonl"
mkdir -p "${BUNDLE_ROOT}"

log_entry() {
  # $1=action $2=manifest-path
  python3 - "$1" "$2" "${APPROVED_BY}" "${LOG_FILE}" <<'PYEOF'
import json, sys, datetime
action, manifest_path, approved_by, log_file = sys.argv[1:5]
digests = None
bundle_id = None
target_version = None

if manifest_path and manifest_path != "null":
    try:
        with open(manifest_path, encoding="utf-8") as f:
            doc = json.load(f)
            bundle_id = doc.get("bundle_id")
            target_version = doc.get("target_version")
            digests = {a["name"]: a.get("digest") for a in doc.get("artifacts", []) if "name" in a}
    except Exception:
        pass

entry = {
    "action": action,
    "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "approved_by": approved_by,
    "bundle_id": bundle_id,
    "target_version": target_version,
    "digests": digests,
}
with open(log_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, sort_keys=True) + "\n")
PYEOF
}

if [[ "${ROLLBACK}" -eq 1 ]]; then
  if [[ ! -d "${BUNDLE_ROOT}/promoted-previous" ]]; then
    echo "ERROR: no promoted-previous/ to roll back to." >&2
    exit 1
  fi
  tmp="${BUNDLE_ROOT}/.promoted-swap-tmp"
  rm -rf "${tmp}"
  mv "${BUNDLE_ROOT}/promoted" "${tmp}"
  mv "${BUNDLE_ROOT}/promoted-previous" "${BUNDLE_ROOT}/promoted"
  mv "${tmp}" "${BUNDLE_ROOT}/promoted-previous"

  log_entry "rollback" "${BUNDLE_ROOT}/promoted/upgrade-bundle.json"
  echo "Rolled back upgrade bundle. promoted/ now matches prior promoted-previous/."
  exit 0
fi

STAGED_MANIFEST="${BUNDLE_ROOT}/staged/upgrade-bundle.json"
if [[ ! -f "${STAGED_MANIFEST}" ]]; then
  echo "ERROR: ${STAGED_MANIFEST} not found — stage a candidate bundle first." >&2
  exit 1
fi

# Validate staged manifest schema and license policy
if ! python3 "${SCRIPT_DIR}/verify-upgrade-bundle.py" --manifest "${STAGED_MANIFEST}"; then
  echo "ERROR: staged upgrade bundle failed schema/policy verification." >&2
  exit 1
fi

if [[ -d "${BUNDLE_ROOT}/promoted" ]]; then
  rm -rf "${BUNDLE_ROOT}/promoted-previous"
  mv "${BUNDLE_ROOT}/promoted" "${BUNDLE_ROOT}/promoted-previous"
fi

cp -r "${BUNDLE_ROOT}/staged" "${BUNDLE_ROOT}/promoted"

# Mark promotion_stage as production in promoted manifest
python3 - "${BUNDLE_ROOT}/promoted/upgrade-bundle.json" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path, "r+", encoding="utf-8") as f:
    doc = json.load(f)
    doc["promotion_stage"] = "production"
    f.seek(0)
    json.dump(doc, f, indent=2)
    f.truncate()
PYEOF

log_entry "promote" "${BUNDLE_ROOT}/promoted/upgrade-bundle.json"
echo "Promoted upgrade bundle: staged/ -> promoted/ (stage: production)."
echo "Audit Log: ${LOG_FILE}"
