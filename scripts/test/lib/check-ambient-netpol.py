#!/usr/bin/env python3
"""Static regression check for ambient NetworkPolicy exceptions."""

from __future__ import annotations

import argparse
import copy
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
AMBIENT = {"database", "devtools", "monitoring", "platform-system", "storage", "dev"}
CCNP_NAME = "allow-ambient-kubelet-probes"


def documents(root: pathlib.Path) -> list[dict]:
    parsed: list[dict] = []
    paths = sorted((root / "gitops/resources").glob("*.yaml"))
    paths.extend(sorted((root / "gitops/resources").glob("*.yml")))
    paths.extend(
        path for path in (root / "scripts/cluster").glob("*.sh") if path.is_file()
    )
    for path in paths:
        text = path.read_text()
        if path.suffix in {".yaml", ".yml"}:
            try:
                parsed.extend(doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict))
            except yaml.YAMLError:
                if "kind: NetworkPolicy" in text:
                    raise
            continue
        heredocs = re.findall(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*\n(.*?)\n\1\b", text, re.S)
        for _, body in heredocs:
            if "kind: NetworkPolicy" in body or "kind: CiliumClusterwideNetworkPolicy" in body:
                parsed.extend(doc for doc in yaml.safe_load_all(body) if isinstance(doc, dict))
    return parsed


def ingress_enabled(spec: dict) -> bool:
    return "Ingress" in spec.get("policyTypes", []) or "ingress" in spec


def tcp_hbone(rule: dict) -> bool:
    return any(
        str(port.get("port")) == "15008" and port.get("protocol", "TCP") == "TCP"
        for port in rule.get("ports", []) or []
    )


def repo_owned(policy: dict) -> bool:
    return policy.get("metadata", {}).get("labels", {}).get(
        "app.kubernetes.io/managed-by"
    ) == "narwhal-gitops"


def default_deny(policy: dict) -> bool:
    spec = policy.get("spec", {})
    if spec.get("podSelector") != {} or not ingress_enabled(spec):
        return False
    # A namespace-wide ingress policy isolates all selected pods unless one rule
    # allows every source on every port.
    return not any(
        not rule.get("from") and "ports" not in rule
        for rule in spec.get("ingress", []) or []
    )


def baseline_default_deny(policy: dict) -> bool:
    meta, spec = policy.get("metadata", {}), policy.get("spec", {})
    return (
        meta.get("name") == "database-default-deny-ingress"
        and meta.get("namespace") == "database"
        and spec.get("podSelector") == {}
        and ingress_enabled(spec)
    )


def validate(docs: list[dict]) -> list[str]:
    problems: list[str] = []
    policies = [
        doc for doc in docs
        if doc.get("apiVersion") == "networking.k8s.io/v1" and doc.get("kind") == "NetworkPolicy"
    ]
    for policy in policies:
        meta, spec = policy.get("metadata", {}), policy.get("spec", {})
        namespace = meta.get("namespace")
        if namespace in AMBIENT and ingress_enabled(spec):
            rules = spec.get("ingress", []) or []
            for index, rule in enumerate(rules):
                if "ports" not in rule:
                    continue  # An ingress rule without ports allows all ports.
                if not tcp_hbone(rule):
                    problems.append(
                        f"{namespace}/{meta.get('name')} ingress rule #{index} has ports but lacks TCP 15008"
                    )
                peerless_hbone_only = (
                    tcp_hbone(rule)
                    and not rule.get("from")
                    and {str(port.get("port")) for port in rule.get("ports", [])} == {"15008"}
                )
                if peerless_hbone_only and any(other.get("from") for other in rules):
                    problems.append(
                        f"{namespace}/{meta.get('name')} ingress rule #{index} allows peer-less HBONE beside peer-restricted rules"
                    )
        if (
            namespace in AMBIENT and repo_owned(policy) and spec.get("podSelector") == {}
            and ingress_enabled(spec) and not baseline_default_deny(policy)
        ):
            peer_deny = any(
                other is not policy
                and other.get("metadata", {}).get("namespace") == namespace
                and repo_owned(other)
                and default_deny(other)
                for other in policies
            )
            if not peer_deny and default_deny(policy):
                problems.append(
                    f"{namespace}/{meta.get('name')} newly isolates all pods without another repo-owned default-deny policy"
                )

    ccnps = [
        doc for doc in docs
        if doc.get("apiVersion") == "cilium.io/v2"
        and doc.get("kind") == "CiliumClusterwideNetworkPolicy"
        and doc.get("metadata", {}).get("name") == CCNP_NAME
    ]
    valid_ccnp = any(
        doc.get("spec", {}).get("endpointSelector") == {}
        and doc.get("spec", {}).get("enableDefaultDeny") == {"ingress": False, "egress": False}
        and "169.254.7.127/32" in [
            cidr for rule in doc.get("spec", {}).get("ingress", [])
            for cidr in rule.get("fromCIDR", [])
        ]
        for doc in ccnps
    )
    if not valid_ccnp:
        problems.append("CCNP allow-ambient-kubelet-probes lacks the required world probe CIDR exception")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    args = parser.parse_args()
    docs = documents(ROOT)
    problems = validate(docs)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    if args.mutation_verify:
        mutations = copy.deepcopy(docs)
        for doc in mutations:
            if doc.get("kind") == "NetworkPolicy" and doc.get("metadata", {}).get("name") == "gitea-ingress-policy":
                doc["spec"]["ingress"][0]["ports"] = [
                    port for port in doc["spec"]["ingress"][0]["ports"]
                    if str(port.get("port")) != "15008"
                ]
        if not any("15008" in issue for issue in validate(mutations)):
            raise AssertionError("mutation removing selected-pod HBONE allowance was not detected")

        mutations = copy.deepcopy(docs)
        for doc in mutations:
            if doc.get("kind") == "NetworkPolicy" and doc.get("metadata", {}).get("name") == "gitea-ingress-policy":
                doc["spec"]["ingress"].append({"ports": [{"port": 15008, "protocol": "TCP"}]})
        if not any("peer-less HBONE" in issue for issue in validate(mutations)):
            raise AssertionError("peer-less HBONE rule beside peer-restricted rules was not detected")

        mutations = copy.deepcopy(docs)
        for doc in mutations:
            if doc.get("kind") == "NetworkPolicy" and doc.get("metadata", {}).get("name") == "gitea-ingress-policy":
                doc["spec"]["ingress"][0]["ports"] = [{"port": 3000, "protocol": "TCP"}]
        if not any("lacks TCP 15008" in issue for issue in validate(mutations)):
            raise AssertionError("mutation removing HBONE from one port-limited rule was not detected")

        mutations = copy.deepcopy(docs)
        for doc in mutations:
            if doc.get("kind") == "NetworkPolicy" and doc.get("metadata", {}).get("name") == "gitea-ingress-policy":
                doc["spec"]["ingress"].append({})
        if validate(mutations):
            raise AssertionError("an ingress rule without ports was not treated as allowing all ports")

        mutations = copy.deepcopy(docs)
        mutations.append({
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "allow-hbone", "namespace": "devtools",
                "labels": {"app.kubernetes.io/managed-by": "narwhal-gitops"},
            },
            "spec": {"podSelector": {}, "policyTypes": ["Ingress"], "ingress": [{"ports": [{"port": 15008, "protocol": "TCP"}]}]},
        })
        if not any("newly isolates all pods" in issue for issue in validate(mutations)):
            raise AssertionError("mutation adding devtools namespace-wide allow-hbone was not detected")

        mutations = copy.deepcopy(docs)
        for doc in mutations:
            if doc.get("kind") == "CiliumClusterwideNetworkPolicy" and doc.get("metadata", {}).get("name") == CCNP_NAME:
                doc["spec"]["ingress"][0]["fromCIDR"] = ["169.254.7.126/32"]
        if not any("CCNP" in issue for issue in validate(mutations)):
            raise AssertionError("mutation changing the probe CIDR was not detected")
        print("PASS: per-rule HBONE, peer-less HBONE, all-ports semantics, namespace isolation, and probe-CIDR mutations verified")
    else:
        print("PASS: ambient ingress policies, namespace-wide isolation, and probe CCNP are covered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
