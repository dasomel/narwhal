#!/usr/bin/env python3
"""Check rendered Argo CD server RBAC and documented proxy allowlist reasons."""

import re
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
ROLE_PATH = ROOT / "gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml"
VERIFY_PATH = ROOT / "scripts/verify/kubelet-authz-check.sh"


def safe_server_role(role):
    return role.get("kind") == "ClusterRole" and role.get("metadata", {}).get("name") == "argocd-server" and all(
        not ("*" in rule.get("apiGroups", []) and "*" in rule.get("resources", []))
        and not any(resource == "nodes/proxy" or resource == "nodes/*" for resource in rule.get("resources", []))
        for rule in role.get("rules", [])
    )


def reasons_present(text):
    blocks = re.findall(r"allowlist\s*=\s*\{(.*?)\n\s*\}", text, re.S)
    if len(blocks) != 2:
        return False
    return all(re.search(r'^\s*"[^"\n]+"\s*:\s*"[^"\n]+",?\s*$', line)
               for block in blocks for line in block.splitlines() if line.strip() and not line.lstrip().startswith("#"))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "role"):
        rendered = subprocess.run(
            ["helm", "template", "narwhal-apps", "gitops/charts/narwhal-apps"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout
        docs = [item for item in yaml.safe_load_all(rendered) if isinstance(item, dict)]
        roles = [item for item in docs if item.get("kind") == "ClusterRole" and item.get("metadata", {}).get("name") == "argocd-server"]
        if len(roles) != 1 or not safe_server_role(roles[0]):
            raise SystemExit("rendered argocd-server ClusterRole is missing or grants wildcard/subresource access")
        if safe_server_role({**roles[0], "rules": roles[0]["rules"] + [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]}):
            raise SystemExit("mutation check failed to detect wildcard RBAC")
        print("PASS rendered argocd-server ClusterRole (wildcard mutation rejected)")
    if mode in ("all", "reasons"):
        text = VERIFY_PATH.read_text(encoding="utf-8")
        if not reasons_present(text):
            raise SystemExit("every verifier allowlist entry must have a non-empty reason")
        mutated = re.sub(r'("serviceaccount:devtools:argocd-application-controller": )"[^"]+"', r'\1""', text, count=2)
        if reasons_present(mutated):
            raise SystemExit("mutation check failed to detect a missing allowlist reason")
        print("PASS allowlist reasons (empty-reason mutation rejected)")


if __name__ == "__main__":
    main()
