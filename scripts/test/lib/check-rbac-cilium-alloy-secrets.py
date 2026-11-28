#!/usr/bin/env python3
"""R430 (lane CA): disable unused Cilium Gateway API and pin exact Alloy RBAC acceptance."""

from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CNI = ROOT / "scripts/cluster/03-cni-install.sh"
OPS = ROOT / "scripts/ops/reconcile-cilium-hardening.sh"
MONITORING_BOOTSTRAP = ROOT / "scripts/cluster/08-2-monitoring.sh"
MONITORING_GITOPS = ROOT / "gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml"
REGRESSION = ROOT / "scripts/test/regression-check-kakao.sh"
ACCEPTANCE = ROOT / "portal/src/data/compliance-acceptance-rules.json"


def errors(cni: str, ops: str, regression: str, acceptance: dict) -> list[str]:
    problems: list[str] = []
    install = cni[cni.index("cilium install --version"):cni.index("\n    }", cni.index("cilium install --version"))]
    if "--set gatewayAPI.enabled=false" not in install:
        problems.append("bootstrap must disable unused Cilium Gateway API")
    if "--set tls.secretSync.enabled=false" not in install:
        problems.append("bootstrap must disable cluster-wide Cilium TLS Secret sync")
    if "secretsNamespace.sync" in install:
        problems.append("bootstrap must not retain ineffective secretsNamespace.sync")
    overlay = ops.split("cat > \"${WORK}/hardening-overlay.yaml\" <<'EOF'", 1)[1].split("\nEOF", 1)[0]
    if "gatewayAPI:\n  enabled: false" not in overlay or "tls:\n  secretSync:\n    enabled: false" not in overlay:
        problems.append("existing-cluster overlay must disable Gateway API and tls.secretSync")
    if "secretsNamespace.sync" in overlay:
        problems.append("existing-cluster overlay must not retain ineffective secretsNamespace.sync")
    exact = [r for r in acceptance.get("rbac", []) if r.get("id") == "rbac:cluster:ClusterRole:k8s-monitoring-alloy-operator:live-failure"]
    if len(exact) != 1 or exact[0] != {
        "id": "rbac:cluster:ClusterRole:k8s-monitoring-alloy-operator:live-failure",
        "scope": "cluster", "namespace": "", "kind": "ClusterRole", "name": "k8s-monitoring-alloy-operator",
        "checks": ["KSV041"], "class": "accept",
        "reasonKey": "compliance.acceptedReason.alloyOperatorClusterRoleFailure",
        "owner": "Alloy Operator 0.5.11 (gitops/charts/narwhal-apps/templates/k8s-monitoring.yaml; live trial 2026-11-28)", "reviewedAt": "2026-11-28"
    }:
        problems.append("portal must contain the exact measured Alloy operator ClusterRole KSV041 acceptance")
    if "check R430 " not in regression or "check-rbac-cilium-alloy-secrets.py --mutation-verify" not in regression:
        problems.append("regression-check-kakao.sh must register R430 with mutation verification")
    return problems


MUTATIONS = (
    ("bootstrap Gateway API re-enabled", "--set gatewayAPI.enabled=false", "--set gatewayAPI.enabled=true", "cni"),
    ("bootstrap TLS Secret sync re-enabled", "--set tls.secretSync.enabled=false", "--set tls.secretSync.enabled=true", "cni"),
    ("overlay Gateway API re-enabled", "gatewayAPI:\n  enabled: false", "gatewayAPI:\n  enabled: true", "ops"),
    ("overlay TLS Secret sync re-enabled", "secretSync:\n    enabled: false", "secretSync:\n    enabled: true", "ops"),
    ("exact acceptance removed", "rbac:cluster:ClusterRole:k8s-monitoring-alloy-operator:live-failure", "rbac:cluster:ClusterRole:other:live-failure", "acceptance"),
    ("R430 wiring removed", "check R430 ", "check R9999 ", "reg"),
)


def main() -> int:
    cni = CNI.read_text()
    ops = OPS.read_text()
    regression = REGRESSION.read_text()
    acceptance = json.loads(ACCEPTANCE.read_text())
    failures = errors(cni, ops, regression, acceptance)
    if failures:
        print("FAIL: " + "; ".join(failures))
        return 1
    if "--mutation-verify" in sys.argv:
        for label, old, new, target in MUTATIONS:
            values = {"cni": cni, "ops": ops, "reg": regression, "acceptance": ACCEPTANCE.read_text()}
            if old not in values[target]:
                print(f"FAIL: mutation source missing: {label}")
                return 1
            values[target] = values[target].replace(old, new, 1)
            trial_acceptance = json.loads(values["acceptance"])
            if not errors(values["cni"], values["ops"], values["reg"], trial_acceptance):
                print(f"FAIL: mutation escaped: {label}")
                return 1
        print(f"PASS: R430 Cilium Gateway API disabled; exact Alloy KSV041 acceptance ({len(MUTATIONS)} mutations rejected)")
        return 0
    print("PASS: R430 Cilium Gateway API disabled; exact Alloy KSV041 acceptance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
