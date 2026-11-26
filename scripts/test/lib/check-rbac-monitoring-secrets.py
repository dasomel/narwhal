#!/usr/bin/env python3
"""R418: Monitoring RBAC secrets access removal (CIS 5.1.2, Trivy KSV041).

Pins:
1. loki-clusterrole (D-LOKI-RBAC, chart 18.4.0): sidecar.rules.resource=configmap,
   rbac.useExistingRole=loki-clusterrole, extraObjects ClusterRole with only configmaps.
2. prometheus-stack-grafana-clusterrole (D-GRAFANA-RBAC, kube-prometheus-stack 86.2.3):
   grafana.sidecar.{dashboards,datasources}.resource=configmap,
   grafana.rbac.useExistingClusterRole=prometheus-stack-grafana-clusterrole,
   grafana.extraObjects ClusterRole with only configmaps.
3. prometheus-stack-kube-state-metrics (D-KSM-RBAC, kube-prometheus-stack 86.2.3):
   kube-state-metrics.collectors is the 27 default collectors without 'secrets'.
4. Identical in GitOps Applications and scripts/cluster/08-2-monitoring.sh, plus offline
   helm template verification against .review/charts/{loki,kube-prometheus-stack} when present.
"""

from __future__ import annotations

import copy
import glob
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "loki": ROOT / "gitops/charts/narwhal-apps/templates/loki.yaml",
    "prom": ROOT / "gitops/charts/narwhal-apps/templates/prometheus-stack.yaml",
    "boot": ROOT / "scripts/cluster/08-2-monitoring.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

EXPECTED_SIDECAR_RULE = [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "watch", "list"]}]
EXPECTED_KSM_COLLECTORS = [
    "certificatesigningrequests", "configmaps", "cronjobs", "daemonsets", "deployments",
    "endpointslices", "horizontalpodautoscalers", "ingresses", "jobs", "leases", "limitranges",
    "mutatingwebhookconfigurations", "namespaces", "networkpolicies", "nodes",
    "persistentvolumeclaims", "persistentvolumes", "poddisruptionbudgets", "pods", "replicasets",
    "replicationcontrollers", "resourcequotas", "services", "statefulsets", "storageclasses",
    "validatingwebhookconfigurations", "volumeattachments",
]
LOKI_LABELS = {"app.kubernetes.io/name": "loki", "app.kubernetes.io/instance": "loki"}
GRAF_LABELS = {"app.kubernetes.io/name": "grafana", "app.kubernetes.io/instance": "prometheus-stack"}


def values_object(text: str) -> dict[str, Any]:
    doc = yaml.safe_load(re.sub(r"\{\{.*?\}\}", "narwhal.local", text)) or {}
    return (((doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def dig(obj: Any, *path: str) -> Any:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def extract_loki_boot_values(text: str) -> dict[str, Any]:
    m = re.search(r"cat <<'LOKIVALUES' > /tmp/loki-values\.yaml\n(.*?)\nLOKIVALUES\n", text, flags=re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def extract_prom_boot_command(text: str) -> list[str]:
    m = re.search(r"if (helm upgrade --install prometheus-stack.*?); then", text, flags=re.S)
    return shlex.split(m.group(1).replace("\\\n", " "), comments=True) if m else []


def extract_prom_boot_flags(text: str) -> tuple[dict[str, str], dict[str, Any]]:
    tokens = extract_prom_boot_command(text)
    set_f: dict[str, str] = {}
    json_f: dict[str, Any] = {}
    for i, t in enumerate(tokens[:-1]):
        if t == "--set":
            k, _, v = tokens[i + 1].partition("=")
            set_f[k] = v
        elif t == "--set-json":
            k, _, v = tokens[i + 1].partition("=")
            try:
                json_f[k] = json.loads(v)
            except Exception:
                json_f[k] = None
    return set_f, json_f


def check_extra_clusterrole(label: str, objs: Any, name: str, labels: dict[str, str]) -> list[str]:
    if not isinstance(objs, list) or len(objs) != 1 or not isinstance(objs[0], dict):
        return [f"{label} must define extraObjects with 1 ClusterRole ({name})"]
    obj = objs[0]
    meta = obj.get("metadata") or {}
    rules = obj.get("rules")
    errs: list[str] = []
    if obj.get("apiVersion") != "rbac.authorization.k8s.io/v1" or obj.get("kind") != "ClusterRole":
        errs.append(f"{label} extraObjects[0] must be rbac.authorization.k8s.io/v1 ClusterRole")
    if meta.get("name") != name or meta.get("labels") != labels:
        errs.append(f"{label} extraObjects[0] metadata must have name={name!r} and labels={labels!r}")
    if rules != EXPECTED_SIDECAR_RULE:
        errs.append(f"{label} extraObjects[0] rules must be {EXPECTED_SIDECAR_RULE!r}, got {rules!r}")
    return errs


def check_loki_dict(label: str, vals: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    if dig(vals, "sidecar", "rules", "resource") != "configmap":
        errs.append(f"{label} must set sidecar.rules.resource: configmap")
    if not dig(vals, "sidecar", "resources", "requests", "cpu") or not dig(vals, "sidecar", "resources", "limits", "memory"):
        errs.append(f"{label} must preserve sidecar.resources")
    if dig(vals, "rbac", "useExistingRole") != "loki-clusterrole":
        errs.append(f"{label} must set rbac.useExistingRole: loki-clusterrole")
    errs.extend(check_extra_clusterrole(label, vals.get("extraObjects"), "loki-clusterrole", LOKI_LABELS))
    return errs


def check_prom_dict(label: str, vals: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    dash = dig(vals, "grafana", "sidecar", "dashboards") or {}
    ds = dig(vals, "grafana", "sidecar", "datasources") or {}
    if dash.get("enabled") is not True or dash.get("resource") != "configmap" or dash.get("searchNamespace") != "ALL":
        errs.append(f"{label} must set grafana.sidecar.dashboards enabled/resource=configmap/searchNamespace=ALL")
    if ds.get("enabled") is not True or ds.get("resource") != "configmap":
        errs.append(f"{label} must set grafana.sidecar.datasources enabled/resource=configmap")
    if dig(vals, "grafana", "rbac", "useExistingClusterRole") != "prometheus-stack-grafana-clusterrole":
        errs.append(f"{label} must set grafana.rbac.useExistingClusterRole: prometheus-stack-grafana-clusterrole")
    errs.extend(check_extra_clusterrole(f"{label} grafana", dig(vals, "grafana", "extraObjects"),
                                        "prometheus-stack-grafana-clusterrole", GRAF_LABELS))
    ksm = vals.get("kube-state-metrics") or {}
    if not dig(ksm, "resources", "requests", "cpu") or not ksm.get("metricLabelsAllowlist"):
        errs.append(f"{label} must preserve kube-state-metrics.resources and metricLabelsAllowlist")
    if ksm.get("collectors") != EXPECTED_KSM_COLLECTORS:
        errs.append(f"{label} kube-state-metrics.collectors must match the 27 non-secret collectors")
    return errs


def check_texts(texts: dict[str, str]) -> list[str]:
    errs: list[str] = []
    if not re.search(r"^\s*targetRevision:\s*18\.4\.0\s*$", texts["loki"], re.M):
        errs.append("loki.yaml targetRevision changed from 18.4.0")
    if not re.search(r"helm upgrade --install loki[^\n]*\\\n(?:[^\n]*\\\n)*?\s*--version 18\.4\.0", texts["boot"]):
        errs.append("08-2-monitoring.sh loki --version changed from 18.4.0")
    if not re.search(r"helm upgrade --install loki[^\n]*\\\n(?:[^\n]*\\\n)*?\s*-f /tmp/loki-values\.yaml[ \t;]", texts["boot"]):
        errs.append("08-2-monitoring.sh loki install must pass -f /tmp/loki-values.yaml")
    if not re.search(r"^\s*targetRevision:\s*86\.2\.3\s*$", texts["prom"], re.M):
        errs.append("prometheus-stack.yaml targetRevision changed from 86.2.3")
    if not re.search(r"helm upgrade --install prometheus-stack[^\n]*\\\n(?:[^\n]*\\\n)*?\s*--version 86\.2\.3", texts["boot"]):
        errs.append("08-2-monitoring.sh prometheus-stack --version changed from 86.2.3")

    errs.extend(check_loki_dict("loki.yaml", values_object(texts["loki"])))
    errs.extend(check_loki_dict("08-2-monitoring.sh LOKIVALUES", extract_loki_boot_values(texts["boot"])))
    errs.extend(check_prom_dict("prometheus-stack.yaml", values_object(texts["prom"])))

    set_f, json_f = extract_prom_boot_flags(texts["boot"])
    if set_f.get("grafana.sidecar.dashboards.resource") != "configmap":
        errs.append("08-2-monitoring.sh missing --set grafana.sidecar.dashboards.resource=configmap")
    if set_f.get("grafana.sidecar.datasources.resource") != "configmap":
        errs.append("08-2-monitoring.sh missing --set grafana.sidecar.datasources.resource=configmap")
    if set_f.get("grafana.rbac.useExistingClusterRole") != "prometheus-stack-grafana-clusterrole":
        errs.append("08-2-monitoring.sh missing --set grafana.rbac.useExistingClusterRole=prometheus-stack-grafana-clusterrole")
    errs.extend(check_extra_clusterrole("08-2-monitoring.sh grafana", json_f.get("grafana.extraObjects"),
                                        "prometheus-stack-grafana-clusterrole", GRAF_LABELS))
    if json_f.get("kube-state-metrics.collectors") != EXPECTED_KSM_COLLECTORS:
        errs.append("08-2-monitoring.sh kube-state-metrics.collectors must match the 27 non-secret collectors")
    if "check R418 " not in texts["reg"] or "check-rbac-monitoring-secrets.py --mutation-verify" not in texts["reg"]:
        errs.append("scripts/test/regression-check-kakao.sh must wire R418 with --mutation-verify")
    return errs


def find_chart(root: Path, dir_name: str, tgz_name: str) -> Path | None:
    review_dir = root / ".review" / "charts" / dir_name
    if review_dir.is_dir():
        return review_dir
    for base in (root, *root.parents[:3]):
        matches = sorted(glob.glob(str(base / f"narwhal-airgap-bundle-*/charts/{tgz_name}")))
        if matches:
            return Path(matches[0])
    return None


def verify_helm_renders(root: Path, texts: dict[str, str]) -> bool:
    loki_chart = find_chart(root, "loki", "loki-18.4.0.tgz")
    prom_chart = find_chart(root, "kube-prometheus-stack", "kube-prometheus-stack-86.2.3.tgz")
    if not (loki_chart and prom_chart):
        return False

    for label, vals in (("loki.yaml", values_object(texts["loki"])),
                        ("08-2-monitoring.sh loki", extract_loki_boot_values(texts["boot"]))):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            yaml.safe_dump(vals, f)
            f.flush()
            out = subprocess.check_output(["helm", "template", "loki", str(loki_chart), "-n", "monitoring", "-f", f.name], text=True)
        docs = [d for d in yaml.safe_load_all(out) if d]
        crs = [d for d in docs if d.get("kind") == "ClusterRole" and d.get("metadata", {}).get("name") == "loki-clusterrole"]
        assert len(crs) == 1 and crs[0].get("rules") == EXPECTED_SIDECAR_RULE, f"{label} loki-clusterrole render"
        crbs = [d for d in docs if d.get("kind") == "ClusterRoleBinding" and d.get("metadata", {}).get("name") == "loki-clusterrolebinding"]
        assert len(crbs) == 1 and crbs[0].get("roleRef", {}).get("name") == "loki-clusterrole", f"{label} loki-clusterrolebinding"
        sts = next(d for d in docs if d.get("kind") == "StatefulSet" and d.get("metadata", {}).get("name") == "loki")
        sc = next(c for c in sts["spec"]["template"]["spec"]["containers"] if c.get("name") == "loki-sc-rules")
        assert {e["name"]: e.get("value") for e in sc.get("env", [])}.get("RESOURCE") == "configmap", f"{label} loki-sc-rules RESOURCE"

    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
        yaml.safe_dump(values_object(texts["prom"]), f)
        f.flush()
        gitops_out = subprocess.check_output(["helm", "template", "prometheus-stack", str(prom_chart), "-n", "monitoring", "-f", f.name], text=True)

    toks = extract_prom_boot_command(texts["boot"])
    flag_args = [toks[i + k] for i, t in enumerate(toks[:-1]) if t in ("--set", "--set-string", "--set-json") for k in (0, 1)]
    boot_out = subprocess.check_output(["helm", "template", "prometheus-stack", str(prom_chart), "-n", "monitoring", *flag_args], text=True)

    for label, out in (("prometheus-stack.yaml", gitops_out), ("08-2-monitoring.sh prometheus-stack", boot_out)):
        docs = [d for d in yaml.safe_load_all(out) if d]
        gcrs = [d for d in docs if d.get("kind") == "ClusterRole" and d.get("metadata", {}).get("name") == "prometheus-stack-grafana-clusterrole"]
        assert len(gcrs) == 1 and gcrs[0].get("rules") == EXPECTED_SIDECAR_RULE, f"{label} grafana ClusterRole render"
        gcrbs = [d for d in docs if d.get("kind") == "ClusterRoleBinding" and d.get("metadata", {}).get("name") == "prometheus-stack-grafana-clusterrolebinding"]
        assert len(gcrbs) == 1 and gcrbs[0].get("roleRef", {}).get("name") == "prometheus-stack-grafana-clusterrole", f"{label} grafana CRB"
        gdep = next(d for d in docs if d.get("kind") == "Deployment" and d.get("metadata", {}).get("name") == "prometheus-stack-grafana")
        for sc_name in ("grafana-sc-dashboard", "grafana-sc-datasources"):
            sc = next(c for c in gdep["spec"]["template"]["spec"]["containers"] if c.get("name") == sc_name)
            assert {e["name"]: e.get("value") for e in sc.get("env", [])}.get("RESOURCE") == "configmap", f"{label} {sc_name} RESOURCE"
        kcrs = [d for d in docs if d.get("kind") == "ClusterRole" and d.get("metadata", {}).get("name") == "prometheus-stack-kube-state-metrics"]
        assert len(kcrs) == 1 and len(kcrs[0].get("rules") or []) == len(EXPECTED_KSM_COLLECTORS), f"{label} ksm rules count"
        assert all("secrets" not in (r.get("resources") or []) for r in kcrs[0]["rules"]), f"{label} ksm secrets rule present"
        kdep = next(d for d in docs if d.get("kind") == "Deployment" and d.get("metadata", {}).get("name") == "prometheus-stack-kube-state-metrics")
        kc = next(c for c in kdep["spec"]["template"]["spec"]["containers"] if c.get("name") == "kube-state-metrics")
        assert [a for a in (kc.get("args") or []) if a.startswith("--resources=")] == [f"--resources={','.join(EXPECTED_KSM_COLLECTORS)}"]
    return True


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("loki chart version bumped without re-verify", "loki", "targetRevision: 18.4.0", "targetRevision: 18.5.0"),
    ("bootstrap loki version bumped without re-verify", "boot", "--version 18.4.0", "--version 18.5.0"),
    ("gitops loki sidecar.rules.resource reverted to both", "loki", "            resource: configmap\n", "            resource: both\n"),
    ("gitops loki rbac.useExistingRole dropped", "loki", "          useExistingRole: loki-clusterrole\n", "          useExistingRole: \"\"\n"),
    ("gitops loki extraObjects grants secrets", "loki", '                resources: ["configmaps"]\n', '                resources: ["configmaps", "secrets"]\n'),
    ("gitops loki extraObjects dropped", "loki", "        extraObjects:\n", "        unusedExtraObjects:\n"),
    ("bootstrap loki sidecar.rules.resource reverted to both", "boot", "  rules:\n    resource: configmap\n", "  rules:\n    resource: both\n"),
    ("bootstrap loki rbac.useExistingRole dropped", "boot", "rbac:\n  useExistingRole: loki-clusterrole\n", "rbac:\n  useExistingRole: \"\"\n"),
    ("bootstrap loki extraObjects grants secrets", "boot", '        resources: ["configmaps"]\n', '        resources: ["configmaps", "secrets"]\n'),
    ("prometheus-stack chart version bumped without re-diff", "prom", "targetRevision: 86.2.3", "targetRevision: 86.3.0"),
    ("bootstrap prometheus-stack version bumped without re-diff", "boot", "--version 86.2.3", "--version 86.3.0"),
    ("gitops grafana dashboards sidecar resource reverted", "prom", "              searchNamespace: ALL\n              resource: configmap\n", "              searchNamespace: ALL\n              resource: both\n"),
    ("gitops grafana datasources sidecar resource reverted", "prom", "            datasources:\n              enabled: true\n              resource: configmap\n", "            datasources:\n              enabled: true\n"),
    ("gitops grafana useExistingClusterRole dropped", "prom", "            useExistingClusterRole: prometheus-stack-grafana-clusterrole\n", "            useExistingClusterRole: \"\"\n"),
    ("gitops grafana extraObjects grants secrets", "prom", '                  resources: ["configmaps"]\n', '                  resources: ["configmaps", "secrets"]\n'),
    ("gitops ksm secrets collector added back", "prom", "            - resourcequotas\n            - services\n", "            - resourcequotas\n            - secrets\n            - services\n"),
    ("gitops ksm collector dropped", "prom", "            - certificatesigningrequests\n", ""),
    ("bootstrap grafana dashboards resource flag dropped", "boot", "    --set grafana.sidecar.dashboards.resource=configmap \\\n", ""),
    ("bootstrap grafana datasources resource flag dropped", "boot", "    --set grafana.sidecar.datasources.resource=configmap \\\n", ""),
    ("bootstrap grafana useExistingClusterRole flag dropped", "boot", "    --set grafana.rbac.useExistingClusterRole=prometheus-stack-grafana-clusterrole \\\n", ""),
    ("bootstrap grafana extraObjects grants secrets", "boot", '"resources":["configmaps"]', '"resources":["configmaps","secrets"]'),
    ("bootstrap ksm secrets collector added back", "boot", '"resourcequotas","services"', '"resourcequotas","secrets","services"'),
    ("R418 wiring dropped", "reg", "check R418 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check_texts(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    rendered = verify_helm_renders(ROOT, texts)
    render_note = "offline helm renders verified" if rendered else "offline helm renders SKIPPED (no chart bundle)"
    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check_texts(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R418 monitoring RBAC secrets access removed + {render_note} ({len(MUTATIONS)} mutations rejected)")
        return 0
    print(f"PASS: R418 monitoring RBAC secrets access removed + {render_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
