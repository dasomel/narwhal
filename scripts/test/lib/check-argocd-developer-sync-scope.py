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
DEVELOPER_POLICIES = {
    "p, role:developer, applications, sync, tenants/*, allow",
    "p, role:developer, applications, get, */*, allow",
    "p, role:developer, logs, get, */*, allow",
}
PORTAL_READER_POLICIES = {
    "p, role:portal-reader, applications, get, */*, allow",
    "p, role:portal-reader, applications, list, */*, allow",
    "p, role:portal-reader, logs, get, */*, allow",
    "p, role:portal-reader, applications, sync, */*, allow",
}
GITOPS_GROUPS = {
    "g, narwhal-portal, role:portal-reader",
    "g, cluster-admin, role:admin",
    "g, developer, role:developer",
    "g, viewer, role:readonly",
    "g, guest, role:none",
}
BOOTSTRAP_GROUPS = GITOPS_GROUPS - {"g, narwhal-portal, role:portal-reader"}
EXPECTED_POLICY_DEFAULT = "policy.default: role:readonly"
EXPECTED_SCOPES = "scopes: '[groups]'"


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

    def normalized(lines: list[str], prefix: tuple[str, ...]) -> set[str]:
        found = set()
        for line in lines:
            fields = tuple(field.strip() for field in line.split(","))
            if fields[: len(prefix)] == prefix:
                found.add(", ".join(fields))
        return found

    def p_rules(lines: list[str], role: str) -> set[str]:
        return normalized(lines, ("p", role))

    def g_rules(lines: list[str]) -> set[str]:
        return normalized(lines, ("g",))

    def setting_matches(text: str, expected: str) -> bool:
        key = expected.split(":", 1)[0]
        configured = [line.strip() for line in text.splitlines() if line.strip().startswith(f"{key}:")]
        return configured == [expected]

    return (
        p_rules(gitops_rules, "role:developer") == DEVELOPER_POLICIES
        and p_rules(bootstrap_rules, "role:developer") == DEVELOPER_POLICIES
        and p_rules(gitops_rules, "role:portal-reader") == PORTAL_READER_POLICIES
        and g_rules(gitops_rules) == GITOPS_GROUPS
        and g_rules(bootstrap_rules) == BOOTSTRAP_GROUPS
        and setting_matches(gitops_text, EXPECTED_POLICY_DEFAULT)
        and setting_matches(bootstrap_text, EXPECTED_POLICY_DEFAULT)
        and setting_matches(gitops_text, EXPECTED_SCOPES)
        and setting_matches(bootstrap_text, EXPECTED_SCOPES)
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
            gitops_text.replace(
                "p, role:portal-reader, applications, sync, */*, allow",
                "p, role:portal-reader, applications, sync, tenants/*, allow",
                1,
            ),
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
        "developer granted admin by group union": (
            gitops_text.replace(
                "    g, developer, role:developer",
                "    g, developer, role:developer\n    g, developer, role:admin",
                1,
            ),
            bootstrap_text,
        ),
        "portal-reader get policy changed": (
            gitops_text.replace(
                "p, role:portal-reader, applications, get, */*, allow",
                "p, role:portal-reader, applications, get, tenants/*, allow",
                1,
            ),
            bootstrap_text,
        ),
        "portal-reader list policy changed": (
            gitops_text.replace(
                "p, role:portal-reader, applications, list, */*, allow",
                "p, role:portal-reader, applications, list, tenants/*, allow",
                1,
            ),
            bootstrap_text,
        ),
        "portal-reader logs policy changed": (
            gitops_text.replace(
                "p, role:portal-reader, logs, get, */*, allow",
                "p, role:portal-reader, logs, get, tenants/*, allow",
                1,
            ),
            bootstrap_text,
        ),
        "portal-reader group binding changed": (
            gitops_text.replace(
                "g, narwhal-portal, role:portal-reader",
                "g, narwhal-portal, role:admin",
                1,
            ),
            bootstrap_text,
        ),
        "GitOps default role widened": (
            gitops_text.replace(EXPECTED_POLICY_DEFAULT, "policy.default: role:admin", 1),
            bootstrap_text,
        ),
        "bootstrap default role widened": (
            gitops_text,
            bootstrap_text.replace(EXPECTED_POLICY_DEFAULT, "policy.default: role:admin", 1),
        ),
        "both default roles widened": (
            gitops_text.replace(EXPECTED_POLICY_DEFAULT, "policy.default: role:admin", 1),
            bootstrap_text.replace(EXPECTED_POLICY_DEFAULT, "policy.default: role:admin", 1),
        ),
        "GitOps group claim scope changed": (
            gitops_text.replace(EXPECTED_SCOPES, "scopes: '[email]'", 1),
            bootstrap_text,
        ),
        "bootstrap group claim scope changed": (
            gitops_text,
            bootstrap_text.replace(EXPECTED_SCOPES, "scopes: '[email]'", 1),
        ),
        "both group claim scopes changed": (
            gitops_text.replace(EXPECTED_SCOPES, "scopes: '[email]'", 1),
            bootstrap_text.replace(EXPECTED_SCOPES, "scopes: '[email]'", 1),
        ),
    }
    for name, (mutated_gitops, mutated_bootstrap) in mutations.items():
        if valid(mutated_gitops, mutated_bootstrap):
            raise AssertionError(f"checker accepted mutation: {name}")
    print(f"PASS: rejected all {len(mutations)} developer-role and portal-reader mutations")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    gitops_text = GITOPS.read_text()
    bootstrap_text = BOOTSTRAP.read_text()
    if not valid(gitops_text, bootstrap_text):
        raise SystemExit("FAIL: Argo CD policies, group bindings, default role or group claim scopes diverge from the reviewed contract")
    if args.mutation_verify:
        mutation_verify(gitops_text, bootstrap_text)
    else:
        print("PASS: Argo CD policy, group bindings, read-only default and group claim scopes match the reviewed contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
