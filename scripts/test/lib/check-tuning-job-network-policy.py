#!/usr/bin/env python3
"""Assert the narwhal-tuning Job pod's NetworkPolicy is actually deny-all.

narwhal-portal#55 hardened the privileged node-tuning Job execution boundary. Part of
that hardening is a NetworkPolicy on the Job pod (podSelector
app.kubernetes.io/name=narwhal-tuning, devtools namespace) that permits neither
ingress nor egress — the pod manipulates the host via nsenter, it does not call out
to anything else in-cluster, and nothing else in-cluster calls it, so unlike
gitea-ingress-policy.yaml (issue #160) there is no allowlist to enumerate: any
non-empty ingress/egress rule here is a regression, not a legitimate caller.

This checks structure, not just presence: a NetworkPolicy that names narwhal-tuning
but carries even one non-empty ingress/egress rule reads as fixed and is not. The
negative case runs against a mutated temp copy — one with a rule added back — never
the real file, and proves the check actually fails when the gap reopens.
"""
import sys

import yaml

DEFAULT_PATH = "gitops/resources/tuning-job-network-policy.yaml"


def load_policy(path: str):
    with open(path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    policies = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    if not policies:
        return None
    for p in policies:
        labels = (p.get("spec", {}).get("podSelector", {}) or {}).get("matchLabels", {})
        if labels.get("app.kubernetes.io/name") == "narwhal-tuning":
            return p
    return policies[0]


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    problems = []

    policy = load_policy(path)
    if policy is None:
        print(f"{path}: no NetworkPolicy resource found", file=sys.stderr)
        return 1

    name = policy.get("metadata", {}).get("name", "<unnamed>")
    spec = policy.get("spec", {})

    pod_selector = (spec.get("podSelector") or {}).get("matchLabels", {})
    if pod_selector.get("app.kubernetes.io/name") != "narwhal-tuning":
        problems.append(f"{name}: podSelector does not target narwhal-tuning (app.kubernetes.io/name=narwhal-tuning)")

    policy_types = set(spec.get("policyTypes") or [])
    if "Ingress" not in policy_types:
        problems.append(f"{name}: policyTypes does not include Ingress")
    if "Egress" not in policy_types:
        problems.append(f"{name}: policyTypes does not include Egress")

    ingress_rules = spec.get("ingress") or []
    if ingress_rules:
        problems.append(f"{name}: ingress is not empty ({len(ingress_rules)} rule(s)) -- this pod has no legitimate in-cluster callers")

    egress_rules = spec.get("egress") or []
    if egress_rules:
        problems.append(f"{name}: egress is not empty ({len(egress_rules)} rule(s)) -- this pod manipulates the host directly, it does not call out")

    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
