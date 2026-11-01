#!/usr/bin/env python3
"""Print the (source-type|source|chart|version|ref|path|commit) chart pins an airgap
install needs, one per line; version-drift warnings go to stderr.

Shared by 03-save-helm-charts.sh (downloads them) and check-bundle.sh (checks the bundle
holds them) so the two never disagree on what "every pin" means (D-BUNDLE-PIN-SHARED).
Usage: discover-charts.py <project-root> <upstream-sources.tsv> [--gitops-only]
  --gitops-only  only the Applications ArgoCD pulls from the Gitea Helm registry.
"""
import glob
import os
import re
import subprocess
import sys

root = sys.argv[1]
upstream_sources_path = sys.argv[2]
gitops_only = "--gitops-only" in sys.argv[3:]


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
            continue  # Application disabled at the template gate
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


raw = parse_gitops(root, upstream_sources)
if not gitops_only:
    raw += parse_bootstrap(root)

if not gitops_only:
    checker = os.path.join(root, "scripts/airgap/lib/check-required-charts.py")
    manual_rows = subprocess.check_output([sys.executable, checker, root], text=True)
    for line in manual_rows.splitlines():
        chart, source, version = line.split("\t")
        raw.append(("helm-repo", source, chart, version, "", "", "",
                    "install-call scanner (pinned in VERSIONS.md)"))

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
