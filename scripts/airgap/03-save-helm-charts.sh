#!/bin/bash
set -euo pipefail

# =============================================================================
# 03-save-helm-charts.sh — Download every Helm chart needed for an airgap install
#
# Chart discovery covers TWO sources (a from-scratch airgap install needs both):
#   1. GitOps charts — chart/version are parsed from
#      gitops/charts/narwhal-apps/templates/*.yaml (ArgoCD Application manifests,
#      one per file); their real pre-bootstrap upstream source comes from
#      lib/chart-upstream-sources.tsv. Application repoURLs deliberately name the
#      in-cluster Gitea Helm registry so ongoing ArgoCD reconciliation remains
#      offline, but that registry does not exist yet while this bundle is built.
#      Only templates with a literal chart + targetRevision are collected; local-path
#      apps (no chart key — e.g. narwhal-platform.yaml, *-policies.yaml) and
#      Applications disabled via `{{- if false }}` (none today; falco.yaml carried it
#      until D-FALCO-045, 2026-10-28, and its chart is bundled again) are skipped.
#      Files are Helm templates containing `{{ }}` syntax, so they're regex-scanned,
#      not YAML-parsed.
#   2. Bootstrap charts — installed by scripts/cluster/*.sh and
#      scripts/common/*.sh BEFORE ArgoCD exists (kube-vip, CNI, CNPG, ingress,
#      monitoring, etc). Parsed from `helm repo add <alias> <url>` plus
#      `helm upgrade --install <rel> <alias>/<chart> ... --version <v>` /
#      `helm pull <alias>/<chart> --version <v>` invocations. `<v>` may be a
#      literal or a ${VAR}/"${VAR}" reference, resolved by grepping the var's
#      default assignment (`VAR="${VAR:-default}"` etc.) across scripts/**.
#      Local-directory installs (e.g. 05-nfs-quota-agent.sh's in-repo
#      CHART_DIR) have no repo alias and are skipped automatically.
#      Special case: Cilium is installed via the `cilium` CLI, not helm, so
#      its chart is added manually from https://helm.cilium.io using the
#      resolved CILIUM_VERSION.
#
# Both sources are merged and deduped by (repo, chart, version). When the same
# chart appears in both with DIFFERENT versions, BOTH are downloaded (bootstrap
# installs one, ArgoCD may later reconcile to another) and a [WARN] version
# drift line is printed so the drift is visible before it causes surprises.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=00-config.sh
source "${SCRIPT_DIR}/00-config.sh"

PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UPSTREAM_SOURCES="${SCRIPT_DIR}/lib/chart-upstream-sources.tsv"
OUT_DIR="${AIRGAP_BUNDLE_DIR}/charts"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)  OUT_DIR="$2"; shift 2 ;;
    *)      echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "${OUT_DIR}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}" /tmp/charts.txt' EXIT

if ! command -v helm >/dev/null 2>&1; then
  echo "ERROR: helm not installed" >&2
  exit 1
fi

# Discover (repo, chart, version) tuples from gitops Application templates AND
# bootstrap shell scripts, dedupe, and print [WARN] lines for version drift.
python3 "${SCRIPT_DIR}/lib/discover-charts.py" "${PROJECT_ROOT}" "${UPSTREAM_SOURCES}" > /tmp/charts.txt

ok=0; fail=0
failed_charts=()
while IFS='|' read -r source_type source chart version source_ref chart_path expected_commit; do
  [[ -z "${source_type}" ]] && continue
  case "${source_type}" in
    helm-repo)
      echo "[chart] ${chart}-${version} from ${source}"
      # Add repo with a unique alias to avoid collisions.
      alias="airgap-$(echo "${source}" | md5sum | cut -c1-8)"
      if ! helm repo add "${alias}" "${source}" --force-update >/dev/null 2>&1; then
        echo "[FAIL] helm repo add failed for ${source}" >&2
        fail=$((fail+1))
        failed_charts+=("${chart}-${version} (repo add: ${source})")
        continue
      fi
      helm repo update "${alias}" >/dev/null 2>&1 || true
      if helm pull "${alias}/${chart}" --version "${version}" --destination "${OUT_DIR}" 2>&1 | tail -2; then
        ok=$((ok+1))
      else
        fail=$((fail+1))
        failed_charts+=("${chart}-${version} (from ${source})")
      fi
      ;;
    git)
      echo "[chart] ${chart}-${version} from ${source}@${source_ref}:${chart_path}"
      chart_dir="${WORK_DIR}/${chart}-${version}"
      if ! git clone --depth 1 --branch "${source_ref}" "${source}" "${chart_dir}" >/dev/null 2>&1; then
        echo "[FAIL] git clone failed for ${source}@${source_ref}" >&2
        fail=$((fail+1))
        failed_charts+=("${chart}-${version} (git clone: ${source}@${source_ref})")
        continue
      fi
      actual_commit="$(git -C "${chart_dir}" rev-parse HEAD)"
      if [[ "${actual_commit}" != "${expected_commit}" ]]; then
        echo "[FAIL] ${source_ref} resolved to ${actual_commit}, expected ${expected_commit}" >&2
        fail=$((fail+1))
        failed_charts+=("${chart}-${version} (moved git ref: ${source_ref})")
        continue
      fi
      if helm package "${chart_dir}/${chart_path}" --destination "${OUT_DIR}" 2>&1 | tail -2; then
        ok=$((ok+1))
      else
        fail=$((fail+1))
        failed_charts+=("${chart}-${version} (from ${source}@${source_ref})")
      fi
      ;;
    *)
      echo "[FAIL] unsupported source type '${source_type}' for ${chart}" >&2
      fail=$((fail+1))
      failed_charts+=("${chart}-${version} (unsupported source type: ${source_type})")
      ;;
  esac
done < /tmp/charts.txt

echo ""
echo "Charts saved: ${ok} | Failed: ${fail}"
echo "Output: ${OUT_DIR}"
ls -la "${OUT_DIR}" | tail -20

if [[ ${fail} -ne 0 ]]; then
  echo "" >&2
  echo "[FAIL] ${fail} chart(s) failed to download:" >&2
  printf '  - %s\n' "${failed_charts[@]}" >&2
fi

[[ ${fail} -eq 0 ]]
