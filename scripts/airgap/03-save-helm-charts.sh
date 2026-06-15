#!/bin/bash
set -euo pipefail

# =============================================================================
# 03-save-helm-charts.sh — Download all Helm charts as .tgz
# Parses chart source/repo/version from gitops/apps/*.yaml and scripts/cluster/*.sh
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUT_DIR="${AIRGAP_BUNDLE_DIR}/charts"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)  OUT_DIR="$2"; shift 2 ;;
    *)      echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "${OUT_DIR}"

if ! command -v helm >/dev/null 2>&1; then
  echo "ERROR: helm not installed" >&2
  exit 1
fi

# Parse chart (repo, name, version) tuples from gitops YAML
python3 - "${PROJECT_ROOT}/gitops/apps" <<'PYEOF' > /tmp/charts.txt
import os, re, sys
root = sys.argv[1]
seen = set()
for f in os.listdir(root):
    if not f.endswith('.yaml') or f == 'app-of-apps.yaml':
        continue
    txt = open(os.path.join(root, f)).read()
    repo = re.search(r'repoURL:\s*([^\s#]+)', txt)
    chart = re.search(r'chart:\s*([^\s#]+)', txt)
    ver = re.search(r'targetRevision:\s*["\']?([^"\'\s#]+)', txt)
    if repo and chart and ver:
        key = f"{repo.group(1)}|{chart.group(1)}|{ver.group(1)}"
        if key not in seen:
            seen.add(key)
            print(key)
PYEOF

ok=0; fail=0
while IFS='|' read -r repo chart version; do
  [[ -z "${repo}" ]] && continue
  echo "[chart] ${chart}-${version} from ${repo}"
  # Add repo with a unique alias to avoid collisions
  alias="airgap-$(echo "${repo}" | md5sum | cut -c1-8)"
  helm repo add "${alias}" "${repo}" --force-update >/dev/null 2>&1 || { fail=$((fail+1)); continue; }
  helm repo update "${alias}" >/dev/null 2>&1 || true
  if helm pull "${alias}/${chart}" --version "${version}" --destination "${OUT_DIR}" 2>&1 | tail -2; then
    ok=$((ok+1))
  else
    fail=$((fail+1))
  fi
done < /tmp/charts.txt

rm -f /tmp/charts.txt
echo ""
echo "Charts saved: ${ok} | Failed: ${fail}"
echo "Output: ${OUT_DIR}"
ls -la "${OUT_DIR}" | tail -20
