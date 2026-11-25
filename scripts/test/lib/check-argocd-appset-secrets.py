#!/usr/bin/env python3
"""R409: compare the actual bootstrap YAML render with the upstream documents."""

import argparse
from copy import deepcopy
from pathlib import Path
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = ROOT / "scripts/cluster/13-argocd.sh"
NAME = "argocd-applicationset-controller"
READ_RULE = {"apiGroups": [""], "resources": ["secrets", "configmaps"], "verbs": ["get", "list", "watch"]}


def role(kind, rules, name=NAME):
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": kind,
        "metadata": {"name": name},
        "rules": rules,
    }


def fixture():
    events = {"apiGroups": [""], "resources": ["events"], "verbs": ["create", "get", "list", "patch", "watch"]}
    lease = {
        "apiGroups": ["coordination.k8s.io"],
        "resources": ["leases"],
        "resourceNames": ["58ac56fa.applicationsets.argoproj.io"],
        "verbs": ["get", "update", "create"],
    }
    other_rule = {**deepcopy(READ_RULE), "resourceNames": ["one-config"]}
    return [
        role("Role", [events, READ_RULE, lease]),
        role("ClusterRole", [READ_RULE], name="argocd-server"),
        role("ClusterRole", [events, READ_RULE, lease, other_rule]),
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": NAME}, "data": {"key": "value"}},
    ]


def target_index(docs, kind):
    [index] = [i for i, doc in enumerate(docs) if doc and doc.get("kind") == kind
               and doc.get("metadata", {}).get("name") == NAME]
    return index


def verify(expected, actual):
    if expected != actual:
        raise ValueError("bootstrap render changed more or less than the exact ApplicationSet ClusterRole rule")


def render(manifest, script=BOOTSTRAP):
    output = subprocess.check_output(
        ["bash", str(script), "--render-install", str(manifest)], text=True, timeout=20,
    )
    return list(yaml.safe_load_all(output))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mutation-verify", action="store_true")
    parser.add_argument("--manifest", type=Path, help="also verify a staged upstream install.yaml")
    args = parser.parse_args()
    bootstrap = BOOTSTRAP.read_text()
    if 'render_argocd_install "$argocd_manifest" | kubectl apply -n devtools -f -' not in bootstrap:
        raise SystemExit("bootstrap must apply the filtered multi-document manifest")

    source = fixture()
    expected = deepcopy(source)
    cluster_index = target_index(source, "ClusterRole")
    expected[cluster_index]["rules"].remove(READ_RULE)
    with tempfile.TemporaryDirectory() as directory:
        manifest = Path(directory) / "install.yaml"
        manifest.write_text(yaml.safe_dump_all(source))
        actual = render(manifest)
        verify(expected, actual)
        manifest.write_text(yaml.safe_dump_all(actual))
        verify(expected, render(manifest))

        if args.mutation_verify:
            manifest.write_text(yaml.safe_dump_all(source))
            mutant = Path(directory) / "13-argocd.sh"
            rule_filter = bootstrap[bootstrap.index(".rules[] | select("):bootstrap.index("\n  )'")]
            mutations = [
                ("cluster-wide read restored", '.kind == "ClusterRole"', '.kind == "UnusedRole"'),
                ("namespace read removed", '.kind == "ClusterRole"', '(.kind == "ClusterRole" or .kind == "Role")'),
                ("other cluster rules removed", rule_filter, ".rules[]"),
            ]
            for label, old, new in mutations:
                changed = bootstrap.replace(old, new, 1)
                if changed == bootstrap:
                    raise SystemExit(f"Could not inject mutation: {label}")
                mutant.write_text(changed)
                mutated = render(manifest, mutant)
                try:
                    verify(expected, mutated)
                except ValueError:
                    print(f"Rejected mutation: {label}")
                else:
                    raise SystemExit(f"Missed mutation: {label}")

    if args.manifest:
        expected = list(yaml.safe_load_all(args.manifest.read_text()))
        if READ_RULE not in expected[target_index(expected, "Role")]["rules"]:
            raise SystemExit("staged upstream Role is missing the devtools read rule")
        expected[target_index(expected, "ClusterRole")]["rules"].remove(READ_RULE)
        verify(expected, render(args.manifest))
        print(f"PASS: staged manifest ({len(expected)} documents)")

    print("PASS: R409 exact rule removal, unchanged surrounding documents and idempotent render")


if __name__ == "__main__":
    main()
