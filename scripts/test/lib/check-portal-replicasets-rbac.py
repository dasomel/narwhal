#!/usr/bin/env python3
"""R378: pin the entire narwhal-portal ClusterRole rule set (narwhal#727).

Verifies the owner decision (2026-11-19): grant ClusterRole narwhal-portal read-only
get/list on apps/replicasets (no watch, no write verbs, no wildcard), pin every
rule in ClusterRole narwhal-portal via exact set equality, and keep
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

RuleTuple = tuple[frozenset[str], frozenset[str], frozenset[str]]

EXPECTED_CLUSTERROLE_RULES: set[RuleTuple] = {
    (
        frozenset({""}),
        frozenset({"nodes", "pods", "persistentvolumeclaims", "namespaces", "services", "events", "configmaps"}),
        frozenset({"get", "list", "watch"}),
    ),
    (frozenset({""}), frozenset({"resourcequotas"}), frozenset({"get", "list"})),
    (frozenset({"storage.k8s.io"}), frozenset({"storageclasses"}), frozenset({"get", "list"})),
    (frozenset({""}), frozenset({"pods/log"}), frozenset({"get"})),
    (frozenset({"policy"}), frozenset({"poddisruptionbudgets"}), frozenset({"get", "list", "watch"})),
    (frozenset({"networking.k8s.io"}), frozenset({"networkpolicies"}), frozenset({"get", "list", "watch"})),
    (frozenset({"cert-manager.io"}), frozenset({"certificates", "clusterissuers"}), frozenset({"get", "list", "watch"})),
    (frozenset({"kyverno.io"}), frozenset({"clusterpolicies", "policies"}), frozenset({"get", "list", "watch"})),
    (frozenset({"apps"}), frozenset({"deployments", "statefulsets", "daemonsets"}), frozenset({"get", "list", "watch"})),
    (frozenset({"apps"}), frozenset({"replicasets"}), frozenset({"get", "list"})),
    (
        frozenset({"rbac.authorization.k8s.io"}),
        frozenset({"clusterrolebindings", "rolebindings", "clusterroles", "roles"}),
        frozenset({"get", "list", "watch"}),
    ),
    (
        frozenset({"aquasecurity.github.io"}),
        frozenset(
            {
                "vulnerabilityreports",
                "clustervulnerabilityreports",
                "exposedsecretreports",
                "configauditreports",
                "clusterconfigauditreports",
                "rbacassessmentreports",
                "clusterrbacassessmentreports",
                "infraassessmentreports",
                "clusterinfraassessmentreports",
                "compliancereports",
                "clustercompliancereports",
            }
        ),
        frozenset({"get", "list", "watch"}),
    ),
    (frozenset({"metrics.k8s.io"}), frozenset({"nodes", "pods"}), frozenset({"get", "list"})),
}


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


def _fmt_rule(rule: RuleTuple) -> str:
    groups, resources, verbs = rule
    return f"(apiGroups={sorted(groups)}, resources={sorted(resources)}, verbs={sorted(verbs)})"


def check(docs: list[dict], doc_text: str) -> list[str]:
    errors: list[str] = []
    crs = [d for d in docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal"]
    if len(crs) != 1:
        return [f"ClusterRole narwhal-portal expected once, got {len(crs)}"]

    # Nothing but plain rules: an aggregationRule (or any other top-level key) would let the
    # aggregation controller add permissions outside this pinned list.
    extra_top = set(crs[0].keys()) - {"apiVersion", "kind", "metadata", "rules"}
    if extra_top:
        errors.append(f"ClusterRole narwhal-portal has unexpected top-level keys {sorted(extra_top)}")

    actual_list: list[RuleTuple] = []
    for rule in crs[0].get("rules") or []:
        extra_keys = set(rule.keys()) - {"apiGroups", "resources", "verbs"}
        if extra_keys:
            errors.append(f"ClusterRole narwhal-portal rule has unexpected keys {sorted(extra_keys)}: {rule}")
        groups = frozenset(rule.get("apiGroups") or [])
        resources = frozenset(rule.get("resources") or [])
        verbs = frozenset(rule.get("verbs") or [])
        if "*" in groups or "*" in resources or "*" in verbs:
            errors.append(f"ClusterRole narwhal-portal must not use wildcards: {rule}")
        actual_list.append((groups, resources, verbs))

    actual_set = set(actual_list)
    if len(actual_list) != len(actual_set):
        errors.append("ClusterRole narwhal-portal contains duplicate rules")
    if actual_set != EXPECTED_CLUSTERROLE_RULES:
        unexpected = sorted(_fmt_rule(r) for r in actual_set - EXPECTED_CLUSTERROLE_RULES)
        missing = sorted(_fmt_rule(r) for r in EXPECTED_CLUSTERROLE_RULES - actual_set)
        errors.append(
            "ClusterRole narwhal-portal rule set drifted from expected pin: "
            f"unexpected={unexpected}, missing={missing}"
        )

    if "(and not `replicasets`)" in doc_text:
        errors.append("docs/common/compliance-hardening.md still claims narwhal-portal omits replicasets")
    for required in (
        "`apps` `replicasets`",
        "spec.replicas == 0",
        "status.replicas == 0",
        "deployment.kubernetes.io/revision",
        "metadata.ownerReferences",
    ):
        if required not in doc_text:
            errors.append(f"docs/common/compliance-hardening.md missing {required!r}")

    return errors


def _apps_rs_rule(docs: list[dict]) -> dict:
    cr = next(d for d in docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal")
    return next(r for r in cr["rules"] if "apps" in r.get("apiGroups", []) and "replicasets" in r.get("resources", []))


def _core_workloads_rule(docs: list[dict]) -> dict:
    cr = next(d for d in docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal")
    return next(r for r in cr["rules"] if "" in r.get("apiGroups", []) and "nodes" in r.get("resources", []))


MUTATIONS = [
    "replicasets rule removed",
    "watch verb added to replicasets",
    "write verb delete added to replicasets",
    "extra resource controllerrevisions in replicasets rule",
    "extra apiGroup in replicasets rule",
    "new rule granting secrets get/list",
    "extra verb update on existing core rule",
    "wildcard resource in apps rule",
    "wildcard verb in apps rule",
    "aggregationRule added to the ClusterRole",
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
    elif name == "extra resource controllerrevisions in replicasets rule":
        _apps_rs_rule(docs)["resources"].append("controllerrevisions")
    elif name == "extra apiGroup in replicasets rule":
        _apps_rs_rule(docs)["apiGroups"].append("batch")
    elif name == "new rule granting secrets get/list":
        cr["rules"].append({"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list"]})
    elif name == "extra verb update on existing core rule":
        _core_workloads_rule(docs)["verbs"].append("update")
    elif name == "wildcard resource in apps rule":
        _apps_rs_rule(docs)["resources"] = ["*"]
    elif name == "wildcard verb in apps rule":
        _apps_rs_rule(docs)["verbs"] = ["*"]
    elif name == "aggregationRule added to the ClusterRole":
        cr["aggregationRule"] = {"clusterRoleSelectors": [{"matchLabels": {"rbac.example/aggregate": "true"}}]}
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
