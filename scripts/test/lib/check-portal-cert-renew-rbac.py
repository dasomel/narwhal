#!/usr/bin/env python3
"""R318: certificates/status update is namespaced, never cluster-wide (D-CERT-RENEW-NS, #562).

The portal ServiceAccount may update certificates/status only through Roles in the namespaces
that hold Certificates. --mutation-verify re-runs the check against broken copies.
"""
import copy
import sys

import yaml

PATH = "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml"
# Namespaces holding Certificates on the live cluster (kubectl get certificates -A, 2026-11-06).
NAMESPACES = {"devtools", "platform-system", "storage"}
SA = {"kind": "ServiceAccount", "name": "narwhal-portal", "namespace": "devtools"}


def load():
    return [d for d in yaml.safe_load_all(open(PATH)) if d]


def grants_status(rule):
    return "certificates/status" in rule.get("resources", []) or "*" in rule.get("resources", [])


def check(docs):
    errors = []
    cr = [d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"] == "narwhal-portal"]
    if len(cr) != 1:
        return ["ClusterRole narwhal-portal not found exactly once"]
    for r in cr[0]["rules"]:
        if "cert-manager.io" in r.get("apiGroups", []) and grants_status(r):
            errors.append("ClusterRole narwhal-portal still grants certificates/status (cluster-wide)")
        if "cert-manager.io" in r.get("apiGroups", []) and "certificates" in r.get("resources", []):
            if set(r["verbs"]) - {"get", "list", "watch"}:
                errors.append("ClusterRole certificates rule has write verbs")
    for d in docs:
        if d["kind"] == "ClusterRoleBinding" and d["metadata"]["name"] != "narwhal-portal":
            if any(s.get("name") == "narwhal-portal" for s in d.get("subjects", [])):
                errors.append("another ClusterRoleBinding binds the portal SA")
    roles = {}
    for d in docs:
        if d["kind"] == "Role" and d["metadata"]["name"] == "narwhal-portal-cert-renew":
            roles[d["metadata"]["namespace"]] = d
    if set(roles) != NAMESPACES:
        errors.append(f"cert-renew Roles in {sorted(roles)}, want {sorted(NAMESPACES)}")
    for ns, role in roles.items():
        rules = role["rules"]
        if rules != [{"apiGroups": ["cert-manager.io"], "resources": ["certificates/status"], "verbs": ["update"]}]:
            errors.append(f"Role in {ns} must grant exactly update on certificates/status")
    binds = {}
    for d in docs:
        if d["kind"] == "RoleBinding" and d["metadata"]["name"] == "narwhal-portal-cert-renew":
            binds[d["metadata"]["namespace"]] = d
    if set(binds) != NAMESPACES:
        errors.append(f"cert-renew RoleBindings in {sorted(binds)}, want {sorted(NAMESPACES)}")
    for ns, b in binds.items():
        if b["roleRef"] != {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "narwhal-portal-cert-renew"}:
            errors.append(f"RoleBinding in {ns} has wrong roleRef")
        if b.get("subjects") != [SA]:
            errors.append(f"RoleBinding in {ns} must bind only {SA['namespace']}/{SA['name']}")
    return errors


def mutate(name):
    docs = copy.deepcopy(load())
    cr = next(d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"] == "narwhal-portal")
    role = lambda ns: next(d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "narwhal-portal-cert-renew"
                           and d["metadata"]["namespace"] == ns)
    if name == "cluster-wide rule restored":
        cr["rules"].append({"apiGroups": ["cert-manager.io"], "resources": ["certificates/status"], "verbs": ["update"]})
    elif name == "wildcard resource":
        cr["rules"].append({"apiGroups": ["cert-manager.io"], "resources": ["*"], "verbs": ["update"]})
    elif name == "namespace Role missing":
        docs.remove(role("storage"))
    elif name == "role widened to certificates":
        role("devtools")["rules"][0]["resources"].append("certificates")
    elif name == "role verb widened":
        role("platform-system")["rules"][0]["verbs"] = ["update", "patch"]
    elif name == "binding subject widened":
        for d in docs:
            if d["kind"] == "RoleBinding" and d["metadata"]["name"] == "narwhal-portal-cert-renew":
                d["subjects"].append({"kind": "Group", "name": "system:authenticated"})
                break
    elif name == "extra namespace":
        extra = copy.deepcopy(role("storage"))
        extra["metadata"]["namespace"] = "kube-system"
        docs.append(extra)
    return docs


MUTATIONS = ["cluster-wide rule restored", "wildcard resource", "namespace Role missing",
             "role widened to certificates", "role verb widened", "binding subject widened", "extra namespace"]


def main():
    errors = check(load())
    if errors:
        print("\n".join(errors))
        return 1
    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            if not check(mutate(m)):
                print(f"FAIL: mutation not rejected: {m}")
                return 1
        print(f"PASS: {len(MUTATIONS)} mutations rejected")
    print("portal cert renew rbac ok")
    return 0


sys.exit(main())
