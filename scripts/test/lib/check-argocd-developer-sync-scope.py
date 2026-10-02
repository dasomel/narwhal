#!/usr/bin/env python3
"""Keep human Argo CD developer sync scope aligned across GitOps and bootstrap."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GITOPS = ROOT / "gitops/charts/narwhal-platform/templates/argocd-config.yaml"
BOOTSTRAP = ROOT / "scripts/cluster/13-argocd.sh"
DEVELOPER_RULE = "p, role:developer, applications, sync, tenants/*, allow"
PORTAL_READER_RULE = "p, role:portal-reader, applications, sync, */*, allow"


def policy_lines(text: str, source: str) -> list[str]:
    if source == "gitops":
        match = re.search(r"(?m)^  policy\.csv: \|\n((?:^    .*\n|^\n)*)", text)
    else:
        start = text.find("  policy.csv: |")
        if start < 0:
            return []
        match = re.search(r"(?m)^  policy\.csv: \|\n((?:^    .*\n|^\n)*)", text[start:])
    if not match:
        return []
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def valid(gitops_text: str, bootstrap_text: str) -> bool:
    gitops_rules = policy_lines(gitops_text, "gitops")
    bootstrap_rules = policy_lines(bootstrap_text, "bootstrap")
    developer = ("p", "role:developer", "applications", "sync")
    portal_reader = ("p", "role:portal-reader", "applications", "sync")

    def sync_rules(lines: list[str], role: tuple[str, ...]) -> list[str]:
        found = []
        for line in lines:
            fields = tuple(field.strip() for field in line.split(","))
            if fields[:4] == role:
                found.append(", ".join(fields))
        return found

    gitops_developer = sync_rules(gitops_rules, developer)
    bootstrap_developer = sync_rules(bootstrap_rules, developer)
    portal_rules = sync_rules(gitops_rules, portal_reader)
    return (
        gitops_developer == [DEVELOPER_RULE]
        and bootstrap_developer == [DEVELOPER_RULE]
        and portal_rules == [PORTAL_READER_RULE]
    )


def mutation_verify(gitops_text: str, bootstrap_text: str) -> None:
    mutations = {
        "GitOps developer widened": (
            gitops_text.replace(DEVELOPER_RULE, "p, role:developer, applications, sync, */*, allow", 1),
            bootstrap_text,
        ),
        "bootstrap developer widened": (
            gitops_text,
            bootstrap_text.replace(DEVELOPER_RULE, "p, role:developer, applications, sync, */*, allow", 1),
        ),
        "GitOps developer diverged": (
            gitops_text.replace(DEVELOPER_RULE, "p, role:developer, applications, sync, platform/*, allow", 1),
            bootstrap_text,
        ),
        "portal-reader policy changed": (
            gitops_text.replace(PORTAL_READER_RULE, "p, role:portal-reader, applications, sync, tenants/*, allow", 1),
            bootstrap_text,
        ),
        "whitespace-obfuscated broad developer rule": (
            gitops_text.replace(
                "    p, role:developer, applications, get, */*, allow",
                "    p, role:developer, applications, get, */*, allow\n    p,  role:developer, applications, sync, */*, allow",
                1,
            ),
            bootstrap_text,
        ),
    }
    for name, (mutated_gitops, mutated_bootstrap) in mutations.items():
        if valid(mutated_gitops, mutated_bootstrap):
            raise AssertionError(f"checker accepted mutation: {name}")
    print(f"PASS: rejected all {len(mutations)} developer-scope and portal-reader mutations")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    gitops_text = GITOPS.read_text()
    bootstrap_text = BOOTSTRAP.read_text()
    if not valid(gitops_text, bootstrap_text):
        raise SystemExit("FAIL: developer sync must be exactly tenants/* in GitOps and bootstrap; portal-reader policy must remain unchanged")
    if args.mutation_verify:
        mutation_verify(gitops_text, bootstrap_text)
    else:
        print("PASS: GitOps and bootstrap developer sync both use tenants/*; portal-reader sync is unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
