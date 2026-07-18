#!/bin/bash
set -euo pipefail

# =============================================================================
# 03-save-helm-charts.sh — Download every Helm chart needed for an airgap install
#
# Chart discovery covers TWO sources (a from-scratch airgap install needs both):
#   1. GitOps charts — parsed from gitops/charts/narwhal-apps/templates/*.yaml
#      (ArgoCD Application manifests, one per file). Only templates with a
#      literal repoURL + chart + targetRevision are collected; local-path apps
#      (repoURL: {{ .Values.repoURL }}, no chart: key — e.g. narwhal-platform.yaml,
#      *-policies.yaml) and Applications disabled via `{{- if false }}` (e.g.
#      falco.yaml, see its header for why) are skipped. Files are Helm templates
#      containing `{{ }}` syntax, so they're regex-scanned, not YAML-parsed.
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

# Discover (repo, chart, version) tuples from gitops Application templates AND
# bootstrap shell scripts, dedupe, and print [WARN] lines for version drift.
python3 - "${PROJECT_ROOT}" <<'PYEOF' > /tmp/charts.txt
import glob
import os
import re
import sys

root = sys.argv[1]


def parse_gitops(root):
    charts = []
    tmpl_dir = os.path.join(root, "gitops/charts/narwhal-apps/templates")
    for path in sorted(glob.glob(os.path.join(tmpl_dir, "*.yaml"))):
        txt = open(path).read()
        if "{{- if false }}" in txt:
            continue  # Application disabled at the template gate (e.g. falco.yaml)
        repo_m = re.search(r"repoURL:\s*([^\s#]+)", txt)
        chart_m = re.search(r"^\s*chart:\s*([^\s#]+)", txt, re.M)
        ver_m = re.search(r"targetRevision:\s*[\"']?([^\"'\s#]+)", txt)
        if not (repo_m and chart_m and ver_m):
            continue
        repo, chart, ver = repo_m.group(1), chart_m.group(1), ver_m.group(1)
        if "{{" in repo or "{{" in chart:
            continue  # local-path app (repoURL: {{ .Values.repoURL }}) — no real helm repo
        charts.append((repo, chart, ver, f"gitops:{os.path.basename(path)}"))
    return charts


def parse_bootstrap(root):
    files = sorted(glob.glob(os.path.join(root, "scripts/cluster/*.sh"))) + sorted(
        glob.glob(os.path.join(root, "scripts/common/*.sh"))
    )
    all_text = {f: open(f).read() for f in files}

    alias_map = {}
    for txt in all_text.values():
        for m in re.finditer(r"helm\s+repo\s+add\s+(\S+)\s+(\S+)", txt):
            alias_map[m.group(1)] = m.group(2)

    def resolve_var(name):
        esc = re.escape(name)
        patterns = [
            re.compile(esc + r'="\$\{' + esc + r":-([^}]+)\}\""),
            re.compile(esc + r"='\$\{" + esc + r":-([^}]+)\}'"),
            re.compile(r':\s*"\$\{' + esc + r':[-=]([^}]+)\}"'),
            re.compile(esc + r'=(?!\$)"?([^"\s]+)"?'),
        ]
        for txt in all_text.values():
            for pat in patterns:
                m = pat.search(txt)
                if m:
                    return m.group(1)
        return None

    def resolve_version_token(raw):
        raw = raw.strip("\"'")
        if raw.startswith("$"):
            var_m = re.match(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", raw)
            if not var_m:
                return None
            return resolve_var(var_m.group(1))
        return raw

    charts = []
    for path, txt in all_text.items():
        lines = txt.splitlines()
        for i, line in enumerate(lines):
            m = re.search(r"helm\s+(?:upgrade\s+--install\s+\S+\s+|pull\s+)(\S+)/(\S+)", line)
            if not m:
                continue
            alias, chart = m.group(1), m.group(2)
            if alias not in alias_map:
                continue  # not a real helm-repo chart (e.g. local-directory install)
            block_lines = [line]
            j = i
            while block_lines[-1].rstrip().endswith("\\") and j + 1 < len(lines):
                j += 1
                block_lines.append(lines[j])
            block = "\n".join(block_lines)
            vm = re.search(r"--version\s+(\S+)", block)
            if not vm:
                print(f"[WARN] {os.path.basename(path)}: no --version found for "
                      f"{alias}/{chart} — skipping", file=sys.stderr)
                continue
            version = resolve_version_token(vm.group(1))
            if not version:
                print(f"[WARN] {os.path.basename(path)}: could not resolve version "
                      f"token '{vm.group(1)}' for {alias}/{chart} — skipping", file=sys.stderr)
                continue
            charts.append((alias_map[alias], chart, version, f"bootstrap:{os.path.basename(path)}"))

    # Special case: cilium is installed via the `cilium` CLI, not helm — the
    # chart itself still needs to be mirrored for an airgap install.
    for path, txt in all_text.items():
        m = re.search(r"cilium\s+install\b.*?--version\s+(\S+)", txt, re.S)
        if m:
            version = resolve_version_token(m.group(1))
            if version:
                charts.append(("https://helm.cilium.io", "cilium", version,
                                f"bootstrap:{os.path.basename(path)} (cilium CLI install)"))
            break

    return charts


raw = parse_gitops(root) + parse_bootstrap(root)

exact = {}
for repo, chart, ver, src in raw:
    exact.setdefault((repo, chart, ver), []).append(src)

by_chart = {}
for (repo, chart, ver), srcs in exact.items():
    by_chart.setdefault((repo, chart), []).append((ver, srcs))

final = []
for (repo, chart), versions in sorted(by_chart.items()):
    if len(versions) > 1:
        detail = ", ".join(f"{v} (from {', '.join(s)})" for v, s in versions)
        print(f"[WARN] version drift for {chart}: {detail}", file=sys.stderr)
    for ver, _srcs in versions:
        final.append((repo, chart, ver))

for repo, chart, ver in sorted(final):
    print(f"{repo}|{chart}|{ver}")
PYEOF

ok=0; fail=0
failed_charts=()
while IFS='|' read -r repo chart version; do
  [[ -z "${repo}" ]] && continue
  echo "[chart] ${chart}-${version} from ${repo}"
  # Add repo with a unique alias to avoid collisions
  alias="airgap-$(echo "${repo}" | md5sum | cut -c1-8)"
  if ! helm repo add "${alias}" "${repo}" --force-update >/dev/null 2>&1; then
    echo "[FAIL] helm repo add failed for ${repo}" >&2
    fail=$((fail+1))
    failed_charts+=("${chart}-${version} (repo add: ${repo})")
    continue
  fi
  helm repo update "${alias}" >/dev/null 2>&1 || true
  if helm pull "${alias}/${chart}" --version "${version}" --destination "${OUT_DIR}" 2>&1 | tail -2; then
    ok=$((ok+1))
  else
    fail=$((fail+1))
    failed_charts+=("${chart}-${version} (from ${repo})")
  fi
done < /tmp/charts.txt

rm -f /tmp/charts.txt
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
