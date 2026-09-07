#!/usr/bin/env python3
"""Assert the APISIX NetworkPolicy restricts the Admin API to its two documented
callers while leaving the gateway data-plane ports (9080/9443) open to any source."""
import sys

import yaml

DEFAULT_PATH = "gitops/resources/apisix-admin-ingress-policy.yaml"
CONTROLLER = {"app.kubernetes.io/name": "apisix-ingress-controller"}
PORTAL = {"app": "narwhal-portal"}
DEVTOOLS = {"kubernetes.io/metadata.name": "devtools"}
ADMIN_PORT = 9180
GATEWAY_PORTS = {9080, 9443}


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    with open(path) as f:
        docs = [doc for doc in yaml.safe_load_all(f) if doc]
    policy = next((doc for doc in docs if doc.get("kind") == "NetworkPolicy"), None)
    problems = []
    if policy is None:
        problems.append("no NetworkPolicy resource found")
    else:
        spec = policy.get("spec", {})
        selector = (spec.get("podSelector") or {}).get("matchLabels") or {}
        if selector.get("app.kubernetes.io/name") != "apisix":
            problems.append("podSelector does not target APISIX")
        if "Ingress" not in (spec.get("policyTypes") or []):
            problems.append("policyTypes does not include Ingress")

        seen = set()
        gateway_open_ports = set()
        for rule in spec.get("ingress") or []:
            entries = rule.get("from") or []
            ports = rule.get("ports") or []
            tcp_ports = {p.get("port") for p in ports if p.get("protocol") == "TCP"}

            if not entries:
                # An unrestricted (no `from`) rule is only acceptable for the gateway
                # data-plane ports — anything else means an unintended open door.
                if not tcp_ports or (tcp_ports - GATEWAY_PORTS):
                    problems.append(
                        "unrestricted ingress rule covers non-gateway port(s) "
                        f"{sorted(tcp_ports - GATEWAY_PORTS) or 'none'}"
                    )
                else:
                    gateway_open_ports |= tcp_ports
                continue

            if tcp_ports != {ADMIN_PORT}:
                problems.append("restricted ingress rule does not limit access to TCP/9180")
            for entry in entries:
                if not any(k in entry for k in ("podSelector", "namespaceSelector", "ipBlock")):
                    problems.append("ingress rule contains an unrestricted source")
                if "ipBlock" in entry:
                    problems.append("ingress rule permits a CIDR instead of a documented workload")
                labels = (entry.get("podSelector") or {}).get("matchLabels") or {}
                namespace = (entry.get("namespaceSelector") or {}).get("matchLabels") or {}
                if labels == CONTROLLER and not namespace:
                    seen.add("controller")
                elif labels == PORTAL and namespace == DEVTOOLS:
                    seen.add("portal")
                else:
                    problems.append("ingress rule permits an undocumented workload or namespace")

        for caller in {"controller", "portal"} - seen:
            problems.append(f"missing required {caller} caller rule")

        missing_gateway = GATEWAY_PORTS - gateway_open_ports
        if missing_gateway:
            problems.append(
                f"gateway port(s) {sorted(missing_gateway)} not reachable from an arbitrary source"
            )

    for problem in problems:
        print(f"{path}: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
