#!/usr/bin/env python3
"""Validate the pre-bootstrap source map for GitOps Helm charts.

Usage: check-chart-upstream-sources.py [path-to-chart-upstream-sources.tsv]
"""

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCES = REPO_ROOT / "scripts/airgap/lib/chart-upstream-sources.tsv"
TEMPLATES_DIR = REPO_ROOT / "gitops/charts/narwhal-apps/templates"
CHART_RE = re.compile(r"^\s*chart:\s*([^\s#]+)", re.MULTILINE)


def load_sources(path):
    sources = {}
    errors = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw or raw.startswith("#"):
            continue
        fields = raw.split("\t")
        if len(fields) < 3:
            errors.append(f"{path}:{lineno}: expected at least 3 tab-separated fields")
            continue
        chart, source_type, source, *details = fields
        if chart in sources:
            errors.append(f"{path}:{lineno}: duplicate source for '{chart}'")
            continue
        if ".svc.cluster.local" in source:
            errors.append(f"{path}:{lineno}: source must not be an in-cluster registry")
        if source_type == "helm-repo":
            if len(fields) != 3 or not source.startswith("https://"):
                errors.append(f"{path}:{lineno}: malformed helm-repo source")
        elif source_type == "git":
            if len(details) != 3:
                errors.append(f"{path}:{lineno}: git source needs tag, chart path, and commit")
            elif not (source.startswith("https://") and details[0] and details[1]
                      and re.fullmatch(r"[0-9a-f]{40}", details[2])):
                errors.append(f"{path}:{lineno}: malformed or unpinned git source")
        else:
            errors.append(f"{path}:{lineno}: unsupported source type '{source_type}'")
        sources[chart] = fields
    return sources, errors


def gitops_charts():
    charts = set()
    for template in sorted(TEMPLATES_DIR.glob("*.yaml")):
        text = template.read_text()
        if "{{- if false }}" in text:
            continue
        match = CHART_RE.search(text)
        if not match:
            continue
        chart = match.group(1)
        if "{{" not in chart:
            charts.add(chart)
    return charts


def main():
    if len(sys.argv) > 2:
        print(f"usage: {Path(sys.argv[0]).name} [path-to-chart-upstream-sources.tsv]", file=sys.stderr)
        return 2

    sources_path = Path(sys.argv[1]) if len(sys.argv) == 2 else DEFAULT_SOURCES
    if not sources_path.is_file():
        print(f"missing source map: {sources_path}", file=sys.stderr)
        return 1

    sources, errors = load_sources(sources_path)
    apps = gitops_charts()
    missing = sorted(apps - sources.keys())
    stale = sorted(sources.keys() - apps)
    if missing:
        errors.append(f"unmapped GitOps charts: {', '.join(missing)}")
    if stale:
        errors.append(f"source map entries without a GitOps chart: {', '.join(stale)}")

    bundler = (REPO_ROOT / "scripts/airgap/03-save-helm-charts.sh").read_text()
    if "chart-upstream-sources.tsv" not in bundler or "upstream_sources[chart]" not in bundler:
        errors.append("03-save-helm-charts.sh does not resolve GitOps charts through the source map")

    if errors:
        print("chart upstream source check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"chart upstream source check passed: {len(apps)} GitOps charts mapped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
