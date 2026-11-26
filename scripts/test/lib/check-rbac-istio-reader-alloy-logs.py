#!/usr/bin/env python3
"""R407/R408: RBAC batch P3 + P4 (CIS 5.1.2 KSV041, 5.1.3 KSV046) values invariants.

Usage: check-rbac-istio-reader-alloy-logs.py {istio-reader|alloy-logs} [--mutation-verify]

R407 istio-reader: the istio-reader ClusterRole/ClusterRoleBinding (istiod chart) and istio-reader-service-account
(base chart) exist only for multicluster remote secrets. `global.enableReaderRBAC: false` must be set on BOTH releases
(each chart guards a different object with the same value) in the GitOps Applications AND in
scripts/cluster/09-istio-ambient.sh (istiod values heredoc + `--set` on the istio-base install), and the existing
istiod `global.defaultPodDisruptionBudget.enabled: false` must survive the merge.

R408 alloy-logs: k8s-monitoring 4.2.0 grants the alloy-logs ClusterRole core [configmaps, secrets] get/list/watch
"for remote.kubernetes.*", which this log collector does not use. `collectors.alloy-logs.rbac.rules` must be the 9
upstream rules with Secret access removed (a Helm list replaces the default), identical in the GitOps Application and
the 08-2-monitoring.sh values heredoc, with no rule reaching secrets or a wildcard resource.

--mutation-verify proves each broken variant is rejected.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
TPL = ROOT / "gitops/charts/narwhal-apps/templates"
FILES = {
    "istiod": TPL / "istiod.yaml",
    "base": TPL / "istio-base.yaml",
    "istio-boot": ROOT / "scripts/cluster/09-istio-ambient.sh",
    "kmon": TPL / "k8s-monitoring.yaml",
    "mon-boot": ROOT / "scripts/cluster/08-2-monitoring.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

GWV = ["get", "list", "watch"]
# k8s-monitoring 4.2.0 collectors/upstream/alloy-values.yaml rbac.rules, with the remote.kubernetes.* rule narrowed.
EXPECTED_RULES = [
    {"apiGroups": ["", "discovery.k8s.io", "networking.k8s.io"],
     "resources": ["endpoints", "endpointslices", "ingresses", "pods", "services"], "verbs": GWV},
    {"apiGroups": [""], "resources": ["pods", "pods/log", "namespaces"], "verbs": GWV},
    {"apiGroups": ["monitoring.grafana.com"], "resources": ["podlogs"], "verbs": GWV},
    {"apiGroups": ["monitoring.coreos.com"], "resources": ["prometheusrules"], "verbs": GWV},
    {"apiGroups": ["monitoring.coreos.com"], "resources": ["alertmanagerconfigs"], "verbs": GWV},
    {"apiGroups": ["monitoring.coreos.com"],
     "resources": ["podmonitors", "servicemonitors", "probes", "scrapeconfigs"], "verbs": GWV},
    {"apiGroups": [""], "resources": ["events"], "verbs": GWV},
    {"apiGroups": [""], "resources": ["configmaps"], "verbs": GWV},
    {"apiGroups": ["apps", "extensions"], "resources": ["replicasets"], "verbs": GWV},
]


def values_object(text: str) -> dict:
    """Read spec.source.helm.valuesObject (template directives, if any, sit on their own lines)."""
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("{{"))
    doc = yaml.safe_load(text) or {}
    return (((doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def dig(obj: object, *path: str) -> object:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def heredoc(text: str, start: str, end: str) -> dict:
    m = re.search(re.escape(start) + r"\n(.*?)\n" + re.escape(end) + r"\n", text, flags=re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def istiod_boot_values(text: str) -> dict:
    return heredoc(text, "cat > /tmp/istiod-values.yaml << 'EOF'", "EOF")


def kmon_boot_values(text: str) -> dict:
    return heredoc(text, "cat <<'K8SMONVALUES' > /tmp/k8s-monitoring-values.yaml", "K8SMONVALUES")


def check_istio_reader(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for label, vals in (("istiod.yaml", values_object(texts["istiod"])),
                        ("09-istio-ambient.sh istiod values", istiod_boot_values(texts["istio-boot"]))):
        if dig(vals, "global", "enableReaderRBAC") is not False:
            errors.append(f"{label} must set global.enableReaderRBAC: false (istio-reader ClusterRole/Binding, KSV041/KSV046), "
                          f"got {dig(vals, 'global', 'enableReaderRBAC')!r}")
        if dig(vals, "global", "defaultPodDisruptionBudget", "enabled") is not False:
            errors.append(f"{label} must keep global.defaultPodDisruptionBudget.enabled: false (merge into the existing global map)")
    if dig(values_object(texts["base"]), "global", "enableReaderRBAC") is not False:
        errors.append("istio-base.yaml must set helm.valuesObject.global.enableReaderRBAC: false (istio-reader-service-account)")
    i = texts["istio-boot"].find("helm upgrade --install istio-base")
    j = texts["istio-boot"].find("||", i)
    base_boot = texts["istio-boot"][i:j] if i >= 0 and j > i else ""
    if not re.search(r"--set global\.enableReaderRBAC=false(\s|\\|$)", base_boot):
        errors.append("09-istio-ambient.sh istio-base install must pass --set global.enableReaderRBAC=false")
    if "check R407 " not in texts["reg"] or "check-rbac-istio-reader-alloy-logs.py istio-reader --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R407 with --mutation-verify")
    return errors


def rules_errors(label: str, vals: dict) -> list[str]:
    rules = dig(vals, "collectors", "alloy-logs", "rbac", "rules")
    errors: list[str] = []
    if not isinstance(rules, list):
        return [f"{label} must set collectors.alloy-logs.rbac.rules (the chart default grants cluster-wide Secret read)"]
    for rule in rules:
        res = rule.get("resources") or []
        if "secrets" in res or "*" in res:
            errors.append(f"{label} rbac rule must not reach secrets or wildcard resources: {rule!r}")
    if rules != EXPECTED_RULES:
        errors.append(f"{label} collectors.alloy-logs.rbac.rules must be the 9 k8s-monitoring 4.2.0 rules with only "
                      f"[configmaps] for the remote.kubernetes.* rule (a Helm list replaces the default), got {rules!r}")
    if dig(vals, "collectors", "alloy-logs", "rbac", "clusterRules") is not None:
        errors.append(f"{label} must leave clusterRules at the chart default (nodes, nodes/pods, nodes/metrics, /metrics)")
    return errors


def check_alloy_logs(texts: dict[str, str]) -> list[str]:
    errors = rules_errors("k8s-monitoring.yaml", values_object(texts["kmon"]))
    errors += rules_errors("08-2-monitoring.sh values", kmon_boot_values(texts["mon-boot"]))
    if not re.search(r"helm upgrade --install k8s-monitoring[^\n]*\\\n(?:[^\n]*\\\n)*?\s*-f /tmp/k8s-monitoring-values\.yaml[ \t;]", texts["mon-boot"]):
        errors.append("08-2-monitoring.sh k8s-monitoring install must pass -f /tmp/k8s-monitoring-values.yaml")
    # EXPECTED_RULES is the 4.2.0 upstream list; a chart bump must re-diff it (review MINOR, 2026-11-26).
    if not re.search(r"^\s*targetRevision:\s*4\.2\.0\s*$", texts["kmon"], re.M):
        errors.append("k8s-monitoring.yaml targetRevision changed: re-diff EXPECTED_RULES against the new chart's collectors/upstream/alloy-values.yaml")
    if not re.search(r"helm upgrade --install k8s-monitoring[^\n]*\\\n(?:[^\n]*\\\n)*?\s*--version 4\.2\.0", texts["mon-boot"]):
        errors.append("08-2-monitoring.sh k8s-monitoring --version changed: re-diff EXPECTED_RULES")
    if "check R408 " not in texts["reg"] or "check-rbac-istio-reader-alloy-logs.py alloy-logs --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R408 with --mutation-verify")
    return errors


MUT_READER: list[tuple[str, str, str, str]] = [
    ("istiod reader RBAC re-enabled", "istiod", "          enableReaderRBAC: false\n", "          enableReaderRBAC: true\n"),
    ("istiod reader value dropped", "istiod", "          enableReaderRBAC: false\n", ""),
    ("istiod global map clobbered (PDB flag lost)", "istiod", "          defaultPodDisruptionBudget:\n            enabled: false\n", ""),
    ("istio-base reader value dropped", "base", "        global:\n          enableReaderRBAC: false\n", ""),
    ("istio-base reader RBAC re-enabled", "base", "          enableReaderRBAC: false\n", "          enableReaderRBAC: true\n"),
    ("bootstrap istiod reader RBAC re-enabled", "istio-boot", "  enableReaderRBAC: false\n", "  enableReaderRBAC: true\n"),
    ("bootstrap istiod reader value dropped", "istio-boot", "  enableReaderRBAC: false\n", ""),
    ("bootstrap istiod PDB flag lost", "istio-boot", "  defaultPodDisruptionBudget:\n    enabled: false\n", ""),
    ("bootstrap istio-base --set dropped", "istio-boot", "  --set global.enableReaderRBAC=false \\\n", ""),
    ("bootstrap istio-base --set flipped", "istio-boot", "--set global.enableReaderRBAC=false", "--set global.enableReaderRBAC=true"),
    ("R407 wiring dropped", "reg", "check R407 ", "check R9999 "),
]

MUT_ALLOY: list[tuple[str, str, str, str]] = [
    ("k8s-monitoring chart bumped without re-diff", "kmon", "targetRevision: 4.2.0", "targetRevision: 4.3.0"),
    ("bootstrap k8s-monitoring bumped without re-diff", "mon-boot", "--version 4.2.0", "--version 4.3.0"),
    ("gitops secrets added back", "kmon", '              resources: ["configmaps"]\n', '              resources: ["configmaps", "secrets"]\n'),
    ("gitops wildcard resource", "kmon", '              resources: ["configmaps"]\n', '              resources: ["*"]\n'),
    ("gitops rbac block dropped (chart default returns)", "kmon", "            rbac:\n              rules:\n", "            noRbac:\n              rules:\n"),
    ("gitops events rule dropped", "kmon", '              resources: ["events"]\n', '              resources: ["eventz"]\n'),
    ("gitops replicasets rule dropped", "kmon", '              resources: ["replicasets"]\n', '              resources: ["deployments"]\n'),
    ("gitops pods/log rule dropped", "kmon", '"pods", "pods/log", "namespaces"', '"pods", "namespaces"'),
    ("gitops verb widened", "kmon", 'resources: ["podlogs"]\n                  verbs: ["get", "list", "watch"]\n',
     'resources: ["podlogs"]\n                  verbs: ["get", "list", "watch", "delete"]\n'),
    ("gitops clusterRules overridden", "kmon", "            rbac:\n              rules:\n", "            rbac:\n              clusterRules: []\n              rules:\n"),
    ("bootstrap secrets added back", "mon-boot", '      resources: ["configmaps"]\n', '      resources: ["configmaps", "secrets"]\n'),
    ("bootstrap rbac block dropped", "mon-boot", "    rbac:\n      rules:\n", "    noRbac:\n      rules:\n"),
    ("bootstrap events rule dropped", "mon-boot", '      resources: ["events"]\n', '      resources: ["eventz"]\n'),
    ("bootstrap values file not consumed", "mon-boot", "    -f /tmp/k8s-monitoring-values.yaml; then", "    -f /tmp/other-values.yaml; then"),
    ("R408 wiring dropped", "reg", "check R408 ", "check R9999 "),
]

MODES = {
    "istio-reader": ("R407 istio-reader RBAC off", check_istio_reader, MUT_READER),
    "alloy-logs": ("R408 alloy-logs Secret access removed", check_alloy_logs, MUT_ALLOY),
}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1 or args[0] not in MODES:
        print(f"usage: {sys.argv[0]} {{istio-reader|alloy-logs}} [--mutation-verify]", file=sys.stderr)
        return 2
    title, check, mutations = MODES[args[0]]
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if "--mutation-verify" in sys.argv:
        for name, key, old, new in mutations:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: {title} ({len(mutations)} mutations rejected)")
        return 0
    print(f"PASS: {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
