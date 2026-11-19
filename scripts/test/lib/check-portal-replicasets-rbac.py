#!/usr/bin/env python3
"""R375: narwhal-portal ClusterRole grants get/list only on apps/replicasets (narwhal#727).

Verifies the owner decision (2026-11-19): grant ClusterRole narwhal-portal read-only
get/list on apps/replicasets (no watch, no write verbs, no wildcard) and keep
docs/common/compliance-hardening.md aligned. --mutation-verify proves broken copies fail.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CHART_DIR = ROOT / "gitops/charts/narwhal-platform"
TEMPLATE_PATH = CHART_DIR / "templates/narwhal-portal-k8s.yaml"
DOC_PATH = ROOT / "docs/common/compliance-hardening.md"
READ_ONLY_WORKLOAD_VERBS = {"get", "list", "watch"}


def load_docs() -> list[dict]:
    if shutil.which("helm"):
        res = subprocess.run(
            [
                "helm",
                "template",
                "narwhal-platform",
                str(CHART_DIR),
                "--set",
                "baseDomain=local.narwhal.internal",
                "--show-only",
                "templates/narwhal-portal-k8s.yaml",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return [d for d in yaml.safe_load_all(res.stdout) if isinstance(d, dict)]
    return [d for d in yaml.safe_load_all(TEMPLATE_PATH.read_text(encoding="utf-8")) if isinstance(d, dict)]


def check(docs: list[dict], doc_text: str) -> list[str]:
    errors: list[str] = []
    crs = [d for d in docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal"]
    if len(crs) != 1:
        return [f"ClusterRole narwhal-portal expected once, got {len(crs)}"]

    rs_verbs: set[str] = set()
    rs_rules = 0
    for rule in crs[0].get("rules") or []:
        groups = set(rule.get("apiGroups") or [])
        resources = set(rule.get("resources") or [])
        verbs = set(rule.get("verbs") or [])
        if "*" in groups or "*" in resources or "*" in verbs:
            errors.append(f"ClusterRole narwhal-portal must not use wildcards: {rule}")
        if "apps" in groups:
            if "replicasets" in resources:
                rs_rules += 1
                rs_verbs.update(verbs)
            if verbs - READ_ONLY_WORKLOAD_VERBS:
                errors.append(f"ClusterRole narwhal-portal apps rule has non-read verbs: {rule}")

    if rs_rules != 1 or rs_verbs != {"get", "list"}:
        errors.append(
            f"ClusterRole narwhal-portal must grant exactly get/list on apps/replicasets once (got rules={rs_rules}, verbs={sorted(rs_verbs)})"
        )

    if "(and not `replicasets`)" in doc_text:
        errors.append("docs/common/compliance-hardening.md still claims narwhal-portal omits replicasets")
    for required in ("`apps` `replicasets`", "spec.replicas == 0", "metadata.ownerReferences"):
        if required not in doc_text:
            errors.append(f"docs/common/compliance-hardening.md missing {required!r}")

    return errors


def _apps_rs_rule(docs: list[dict]) -> dict:
    cr = next(d for d in docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal")
    return next(r for r in cr["rules"] if "apps" in r.get("apiGroups", []) and "replicasets" in r.get("resources", []))


MUTATIONS = [
    "replicasets rule removed",
    "watch verb added to replicasets",
    "write verb delete added to replicasets",
    "wildcard resource in apps rule",
    "wildcard verb in apps rule",
    "stale compliance-hardening doc text",
]


def mutate(name: str, base_docs: list[dict], base_doc_text: str) -> tuple[list[dict], str]:
    docs = copy.deepcopy(base_docs)
    doc_text = base_doc_text
    cr = next(d for d in docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal")
    if name == "replicasets rule removed":
        cr["rules"].remove(_apps_rs_rule(docs))
    elif name == "watch verb added to replicasets":
        _apps_rs_rule(docs)["verbs"].append("watch")
    elif name == "write verb delete added to replicasets":
        _apps_rs_rule(docs)["verbs"].append("delete")
    elif name == "wildcard resource in apps rule":
        _apps_rs_rule(docs)["resources"] = ["*"]
    elif name == "wildcard verb in apps rule":
        _apps_rs_rule(docs)["verbs"] = ["*"]
    elif name == "stale compliance-hardening doc text":
        doc_text = doc_text.replace("`apps` `replicasets`", "(and not `replicasets`)", 1)
    return docs, doc_text


def main() -> int:
    docs = load_docs()
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    errors = check(docs, doc_text)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            m_docs, m_doc_text = mutate(m, docs, doc_text)
            if not check(m_docs, m_doc_text):
                print(f"FAIL: mutation not rejected: {m}", file=sys.stderr)
                return 1
        print(f"PASS: {len(MUTATIONS)} mutations rejected")
    print("portal apps/replicasets read-only rbac ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
