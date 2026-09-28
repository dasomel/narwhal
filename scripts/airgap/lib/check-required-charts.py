#!/usr/bin/env python3
"""Validate install-required chart coverage and print manual chart rows."""
import argparse
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--manual-sources", type=Path)
args = parser.parse_args()
root = args.root.resolve()
manual_path = args.manual_sources or root / "scripts/airgap/lib/manual-chart-sources.tsv"

required = set()
scan = root / "scripts/airgap/lib/scan-bundle-requirements.py"
for line in subprocess.check_output([sys.executable, str(scan), str(root)], text=True).splitlines():
    kind, name = line.split("\t", 1)
    if kind == "charts":
        required.add(name)

upstream_path = root / "scripts/airgap/lib/chart-upstream-sources.tsv"
known = set()
for raw in upstream_path.read_text().splitlines():
    if raw and not raw.startswith("#"):
        known.add(raw.split("\t", 1)[0])
# These are supplied by non-repository paths in 03-save-helm-charts.sh:
# cilium via its CLI chart repo, and nfs-quota-agent from the pinned source tarball.
known.update(("cilium", "nfs-quota-agent"))

manual = {}
for lineno, raw in enumerate(manual_path.read_text().splitlines(), 1):
    if not raw or raw.startswith("#"):
        continue
    fields = raw.split("\t")
    if len(fields) != 3 or not fields[1].startswith("https://") or not fields[2]:
        raise ValueError(f"{manual_path}:{lineno}: expected chart, HTTPS source, and version")
    chart, source, version = fields
    if chart in manual:
        raise ValueError(f"{manual_path}:{lineno}: duplicate chart '{chart}'")
    manual[chart] = (source, version)

uncovered = required - known - set(manual)
if uncovered:
    raise ValueError("install references chart(s) without a bundle source/version: " + ", ".join(sorted(uncovered)))
for chart in sorted(required - known):
    source, version = manual[chart]
    print("\t".join((chart, source, version)))
