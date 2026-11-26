#!/usr/bin/env python3
"""R420: ArgoCD privilege split Stage 1 (D-OWN-ARGO-STAGED).

Verifies:
1. gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml (single source applied
   verbatim by scripts/cluster/13-argocd.sh and rendered by helm template narwhal-apps):
   - No Helm template directives (must stay plain YAML for bootstrap kubectl apply).
   - Zero cluster-wide secrets access (KSV041), zero '*' in apiGroups/resources/verbs
     (KSV044/KSV045/KSV046), zero nodes/proxy or pods/exec/attach, and zero write verbs
     outside argoproj.io {applications, applications/finalizers, applicationsets} (KSV048).
   - Preserves required portal + ArgoCD UI reads (non-secret core/apps/batch/autoscaling/
     networking/policy/rbac/storage/apiextensions + explicit CRD plural names), pods/log get,
     and argoproj.io get/list/watch + delete/patch.
2. gitops/resources/argocd-projects.yaml:
   - AppProject/platform clusterResourceWhitelist equals the exact 12-kind design inventory
     (no '*', no StorageClass, no CSIDriver).
   - AppProject/tenants clusterResourceWhitelist stays empty ([]).
3. portal/src/data/compliance-acceptance-rules.json, portal/src/lib/i18n.ts, and
   docs/common/compliance-hardening.md:
   - ClusterRole/argocd-server is removed from acceptances (KSV041/KSV046/KSV048 fixed).
   - ClusterRole/argocd-application-controller accepts exact checks KSV044/KSV046 with
     D-OWN-ARGO-STAGED bilingual reason and matching docs table row.
4. scripts/test/regression-check-kakao.sh registers R420 with --mutation-verify.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "server_rbac": ROOT / "gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml",
    "bootstrap": ROOT / "scripts/cluster/13-argocd.sh",
    "projects": ROOT / "gitops/resources/argocd-projects.yaml",
    "rules": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

WRITE_VERBS = {
    "create",
    "update",
    "patch",
    "delete",
    "deletecollection",
    "escalate",
    "bind",
    "impersonate",
    "*",
}

FORBIDDEN_RESOURCES = {
    "secrets",
    "nodes/proxy",
    "nodes/*",
    "pods/exec",
    "pods/attach",
}

REQUIRED_READ_GROUPS = {
    "": {
        "configmaps",
        "endpoints",
        "events",
        "limitranges",
        "namespaces",
        "nodes",
        "persistentvolumeclaims",
        "persistentvolumes",
        "pods",
        "resourcequotas",
        "serviceaccounts",
        "services",
    },
    "apps": {"controllerrevisions", "daemonsets", "deployments", "replicasets", "statefulsets"},
    "batch": {"cronjobs", "jobs"},
    "autoscaling": {"horizontalpodautoscalers"},
    "networking.k8s.io": {"ingressclasses", "ingresses", "networkpolicies"},
    "policy": {"poddisruptionbudgets"},
    "rbac.authorization.k8s.io": {"clusterrolebindings", "clusterroles", "rolebindings", "roles"},
    "storage.k8s.io": {"csidrivers", "csinodes", "csistoragecapacities", "storageclasses", "volumeattachments"},
    "apiextensions.k8s.io": {"customresourcedefinitions"},
    "argoproj.io": {"applications", "applications/finalizers", "applicationsets", "appprojects"},
    "apisix.apache.org": {"apisixroutes", "apisixtlses", "apisixupstreams"},
    "aquasecurity.github.io": {"clustercompliancereports", "configauditreports", "vulnerabilityreports"},
    "cert-manager.io": {"certificates", "clusterissuers", "issuers"},
    "cilium.io": {"ciliumclusterwidenetworkpolicies", "ciliumnetworkpolicies"},
    "k8s.keycloak.org": {"keycloaks"},
    "kyverno.io": {"clusterpolicies", "policies"},
    "metallb.io": {"ipaddresspools", "l2advertisements"},
    "monitoring.coreos.com": {"alertmanagerconfigs", "podmonitors", "prometheusrules", "servicemonitors"},
    "postgresql.cnpg.io": {"clusters", "scheduledbackups"},
    "security.istio.io": {"peerauthentications"},
}

EXPECTED_PLATFORM_CLUSTER_WHITELIST = {
    ("", "Namespace"),
    ("admissionregistration.k8s.io", "MutatingWebhookConfiguration"),
    ("admissionregistration.k8s.io", "ValidatingWebhookConfiguration"),
    ("apiextensions.k8s.io", "CustomResourceDefinition"),
    ("aquasecurity.github.io", "ClusterComplianceReport"),
    ("cert-manager.io", "ClusterIssuer"),
    ("cilium.io", "CiliumClusterwideNetworkPolicy"),
    ("kyverno.io", "ClusterPolicy"),
    ("networking.k8s.io", "IngressClass"),
    ("rbac.authorization.k8s.io", "ClusterRole"),
    ("rbac.authorization.k8s.io", "ClusterRoleBinding"),
    ("scheduling.k8s.io", "PriorityClass"),
}

ALLOWED_ARGOPROJ_WRITE_RESOURCES = {"applications", "applications/finalizers", "applicationsets"}
ALLOWED_ARGOPROJ_WRITE_VERBS = {"delete", "patch"}


def check_server_role(role: dict, label: str, errors: list[str]) -> None:
    if role.get("kind") != "ClusterRole" or (role.get("metadata") or {}).get("name") != "argocd-server":
        errors.append(f"{label}: expected ClusterRole/argocd-server")
        return

    rules = role.get("rules") or []
    if not rules:
        errors.append(f"{label}: rules must not be empty")
        return

    read_coverage: dict[str, set[str]] = {}
    pods_log_get = False
    argoproj_write: set[tuple[str, str]] = set()

    for idx, rule in enumerate(rules):
        groups = rule.get("apiGroups") or []
        resources = rule.get("resources") or []
        verbs = set(rule.get("verbs") or [])

        if "*" in groups:
            errors.append(f"{label}: rule[{idx}] must not use wildcard apiGroup '*'")
        if any(r == "*" or r.endswith("/*") for r in resources):
            errors.append(f"{label}: rule[{idx}] must not use wildcard resource {resources!r}")
        if "*" in verbs:
            errors.append(f"{label}: rule[{idx}] must not use wildcard verb '*'")
        if "grafana.integreatly.org" in groups:
            errors.append(f"{label}: rule[{idx}] must not reference uninstalled group grafana.integreatly.org")

        for res in resources:
            if res in FORBIDDEN_RESOURCES:
                errors.append(f"{label}: rule[{idx}] grants forbidden resource {res!r}")

        write_verbs = verbs & WRITE_VERBS
        if write_verbs:
            if groups != ["argoproj.io"]:
                errors.append(
                    f"{label}: rule[{idx}] grants write verbs {sorted(write_verbs)} outside argoproj.io (groups={groups!r})"
                )
            elif not set(resources) <= ALLOWED_ARGOPROJ_WRITE_RESOURCES or not write_verbs <= ALLOWED_ARGOPROJ_WRITE_VERBS:
                errors.append(
                    f"{label}: rule[{idx}] argoproj.io write rule must stay within "
                    f"{sorted(ALLOWED_ARGOPROJ_WRITE_RESOURCES)} / {sorted(ALLOWED_ARGOPROJ_WRITE_VERBS)}"
                )
            else:
                for res in resources:
                    for v in write_verbs:
                        argoproj_write.add((res, v))

        if {"get", "list", "watch"} <= verbs:
            for g in groups:
                read_coverage.setdefault(g, set()).update(resources)
        if "" in groups and "pods/log" in resources and "get" in verbs:
            pods_log_get = True

    if not pods_log_get:
        errors.append(f"{label}: missing get on core pods/log")
    for res in ALLOWED_ARGOPROJ_WRITE_RESOURCES:
        for v in ALLOWED_ARGOPROJ_WRITE_VERBS:
            if (res, v) not in argoproj_write:
                errors.append(f"{label}: missing argoproj.io {v} on {res}")
    for group, required in REQUIRED_READ_GROUPS.items():
        missing = sorted(required - read_coverage.get(group, set()))
        if missing:
            errors.append(f"{label}: group {group!r} missing get/list/watch on {missing}")


def check(texts: dict[str, str], check_helm: bool = False) -> list[str]:
    errors: list[str] = []

    raw_rbac = texts["server_rbac"]
    if "{{" in raw_rbac or "}}" in raw_rbac:
        errors.append("argocd-server-rbac.yaml must stay plain YAML (13-argocd.sh applies it directly without helm)")
    if "D-ARGO-SERVER-RO" not in raw_rbac:
        errors.append("argocd-server-rbac.yaml missing D-ARGO-SERVER-RO decision comment")

    try:
        rbac_docs = [d for d in yaml.safe_load_all(raw_rbac) if d]
    except yaml.YAMLError as exc:
        errors.append(f"argocd-server-rbac.yaml YAML parse error: {exc}")
        rbac_docs = []

    if len(rbac_docs) != 1:
        errors.append(f"argocd-server-rbac.yaml must contain exactly 1 document (got {len(rbac_docs)})")
    else:
        check_server_role(rbac_docs[0], "argocd-server-rbac.yaml", errors)

    if check_helm:
        rendered = subprocess.run(
            ["helm", "template", "narwhal-apps", "gitops/charts/narwhal-apps"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        helm_roles = [
            d
            for d in yaml.safe_load_all(rendered)
            if isinstance(d, dict)
            and d.get("kind") == "ClusterRole"
            and (d.get("metadata") or {}).get("name") == "argocd-server"
        ]
        if len(helm_roles) != 1:
            errors.append(f"helm template narwhal-apps rendered {len(helm_roles)} argocd-server ClusterRoles")
        else:
            check_server_role(helm_roles[0], "helm-rendered argocd-server", errors)
            if rbac_docs and helm_roles[0] != rbac_docs[0]:
                errors.append("helm-rendered argocd-server ClusterRole drifted from raw argocd-server-rbac.yaml")

    # Bootstrap parity: 13-argocd.sh must apply the exact same argocd-server-rbac.yaml and argocd-projects.yaml.
    boot = texts["bootstrap"]
    if "-f /home/vagrant/configs/gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml" not in boot:
        errors.append("13-argocd.sh must apply gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml")
    if "-f /home/vagrant/configs/gitops/resources/argocd-projects.yaml" not in boot:
        errors.append("13-argocd.sh must apply gitops/resources/argocd-projects.yaml")

    # AppProject clusterResourceWhitelist
    try:
        proj_docs = [d for d in yaml.safe_load_all(texts["projects"]) if d]
    except yaml.YAMLError as exc:
        errors.append(f"argocd-projects.yaml YAML parse error: {exc}")
        proj_docs = []

    projects = {
        (d.get("metadata") or {}).get("name"): d.get("spec") or {}
        for d in proj_docs
        if d.get("kind") == "AppProject"
    }
    if "platform" not in projects:
        errors.append("argocd-projects.yaml missing AppProject/platform")
    else:
        wl = projects["platform"].get("clusterResourceWhitelist")
        if not isinstance(wl, list):
            errors.append("AppProject/platform clusterResourceWhitelist must be an explicit list")
        else:
            pairs = [(item.get("group"), item.get("kind")) for item in wl if isinstance(item, dict)]
            if len(pairs) != len(set(pairs)):
                errors.append("AppProject/platform clusterResourceWhitelist has duplicate entries")
            pair_set = set(pairs)
            if pair_set != EXPECTED_PLATFORM_CLUSTER_WHITELIST:
                missing = sorted(EXPECTED_PLATFORM_CLUSTER_WHITELIST - pair_set)
                extra = sorted(pair_set - EXPECTED_PLATFORM_CLUSTER_WHITELIST)
                errors.append(f"AppProject/platform clusterResourceWhitelist mismatch: missing={missing}, extra={extra}")

    if "tenants" not in projects:
        errors.append("argocd-projects.yaml missing AppProject/tenants")
    else:
        twl = projects["tenants"].get("clusterResourceWhitelist")
        if twl != []:
            errors.append(f"AppProject/tenants clusterResourceWhitelist must stay empty ([]), got {twl!r}")

    # Acceptance rules + i18n + compliance-hardening.md
    data = json.loads(texts["rules"])
    rbac_rules = data.get("rbac") or []
    server_rules = [r for r in rbac_rules if r.get("kind") == "ClusterRole" and r.get("name") == "argocd-server"]
    if server_rules:
        errors.append("compliance-acceptance-rules.json must not retain ClusterRole/argocd-server acceptance (KSV041/046/048 fixed)")

    ctrl_rules = [
        r for r in rbac_rules if r.get("kind") == "ClusterRole" and r.get("name") == "argocd-application-controller"
    ]
    if len(ctrl_rules) != 1:
        errors.append(f"compliance-acceptance-rules.json must hold exactly 1 ClusterRole/argocd-application-controller rule (got {len(ctrl_rules)})")
    else:
        rule = ctrl_rules[0]
        if set(rule.get("checks") or []) != {"KSV044", "KSV046"}:
            errors.append(f"ClusterRole/argocd-application-controller checks must be exactly KSV044/KSV046, got {rule.get('checks')!r}")
        if rule.get("scope") != "cluster" or rule.get("namespace") != "" or rule.get("class") != "accept":
            errors.append("ClusterRole/argocd-application-controller must be cluster-scoped accept rule")
        reason_key = rule.get("reasonKey", "")
        if reason_key != "compliance.acceptedReason.rbacArgocdApplicationController":
            errors.append(f"unexpected reasonKey for argocd-application-controller: {reason_key!r}")
        matches = re.findall(r'"' + re.escape(reason_key) + r'": "([^"\n]+)"', texts["i18n"])
        if len(matches) != 2:
            errors.append(f"i18n.ts must define ko and en translations for {reason_key}")
        else:
            en_reason = matches[1]
            for token in ("D-OWN-ARGO-STAGED", "KSV050", "option C"):
                if token not in en_reason:
                    errors.append(f"English i18n reason for argocd-application-controller missing {token!r}")
            want_row = f"| cluster | ClusterRole/argocd-application-controller | KSV044, KSV046 | accept | {en_reason} |"
            if want_row not in texts["doc"]:
                errors.append("docs/common/compliance-hardening.md missing matching argocd-application-controller RBAC table row")

    if "| cluster | ClusterRole/argocd-server |" in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md must not retain ClusterRole/argocd-server acceptance row")
    if "D-OWN-ARGO-STAGED" not in texts["doc"] or "D-ARGO-SERVER-RO" not in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md missing Stage 1 ArgoCD privilege split section")

    if "check R420 " not in texts["reg"] or "check-argocd-privilege-split-stage1.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must register R420 with --mutation-verify")

    return errors


def build_mutations(texts: dict[str, str]) -> list[tuple[str, str, str]]:
    muts: list[tuple[str, str, str]] = []

    def add_replace(label: str, key: str, old: str, new: str) -> None:
        if old not in texts[key]:
            raise RuntimeError(f"mutation anchor missing for {label!r}")
        muts.append((label, key, texts[key].replace(old, new, 1)))

    add_replace("restore core secrets read", "server_rbac", "      - resourcequotas\n", "      - resourcequotas\n      - secrets\n")
    add_replace("restore CRD wildcard resource", "server_rbac", "    resources: [challenges, orders]", '    resources: ["*"]')
    add_replace("restore workload delete/patch", "server_rbac", "  - apiGroups: [argoproj.io]\n    resources: [applications, applications/finalizers, applicationsets]\n    verbs: [delete, patch]", "  - apiGroups: [argoproj.io]\n    resources: [applications, applications/finalizers, applicationsets]\n    verbs: [delete, patch]\n  - apiGroups: [apps]\n    resources: [deployments]\n    verbs: [delete, patch]")
    add_replace("restore pods delete", "server_rbac", "  - apiGroups: [\"\"]\n    resources: [pods/log]\n    verbs: [get]", "  - apiGroups: [\"\"]\n    resources: [pods/log]\n    verbs: [get]\n  - apiGroups: [\"\"]\n    resources: [pods]\n    verbs: [delete]")
    add_replace("restore jobs create", "server_rbac", "  - apiGroups: [\"\"]\n    resources: [pods/log]\n    verbs: [get]", "  - apiGroups: [\"\"]\n    resources: [pods/log]\n    verbs: [get]\n  - apiGroups: [batch]\n    resources: [jobs]\n    verbs: [create]")
    add_replace("drop pods/log get", "server_rbac", "  - apiGroups: [\"\"]\n    resources: [pods/log]\n    verbs: [get]\n", "")
    add_replace("drop argoproj.io patch/delete", "server_rbac", "    verbs: [delete, patch]", "    verbs: [get]")
    add_replace("inject Helm template syntax into server rbac", "server_rbac", "  name: argocd-server", "  name: {{ .Values.argocdServer | default \"argocd-server\" }}")
    add_replace("drop bootstrap server-rbac apply", "bootstrap", "-f /home/vagrant/configs/gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml", "-f /dev/null")
    add_replace("restore platform clusterResourceWhitelist wildcard", "projects", "    - group: \"\"\n      kind: Namespace", "    - group: \"*\"\n      kind: \"*\"")
    add_replace("add StorageClass to platform clusterResourceWhitelist", "projects", "    - group: scheduling.k8s.io\n      kind: PriorityClass", "    - group: scheduling.k8s.io\n      kind: PriorityClass\n    - group: storage.k8s.io\n      kind: StorageClass")
    add_replace("widen tenants clusterResourceWhitelist", "projects", "  clusterResourceWhitelist: []", "  clusterResourceWhitelist:\n    - group: \"\"\n      kind: Namespace")

    data = json.loads(texts["rules"])
    reaccept = copy.deepcopy(data)
    reaccept["rbac"].append({
        "id": "rbac:cluster:ClusterRole:argocd-server:accept",
        "scope": "cluster",
        "namespace": "",
        "kind": "ClusterRole",
        "name": "argocd-server",
        "checks": ["KSV041"],
        "class": "accept",
        "reasonKey": "compliance.acceptedReason.rbacArgocdApplicationController",
        "owner": "narwhal / argocd v3.4.4 (gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml)",
        "reviewedAt": "2026-11-27",
    })
    muts.append(("re-accept ClusterRole/argocd-server", "rules", json.dumps(reaccept)))

    widen = copy.deepcopy(data)
    next(r for r in widen["rbac"] if r["name"] == "argocd-application-controller")["checks"].append("KSV050")
    muts.append(("widen argocd-application-controller checks with KSV050", "rules", json.dumps(widen)))

    dropped = copy.deepcopy(data)
    dropped["rbac"] = [r for r in dropped["rbac"] if r["name"] != "argocd-application-controller"]
    muts.append(("drop argocd-application-controller acceptance", "rules", json.dumps(dropped)))

    add_replace("drop R420 registration", "reg", "check R420 ", "check R9999 ")
    return muts


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts, check_helm=True)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        muts = build_mutations(texts)
        for label, key, value in muts:
            mutated = dict(texts, **{key: value})
            if not check(mutated, check_helm=False):
                print(f"FAIL: mutation not rejected: {label}", file=sys.stderr)
                return 1
        print(f"PASS: R420 ArgoCD privilege split Stage 1 ({len(muts)} mutations rejected)")
        return 0

    print("PASS: R420 ArgoCD privilege split Stage 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
