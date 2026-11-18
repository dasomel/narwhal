#!/usr/bin/env python3
"""R358: Lane 5 Security & Istio hardening invariants across falco, trivy-operator, kyverno, and istiod waypoint."""

import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FALCO_APP = ROOT / "gitops/charts/narwhal-apps/templates/falco.yaml"
TRIVY_APP = ROOT / "gitops/charts/narwhal-apps/templates/trivy-operator.yaml"
KYVERNO_APP = ROOT / "gitops/charts/narwhal-apps/templates/kyverno.yaml"
KYVERNO_SCRIPT = ROOT / "scripts/cluster/08-3-security.sh"
ISTIOD_APP = ROOT / "gitops/charts/narwhal-apps/templates/istiod.yaml"
ISTIO_SCRIPT = ROOT / "scripts/cluster/09-istio-ambient.sh"
KYVERNO_POL = ROOT / "gitops/resources/kyverno-policies.yaml"

HELM_COMMENT_RE = re.compile(r"\A\{\{-\s*/\*.*?\*/\s*-?\}\}\s*", re.S)


def _load_app_values(text: str, name: str) -> dict:
    body = HELM_COMMENT_RE.sub("", text, count=1)
    doc = yaml.safe_load(body)
    assert isinstance(doc, dict) and doc.get("kind") == "Application", f"{name}: expected Application"
    vals = ((doc.get("spec") or {}).get("source") or {}).get("helm", {}).get("valuesObject")
    assert isinstance(vals, dict), f"{name}: missing spec.source.helm.valuesObject"
    return vals


def _extract_heredoc(script_text: str, delimiter_name: str) -> dict:
    match = re.search(rf"cat > {re.escape(delimiter_name)} << 'EOF'\n(.*?)\nEOF", script_text, re.S)
    assert match, f"heredoc {delimiter_name} not found in script"
    vals = yaml.safe_load(match.group(1))
    assert isinstance(vals, dict), f"{delimiter_name}: expected dict from heredoc"
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

    # 3. Kyverno 4 controllers limits.cpu + webhook self-lockout exclusion (GitOps + Bootstrap script)
    kyv_vals = _load_app_values(texts["kyverno"], "kyverno.yaml")
    kyv_boot_vals = _extract_heredoc(texts["kyverno_script"], "/tmp/kyverno-values.yaml")

    for label, v in [("gitops", kyv_vals), ("bootstrap", kyv_boot_vals)]:
        adm_cpu = (((v.get("admissionController") or {}).get("container") or {}).get("resources") or {}).get("limits", {}).get("cpu")
        if adm_cpu != "500m":
            errs.append(f"kyverno ({label}): admissionController.container.resources.limits.cpu must be 500m, got {adm_cpu!r}")
        for ctrl, expected in [("backgroundController", "300m"), ("cleanupController", "200m"), ("reportsController", "300m")]:
            c_cpu = ((v.get(ctrl) or {}).get("resources") or {}).get("limits", {}).get("cpu")
            if c_cpu != expected:
                errs.append(f"kyverno ({label}): {ctrl}.resources.limits.cpu must be {expected}, got {c_cpu!r}")

        exprs = (((v.get("config") or {}).get("webhooks") or {}).get("namespaceSelector") or {}).get("matchExpressions") or []
        excluded_ns = set()
        for expr in exprs:
            if expr.get("key") == "kubernetes.io/metadata.name" and expr.get("operator") == "NotIn":
                excluded_ns.update(expr.get("values") or [])
        for req_ns in ("kube-system", "istio-system", "platform-system"):
            if req_ns not in excluded_ns:
                errs.append(f"kyverno ({label}): webhook namespaceSelector must keep excluding {req_ns}")

    # 4. Istiod waypoint seccompProfile (GitOps + Bootstrap script)
    istio_vals = _load_app_values(texts["istiod"], "istiod.yaml")
    istio_boot_vals = _extract_heredoc(texts["istio_script"], "/tmp/istiod-values.yaml")

    for label, v in [("gitops", istio_vals), ("bootstrap", istio_boot_vals)]:
        gw_seccomp = ((v.get("gateways") or {}).get("seccompProfile") or {}).get("type")
        if gw_seccomp != "RuntimeDefault":
            errs.append(f"istiod ({label}): gateways.seccompProfile.type must be RuntimeDefault, got {gw_seccomp!r}")

    # 5. Kyverno policies: add-waypoint-seccomp must NOT be present
    pols = [d for d in yaml.safe_load_all(texts["policies"]) if isinstance(d, dict) and d.get("kind") == "ClusterPolicy"]
    wp_pols = [p for p in pols if (p.get("metadata") or {}).get("name") == "add-waypoint-seccomp"]
    if wp_pols:
        errs.append(f"kyverno-policies.yaml: add-waypoint-seccomp ClusterPolicy must be removed, found {len(wp_pols)}")

    return errs


def main() -> int:
    texts = {
        "falco": FALCO_APP.read_text(),
        "trivy": TRIVY_APP.read_text(),
        "kyverno": KYVERNO_APP.read_text(),
        "kyverno_script": KYVERNO_SCRIPT.read_text(),
        "istiod": ISTIOD_APP.read_text(),
        "istio_script": ISTIO_SCRIPT.read_text(),
        "policies": KYVERNO_POL.read_text(),
    }
    errs = check_texts(texts)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        mutations = [
            (
                "falco",
                "          securityContext:\n            allowPrivilegeEscalation: false\n            readOnlyRootFilesystem: true",
                "          securityContext:\n            allowPrivilegeEscalation: false\n            readOnlyRootFilesystem: false",
            ),
            ("falco", "runAsNonRoot: true", "runAsNonRoot: false"),
            ("trivy", "        podSecurityContext:\n          runAsNonRoot: true", "        podSecurityContext:\n          runAsNonRoot: false"),
            ("trivy", "          limits:\n            cpu: 500m\n            memory: 1Gi", "          limits:\n            memory: 1Gi"),
            ("trivy", "            securityContext:\n              allowPrivilegeEscalation: false", "            securityContext:\n              allowPrivilegeEscalation: true"),
            ("kyverno", "            resources:\n              limits:\n                cpu: 500m", "            resources:\n              limits: {}"),
            ("kyverno", "        reportsController:\n          replicas: 2\n          resources:\n            limits:\n              cpu: 300m", "        reportsController:\n          replicas: 2"),
            ("kyverno", "                    - platform-system", "                    # - platform-system"),
            ("kyverno_script", "    resources:\n      limits:\n        cpu: 500m", "    resources:\n      limits:\n        cpu: 100m"),
            ("kyverno_script", "cleanupController:\n  replicas: 1\n  resources:\n    limits:\n      cpu: 200m", "cleanupController:\n  replicas: 1"),
            ("kyverno_script", "            - platform-system", "            # - platform-system"),
            ("istiod", "        gateways:\n          seccompProfile:\n            type: RuntimeDefault", "        gateways:\n          seccompProfile:\n            type: Unconfined"),
            ("istio_script", "gateways:\n  seccompProfile:\n    type: RuntimeDefault", "gateways:\n  seccompProfile:\n    type: Unconfined"),
            ("policies", "  name: tuning-job-control-plane-node-guard", "  name: add-waypoint-seccomp\n---\napiVersion: kyverno.io/v1\nkind: ClusterPolicy\nmetadata:\n  name: tuning-job-control-plane-node-guard"),
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
        print(f"ok: Lane 5 falco/trivy/kyverno/istiod security hardening & bootstrap parity verified; {caught} mutations caught")
    else:
        print("ok: Lane 5 falco/trivy/kyverno/istiod security hardening & bootstrap parity verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
