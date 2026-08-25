#!/bin/bash
set -euo pipefail

# =============================================================================
# fetch-security-db.sh — Stage vulnerability/security-scanner DB artifacts into
#                         the airgap bundle (narwhal#48)
#
# WHY THIS EXISTS:
#   gitops/charts/narwhal-apps/templates/trivy-operator.yaml runs Trivy in
#   Standalone mode, and Trivy fetches its vulnerability DB / Java DB / checks
#   bundle itself, over its own OCI registry client, from INSIDE the scan Job pod
#   at scan time. That pull does not go through containerd, so the existing
#   06-configure-mirrors.sh hosts.toml mirror (built for kubelet/containerd image
#   pulls) cannot intercept or redirect it — a scan Job in a network with no
#   egress will simply fail to fetch a DB. This script does the OTHER half of the
#   fix: pulling those artifacts once, on an internet-connected staging host, at
#   BUNDLE BUILD time — matching 02-save-images.sh's `skopeo copy docker://<ref>
#   oci:<dest>` idiom exactly, so this is the same pattern applied to a different
#   artifact list, not a new mechanism. (The other half — pointing Trivy at an
#   internal registry instead of ghcr.io — is the trivy-operator.yaml change.)
#
# WHY A SEPARATE MANIFEST FROM binary-checksums.tsv:
#   binary-checksums.tsv pins a SHA-256 that must match on every future fetch —
#   it assumes the content behind a given version is immutable, which is true for
#   a tagged GitHub release. It is NOT true here: ghcr.io/aquasecurity/trivy-db:2
#   is a live vulnerability feed re-published under the same tag ("2" is a DB
#   SCHEMA version, not a content version) roughly every 6 hours upstream. A
#   pinned golden digest would go stale within a day and turn this into a check
#   that always fails for the wrong reason. Instead, every fetch records the
#   digest it actually received — that record becomes the audit trail (which DB,
#   which digest, fetched when) that AC "scan 결과에 사용한 DB version/digest를
#   evidence로 기록" and "source/version/release date 메타데이터 기록" ask for,
#   and promote-security-db.sh (a separate script) is the deliberate promotion
#   gate that decides when a freshly staged fetch becomes what the cluster serves.
#
# INTEGRITY CHECK THIS SCRIPT DOES DO: it re-derives the digest of what actually
# landed on disk (skopeo inspect --raw against the local OCI layout) and compares
# it to the digest the registry reported BEFORE the copy — catching a truncated
# download or a swapped artifact mid-transfer, the same class of bug a pinned
# checksum defends against, just verified against itself instead of git history.
# It also enforces a minimum size per artifact (MIN_SIZE_BYTES below) as a floor
# against a redirect-to-an-error-page or empty-body response silently "succeeding".
#
# USAGE:
#   scripts/airgap/lib/fetch-security-db.sh [--list <security-db.txt>] [--bundle <dir>]
#
# Requires: skopeo (network access — run on an internet-connected staging host,
# same as 02-save-images.sh).
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../00-config.sh
source "${SCRIPT_DIR}/../00-config.sh"

LIST_FILE="${SCRIPT_DIR}/../security-db.txt"
BUNDLE="${AIRGAP_BUNDLE_DIR}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --list)   LIST_FILE="$2"; shift 2 ;;
    --bundle) BUNDLE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ ! -f "${LIST_FILE}" ]]; then
  echo "ERROR: security DB list not found: ${LIST_FILE}" >&2
  exit 1
fi

if ! command -v skopeo >/dev/null 2>&1; then
  echo "ERROR: skopeo not installed. Install: brew install skopeo (mac) / apt install skopeo (ubuntu)" >&2
  exit 1
fi

STAGED_DIR="${BUNDLE}/security-db/staged"
mkdir -p "${STAGED_DIR}"

# Sanity floor per artifact, in bytes — well under the smallest real fetch measured
# 2026-08-25 (trivy-checks ~170KB, trivy-db ~114MB, trivy-java-db ~955MB), so a hit
# means something is badly wrong (redirect page, empty body, auth challenge body),
# not routine content-size drift. Unknown names get no floor (0) rather than being
# rejected outright — security-db.txt is the source of truth for WHICH names exist.
min_size_for() {
  case "$1" in
    trivy-db)      echo 20000000 ;;   # 20MB
    trivy-java-db) echo 100000000 ;;  # 100MB
    trivy-checks)  echo 10000 ;;      # 10KB
    *)             echo 0 ;;
  esac
}

manifest_entries="[]"
fail=0
ok=0

while IFS=$'\t' read -r name ref; do
  [[ -z "${name}" || "${name}" =~ ^# ]] && continue
  tag="${ref##*:}"
  dest="${STAGED_DIR}/${name}"

  echo "[fetch] ${name} <- ${ref}"

  # Digest the registry reports BEFORE the copy — this is what "received intact"
  # gets checked against below, not a value pinned in git (see header).
  pre_digest="$(skopeo inspect --raw "docker://${ref}" 2>/dev/null | shasum -a 256 | cut -d' ' -f1 || true)"
  if [[ -z "${pre_digest}" ]]; then
    echo "[FAIL] ${name}: could not read manifest from ${ref}" >&2
    fail=$((fail + 1))
    continue
  fi

  rm -rf "${dest}"
  if ! skopeo copy --retry-times 3 --quiet "docker://${ref}" "oci:${dest}:${tag}" 2>&1 | tail -5; then
    echo "[FAIL] ${name}: skopeo copy failed" >&2
    fail=$((fail + 1))
    continue
  fi

  post_digest="$(skopeo inspect --raw "oci:${dest}:${tag}" 2>/dev/null | shasum -a 256 | cut -d' ' -f1 || true)"
  if [[ "${post_digest}" != "${pre_digest}" ]]; then
    echo "[FAIL] ${name}: local copy digest (${post_digest}) != registry digest (${pre_digest}) at fetch time" >&2
    fail=$((fail + 1))
    continue
  fi

  # BSD stat (macOS staging hosts) first, GNU stat (Linux staging hosts) as fallback —
  # -print0/-0 throughout so a filename with a space or newline can't split the sum.
  size_bytes="$(find "${dest}" -type f -print0 | xargs -0 stat -f%z 2>/dev/null | awk '{s+=$1} END{print s+0}')"
  if [[ "${size_bytes}" = "0" ]]; then
    size_bytes="$(find "${dest}" -type f -print0 | xargs -0 stat -c%s 2>/dev/null | awk '{s+=$1} END{print s+0}')"
  fi
  min_size="$(min_size_for "${name}")"
  if [[ "${size_bytes}" -lt "${min_size}" ]]; then
    echo "[FAIL] ${name}: staged size ${size_bytes}B is under the ${min_size}B sanity floor" >&2
    fail=$((fail + 1))
    continue
  fi

  fetched_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  entry=$(python3 - "$name" "$ref" "$tag" "sha256:${post_digest}" "${fetched_at}" "${size_bytes}" <<'PYEOF'
import json, sys
name, ref, tag, digest, fetched_at, size_bytes = sys.argv[1:7]
print(json.dumps({
    "name": name,
    "source_ref": ref,
    "tag": tag,
    "digest": digest,
    "fetched_at": fetched_at,
    "size_bytes": int(size_bytes),
}))
PYEOF
)
  manifest_entries="$(python3 - "${manifest_entries}" "${entry}" <<'PYEOF'
import json, sys
entries = json.loads(sys.argv[1])
entries.append(json.loads(sys.argv[2]))
print(json.dumps(entries))
PYEOF
)"
  echo "  ${name}: sha256:${post_digest} (${size_bytes}B) fetched ${fetched_at}"
  ok=$((ok + 1))
done < "${LIST_FILE}"

python3 - "${STAGED_DIR}/manifest.json" "${manifest_entries}" <<'PYEOF'
import json, sys
out_path, entries_json = sys.argv[1], sys.argv[2]
entries = json.loads(entries_json)
doc = {
    "$schema": "narwhal-security-db-manifest-v1",
    "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "artifacts": entries,
}
with open(out_path, "w") as f:
    json.dump(doc, f, indent=2, sort_keys=True)
    f.write("\n")
PYEOF

echo ""
echo "Fetched: ${ok} | Failed: ${fail}"
echo "Manifest: ${STAGED_DIR}/manifest.json"
[[ ${fail} -eq 0 ]]
