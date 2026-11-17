#!/usr/bin/env python3
"""R354: keep custom RBAC fixes narrow and prevent their regression."""

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[3]
RBAC = ROOT / "gitops/resources/rbac-policies.yaml"


def errors(text: str) -> list[str]:
    docs = [item for item in yaml.safe_load_all(text) if item]
    roles = {item.get("metadata", {}).get("name"): item for item in docs if item.get("kind") in {"Role", "ClusterRole"}}
    platform = roles.get("platform-admin", {})
    pod_log_rules = [rule for rule in platform.get("rules", []) if "pods/log" in rule.get("resources", [])]
    issues = []
    if len(pod_log_rules) != 1 or set(pod_log_rules[0].get("verbs", [])) != {"get", "list", "watch"}:
        issues.append("platform-admin pods/log must have exactly get/list/watch")
    if any("pods/log" in rule.get("resources", []) and "delete" in rule.get("verbs", []) for rule in platform.get("rules", [])):
        issues.append("platform-admin must not delete pod logs")
    velero = roles.get("velero-ui-custom-role", {})
    if any("secrets" in rule.get("resources", []) for rule in velero.get("rules", [])):
        issues.append("velero-ui-custom-role must not access Secrets")
    return issues


def main() -> int:
    original = RBAC.read_text()
    issues = errors(original)
    if issues:
        print("FAIL: " + "; ".join(issues))
        return 1
    if "--mutation-verify" in sys.argv:
        mutations = [
            original.replace('resources: ["pods/log"]\n    verbs: ["get", "list", "watch"]', 'resources: ["pods/log"]\n    verbs: ["get", "delete"]', 1),
            original.replace("      - pods/status\n      - configmaps", "      - pods/status\n      - secrets\n      - configmaps", 1),
        ]
        if any(not errors(mutated) for mutated in mutations):
            print("FAIL: a forbidden RBAC mutation was not detected")
            return 1
    print("PASS: platform-admin logs are read-only; Velero UI cannot access Secrets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
