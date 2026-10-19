#!/usr/bin/env python3
"""Velero fs-backup exclusions for the root_squash-unreadable volumes and the pods/log grant (D-pvb-exclude, D-pvb-log, #431)."""

import re
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
KEY = "backup.velero.io/backup-volumes-excludes"
ESCAPED_KEY = KEY.replace(".", "\\.")


def load(path):
    return list(yaml.safe_load_all((ROOT / path).read_text()))


def app_values(path):
    return load(path)[0]["spec"]["source"]["helm"]["valuesObject"]


def check():
    problems = []

    def expect(label, actual, volume):
        if actual != volume:
            problems.append(f"{label}: {KEY} must be {volume!r}, got {actual!r}")

    # GitOps-owned: OpenBao (server.annotations), Harbor jobservice, CNPG narwhal-db.
    expect("openbao.yaml server.annotations",
           app_values("gitops/charts/narwhal-apps/templates/openbao.yaml")["server"].get("annotations", {}).get(KEY), "data")
    expect("harbor.yaml jobservice.podAnnotations",
           app_values("gitops/charts/narwhal-apps/templates/harbor.yaml")["jobservice"].get("podAnnotations", {}).get(KEY), "job-logs")
    cluster = next((d for d in load("gitops/resources/narwhal-db.yaml") if d and d.get("kind") == "Cluster"), None)
    if cluster is None:
        problems.append("narwhal-db.yaml: CNPG Cluster missing")
    else:
        expect("narwhal-db.yaml spec.inheritedMetadata.annotations",
               cluster["spec"].get("inheritedMetadata", {}).get("annotations", {}).get(KEY), "pgdata")

    # Script paths install the same values on a clean Phase 2 (before ArgoCD adopts them).
    storage = (ROOT / "scripts/cluster/08-4-storage.sh").read_text()
    if not re.search(rf"^  annotations:\n    {re.escape(KEY)}: data$", storage, re.M):
        problems.append(f"08-4-storage.sh: OpenBao values heredoc lacks server annotations {KEY}: data")
    for script, flag in (("scripts/cluster/08-5-registry.sh", f"jobservice.podAnnotations.{ESCAPED_KEY}=job-logs"),
                         ("scripts/cluster/12-gitea.sh", f"gitea.podAnnotations.{ESCAPED_KEY}=data")):
        if f"--set-string '{flag}'" not in (ROOT / script).read_text():
            problems.append(f"{script}: missing --set-string '{flag}'")

    # pods/log get, namespace storage, velero-server only (generator output, never hand-edited).
    docs = [d for d in load("gitops/resources/velero-server-rbac.yaml") if d]
    role = next((d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == "velero-pod-logs"), None)
    binding = next((d for d in docs if d["kind"] == "RoleBinding" and d["metadata"]["name"] == "velero-pod-logs"), None)
    if role is None or binding is None:
        problems.append("velero-server-rbac.yaml: Role/RoleBinding velero-pod-logs missing")
    else:
        if role["metadata"].get("namespace") != "storage" or binding["metadata"].get("namespace") != "storage":
            problems.append("velero-pod-logs must be namespaced to storage")
        if role["rules"] != [{"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]}]:
            problems.append(f"velero-pod-logs rules must be exactly pods/log get, got {role['rules']}")
        if binding["roleRef"].get("kind") != "Role" or binding["roleRef"].get("name") != "velero-pod-logs":
            problems.append("velero-pod-logs RoleBinding must reference Role velero-pod-logs")
        if binding["subjects"] != [{"kind": "ServiceAccount", "name": "velero-server", "namespace": "storage"}]:
            problems.append(f"velero-pod-logs subjects must be only velero-server/storage, got {binding['subjects']}")
    return problems


if __name__ == "__main__":
    found = check()
    for problem in found:
        print(problem, file=sys.stderr)
    sys.exit(1 if found else 0)
