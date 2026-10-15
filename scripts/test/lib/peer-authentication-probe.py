#!/usr/bin/env python3
"""Probe trees for check-peer-authentication.py default mode (Narwhal#126).

Usage: peer-authentication-probe.py <probe>. Copies the two real baselined files into a temp
ROOT, applies one mutation, runs the checker against that tree (PEER_AUTH_ROOT) and exits with
the checker's code. `control` must pass; every other probe is a bypass that must FAIL.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[3]
CHECKER = REPO / "scripts/test/lib/check-peer-authentication.py"
BOOTSTRAP = "scripts/cluster/09-istio-ambient.sh"
CHART = "gitops/charts/narwhal-platform/templates/istio-ambient-policies.yaml"
HEAD = "apiVersion: security.istio.io/v1\nkind: PeerAuthentication\n"
SECOND = HEAD + "metadata:\n  name: default\n  namespace: istio-system\nspec:\n  mtls:\n    mode: PERMISSIVE\n"
LEAK = HEAD + "metadata:\n  name: leak\n  namespace: payments\nspec:\n  mtls:\n    mode: PERMISSIVE\n"


def heredoc(body: str) -> str:
    return f"\ncat <<'EOF' | kubectl apply -f -\n{body}EOF\n"


def append(rel: str, text: str):
    def mutate(root: pathlib.Path) -> None:
        with (root / rel).open("a") as fh:
            fh.write(text)
    return mutate


def replace(rel: str, old: str, new: str):
    def mutate(root: pathlib.Path) -> None:
        path = root / rel
        text = path.read_text()
        assert old in text, old
        path.write_text(text.replace(old, new, 1))
    return mutate


def add(rel: str, text: str):
    def mutate(root: pathlib.Path) -> None:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return mutate


JSON_LEAK = '{"apiVersion": "security.istio.io/v1", "kind": "PeerAuthentication", "metadata": {"name": "leak", ' \
            '"namespace": "payments"}, "spec": {"mtls": {"mode": "PERMISSIVE"}}}\n'
PROBES = {
    "control": lambda root: None,
    # baseline-key bypasses
    "mode-changed": replace(BOOTSTRAP, "    mode: PERMISSIVE\nEOF", "    mode: DISABLE\nEOF"),
    "second-selector-disable": append(BOOTSTRAP, heredoc(
        HEAD + "metadata:\n  name: default\n  namespace: istio-system\nspec:\n  selector:\n    matchLabels:\n"
        "      app: x\n  mtls:\n    mode: DISABLE\n")),
    "duplicate-entry": append(BOOTSTRAP, heredoc(SECOND)),
    "entry-fixed": replace(BOOTSTRAP, "    mode: PERMISSIVE\nEOF", "    mode: STRICT\nEOF"),
    # scan scope
    "scope-scripts-other": add("scripts/other/x.sh", heredoc(LEAK)),
    "scope-scripts-nested": add("scripts/cluster/sub/x.sh", heredoc(LEAK)),
    "scope-gitops-json": add("gitops/x.json", JSON_LEAK),
    "scope-gitops-tpl": add("gitops/x.tpl", LEAK),
}


def main() -> int:
    name = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        for rel in (BOOTSTRAP, CHART):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, root / rel)
        PROBES[name](root)
        return subprocess.run([sys.executable, str(CHECKER)], env={**os.environ, "PEER_AUTH_ROOT": tmp}).returncode


if __name__ == "__main__":
    raise SystemExit(main())
