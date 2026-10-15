#!/usr/bin/env python3
"""Keep human Argo CD developer sync scope aligned across GitOps and bootstrap."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

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


BLOCK_RE = r"(?m)^  (['\"]?)policy\.csv\1: \|\n((?:^    .*\n|^\n)*)"


def policy_lines(text: str, source: str) -> list[str]:
    if source == "gitops":
        match = re.search(BLOCK_RE, text)
    else:
        found = re.search(r"(?m)^  (['\"]?)policy\.csv\1: \|", text)
        if not found:
            return []
        match = re.search(BLOCK_RE, text[found.start():])
    if not match:
        return []
    return [line.strip() for line in match.group(2).splitlines() if line.strip()]


NONE_POLICIES = {"p, role:none, applications, get, */*, deny"}
# D1: allowlist the COMPLETE p/g line set of each source (not just the two reviewed roles),
# so a new subject, role:admin grant or dropped deny line is rejected. Cost: any intended
# policy change must update this contract. Escape: edit these sets together with both sources.
EXPECTED_LINES = {
    "gitops": DEVELOPER_POLICIES | NONE_POLICIES | PORTAL_READER_POLICIES | GITOPS_GROUPS,
    "bootstrap": DEVELOPER_POLICIES | NONE_POLICIES | BOOTSTRAP_GROUPS,
}
TEMPLATE_MARKERS = ("{{", "${", "$(", "`")
RBAC_NAME = "argocd-rbac-cm"
# D2: YAML allows quoted scalars and trailing comments, so names and keys are normalised
# before comparison; a later document wins in Argo, so an unrecognised spelling is a bypass.
KEY_RE = r"(?m)^[ \t]*(['\"]?)(policy\.[\w.\-]*csv)\1[ \t]*:"
NAME_RE = re.compile(r"""^[ \t]*name[ \t]*:[ \t]*(?:(['"])(.*?)\1|([^\s#'"]+))[ \t]*(?:#.*)?$""")


def rbac_declarations(text: str) -> int:
    count = 0
    for line in text.splitlines():
        match = NAME_RE.match(line)
        if match and (match.group(2) if match.group(1) else match.group(3)) == RBAC_NAME:
            count += 1
    return count


# D3: line regexes cannot follow YAML notation (flow style, tags, escapes, `? key`), so the
# sources are pinned structurally too: exact document counts (any new document changes the
# count whatever its notation) and the PARSED argocd-rbac-cm data. Cost: adding a document
# to either source needs a matching bump here. Escape: update these pins with the source.
GITOPS_DOCS = 3
BOOTSTRAP_HEREDOC_DOCS = [1, 1, 1, 1, 1]  # per heredoc, in file order
RBAC_MENTIONS = {"gitops": 2, "bootstrap": 2}  # raw `argocd-rbac-cm` substrings
EXPECTED_DATA_KEYS = {"policy.csv", "policy.default", "scopes"}


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in seen:
                raise yaml.YAMLError(f"duplicate mapping key {key!r}")
            seen.add(key)
        return super().construct_mapping(node, deep)


def load_docs(text: str, label: str, found: list[str]) -> list:
    """Parse text; fail closed on tags, anchors, aliases, duplicate keys or parse errors."""
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.AliasEvent):
                found.append(f"{label}: YAML alias is not allowed")
            if getattr(event, "anchor", None):
                found.append(f"{label}: YAML anchor is not allowed")
            if getattr(event, "tag", None):
                found.append(f"{label}: YAML tag {event.tag!r} is not allowed")
        docs = list(yaml.load_all(text, Loader=StrictLoader))
    except yaml.YAMLError as exc:
        found.append(f"{label}: unparseable YAML ({str(exc).splitlines()[0]})")
        return []
    return [doc for doc in docs if doc is not None]


def heredocs(text: str) -> list[tuple[str, str]]:
    blocks, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        match = re.search(r"(?<!<)<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1", lines[i])
        if not match:
            i += 1
            continue
        body = []
        i += 1
        while i < len(lines) and lines[i].strip() != match.group(2):
            body.append(lines[i])
            i += 1
        blocks.append((match.group(1), "\n".join(body) + "\n"))
        i += 1
    return blocks


def structural_problems(gitops_text: str, bootstrap_text: str) -> list[str]:
    found: list[str] = []
    docs_by_source: dict[str, list] = {}
    gitops_docs = load_docs(re.sub(r"\{\{.*?\}\}", "HELMVALUE", gitops_text), "gitops", found)
    if len(gitops_docs) != GITOPS_DOCS:
        found.append(f"gitops: document count {len(gitops_docs)} != {GITOPS_DOCS}")
    docs_by_source["gitops"] = gitops_docs
    blocks = heredocs(bootstrap_text)
    if len(blocks) != len(BOOTSTRAP_HEREDOC_DOCS):
        found.append(f"bootstrap: heredoc count {len(blocks)} != {len(BOOTSTRAP_HEREDOC_DOCS)}")
    bootstrap_docs: list = []
    for index, (quote, body) in enumerate(blocks):
        if not quote and ("`" in body or "$(" in body):
            found.append(f"bootstrap: heredoc {index} runs command substitution inside YAML")
        # a bare ${VAR} line injects a block of keys; model it as one extra key so it can never
        # hide inside the argocd-rbac-cm data mapping
        body = re.sub(r"(?m)^\$\{[^}]*\}$", "  SUBSTITUTED: x", body)
        body = re.sub(r"\$\{[^}]*\}", "SUBSTITUTED", body)
        docs = load_docs(body, f"bootstrap heredoc {index}", found)
        if index < len(BOOTSTRAP_HEREDOC_DOCS) and len(docs) != BOOTSTRAP_HEREDOC_DOCS[index]:
            found.append(f"bootstrap: heredoc {index} document count {len(docs)} != {BOOTSTRAP_HEREDOC_DOCS[index]}")
        bootstrap_docs += docs
    docs_by_source["bootstrap"] = bootstrap_docs
    for source, text in (("gitops", gitops_text), ("bootstrap", bootstrap_text)):
        if text.count(RBAC_NAME) != RBAC_MENTIONS[source]:
            found.append(f"{source}: raw {RBAC_NAME} mentions {text.count(RBAC_NAME)} != {RBAC_MENTIONS[source]}")
        rbac = [
            d for d in docs_by_source[source]
            if isinstance(d, dict) and d.get("kind") == "ConfigMap"
            and isinstance(d.get("metadata"), dict) and d["metadata"].get("name") == RBAC_NAME
        ]
        if len(rbac) != 1:
            found.append(f"{source}: parsed {RBAC_NAME} ConfigMap count {len(rbac)} != 1")
            continue
        data = rbac[0].get("data")
        if not isinstance(data, dict) or set(data) != EXPECTED_DATA_KEYS:
            found.append(f"{source}: parsed data keys {sorted(data) if isinstance(data, dict) else data!r} != {sorted(EXPECTED_DATA_KEYS)}")
            continue
        if data["policy.default"] != "role:readonly" or data["scopes"] != "[groups]":
            found.append(f"{source}: parsed policy.default/scopes differ from reviewed values")
        csv = [", ".join(f.strip() for f in ln.split(",")) for ln in str(data["policy.csv"]).splitlines() if ln.strip()]
        if sorted(csv) != sorted(EXPECTED_LINES[source]):
            found.append(f"{source}: parsed policy.csv lines differ from reviewed set (extra={sorted(set(csv) - EXPECTED_LINES[source])}, missing={sorted(EXPECTED_LINES[source] - set(csv))})")
    return found


def problems(gitops_text: str, bootstrap_text: str) -> list[str]:
    found: list[str] = []
    for source, text in (("gitops", gitops_text), ("bootstrap", bootstrap_text)):
        keys = [key for _, key in re.findall(KEY_RE, text)]
        if keys != ["policy.csv"]:
            found.append(f"{source}: policy csv keys {keys} != ['policy.csv']")
        declared = rbac_declarations(text)
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
    return found + structural_problems(gitops_text, bootstrap_text)


def valid(gitops_text: str, bootstrap_text: str) -> bool:
    return not problems(gitops_text, bootstrap_text)


def structural_cases(gitops_text: str, bootstrap_text: str) -> dict:
    """Notation tricks that line regexes miss; each must be rejected by the structural layer."""
    flow_doc = (
        "{apiVersion: v1, kind: ConfigMap, metadata: {name: argocd-rbac-cm}, "
        'data: {policy.csv: "p, role:developer, applications, sync, */*, allow"}}'
    )
    flow_data = (
        'data: {policy.csv: "p, role:developer, applications, sync, */*, allow", '
        'policy.default: "role:readonly", scopes: "[groups]"}'
    )

    def swap(text: str, old: str, new: str) -> str:
        assert text.count(old) == 1, old
        return text.replace(old, new, 1)

    def with_data(text: str, source: str, new_data: str) -> str:
        start = text.index("\ndata:\n", text.index("\n  name: argocd-rbac-cm\n")) + 1
        end = text.index("\nEOF", start) if source == "bootstrap" else len(text)
        return text[:start] + new_data + text[end:]

    edits = {
        "one-line flow document after ---": (
            lambda t, src: t + "\n---\n" + flow_doc + "\n" if src == "gitops"
            else swap(t, "  scopes: '[groups]'\nEOF", "  scopes: '[groups]'\n---\n" + flow_doc + "\nEOF"),
            "document count"),
        "flow data mapping widened": (lambda t, src: with_data(t, src, flow_data + "\n"), "parsed policy.csv lines"),
        "flow metadata plus ? policy.csv widened": (
            lambda t, src: swap(
                swap(t, "metadata:\n  name: argocd-rbac-cm\n  namespace: devtools\n  labels:\n",
                     "metadata: {name: argocd-rbac-cm, namespace: devtools}\nx-labels:\n"),
                "  policy.csv: |\n    p, role:developer, applications, sync, tenants/*, allow",
                "  ? policy.csv\n  : |\n    p, role:developer, applications, sync, */*, allow"),
            "parsed policy.csv lines"),
        "escaped name hides the ConfigMap": (
            lambda t, src: swap(t, "\n  name: argocd-rbac-cm\n", '\n  name: "argocd-rbac\\x2dcm"\n'),
            "raw argocd-rbac-cm mentions"),
        "!!str tagged name": (
            lambda t, src: swap(t, "\n  name: argocd-rbac-cm\n", "\n  name: !!str argocd-rbac-cm\n"), "YAML tag"),
        "duplicate scopes key": (
            lambda t, src: swap(t, "  scopes: '[groups]'", "  scopes: '[groups]'\n  scopes: '[email]'"),
            "duplicate mapping key"),
        "YAML anchor": (lambda t, src: swap(t, "  scopes: '[groups]'", "  scopes: &a '[groups]'"), "YAML anchor"),
        "folded policy.csv": (
            lambda t, src: swap(t, "  policy.csv: |\n", "  policy.csv: >-\n"), "parsed policy.csv lines"),
    }
    cases = {}
    for name, (edit, reason) in edits.items():
        cases[f"{name} (GitOps)"] = (edit(gitops_text, "gitops"), bootstrap_text, reason)
        cases[f"{name} (bootstrap)"] = (gitops_text, edit(bootstrap_text, "bootstrap"), reason)
    return cases


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
        **{
            f"second rbac-cm with {label} name ({src})": (
                gitops_text + (extra if src == "GitOps" else ""),
                bootstrap_text + (extra if src == "bootstrap" else ""),
                "declared 2 times",
            )
            for label, name in (
                ("single-quoted", "'argocd-rbac-cm'"),
                ("double-quoted", '"argocd-rbac-cm"'),
                ("trailing-comment", "argocd-rbac-cm # c"),
            )
            for src in ("GitOps", "bootstrap")
            for extra in (
                "\n---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: " + name + "\ndata:\n"
                "  policy.csv: |\n    p, role:developer, applications, sync, */*, allow\n",
            )
        },
        **{
            f"second rbac-cm with {label} policy.csv key ({src})": (
                gitops_text + (extra if src == "GitOps" else ""),
                bootstrap_text + (extra if src == "bootstrap" else ""),
                "policy csv keys",
            )
            for label, key in (("double-quoted", '"policy.csv"'), ("single-quoted", "'policy.csv'"))
            for src in ("GitOps", "bootstrap")
            for extra in (
                "\n---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: other\ndata:\n  " + key + ": |\n"
                "    p, role:developer, applications, sync, */*, allow\n",
            )
        },
        **structural_cases(gitops_text, bootstrap_text),
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
