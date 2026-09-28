#!/usr/bin/env python3
"""Check every rendered Kubernetes document has apiVersion and kind."""

import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def main():
    rendered = subprocess.run(
        ["helm", "template", "kubernetes-dashboard", "gitops/charts/kubernetes-dashboard", "-n", "devtools"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    for document in yaml.safe_load_all(rendered):
        if document is None or document == {}:
            continue
        if not isinstance(document, dict):
            raise SystemExit("rendered document <missing>/<missing> lacks a non-empty apiVersion or kind")
        metadata = document.get("metadata")
        name = metadata.get("name", "<missing>") if isinstance(metadata, dict) else "<missing>"
        kind = document.get("kind")
        api_version = document.get("apiVersion")
        if not isinstance(api_version, str) or not api_version.strip() or not isinstance(kind, str) or not kind.strip():
            kind = kind if isinstance(kind, str) and kind.strip() else "<missing>"
            raise SystemExit(f"rendered document {kind}/{name} lacks a non-empty apiVersion or kind")
    print("PASS all rendered documents have non-empty apiVersion and kind")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr, end="")
        raise SystemExit(error.returncode)
