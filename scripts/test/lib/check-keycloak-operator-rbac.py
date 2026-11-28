#!/usr/bin/env python3
"""R427: keep Keycloak operator Secret permissions namespace-scoped (lane KC)."""
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "scripts/cluster/keycloak-operator-rbac.yaml"
BOOTSTRAP = ROOT / "scripts/cluster/11-keycloak.sh"


def parse(text):
    return [obj for obj in yaml.safe_load_all(text) if obj]


def check(text, bootstrap=None):
    errors = []
    docs = parse(text)
    roles = {obj["metadata"]["name"]: obj for obj in docs if obj.get("kind") == "ClusterRole"}
    expected = {
        "keycloakcontroller-cluster-role": 6,
        "keycloakrealmimportcontroller-cluster-role": 2,
    }
    if set(roles) != set(expected):
        errors.append("both pinned upstream ClusterRoles must be overridden")
    for name, rule_count in expected.items():
        role = roles.get(name)
        if role is None:
            continue
        rules = role.get("rules", [])
        if len(rules) != rule_count:
            errors.append(f"{name}: non-Secret upstream rule set changed")
        if any("secrets" in rule.get("resources", []) for rule in rules):
            errors.append(f"{name}: cluster-wide Secret rule remains")
    ns_roles = [o for o in docs if o.get("kind") == "Role" and o["metadata"].get("namespace") == "iam"]
    secret_roles = [r for r in ns_roles if any("secrets" in rule.get("resources", []) for rule in r.get("rules", []))]
    if len(secret_roles) != 1:
        errors.append("RealmImport Secret Role missing or duplicated in iam")
    bindings = [o for o in docs if o.get("kind") == "RoleBinding" and o["metadata"].get("namespace") == "iam"]
    if not any(
        b.get("roleRef", {}).get("kind") == "Role"
        and b.get("roleRef", {}).get("name") == "keycloakrealmimportcontroller-secret-access"
        and {"kind": s.get("kind"), "name": s.get("name"), "namespace": s.get("namespace")} ==
            {"kind": "ServiceAccount", "name": "keycloak-operator", "namespace": "iam"}
        for b in bindings for s in b.get("subjects", [])
    ):
        errors.append("RealmImport Secret RoleBinding must bind the iam operator ServiceAccount")
    if bootstrap is not None:
        up_line = 'kubectl apply -n iam -f "$(manifest keycloak-kubernetes.yml)"'
        narrow_line = 'kubectl apply -n iam -f /home/vagrant/scripts/cluster/keycloak-operator-rbac.yaml'
        if 'kubectl apply -n iam -f "$(manifest keycloak-kubernetes.yml)"' not in bootstrap:
            errors.append("pinned upstream manifest apply missing")
        if 'kubectl apply -n iam -f /home/vagrant/scripts/cluster/keycloak-operator-rbac.yaml' not in bootstrap:
            errors.append("narrowing manifest must apply after upstream manifest")
        if up_line in bootstrap and narrow_line in bootstrap and bootstrap.index('kubectl apply -n iam -f "$(manifest keycloak-kubernetes.yml)"') > bootstrap.index(
            'kubectl apply -n iam -f /home/vagrant/scripts/cluster/keycloak-operator-rbac.yaml'
        ):
            errors.append("narrowing manifest must apply after upstream RBAC")
        if "D-KC-RBAC-NARROW" not in bootstrap:
            errors.append("decision comment missing")
        apply_lines = [l.strip() for l in bootstrap.splitlines() if "kubectl apply" in l and "keycloak-operator-rbac.yaml" in l]
        if apply_lines != ["kubectl apply -n iam -f /home/vagrant/scripts/cluster/keycloak-operator-rbac.yaml"]:
            errors.append("narrowing manifest must be applied once via the absolute /home/vagrant path (Vagrant runs from /tmp/vagrant-shell)")
    return errors


def main():
    manifest = MANIFEST.read_text()
    bootstrap = BOOTSTRAP.read_text()
    errors = check(manifest, bootstrap)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    mutations = []
    for name in ("keycloakcontroller-cluster-role", "keycloakrealmimportcontroller-cluster-role"):
        docs = parse(manifest)
        role = next(o for o in docs if o.get("kind") == "ClusterRole" and o["metadata"]["name"] == name)
        role["rules"].append({"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list", "watch"]})
        mutations.append((f"restore cluster-wide secrets on {name}", yaml.safe_dump_all(docs, sort_keys=False)))
    docs = parse(manifest)
    docs = [o for o in docs if not (o.get("kind") == "RoleBinding" and o["metadata"].get("namespace") == "iam")]
    mutations.append(("drop the iam RoleBinding", yaml.safe_dump_all(docs, sort_keys=False)))
    up = 'kubectl apply -n iam -f "$(manifest keycloak-kubernetes.yml)"'
    narrow = "kubectl apply -n iam -f /home/vagrant/scripts/cluster/keycloak-operator-rbac.yaml"
    boot_mutations = [
        ("narrowing apply removed", bootstrap.replace(narrow, "true")),
        ("narrowing applied before upstream", bootstrap.replace(narrow, "true").replace(up, narrow + "\n" + up)),
        ("relative BASH_SOURCE path", bootstrap.replace(narrow, 'kubectl apply -n iam -f "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/keycloak-operator-rbac.yaml"')),
    ]
    if "--mutation-verify" in sys.argv:
        for label, changed_boot in boot_mutations:
            assert changed_boot != bootstrap, f"mutation target missing: {label}"
            if not check(manifest, changed_boot):
                print(f"FAIL: mutation not rejected: {label}", file=sys.stderr)
                return 1
        for label, changed in mutations:
            if not check(changed):
                print(f"FAIL: mutation not rejected: {label}", file=sys.stderr)
                return 1
        print(f"PASS: R427 Keycloak operator Secret RBAC namespace-scoped ({len(mutations) + len(boot_mutations)} mutations rejected)")
    else:
        print("PASS: R427 Keycloak operator Secret RBAC namespace-scoped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
