#!/usr/bin/env python3
"""R358: Lane 5 Security & Kyverno hardening invariants across falco, trivy-operator, kyverno, and waypoint policy."""

import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FALCO_APP = ROOT / "gitops/charts/narwhal-apps/templates/falco.yaml"
TRIVY_APP = ROOT / "gitops/charts/narwhal-apps/templates/trivy-operator.yaml"
KYVERNO_APP = ROOT / "gitops/charts/narwhal-apps/templates/kyverno.yaml"
KYVERNO_POL = ROOT / "gitops/resources/kyverno-policies.yaml"

HELM_COMMENT_RE = re.compile(r"\A\{\{-\s*/\*.*?\*/\s*-?\}\}\s*", re.S)


def _load_app_values(text: str, name: str) -> dict:
    body = HELM_COMMENT_RE.sub("", text, count=1)
    doc = yaml.safe_load(body)
    assert isinstance(doc, dict) and doc.get("kind") == "Application", f"{name}: expected Application"
    vals = ((doc.get("spec") or {}).get("source") or {}).get("helm", {}).get("valuesObject")
    assert isinstance(vals, dict), f"{name}: missing spec.source.helm.valuesObject"
    return vals


def check_texts(texts: dict[str, str]) -> list[str]:
    errs: list[str] = []

    # 1. Falcosidekick securityContext
    falco_vals = _load_app_values(texts["falco"], "falco.yaml")
    sk = falco_vals.get("falcosidekick") or {}
    sk_psc = sk.get("podSecurityContext") or {}
    sk_sc = sk.get("securityContext") or {}
    if sk_psc.get("runAsNonRoot") is not True:
        errs.append("falco.yaml: falcosidekick.podSecurityContext.runAsNonRoot must be true")
    if sk_psc.get("runAsUser") != 1234 or sk_psc.get("runAsGroup") != 1234 or sk_psc.get("fsGroup") != 1234:
        errs.append("falco.yaml: falcosidekick.podSecurityContext must pin uid/gid/fsGroup 1234")
    if (sk_psc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        errs.append("falco.yaml: falcosidekick.podSecurityContext.seccompProfile.type must be RuntimeDefault")
    if sk_sc.get("allowPrivilegeEscalation") is not False:
        errs.append("falco.yaml: falcosidekick.securityContext.allowPrivilegeEscalation must be false")
    if sk_sc.get("readOnlyRootFilesystem") is not True:
        errs.append("falco.yaml: falcosidekick.securityContext.readOnlyRootFilesystem must be true")
    if "ALL" not in ((sk_sc.get("capabilities") or {}).get("drop") or []):
        errs.append("falco.yaml: falcosidekick.securityContext.capabilities.drop must include ALL")

    # 2. Trivy Operator + Trivy Server
    trivy_vals = _load_app_values(texts["trivy"], "trivy-operator.yaml")
    to_psc = trivy_vals.get("podSecurityContext") or {}
    if to_psc.get("runAsNonRoot") is not True:
        errs.append("trivy-operator.yaml: podSecurityContext.runAsNonRoot must be true")
    if (to_psc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        errs.append("trivy-operator.yaml: podSecurityContext.seccompProfile.type must be RuntimeDefault")
    to_limits = (trivy_vals.get("resources") or {}).get("limits") or {}
    if not to_limits.get("cpu"):
        errs.append("trivy-operator.yaml: top-level resources.limits.cpu must be set")

    ts = (trivy_vals.get("trivy") or {}).get("server") or {}
    ts_psc = ts.get("podSecurityContext") or {}
    ts_sc = ts.get("securityContext") or {}
    if (ts_psc.get("seccompProfile") or {}).get("type") != "RuntimeDefault":
        errs.append("trivy-operator.yaml: trivy.server.podSecurityContext.seccompProfile.type must be RuntimeDefault")
    if ts_sc.get("allowPrivilegeEscalation") is not False:
        errs.append("trivy-operator.yaml: trivy.server.securityContext.allowPrivilegeEscalation must be false")
    if "ALL" not in ((ts_sc.get("capabilities") or {}).get("drop") or []):
        errs.append("trivy-operator.yaml: trivy.server.securityContext.capabilities.drop must include ALL")

    # 3. Kyverno 4 controllers limits.cpu + webhook self-lockout exclusion
    kyv_vals = _load_app_values(texts["kyverno"], "kyverno.yaml")
    if not (((kyv_vals.get("admissionController") or {}).get("container") or {}).get("resources") or {}).get("limits", {}).get("cpu"):
        errs.append("kyverno.yaml: admissionController.container.resources.limits.cpu must be set")
    for ctrl in ("backgroundController", "cleanupController", "reportsController"):
        if not ((kyv_vals.get(ctrl) or {}).get("resources") or {}).get("limits", {}).get("cpu"):
            errs.append(f"kyverno.yaml: {ctrl}.resources.limits.cpu must be set")
    exprs = (((kyv_vals.get("config") or {}).get("webhooks") or {}).get("namespaceSelector") or {}).get("matchExpressions") or []
    excluded_ns = set()
    for expr in exprs:
        if expr.get("key") == "kubernetes.io/metadata.name" and expr.get("operator") == "NotIn":
            excluded_ns.update(expr.get("values") or [])
    for req_ns in ("kube-system", "istio-system", "platform-system"):
        if req_ns not in excluded_ns:
            errs.append(f"kyverno.yaml: webhook namespaceSelector must keep excluding {req_ns}")

    # 4. Waypoint seccomp mutate ClusterPolicy
    pols = [d for d in yaml.safe_load_all(texts["policies"]) if isinstance(d, dict) and d.get("kind") == "ClusterPolicy"]
    wp_pols = [p for p in pols if (p.get("metadata") or {}).get("name") == "add-waypoint-seccomp"]
    if len(wp_pols) != 1:
        errs.append(f"kyverno-policies.yaml: expected 1 add-waypoint-seccomp policy, found {len(wp_pols)}")
        return errs
    wp = wp_pols[0]
    spec = wp.get("spec") or {}
    if spec.get("validationFailureAction") != "Audit":
        errs.append("kyverno-policies.yaml: add-waypoint-seccomp must have validationFailureAction: Audit")
    if spec.get("background") is not False:
        errs.append("kyverno-policies.yaml: add-waypoint-seccomp must set background: false")
    rules = spec.get("rules") or []
    if len(rules) != 1:
        errs.append("kyverno-policies.yaml: add-waypoint-seccomp must have exactly 1 rule")
        return errs
    r0 = rules[0]
    match_any = (r0.get("match") or {}).get("any") or []
    res = (match_any[0].get("resources") if match_any else {}) or {}
    if res.get("kinds") != ["Pod"]:
        errs.append("kyverno-policies.yaml: add-waypoint-seccomp must match kinds: [Pod]")
    if set(res.get("namespaces") or []) != {"devtools", "monitoring"}:
        errs.append("kyverno-policies.yaml: add-waypoint-seccomp namespaces must be devtools and monitoring")
    sel = (res.get("selector") or {}).get("matchLabels") or {}
    if sel.get("gateway.networking.k8s.io/gateway-class-name") != "istio-waypoint":
        errs.append("kyverno-policies.yaml: add-waypoint-seccomp must match label gateway.networking.k8s.io/gateway-class-name: istio-waypoint")
    psm_sc = (((r0.get("mutate") or {}).get("patchStrategicMerge") or {}).get("spec") or {}).get("securityContext") or {}
    if (psm_sc.get("+(seccompProfile)") or {}).get("type") != "RuntimeDefault":
        errs.append("kyverno-policies.yaml: add-waypoint-seccomp must mutate +(seccompProfile).type: RuntimeDefault")

    return errs


def main() -> int:
    texts = {
        "falco": FALCO_APP.read_text(),
        "trivy": TRIVY_APP.read_text(),
        "kyverno": KYVERNO_APP.read_text(),
        "policies": KYVERNO_POL.read_text(),
    }
    errs = check_texts(texts)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        mutations = [
            ("falco", "readOnlyRootFilesystem: true", "readOnlyRootFilesystem: false"),
            ("falco", "runAsNonRoot: true", "runAsNonRoot: false"),
            ("trivy", "        podSecurityContext:\n          runAsNonRoot: true", "        podSecurityContext:\n          runAsNonRoot: false"),
            ("trivy", "          limits:\n            cpu: 500m\n            memory: 1Gi", "          limits:\n            memory: 1Gi"),
            ("trivy", "            securityContext:\n              allowPrivilegeEscalation: false", "            securityContext:\n              allowPrivilegeEscalation: true"),
            ("kyverno", "            resources:\n              limits:\n                cpu: 500m", "            resources:\n              limits: {}"),
            ("kyverno", "        reportsController:\n          replicas: 2\n          resources:\n            limits:\n              cpu: 300m", "        reportsController:\n          replicas: 2"),
            ("kyverno", "                    - platform-system", "                    # - platform-system"),
            ("policies", "  name: add-waypoint-seccomp\n  annotations:\n    policies.kyverno.io/title: Add Seccomp Profile to Istio Waypoints\n    policies.kyverno.io/category: Best Practices\n    policies.kyverno.io/severity: medium\n    policies.kyverno.io/subject: Pod\n    policies.kyverno.io/description: >-\n      Mutates Istio-managed waypoint Pods at admission to add a pod-level\n      seccompProfile of type RuntimeDefault when none is set. Targets only\n      Pods carrying gateway.networking.k8s.io/gateway-class-name: istio-waypoint\n      in namespaces reached by Kyverno webhooks (devtools, monitoring);\n      platform-system is excluded at the webhook namespaceSelector level for\n      admission self-lockout safety (kyverno.yaml).\nspec:\n  validationFailureAction: Audit", "  name: add-waypoint-seccomp\n  annotations:\n    policies.kyverno.io/title: Add Seccomp Profile to Istio Waypoints\n    policies.kyverno.io/category: Best Practices\n    policies.kyverno.io/severity: medium\n    policies.kyverno.io/subject: Pod\n    policies.kyverno.io/description: >-\n      Mutates Istio-managed waypoint Pods at admission to add a pod-level\n      seccompProfile of type RuntimeDefault when none is set. Targets only\n      Pods carrying gateway.networking.k8s.io/gateway-class-name: istio-waypoint\n      in namespaces reached by Kyverno webhooks (devtools, monitoring);\n      platform-system is excluded at the webhook namespaceSelector level for\n      admission self-lockout safety (kyverno.yaml).\nspec:\n  validationFailureAction: Enforce"),
            ("policies", "                  gateway.networking.k8s.io/gateway-class-name: istio-waypoint", "                  app: any"),
        ]
        caught = 0
        for key, old, new in mutations:
            assert old in texts[key], f"mutation needle missing in {key}: {old!r}"
            mutated = copy.deepcopy(texts)
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check_texts(mutated):
                print(f"mutation survived ({key}): {old!r}", file=sys.stderr)
                return 1
            caught += 1
        print(f"ok: Lane 5 falco/trivy/kyverno/waypoint security hardening verified; {caught} mutations caught")
    else:
        print("ok: Lane 5 falco/trivy/kyverno/waypoint security hardening verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
