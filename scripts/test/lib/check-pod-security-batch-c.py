#!/usr/bin/env python3
"""R411: Batch C node-agent security contexts and removed acceptances.

1. istio-cni: `cni.seccompProfile.type` is RuntimeDefault in the GitOps Application and in the 09-istio-ambient.sh values
   heredoc (chart cni 1.30.1 renders it on the install-cni container, confirmed by `helm template`). install-cni is not
   privileged (explicit capabilities, measured Seccomp 0), so the profile really applies. Never set it for ztunnel: that
   chart has no seccomp key (the container context is hard-coded) and a silently ignored key would look like a fix.
2. kube-vip: both static Pod manifests (02-init-cluster.sh first master, 02-join-control-plane.sh joining masters) carry the
   pod-level seccompProfile RuntimeDefault and a container context that drops ALL, adds exactly NET_ADMIN and NET_RAW,
   sets allowPrivilegeEscalation false, and keeps UID 0 with a GID above 10000. The container must neither
   be privileged nor override pod seccomp with Unconfined.
3. The exact Batch C acceptances removed after review must stay actionable.
--mutation-verify proves each broken variant is rejected.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "cni": ROOT / "gitops/charts/narwhal-apps/templates/istio-cni.yaml",
    "cni-boot": ROOT / "scripts/cluster/09-istio-ambient.sh",
    "vip-init": ROOT / "scripts/cluster/02-init-cluster.sh",
    "vip-join": ROOT / "scripts/cluster/02-join-control-plane.sh",
    "rules": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "metallb": ROOT / "gitops/charts/narwhal-apps/templates/metallb.yaml",
    "net-boot": ROOT / "scripts/cluster/08-1-networking.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

REMOVED_ACCEPTANCES = {
    ("security-system", "DaemonSet", "falco"): ("KSV004", "KSV030", "KSV104"),
    ("kube-system", "DaemonSet", "cilium"): ("KSV025",),
    ("kube-system", "DaemonSet", "cilium-envoy"): ("KSV021", "KSV025"),
    ("platform-system", "DaemonSet", "metallb-speaker"): ("KSV021",),
    ("kube-system", "Pod", "kube-vip-narwhal-master-1"): ("KSV021",),
    ("kube-system", "DaemonSet", "csi-nfs-node"): ("KSV021",),
}


def rule_target(rule: dict) -> tuple[str, str, str]:
    return rule["namespace"], rule["kind"], rule["name"]


def values_object(text: str) -> dict:
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("{{"))
    doc = yaml.safe_load(text) or {}
    return (((doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def heredoc(text: str, marker: str) -> dict:
    m = re.search(re.escape(marker) + r" ?<<\s*'?EOF'?\n(.*?)\nEOF\n", text, flags=re.S)
    return (yaml.safe_load(m.group(1)) or {}) if m else {}


def vip_pod(text: str) -> dict:
    m = re.search(r"tee /etc/kubernetes/manifests/kube-vip\.yaml\n(.*?)\nEOF\n", text, flags=re.S)
    if not m:
        return {}
    return yaml.safe_load(re.sub(r"\$\{[^}]*\}", "x", m.group(1))) or {}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    if not re.search(r"^\s*targetRevision:\s*1\.30\.1\s*$", texts["cni"], re.M):
        errors.append("istio-cni.yaml targetRevision changed from 1.30.1: re-render cni.seccompProfile and re-verify")
    for label, vals in (("istio-cni.yaml", values_object(texts["cni"])),
                        ("09-istio-ambient.sh istio-cni values", heredoc(texts["cni-boot"], "cat > /tmp/istio-cni-values.yaml"))):
        sp = (vals.get("cni") or {}).get("seccompProfile") if isinstance(vals.get("cni"), dict) else None
        if sp != {"type": "RuntimeDefault"}:
            errors.append(f"{label} cni.seccompProfile must be {{type: RuntimeDefault}}, got {sp!r}")

    for label in ("vip-init", "vip-join"):
        spec = (vip_pod(texts[label]).get("spec") or {})
        if (spec.get("securityContext") or {}).get("seccompProfile") != {"type": "RuntimeDefault"}:
            errors.append(f"{label} kube-vip pod securityContext.seccompProfile must be RuntimeDefault")
        container = next((c for c in spec.get("containers", []) if c.get("name") == "kube-vip"), {})
        sc = container.get("securityContext") or {}
        caps = sc.get("capabilities") or {}
        if caps.get("drop") != ["ALL"]:
            errors.append(f"{label} kube-vip must drop ALL capabilities, got {caps.get('drop')!r}")
        if sorted(caps.get("add") or []) != ["NET_ADMIN", "NET_RAW"]:
            errors.append(f"{label} kube-vip must add exactly NET_ADMIN and NET_RAW, got {caps.get('add')!r}")
        if sc.get("allowPrivilegeEscalation") is not False:
            errors.append(f"{label} kube-vip must set allowPrivilegeEscalation false")
        if sc.get("privileged") is True:
            errors.append(f"{label} kube-vip must not set privileged true")
        if (sc.get("seccompProfile") or {}).get("type") == "Unconfined":
            errors.append(f"{label} kube-vip container must not set seccompProfile Unconfined")
        gid = sc.get("runAsGroup")
        if sc.get("runAsUser") != 0 or type(gid) is not int or gid <= 10000:
            errors.append(f"{label} kube-vip must use UID 0 and GID above 10000")

    # MetalLB speaker: a pod-level runAsGroup needs runAsUser, or containerd cannot build the sandbox user.
    if "metallb" in texts and "net-boot" in texts:
        m = re.search(r"\n        speaker:\n(?:(?!        \S).*\n)*?          securityContext:\n((?:            .*\n)+)", texts["metallb"])
        sc_txt = m.group(1) if m else ""
        if not re.search(r"^\s*runAsUser:\s*0\s*$", sc_txt, re.M) or not re.search(r"^\s*runAsGroup:\s*10100\s*$", sc_txt, re.M):
            errors.append("metallb.yaml speaker.securityContext must set runAsUser 0 together with runAsGroup 10100")
        for flag in ("--set speaker.securityContext.runAsUser=0", "--set speaker.securityContext.runAsGroup=10100"):
            if flag not in texts["net-boot"]:
                errors.append(f"08-1-networking.sh must pass {flag}")
    for rule in json.loads(texts["rules"])["configAudit"]:
        target = rule_target(rule)
        for check_id in REMOVED_ACCEPTANCES.get(target, ()):
            if check_id in rule["checks"]:
                errors.append(f"{'/'.join(target)} must leave {check_id} actionable")

    if "check R411 " not in texts["reg"] or "check-pod-security-batch-c.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R411 with --mutation-verify")
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("metallb speaker runAsUser dropped (sandbox breaks)", "metallb", "            runAsUser: 0\n            runAsGroup: 10100\n", "            runAsGroup: 10100\n"),
    ("metallb bootstrap speaker runAsUser dropped", "net-boot", "    --set speaker.securityContext.runAsUser=0 \\\n", ""),
    ("metallb speaker GID dropped", "metallb", "            runAsUser: 0\n            runAsGroup: 10100\n", "            runAsUser: 0\n"),
    ("istio-cni chart bumped without re-verify", "cni", "targetRevision: 1.30.1", "targetRevision: 1.31.0"),
    ("istio-cni GitOps seccomp dropped", "cni", "          seccompProfile:\n            type: RuntimeDefault\n", ""),
    ("istio-cni GitOps seccomp Unconfined", "cni", "            type: RuntimeDefault\n", "            type: Unconfined\n"),
    ("istio-cni bootstrap seccomp dropped", "cni-boot", "  seccompProfile:\n    type: RuntimeDefault\ntolerations:", "tolerations:"),
    ("istio-cni bootstrap seccomp wrong level", "cni-boot", "  seccompProfile:\n    type: RuntimeDefault\ntolerations:", "seccompProfile:\n  type: RuntimeDefault\ntolerations:"),
    ("kube-vip init pod seccomp dropped", "vip-init", "  securityContext:\n    seccompProfile:\n      type: RuntimeDefault\n  containers:", "  containers:"),
    ("kube-vip join pod seccomp dropped", "vip-join", "  securityContext:\n    seccompProfile:\n      type: RuntimeDefault\n  containers:", "  containers:"),
    ("kube-vip init drop ALL dropped", "vip-init", "        drop:\n        - ALL\n", ""),
    ("kube-vip join drop ALL dropped", "vip-join", "        drop:\n        - ALL\n", ""),
    ("kube-vip init allowPrivilegeEscalation dropped", "vip-init", "      allowPrivilegeEscalation: false\n", ""),
    ("kube-vip join allowPrivilegeEscalation true", "vip-join", "      allowPrivilegeEscalation: false\n", "      allowPrivilegeEscalation: true\n"),
    ("kube-vip init extra capability", "vip-init", "        - NET_RAW\n    volumeMounts:", "        - NET_RAW\n        - SYS_ADMIN\n    volumeMounts:"),
    ("kube-vip join NET_RAW dropped from add", "vip-join", "        - NET_RAW\n    volumeMounts:", "    volumeMounts:"),
    ("kube-vip init UID no longer owns kubeconfig", "vip-init", "      runAsUser: 0\n", "      runAsUser: 10100\n"),
    ("kube-vip join low GID", "vip-join", "      runAsGroup: 10100\n", "      runAsGroup: 10000\n"),
    ("R411 wiring dropped", "reg", "check R411 ", "check R9999 "),
]

for path in ("vip-init", "vip-join"):
    anchor = "      allowPrivilegeEscalation: false\n"
    MUTATIONS.extend([
        (f"{path} privileged container", path, anchor, anchor + "      privileged: true\n"),
        (f"{path} container seccomp overrides pod", path, anchor, anchor + "      seccompProfile:\n        type: Unconfined\n"),
    ])


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
        acceptance_mutations = 0
        for target, checks in REMOVED_ACCEPTANCES.items():
            for check_id in checks:
                rules = json.loads(texts["rules"])
                rule = next(rule for rule in rules["configAudit"] if rule_target(rule) == target)
                rule["checks"].append(check_id)
                mutated = {**texts, "rules": json.dumps(rules)}
                expected = f"{'/'.join(target)} must leave {check_id} actionable"
                if expected not in check(mutated):
                    print(f"FAIL: mutation not rejected: {expected}", file=sys.stderr)
                    return 1
                acceptance_mutations += 1
        print(f"PASS: R411 pod-security Batch C ({len(MUTATIONS) + acceptance_mutations} mutations rejected)")
        return 0
    print("PASS: R411 pod-security Batch C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
