#!/usr/bin/env python3
"""R313: every CNPG Cluster has required hostname pod anti-affinity (D-CNPG-AA-REQUIRED, #564).

Checks gitops/resources/narwhal-db.yaml (all postgresql.cnpg.io Cluster docs under gitops/) and the
Cluster heredoc in scripts/cluster/07-cnpg.sh. --mutation-verify re-runs against broken copies.
"""
import copy
import glob
import re
import sys

import yaml

HOST = "kubernetes.io/hostname"
BOOT = "scripts/cluster/07-cnpg.sh"


def gitops_clusters():
    out = []
    for f in sorted(glob.glob("gitops/**/*.yaml", recursive=True)):
        try:
            docs = list(yaml.safe_load_all(open(f)))
        except yaml.YAMLError:
            continue  # helm templates with directives are not CNPG Clusters
        out += [(f, d) for d in docs if isinstance(d, dict) and d.get("kind") == "Cluster"
                and str(d.get("apiVersion", "")).startswith("postgresql.cnpg.io/")]
    return out


def bootstrap_cluster(text):
    m = re.search(r"<<EOF\n(apiVersion: postgresql\.cnpg\.io/v1\nkind: Cluster\n.*?)\nEOF\n", text, re.S)
    if not m:
        return None
    return yaml.safe_load(re.sub(r"\$\{[A-Z_]+\}", "x", m.group(1)))


def check_one(label, c, errors):
    aff = (c.get("spec") or {}).get("affinity") or {}
    if aff.get("podAntiAffinityType") != "required":
        errors.append(f"{label}: spec.affinity.podAntiAffinityType must be required (got {aff.get('podAntiAffinityType')!r})")
    if aff.get("topologyKey") != HOST:
        errors.append(f"{label}: spec.affinity.topologyKey must be {HOST}")
    if aff.get("enablePodAntiAffinity") is False:
        errors.append(f"{label}: enablePodAntiAffinity must not be false")
    if (c.get("spec") or {}).get("instances", 0) > 3:
        errors.append(f"{label}: instances > 3 workers cannot all be placed with required anti-affinity")


def check(clusters, boot):
    errors = []
    if not clusters:
        errors.append("no CNPG Cluster found under gitops/")
    for f, c in clusters:
        check_one(f"{f}:{c['metadata']['name']}", c, errors)
    if boot is None:
        errors.append(f"{BOOT}: Cluster heredoc not found")
    else:
        check_one(BOOT, boot, errors)
    return errors


def load():
    return gitops_clusters(), bootstrap_cluster(open(BOOT).read())


def mutate(name):
    clusters, boot = load()
    clusters = copy.deepcopy(clusters)
    boot = copy.deepcopy(boot)
    aff = clusters[0][1]["spec"]["affinity"]
    if name == "gitops preferred":
        aff["podAntiAffinityType"] = "preferred"
    elif name == "gitops type removed":
        del aff["podAntiAffinityType"]
    elif name == "gitops topologyKey zone":
        aff["topologyKey"] = "topology.kubernetes.io/zone"
    elif name == "gitops affinity removed":
        del clusters[0][1]["spec"]["affinity"]
    elif name == "bootstrap preferred":
        boot["spec"]["affinity"]["podAntiAffinityType"] = "preferred"
    elif name == "bootstrap affinity removed":
        del boot["spec"]["affinity"]
    elif name == "more instances than workers":
        clusters[0][1]["spec"]["instances"] = 4
    return clusters, boot


MUTATIONS = ["gitops preferred", "gitops type removed", "gitops topologyKey zone", "gitops affinity removed",
             "bootstrap preferred", "bootstrap affinity removed", "more instances than workers"]


def main():
    errors = check(*load())
    if errors:
        print("\n".join(errors))
        return 1
    if "--mutation-verify" in sys.argv:
        for m in MUTATIONS:
            if not check(*mutate(m)):
                print(f"FAIL: mutation not rejected: {m}")
                return 1
        print(f"PASS: {len(MUTATIONS)} mutations rejected")
    print("cnpg required anti-affinity ok")
    return 0


sys.exit(main())
