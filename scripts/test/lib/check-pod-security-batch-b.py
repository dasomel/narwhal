#!/usr/bin/env python3
"""R410: pod-security Batch B (OpenBao injector, Headlamp) UID/GID > 10000 invariants (CIS 5.7.3 KSV020/KSV021).

Both workloads are stateless, so the identity moves to 10100 (the same UID as the openbao-unseal Job); the
chart keys were confirmed by `helm template` of the pinned charts (openbao 0.28.3 `injector.uid/gid`, headlamp 0.46.0
`securityContext` + initContainers). Verified for the GitOps Application (valuesObject) AND the bootstrap install:
1. OpenBao: injector.uid == injector.gid == 10100 in openbao.yaml and in the 08-4-storage.sh values heredoc, while the
   server keeps the chart default (no server.uid/gid, no server.statefulSet.securityContext.pod): its Raft PVC is
   owned by 100:1000 and a changed UID would lock it out.
2. Headlamp: the main container securityContext AND the ca-bundle initContainer carry runAsUser == runAsGroup == 10100
   in headlamp.yaml and in the 08-3-security.sh values heredoc, keeping the readOnlyRootFilesystem hardening.
3. Harbor is deliberately NOT changed: chart 1.19.1 hardcodes pod runAsUser 10000 and its only knob,
   containerSecurityContext, is one value shared by core/jobservice/registry/redis (their PVC data is owned by
   10000/999), so harbor.yaml and 08-5-registry.sh must not set containerSecurityContext.runAsUser/runAsGroup.
4. R410 is wired into scripts/test/regression-check-kakao.sh with --mutation-verify.
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
    "openbao": TPL / "openbao.yaml",
    "headlamp": TPL / "headlamp.yaml",
    "harbor": TPL / "harbor.yaml",
    "openbao-boot": ROOT / "scripts/cluster/08-4-storage.sh",
    "headlamp-boot": ROOT / "scripts/cluster/08-3-security.sh",
    "harbor-boot": ROOT / "scripts/cluster/08-5-registry.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

ID = 10100


def values_object(text: str) -> dict:
    """Strip Helm template directives (all of them sit on their own lines) and read spec.source.helm.valuesObject."""
    text = re.sub(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", "", text, flags=re.S)
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("{{"))
    text = text.replace("{{ .Values.baseDomain }}", "example.test")
    doc = yaml.safe_load(text) or {}
    return (((doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def heredoc(text: str, marker: str) -> dict:
    """The Helm values heredoc written to `marker` (quoted or unquoted EOF) in a bootstrap script."""
    m = re.search(re.escape(marker) + r" ?<<\s*'?EOF'?\n(.*?)\nEOF\n", text, flags=re.S)
    return (yaml.safe_load(m.group(1).replace("${DOMAIN}", "example.test")) or {}) if m else {}


def dig(obj: object, *path: str) -> object:
    for key in path:
        obj = obj.get(key) if isinstance(obj, dict) else None
    return obj


def uid_gid_errors(label: str, sc: object) -> list[str]:
    sc = sc if isinstance(sc, dict) else {}
    if sc.get("runAsUser") != ID or sc.get("runAsGroup") != ID:
        return [f"{label} must set runAsUser and runAsGroup {ID}, got {sc!r}"]
    return []


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    bao = {"openbao.yaml": values_object(texts["openbao"]),
           "08-4-storage.sh openbao values": heredoc(texts["openbao-boot"], "cat > /tmp/openbao-values.yaml")}
    for label, vals in bao.items():
        inj = dig(vals, "injector") or {}
        if inj.get("uid") != ID or inj.get("gid") != ID:
            errors.append(f"{label} injector.uid and injector.gid must be {ID}, got {inj.get('uid')!r}/{inj.get('gid')!r}")
        csc = inj.get("securityContext", {}).get("container") if isinstance(inj.get("securityContext"), dict) else None
        if not isinstance(csc, dict) or csc.get("readOnlyRootFilesystem") is not True:
            errors.append(f"{label} injector.securityContext.container must keep readOnlyRootFilesystem true (webhook TLS is in memory)")
        srv = dig(vals, "server") or {}
        if "uid" in srv or "gid" in srv or dig(srv, "statefulSet", "securityContext", "pod"):
            errors.append(f"{label} must not change the OpenBao server pod identity (Raft PVC is owned by 100:1000)")

    hl = {"headlamp.yaml": values_object(texts["headlamp"]),
          "08-3-security.sh headlamp values": heredoc(texts["headlamp-boot"], "cat > /tmp/headlamp-values.yaml")}
    for label, vals in hl.items():
        sc = vals.get("securityContext") or {}
        errors += uid_gid_errors(f"{label} securityContext", sc)
        if sc.get("readOnlyRootFilesystem") is not True:
            errors.append(f"{label} securityContext must keep readOnlyRootFilesystem true (chart adds the headlamp-tmp emptyDir)")
        init = [c for c in (vals.get("initContainers") or []) if isinstance(c, dict) and c.get("name") == "ca-bundle"]
        if not init:
            errors.append(f"{label} ca-bundle initContainer missing")
        else:
            errors += uid_gid_errors(f"{label} ca-bundle securityContext", init[0].get("securityContext"))

    harbor = values_object(texts["harbor"])
    if any(k in (harbor.get("containerSecurityContext") or {}) for k in ("runAsUser", "runAsGroup")):
        errors.append("harbor.yaml must not set containerSecurityContext.runAsUser/runAsGroup (shared by the PVC-owning core/jobservice/registry/redis)")
    if re.search(r"--set(-string)? containerSecurityContext\.runAs(User|Group)=", texts["harbor-boot"]):
        errors.append("08-5-registry.sh must not set containerSecurityContext.runAsUser/runAsGroup (shared by the PVC-owning components)")

    if "check R410 " not in texts["reg"] or "check-pod-security-batch-b.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R410 with --mutation-verify")
    # The uid/gid keys were verified against these chart versions; a bump must re-render (review MINOR, 2026-11-26).
    for key, ver in (("openbao", "0.28.3"), ("headlamp", "0.46.0")):
        if not re.search(rf"^\s*targetRevision:\s*{re.escape(ver)}\s*$", texts[key], re.M):
            errors.append(f"{key}.yaml targetRevision changed from {ver}: re-render and re-verify the uid/gid keys")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("openbao chart bumped without re-verify", "openbao", "targetRevision: 0.28.3", "targetRevision: 0.29.0"),
    ("headlamp chart bumped without re-verify", "headlamp", "targetRevision: 0.46.0", "targetRevision: 0.47.0"),
    ("openbao injector uid dropped", "openbao", "          uid: 10100\n", ""),
    ("openbao injector gid left at chart default", "openbao", "          gid: 10100\n", "          gid: 1000\n"),
    ("openbao server UID moved with it", "openbao", "          updateStrategyType: OnDelete\n", "          updateStrategyType: OnDelete\n          uid: 10100\n"),
    ("openbao injector readOnlyRootFilesystem dropped", "openbao", "              readOnlyRootFilesystem: true\n              capabilities:\n                drop:\n                  - ALL\n        ui:", "              capabilities:\n                drop:\n                  - ALL\n        ui:"),
    ("openbao bootstrap injector uid dropped", "openbao-boot", "  uid: 10100\n", ""),
    ("openbao bootstrap injector gid dropped", "openbao-boot", "  gid: 10100\n", ""),
    ("headlamp main runAsUser dropped", "headlamp", "          runAsUser: 10100\n          runAsGroup: 10100\n          allowPrivilegeEscalation", "          runAsGroup: 10100\n          allowPrivilegeEscalation"),
    ("headlamp main runAsGroup dropped", "headlamp", "          runAsGroup: 10100\n          allowPrivilegeEscalation", "          allowPrivilegeEscalation"),
    ("headlamp ca-bundle runAsGroup dropped", "headlamp", "              runAsUser: 10100\n              runAsGroup: 10100\n", "              runAsUser: 10100\n"),
    ("headlamp ca-bundle left at 100", "headlamp", "              runAsUser: 10100\n", "              runAsUser: 100\n"),
    ("headlamp readOnlyRootFilesystem dropped", "headlamp", "          allowPrivilegeEscalation: false\n          readOnlyRootFilesystem: true\n", "          allowPrivilegeEscalation: false\n"),
    ("headlamp bootstrap main runAsUser dropped", "headlamp-boot", "  runAsUser: 10100\n  runAsGroup: 10100\n  allowPrivilegeEscalation", "  runAsGroup: 10100\n  allowPrivilegeEscalation"),
    ("headlamp bootstrap ca-bundle runAsGroup dropped", "headlamp-boot", "      runAsUser: 10100\n      runAsGroup: 10100\n", "      runAsUser: 10100\n"),
    ("headlamp bootstrap ca-bundle left at 100", "headlamp-boot", "      runAsUser: 10100\n", "      runAsUser: 100\n"),
    ("harbor global container UID set", "harbor", "        existingSecretSecretKey: harbor-shared-secrets\n", "        existingSecretSecretKey: harbor-shared-secrets\n        containerSecurityContext:\n          runAsUser: 10100\n"),
    ("harbor bootstrap global container GID set", "harbor-boot", "    --set trivy.enabled=false \\\n", "    --set trivy.enabled=false \\\n    --set containerSecurityContext.runAsGroup=10100 \\\n"),
    ("R410 wiring dropped", "reg", "check R410 ", "check R9999 "),
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
        print(f"PASS: R410 pod-security Batch B ({len(MUTATIONS)} mutations rejected)")
        return 0
    print("PASS: R410 pod-security Batch B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
