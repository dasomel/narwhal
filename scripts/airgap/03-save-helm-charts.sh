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
#      Applications disabled via `{{- if false }}` (e.g. falco.yaml, see its header
#      for why) are skipped. Files are Helm templates containing `{{ }}` syntax, so
#      they're regex-scanned, not YAML-parsed.
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
python3 - "${PROJECT_ROOT}" "${UPSTREAM_SOURCES}" <<'PYEOF' > /tmp/charts.txt
import glob
import os
import re
import sys

root = sys.argv[1]
upstream_sources_path = sys.argv[2]


def load_upstream_sources(path):
    sources = {}
    with open(path) as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            chart, source_type, source, *details = fields
            if chart in sources:
                raise ValueError(f"{path}:{lineno}: duplicate chart '{chart}'")
            if source_type == "helm-repo":
                if details:
                    raise ValueError(
                        f"{path}:{lineno}: helm-repo rows need exactly 3 columns"
                    )
                if not source.startswith("https://"):
                    raise ValueError(
                        f"{path}:{lineno}: helm-repo source must use https://"
                    )
                sources[chart] = (source_type, source, "", "", "")
            elif source_type == "git":
                if len(details) != 3:
                    raise ValueError(
                        f"{path}:{lineno}: git rows need tag, chart path, and expected commit"
                    )
                source_ref, chart_path, expected_commit = details
                if not source.startswith("https://") or not chart_path:
                    raise ValueError(
                        f"{path}:{lineno}: invalid git source or chart path"
                    )
                if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
                    raise ValueError(
                        f"{path}:{lineno}: expected commit must be a 40-character SHA"
                    )
                sources[chart] = (source_type, source, source_ref, chart_path, expected_commit)
            else:
                raise ValueError(
                    f"{path}:{lineno}: unsupported source type '{source_type}'"
                )
    if not sources:
        raise ValueError(f"{path}: no upstream chart sources found")
    return sources


upstream_sources = load_upstream_sources(upstream_sources_path)


def parse_gitops(root, upstream_sources):
    charts = []
    tmpl_dir = os.path.join(root, "gitops/charts/narwhal-apps/templates")
    for path in sorted(glob.glob(os.path.join(tmpl_dir, "*.yaml"))):
        txt = open(path).read()
        if "{{- if false }}" in txt:
            continue  # Application disabled at the template gate (e.g. falco.yaml)
        chart_m = re.search(r"^\s*chart:\s*([^\s#]+)", txt, re.M)
        ver_m = re.search(r"targetRevision:\s*[\"']?([^\"'\s#]+)", txt)
        if not (chart_m and ver_m):
            continue
        chart, ver = chart_m.group(1), ver_m.group(1)
        if "{{" in chart:
            continue  # local-path app (repoURL: {{ .Values.repoURL }}) — no real helm repo
        if chart not in upstream_sources:
            raise ValueError(
                f"{path}: no public upstream source mapped for GitOps chart '{chart}'"
            )
        source_type, source, source_ref, chart_path, expected_commit = upstream_sources[chart]
        charts.append((source_type, source, chart, ver, source_ref, chart_path,
                       expected_commit, f"gitops:{os.path.basename(path)}"))
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
            charts.append(("helm-repo", alias_map[alias], chart, version, "", "", "",
                           f"bootstrap:{os.path.basename(path)}"))

    # Special case: cilium is installed via the `cilium` CLI, not helm — the
    # chart itself still needs to be mirrored for an airgap install.
    for path, txt in all_text.items():
        m = re.search(r"cilium\s+install\b.*?--version\s+(\S+)", txt, re.S)
        if m:
            version = resolve_version_token(m.group(1))
            if version:
                charts.append(("helm-repo", "https://helm.cilium.io", "cilium", version,
                               "", "", "",
                               f"bootstrap:{os.path.basename(path)} (cilium CLI install)"))
            break

    return charts


raw = parse_gitops(root, upstream_sources) + parse_bootstrap(root)

exact = {}
for source_type, source, chart, ver, source_ref, chart_path, expected_commit, src in raw:
    exact.setdefault((source_type, source, chart, ver, source_ref, chart_path,
                      expected_commit), []).append(src)

by_chart = {}
for (source_type, source, chart, ver, source_ref, chart_path, expected_commit), srcs in exact.items():
    by_chart.setdefault(chart, []).append((ver, srcs))

for chart, entries in sorted(by_chart.items()):
    versions = {}
    for ver, srcs in entries:
        versions.setdefault(ver, []).extend(srcs)
    if len(versions) > 1:
        detail = ", ".join(
            f"{ver} (from {', '.join(srcs)})" for ver, srcs in sorted(versions.items())
        )
        print(f"[WARN] version drift for {chart}: {detail}", file=sys.stderr)

for source_type, source, chart, ver, source_ref, chart_path, expected_commit in sorted(exact):
    print("|".join((source_type, source, chart, ver, source_ref, chart_path,
                    expected_commit)))
PYEOF

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
