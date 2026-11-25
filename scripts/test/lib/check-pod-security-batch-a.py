#!/usr/bin/env python3
"""R405: pod-security Batch A (cert-manager, MetalLB controller, trivy-server, istiod) values invariants.

Each value below was chosen from the image's own identity (crane config / passwd of the pinned image), so the
live UID/GID does not change; see CHANGELOG and the D-PSA-BATCH-A comments in the Application templates.

Verifies, for the GitOps Application (valuesObject) AND the bootstrap install that must stay identical:
1. cert-manager: securityContext, webhook.securityContext and cainjector.securityContext all carry
   runAsUser == runAsGroup == 65532 (bootstrap: matching --set flags in scripts/cluster/08-1-networking.sh).
2. MetalLB: controller.securityContext.runAsGroup == 65534 AND the existing seccompProfile RuntimeDefault is kept
   (bootstrap: --set controller.securityContext.runAsGroup=65534 next to the seccomp --set).
3. trivy-server: trivy.server.podSecurityContext.runAsGroup == 65534 AND the existing seccompProfile RuntimeDefault
   is kept, with the container securityContext (allowPrivilegeEscalation false, drop ALL) untouched. trivy-operator
   has no bootstrap install (GitOps only).
4. istiod: TOP-LEVEL seccompProfile RuntimeDefault (gateways.seccompProfile does not reach the discovery container)
   in the Application and in the 09-istio-ambient.sh values heredoc. istio-cni is deliberately not touched.
5. R405 is wired into scripts/test/regression-check-kakao.sh with --mutation-verify.
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
    "cert-manager": TPL / "cert-manager.yaml",
    "metallb": TPL / "metallb.yaml",
    "trivy": TPL / "trivy-operator.yaml",
    "istiod": TPL / "istiod.yaml",
    "net": ROOT / "scripts/cluster/08-1-networking.sh",
    "istio-boot": ROOT / "scripts/cluster/09-istio-ambient.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

CM_ID = 65532
NOBODY = 65534
RD = {"type": "RuntimeDefault"}


def values_object(text: str) -> dict:
    """Strip Helm template directives (all of them sit on their own lines) and read spec.source.helm.valuesObject."""
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("{{"))
    doc = yaml.safe_load(text) or {}
    return (((doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def dig(obj: object, *path: str) -> object:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def istiod_boot_values(text: str) -> dict:
    m = re.search(r"cat > /tmp/istiod-values\.yaml << 'EOF'\n(.*?)\nEOF\n", text, flags=re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def block(text: str, start: str, end: str) -> str:
    """The install block of one release; apisix and others in the same script use the same flag names."""
    i = text.find(start)
    j = text.find(end, i)
    return text[i:j] if i >= 0 and j > i else ""


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    cm_boot = block(texts["net"], "=== Installing cert-manager ===", "CERT_MANAGER_OK=true")
    mlb_boot = block(texts["net"], "=== Installing MetalLB ===", "METALLB_OK=true")
    cm = values_object(texts["cert-manager"])
    for label, path in (("controller", ("securityContext",)), ("webhook", ("webhook", "securityContext")),
                        ("cainjector", ("cainjector", "securityContext"))):
        sc = dig(cm, *path) or {}
        if sc.get("runAsUser") != CM_ID or sc.get("runAsGroup") != CM_ID:
            errors.append(f"cert-manager.yaml {label} securityContext must set runAsUser/runAsGroup {CM_ID}, got {sc!r}")
    for prefix in ("", "webhook.", "cainjector."):
        for key in ("runAsUser", "runAsGroup"):
            flag = f"--set {prefix}securityContext.{key}={CM_ID} "
            if flag not in cm_boot:
                errors.append(f"08-1-networking.sh cert-manager install missing `{flag.strip()}`")

    mlb = dig(values_object(texts["metallb"]), "controller", "securityContext") or {}
    if mlb.get("runAsGroup") != NOBODY:
        errors.append(f"metallb.yaml controller.securityContext.runAsGroup must be {NOBODY}, got {mlb!r}")
    if mlb.get("seccompProfile") != RD:
        errors.append(f"metallb.yaml controller.securityContext must keep seccompProfile RuntimeDefault, got {mlb!r}")
    if f"--set controller.securityContext.runAsGroup={NOBODY} " not in mlb_boot:
        errors.append("08-1-networking.sh MetalLB install missing --set controller.securityContext.runAsGroup=65534")
    if "--set controller.securityContext.seccompProfile.type=RuntimeDefault" not in mlb_boot:
        errors.append("08-1-networking.sh MetalLB install must keep the controller seccompProfile --set")

    srv = dig(values_object(texts["trivy"]), "trivy", "server") or {}
    psc = srv.get("podSecurityContext") or {}
    if psc.get("runAsGroup") != NOBODY:
        errors.append(f"trivy-operator.yaml trivy.server.podSecurityContext.runAsGroup must be {NOBODY}, got {psc!r}")
    if psc.get("seccompProfile") != RD:
        errors.append(f"trivy-operator.yaml trivy.server.podSecurityContext must keep seccompProfile RuntimeDefault, got {psc!r}")
    if dig(values_object(texts["trivy"]), "operator", "builtInServerRegistryInsecure") is not True:
        errors.append("trivy-operator.yaml operator.builtInServerRegistryInsecure must be true (Harbor serves HTTP only; D-TRIVY-SERVER-INSECURE)")
    csc = srv.get("securityContext") or {}
    if csc.get("allowPrivilegeEscalation") is not False or csc.get("capabilities") != {"drop": ["ALL"]}:
        errors.append(f"trivy-operator.yaml trivy.server.securityContext must keep allowPrivilegeEscalation false + drop ALL, got {csc!r}")

    for label, vals in (("istiod.yaml", values_object(texts["istiod"])),
                        ("09-istio-ambient.sh values", istiod_boot_values(texts["istio-boot"]))):
        if vals.get("seccompProfile") != RD:
            errors.append(f"{label} must set TOP-LEVEL seccompProfile RuntimeDefault (gateways.* does not reach istiod), got {vals.get('seccompProfile')!r}")
        if dig(vals, "gateways", "seccompProfile") != RD:
            errors.append(f"{label} must keep gateways.seccompProfile RuntimeDefault")

    # The heredoc values only matter if the istiod install actually consumes them (review MINOR, 2026-11-26).
    if not re.search(r'helm upgrade --install istiod[^\n]*\\\n(?:[^\n]*\\\n)*?\s*-f /tmp/istiod-values\.yaml', texts["istio-boot"]):
        errors.append("09-istio-ambient.sh istiod install must pass -f /tmp/istiod-values.yaml")

    if "check R405 " not in texts["reg"] or "check-pod-security-batch-a.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R405 with --mutation-verify")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("cert-manager controller runAsUser dropped", "cert-manager", "        securityContext:\n          runAsUser: 65532\n          runAsGroup: 65532\n", ""),
    ("cert-manager webhook runAsGroup dropped", "cert-manager", "              memory: 128Mi\n          securityContext:\n            runAsUser: 65532\n            runAsGroup: 65532\n", "              memory: 128Mi\n          securityContext:\n            runAsUser: 65532\n"),
    ("cert-manager cainjector UID invented", "cert-manager", "              memory: 256Mi\n          securityContext:\n            runAsUser: 65532\n", "              memory: 256Mi\n          securityContext:\n            runAsUser: 12345\n"),
    ("cert-manager bootstrap webhook --set dropped", "net", "    --set webhook.securityContext.runAsGroup=65532 \\\n", ""),
    ("cert-manager bootstrap cainjector --set dropped", "net", "    --set cainjector.securityContext.runAsUser=65532 \\\n", ""),
    ("cert-manager bootstrap controller --set dropped", "net", "    --set securityContext.runAsUser=65532 \\\n    --set securityContext.runAsGroup=65532 \\\n    --set webhook", "    --set webhook"),
    ("metallb runAsGroup dropped", "metallb", "            runAsGroup: 65534\n", ""),
    ("metallb seccomp dropped (map replaced)", "metallb", "            runAsGroup: 65534\n            seccompProfile:\n              type: RuntimeDefault\n", "            runAsGroup: 65534\n"),
    ("metallb bootstrap runAsGroup dropped", "net", "    --set controller.securityContext.runAsGroup=65534 \\\n", ""),
    ("trivy-server runAsGroup dropped", "trivy", "              runAsGroup: 65534\n", ""),
    ("trivy-server seccomp dropped", "trivy", "              runAsGroup: 65534\n              seccompProfile:\n                type: RuntimeDefault\n", "              runAsGroup: 65534\n"),
    ("trivy-server container hardening dropped", "trivy", "              allowPrivilegeEscalation: false\n              capabilities:\n                drop:\n                  - ALL\n            resources:\n              requests:\n                cpu: 100m\n                memory: 512Mi", "            resources:\n              requests:\n                cpu: 100m\n                memory: 512Mi"),
    ("istiod top-level seccomp dropped (gateways only)", "istiod", "        seccompProfile:\n          type: RuntimeDefault\n        gateways:", "        gateways:"),
    ("trivy-server registry insecure dropped", "trivy", "          builtInServerRegistryInsecure: true\n", ""),
    ("istiod gateways seccomp dropped", "istiod", "        gateways:\n          seccompProfile:\n            type: RuntimeDefault\n", "        gateways: {}\n"),
    ("istiod bootstrap gateways seccomp dropped", "istio-boot", "gateways:\n  seccompProfile:\n    type: RuntimeDefault\n", "gateways: {}\n"),
    ("istiod bootstrap values file not consumed", "istio-boot", "  -f /tmp/istiod-values.yaml \\\n", "  -f /tmp/other-values.yaml \\\n"),
    ("istiod bootstrap top-level seccomp dropped", "istio-boot", "seccompProfile:\n  type: RuntimeDefault\ngateways:", "gateways:"),
    ("R405 wiring dropped", "reg", "check R405 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R405 pod-security Batch A ({len(MUTATIONS)} mutations rejected)")
        return 0
    print("PASS: R405 pod-security Batch A")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
