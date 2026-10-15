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


NONE_POLICIES = {"p, role:none, applications, get, */*, deny"}
# D1: allowlist the COMPLETE p/g line set of each source (not just the two reviewed roles),
# so a new subject, role:admin grant or dropped deny line is rejected. Cost: any intended
# policy change must update this contract. Escape: edit these sets together with both sources.
EXPECTED_LINES = {
    "gitops": DEVELOPER_POLICIES | NONE_POLICIES | PORTAL_READER_POLICIES | GITOPS_GROUPS,
    "bootstrap": DEVELOPER_POLICIES | NONE_POLICIES | BOOTSTRAP_GROUPS,
}
TEMPLATE_MARKERS = ("{{", "${", "$(", "`")
RBAC_NAME_LINE = "name: argocd-rbac-cm"


def problems(gitops_text: str, bootstrap_text: str) -> list[str]:
    found: list[str] = []
    for source, text in (("gitops", gitops_text), ("bootstrap", bootstrap_text)):
        keys = re.findall(r"(?m)^[ \t]*(policy\.[\w.\-]*csv)[ \t]*:", text)
        if keys != ["policy.csv"]:
            found.append(f"{source}: policy csv keys {keys} != ['policy.csv']")
        declared = sum(1 for line in text.splitlines() if line.strip() == RBAC_NAME_LINE)
        if declared != 1:
            found.append(f"{source}: argocd-rbac-cm declared {declared} times, expected exactly 1")
        lines = policy_lines(text, source)
        if not lines:
            found.append(f"{source}: policy.csv missing or unparseable")
        normalized: list[str] = []
        for line in lines:
            if any(marker in line for marker in TEMPLATE_MARKERS):
                found.append(f"{source}: templated policy text is undecidable: {line}")
                continue
            fields = [field.strip() for field in line.split(",")]
            if fields[0] not in ("p", "g"):
                found.append(f"{source}: unparseable policy line: {line}")
                continue
            normalized.append(", ".join(fields))
        extra = sorted(set(normalized) - EXPECTED_LINES[source])
        missing = sorted(EXPECTED_LINES[source] - set(normalized))
        if extra or missing or len(normalized) != len(set(normalized)):
            found.append(f"{source}: policy lines differ from reviewed set (extra={extra}, missing={missing})")
        for expected in (EXPECTED_POLICY_DEFAULT, EXPECTED_SCOPES):
            key = expected.split(":", 1)[0]
            configured = [ln.strip() for ln in text.splitlines() if ln.strip().startswith(f"{key}:")]
            if configured != [expected]:
                found.append(f"{source}: {key} is {configured}, expected {expected}")
    return found


def valid(gitops_text: str, bootstrap_text: str) -> bool:
    return not problems(gitops_text, bootstrap_text)


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

    def after(text: str, anchor: str, addition: str) -> str:
        assert anchor in text, anchor
        return text.replace(anchor, anchor + addition, 1)

    broad = "p, alice, applications, sync, */*, allow"
    anchor_line = "    p, role:none, applications, get, */*, deny"
    reasoned = {
        "unreviewed subject sync (GitOps)": (
            after(gitops_text, anchor_line, f"\n    {broad}"), bootstrap_text, "policy lines differ"),
        "unreviewed subject sync (bootstrap)": (
            gitops_text, after(bootstrap_text, anchor_line, f"\n    {broad}"), "policy lines differ"),
        "role:admin wildcard added (GitOps)": (
            after(gitops_text, anchor_line, "\n    p, role:admin, *, *, */*, allow"), bootstrap_text,
            "policy lines differ"),
        "role:admin wildcard added (bootstrap)": (
            gitops_text, after(bootstrap_text, anchor_line, "\n    p, role:admin, *, *, */*, allow"),
            "policy lines differ"),
        "policy.override.csv developer */* (GitOps)": (
            after(gitops_text, "  policy.default: role:readonly",
                  "\n  policy.override.csv: |\n    p, role:developer, applications, sync, */*, allow"),
            bootstrap_text, "policy csv keys"),
        "policy.override.csv developer */* (bootstrap)": (
            gitops_text,
            after(bootstrap_text, "  policy.default: role:readonly",
                  "\n  policy.override.csv: |\n    p, role:developer, applications, sync, */*, allow"),
            "policy csv keys"),
        "argocd-rbac-cm declared twice after --- (GitOps)": (
            gitops_text + "\n---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: argocd-rbac-cm\n"
            "  namespace: devtools\ndata:\n  policy.csv: |\n"
            "    p, role:developer, applications, sync, */*, allow\n",
            bootstrap_text, "declared 2 times"),
        "argocd-rbac-cm declared twice after --- (bootstrap)": (
            gitops_text,
            bootstrap_text + "\n---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: argocd-rbac-cm\n",
            "declared 2 times"),
        "role:none deny line deleted (GitOps)": (
            gitops_text.replace(anchor_line + "\n", "", 1), bootstrap_text, "policy lines differ"),
        "role:none deny line deleted (bootstrap)": (
            gitops_text, bootstrap_text.replace(anchor_line + "\n", "", 1), "policy lines differ"),
        "templated policy line (GitOps)": (
            after(gitops_text, anchor_line, "\n    g, {{ .Values.group }}, role:admin"), bootstrap_text,
            "templated policy text"),
    }
    for name, (mutated_gitops, mutated_bootstrap, reason) in reasoned.items():
        if mutated_gitops == gitops_text and mutated_bootstrap == bootstrap_text:
            raise AssertionError(f"mutation did not change input: {name}")
        found = problems(mutated_gitops, mutated_bootstrap)
        if not any(reason in item for item in found):
            raise AssertionError(f"mutation not rejected for intended reason {reason!r}: {name}: {found}")
    print(f"PASS: rejected all {len(mutations) + len(reasoned)} developer-role, portal-reader and full-policy-set mutations")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    gitops_text = GITOPS.read_text()
    bootstrap_text = BOOTSTRAP.read_text()
    if not valid(gitops_text, bootstrap_text):
        details = "; ".join(problems(gitops_text, bootstrap_text))
        raise SystemExit(f"FAIL: Argo CD policies, group bindings, default role or group claim scopes diverge from the reviewed contract: {details}")
    if args.mutation_verify:
        mutation_verify(gitops_text, bootstrap_text)
    else:
        print("PASS: Argo CD policy, group bindings, read-only default and group claim scopes match the reviewed contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
