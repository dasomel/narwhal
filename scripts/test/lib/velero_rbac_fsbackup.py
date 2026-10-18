#!/usr/bin/env python3
"""Fsbackup RBAC checks and mutation verification for Velero (issue #418, D-vrbac-4)."""

import copy
from pathlib import Path
from typing import Callable
import yaml

FORBIDDEN_EVENT_GROUPS = {"", "events.k8s.io"}


def check_fsbackup_role(path: Path, role: dict) -> list[str]:
    problems = []

    # (a) the union of verbs on ("", "nodes") across ClusterRole rules must equal exactly {"get"}
    node_verbs = set()
    for rule in role.get("rules", []):
        groups = rule.get("apiGroups", [])
        resources = rule.get("resources", [])
        verbs = rule.get("verbs", [])
        if "" in groups and "nodes" in resources:
            node_verbs.update(verbs)
    if node_verbs != {"get"}:
        problems.append(f"{path}: nodes verbs across ClusterRole rules must equal exactly {{'get'}}, got {node_verbs}")

    # (b) no ClusterRole rule may mention resource "events" in group "" or "events.k8s.io"
    for rule in role.get("rules", []):
        groups = set(rule.get("apiGroups", []))
        resources = set(rule.get("resources", []))
        if "events" in resources and (groups & FORBIDDEN_EVENT_GROUPS):
            problems.append(f"{path}: cluster-wide events rule is forbidden (groups: {sorted(groups)})")

    # (c) Role and RoleBinding velero-node-agent-events in namespace storage
    try:
        docs = list(yaml.safe_load_all(path.read_text()))
    except (yaml.YAMLError, OSError) as exc:
        return [f"{path}: failed to read documents for fsbackup check: {exc}"]

    roles = [d for d in docs if isinstance(d, dict) and d.get("kind") == "Role"]
    matching_roles = [r for r in roles if r.get("metadata", {}).get("name") == "velero-node-agent-events"]
    if len(matching_roles) != 1 or len(roles) != 1:
        problems.append(f"{path}: expected exactly one Role named velero-node-agent-events, found {len(matching_roles)} matching out of {len(roles)} Roles")
    elif matching_roles:
        role_doc = matching_roles[0]
        if role_doc.get("metadata", {}).get("namespace") != "storage":
            problems.append(f"{path}: Role velero-node-agent-events namespace must be storage, got {role_doc.get('metadata', {}).get('namespace')}")
        rules = role_doc.get("rules", [])
        if len(rules) != 1:
            problems.append(f"{path}: Role velero-node-agent-events must have exactly 1 rule, got {len(rules)}")
        else:
            r0 = rules[0]
            if r0.get("apiGroups") != [""]:
                problems.append(f"{path}: Role velero-node-agent-events apiGroups must be [''], got {r0.get('apiGroups')}")
            if r0.get("resources") != ["events"]:
                problems.append(f"{path}: Role velero-node-agent-events resources must be ['events'], got {r0.get('resources')}")
            verbs = set(r0.get("verbs", []))
            if verbs != {"get", "list", "watch"} or len(r0.get("verbs", [])) != 3:
                problems.append(f"{path}: Role velero-node-agent-events verbs must be get, list, watch; got {r0.get('verbs')}")

    bindings = [d for d in docs if isinstance(d, dict) and d.get("kind") == "RoleBinding"]
    matching_bindings = [b for b in bindings if b.get("metadata", {}).get("name") == "velero-node-agent-events"]
    if len(matching_bindings) != 1 or len(bindings) != 1:
        problems.append(f"{path}: expected exactly one RoleBinding named velero-node-agent-events, found {len(matching_bindings)} matching out of {len(bindings)} RoleBindings")
    elif matching_bindings:
        binding_doc = matching_bindings[0]
        if binding_doc.get("metadata", {}).get("namespace") != "storage":
            problems.append(f"{path}: RoleBinding velero-node-agent-events namespace must be storage, got {binding_doc.get('metadata', {}).get('namespace')}")
        role_ref = binding_doc.get("roleRef", {})
        if (role_ref.get("apiGroup") != "rbac.authorization.k8s.io" or
                role_ref.get("kind") != "Role" or
                role_ref.get("name") != "velero-node-agent-events"):
            problems.append(f"{path}: RoleBinding roleRef must be Role velero-node-agent-events in rbac.authorization.k8s.io")
        subjects = binding_doc.get("subjects", [])
        expected_subject = {"kind": "ServiceAccount", "name": "velero-server", "namespace": "storage"}
        if len(subjects) != 1 or subjects[0] != expected_subject:
            problems.append(f"{path}: RoleBinding subjects must be exactly [{expected_subject}], got {subjects}")

    return problems


def _mut_nodes_list(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "ClusterRole")
    for r in role["rules"]:
        if "" in r.get("apiGroups", []) and "nodes" in r.get("resources", []):
            r["verbs"] = ["get", "list"]


def _mut_nodes_watch(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "ClusterRole")
    for r in role["rules"]:
        if "" in r.get("apiGroups", []) and "nodes" in r.get("resources", []):
            r["verbs"] = ["get", "watch"]


def _mut_nodes_delete(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "ClusterRole")
    for r in role["rules"]:
        if "" in r.get("apiGroups", []) and "nodes" in r.get("resources", []):
            r["verbs"] = ["get", "delete"]


def _mut_remove_nodes_get(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "ClusterRole")
    role["rules"] = [r for r in role["rules"] if not ("" in r.get("apiGroups", []) and "nodes" in r.get("resources", []))]


def _mut_cluster_wide_events(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "ClusterRole")
    role["rules"].append({"apiGroups": [""], "resources": ["events"], "verbs": ["get"]})


def _mut_role_missing(docs: list[dict]) -> None:
    docs[:] = [d for d in docs if d.get("kind") != "Role"]


def _mut_role_events_create(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "Role")
    for r in role["rules"]:
        if "events" in r.get("resources", []):
            r["verbs"] = ["get", "list", "watch", "create"]


def _mut_role_extra_secrets(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "Role")
    for r in role["rules"]:
        if "events" in r.get("resources", []):
            r["resources"].append("secrets")


def _mut_role_namespace_default(docs: list[dict]) -> None:
    role = next(d for d in docs if d.get("kind") == "Role")
    role["metadata"]["namespace"] = "default"


def _mut_rb_another_subject(docs: list[dict]) -> None:
    rb = next(d for d in docs if d.get("kind") == "RoleBinding")
    rb["subjects"] = [{"kind": "ServiceAccount", "name": "another-sa", "namespace": "storage"}]


def _mut_rb_another_role(docs: list[dict]) -> None:
    rb = next(d for d in docs if d.get("kind") == "RoleBinding")
    rb["roleRef"]["name"] = "another-role"


FSBACKUP_MUTATIONS = [
    ("nodes+list", _mut_nodes_list),
    ("nodes+watch", _mut_nodes_watch),
    ("nodes+delete", _mut_nodes_delete),
    ("remove nodes get", _mut_remove_nodes_get),
    ("cluster-wide events rule", _mut_cluster_wide_events),
    ("Role missing", _mut_role_missing),
    ("Role events+create", _mut_role_events_create),
    ("Role with extra resource secrets", _mut_role_extra_secrets),
    ("Role in namespace default", _mut_role_namespace_default),
    ("RoleBinding to another subject", _mut_rb_another_subject),
    ("RoleBinding to another Role", _mut_rb_another_role),
]


def mutation_verify_fsbackup(role_path: Path, temp_dir: Path, check_role_fn: Callable[[Path], list[str]]) -> list[str]:
    problems = []
    base_docs = list(yaml.safe_load_all(role_path.read_text()))
    for name, mutator in FSBACKUP_MUTATIONS:
        mutated_docs = copy.deepcopy(base_docs)
        mutator(mutated_docs)
        target = temp_dir / f"mutated-{name.replace(' ', '_').replace('+', '_')}.yaml"
        target.write_text(yaml.safe_dump_all(mutated_docs, sort_keys=False))
        if not check_role_fn(target):
            problems.append(f"safety mutation escaped: {name}")
    return problems
