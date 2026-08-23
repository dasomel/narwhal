#!/usr/bin/env python3
"""Assert the gitea-http NetworkPolicy is a real default-deny-others boundary.

Issue #160's own fix (492e65a) narrowed REVERSE_PROXY_TRUSTED_PROXIES from "*" to the
pod network CIDR and said plainly that the CIDR is a second layer, not a boundary --
every pod in the cluster shares it. The actual boundary is an ingress NetworkPolicy on
gitea-http that enumerates its legitimate direct callers (APISIX, ArgoCD, the portal,
Kaniko) instead of trusting the whole pod network.

This checks structure, not just presence: a NetworkPolicy that names gitea-http but
still carries a bare allow-all `from: []` / `- {}` rule looks like a fix and is not
one -- that is exactly the shape this script is here to catch. Takes an optional path
so the regression suite can point it at a mutated temp copy and prove the negative case
actually fails.
"""
import sys

import yaml

DEFAULT_PATH = "gitops/resources/gitea-ingress-policy.yaml"

# One selector key -> label pair per required caller. A caller counts as allowed only
# if some ingress `from` entry carries a podSelector with this exact match.
REQUIRED_CALLERS = {
    "apisix": ("app.kubernetes.io/name", "apisix"),
    "argocd-repo-server": ("app.kubernetes.io/name", "argocd-repo-server"),
    "argocd-application-controller": ("app.kubernetes.io/name", "argocd-application-controller"),
    "narwhal-portal": ("app", "narwhal-portal"),
}


def load_policy(path: str):
    with open(path) as f:
        docs = [d for d in yaml.safe_load_all(f) if d]
    policies = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    if not policies:
        return None
    # Prefer the one whose podSelector targets gitea, in case the file ever grows a
    # second NetworkPolicy alongside it.
    for p in policies:
        labels = (p.get("spec", {}).get("podSelector", {}) or {}).get("matchLabels", {})
        if labels.get("app.kubernetes.io/name") == "gitea":
            return p
    return policies[0]


def is_allow_all(from_entries) -> bool:
    """True if any entry in an ingress rule's `from` list grants unrestricted access:
    an empty/missing `from`, or an entry with none of podSelector/namespaceSelector/ipBlock."""
    if not from_entries:
        return True
    for entry in from_entries:
        if not any(k in entry for k in ("podSelector", "namespaceSelector", "ipBlock")):
            return True
    return False


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
    if pod_selector.get("app.kubernetes.io/name") != "gitea":
        problems.append(f"{name}: podSelector does not target gitea (app.kubernetes.io/name=gitea)")

    if "Ingress" not in (spec.get("policyTypes") or []):
        problems.append(f"{name}: policyTypes does not include Ingress")

    ingress_rules = spec.get("ingress") or []
    if not ingress_rules:
        problems.append(f"{name}: no ingress rules -- this denies everyone, including the required callers")

    for i, rule in enumerate(ingress_rules):
        if is_allow_all(rule.get("from")):
            problems.append(f"{name}: ingress rule #{i} allows all sources (missing/empty selector) -- not default-deny-others")

    # Flatten every podSelector matchLabels pair seen across every `from` entry.
    seen_labels = set()
    for rule in ingress_rules:
        for entry in rule.get("from") or []:
            labels = (entry.get("podSelector") or {}).get("matchLabels") or {}
            for k, v in labels.items():
                seen_labels.add((k, v))

    for caller, (key, value) in REQUIRED_CALLERS.items():
        if (key, value) not in seen_labels:
            problems.append(f"{name}: no ingress rule allows {caller} ({key}={value})")

    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
