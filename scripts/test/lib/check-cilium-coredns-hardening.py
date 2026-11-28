#!/usr/bin/env python3
"""R423: Cilium 1.19.4 workload hardening, bpffs mount, and exact Cilium/CoreDNS acceptances."""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "prereq": ROOT / "scripts/common/01-prerequisites.sh",
    "cni": ROOT / "scripts/cluster/03-cni-install.sh",
    "ops": ROOT / "scripts/ops/reconcile-cilium-hardening.sh",
    "accepted": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

REQUIRED_CNI_FLAGS = (
    "--set bpf.autoMount.enabled=false",
    "--set podSecurityContext.seccompProfile.type=RuntimeDefault",
    "--set securityContext.seLinuxOptions=null",
    "--set envoy.podSecurityContext.seccompProfile.type=RuntimeDefault",
    "--set envoy.podSecurityContext.runAsUser=0",
    "--set envoy.podSecurityContext.runAsGroup=10100",
    "--set envoy.securityContext.seLinuxOptions=null",
    '--set "envoy.extraHostPathMounts[0].name=bpf-maps"',
    '--set "envoy.extraHostPathMounts[0].mountPath=/sys/fs/bpf"',
    '--set "envoy.extraHostPathMounts[0].hostPath=/sys/fs/bpf"',
    '--set "envoy.extraHostPathMounts[0].hostPathType=DirectoryOrCreate"',
    '--set "envoy.extraHostPathMounts[0].readOnly=false"',
    '--set "envoy.extraHostPathMounts[0].mountPropagation=HostToContainer"',
    "--set hubble.ui.securityContext.runAsNonRoot=true",
    "--set hubble.ui.securityContext.seccompProfile.type=RuntimeDefault",
    '--set "hubble.ui.frontend.securityContext.capabilities.drop[0]=ALL"',
    '--set "hubble.ui.backend.securityContext.capabilities.drop[0]=ALL"',
    "--set operator.podSecurityContext.runAsNonRoot=true",
    "--set operator.podSecurityContext.runAsUser=10100",
    "--set operator.podSecurityContext.runAsGroup=10100",
)

EXPECTED_RULES = {
    ("kube-system", "DaemonSet", "cilium", "accept"): {
        "KSV001", "KSV005", "KSV009", "KSV012", "KSV020", "KSV021",
        "KSV022", "KSV023", "KSV024", "KSV106", "KSV119", "KSV120", "KSV121",
    },
    ("kube-system", "DaemonSet", "cilium-envoy", "accept"): {
        "KSV001", "KSV005", "KSV009", "KSV012", "KSV020",
        "KSV022", "KSV023", "KSV024", "KSV105", "KSV106",
    },
    ("kube-system", "ReplicaSet", "cilium-operator", "accept"): {
        "KSV009", "KSV024",
    },
    ("kube-system", "ReplicaSet", "hubble-ui", "accept"): {
        "KSV020", "KSV021",
    },
    ("kube-system", "ReplicaSet", "coredns", "accept"): {
        "KSV012", "KSV020", "KSV021", "KSV022", "KSV030", "KSV104",
    },
}

REASON_KEYS = (
    "verifiedciliumHost",
    "verifiedciliumEnvoyHost",
    "ciliumOperatorHost",
    "hubbleUiUid1001",
    "corednsKubeadm",
)


def _find_cilium_chart() -> Path | None:
    candidates = [
        ROOT / ".review/charts/cilium-1.19.4.tgz",
        *sorted((ROOT.parent / "narwhal").glob("narwhal-airgap-bundle-*/charts/cilium-1.19.4.tgz")),
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _verify_helm_render(cni_text: str) -> list[str]:
    chart = _find_cilium_chart()
    if chart is None or shutil.which("helm") is None:
        return []
    m = re.search(r"cilium install --version \"\$\{CILIUM_VERSION\}\"\s*\\\n(.*?)\n\s*\}", cni_text, flags=re.S)
    if not m:
        return ["03-cni-install.sh: missing cilium install block for helm render"]
    raw_flags = re.findall(r'--set\s+"([^"]+)"|--set\s+(\S+)', m.group(1))
    set_args: list[str] = []
    for quoted, plain in raw_flags:
        expr = quoted or plain
        expr = expr.replace("${K8S_API_SERVER}", "192.168.56.100").replace("${POD_NETWORK_CIDR}", "10.244.0.0/16")
        set_args.extend(["--set", expr])
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        subprocess.run(["tar", "xzf", str(chart), "-C", str(tmp)], check=True)
        res = subprocess.run(
            ["helm", "template", "cilium", str(tmp / "cilium"), "-n", "kube-system", *set_args],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            return [f"helm template failed: {res.stderr.strip()}"]
        out = res.stdout
        errs: list[str] = []
        workloads = {
            (doc.get("kind"), (doc.get("metadata") or {}).get("name")): doc
            for doc in yaml.safe_load_all(out)
            if isinstance(doc, dict) and doc.get("kind") in ("DaemonSet", "Deployment")
        }
        ds_cilium = workloads.get(("DaemonSet", "cilium"))
        ds_envoy = workloads.get(("DaemonSet", "cilium-envoy"))
        dp_op = workloads.get(("Deployment", "cilium-operator"))
        dp_ui = workloads.get(("Deployment", "hubble-ui"))
        if not ds_cilium or not ds_envoy or not dp_op or not dp_ui:
            return ["rendered Cilium manifests missing expected DaemonSet/Deployment"]

        c_spec = ds_cilium["spec"]["template"]["spec"]
        if any(c.get("name") == "mount-bpf-fs" for c in c_spec.get("initContainers") or []):
            errs.append("rendered DaemonSet/cilium still contains mount-bpf-fs initContainer")
        if (c_spec.get("securityContext") or {}).get("seccompProfile") != {"type": "RuntimeDefault"}:
            errs.append("rendered DaemonSet/cilium seccompProfile is not RuntimeDefault")
        for c in [*c_spec.get("containers", []), *(c_spec.get("initContainers") or [])]:
            if (c.get("securityContext") or {}).get("seLinuxOptions") is not None:
                errs.append(f"rendered DaemonSet/cilium container {c.get('name')} still has seLinuxOptions")

        e_spec = ds_envoy["spec"]["template"]["spec"]
        if (e_spec.get("securityContext") or {}).get("seccompProfile") != {"type": "RuntimeDefault"}:
            errs.append("rendered DaemonSet/cilium-envoy seccompProfile is not RuntimeDefault")
        if (e_spec.get("securityContext") or {}).get("runAsUser") != 0 or (e_spec.get("securityContext") or {}).get("runAsGroup") != 10100:
            errs.append("rendered DaemonSet/cilium-envoy must set runAsUser 0 and runAsGroup 10100")
        e_main = e_spec["containers"][0]
        if (e_main.get("securityContext") or {}).get("seLinuxOptions") is not None:
            errs.append("rendered DaemonSet/cilium-envoy still has seLinuxOptions")
        if not any(m.get("mountPath") == "/sys/fs/bpf" and m.get("name") == "bpf-maps" for m in e_main.get("volumeMounts") or []):
            errs.append("rendered DaemonSet/cilium-envoy missing /sys/fs/bpf bpf-maps volumeMount")

        o_sc = dp_op["spec"]["template"]["spec"].get("securityContext") or {}
        if o_sc.get("runAsNonRoot") is not True or o_sc.get("runAsUser") != 10100 or o_sc.get("runAsGroup") != 10100:
            errs.append("rendered Deployment/cilium-operator must set runAsNonRoot true and UID/GID 10100")

        u_spec = dp_ui["spec"]["template"]["spec"]
        u_sc = u_spec.get("securityContext") or {}
        if u_sc.get("runAsNonRoot") is not True or u_sc.get("seccompProfile") != {"type": "RuntimeDefault"}:
            errs.append("rendered Deployment/hubble-ui must set runAsNonRoot true and RuntimeDefault seccomp")
        for c in u_spec["containers"]:
            if ((c.get("securityContext") or {}).get("capabilities") or {}).get("drop") != ["ALL"]:
                errs.append(f"rendered Deployment/hubble-ui container {c.get('name')} must drop ALL capabilities")
        return errs


def _make_dummy_chart_tgz(dest: Path) -> None:
    with tarfile.open(dest, "w:gz") as tf:
        data = b"apiVersion: v2\nname: cilium\nversion: 1.19.4\n"
        info = tarfile.TarInfo("cilium/Chart.yaml")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))


def _verify_ops_merge_behavior(ops_text: str) -> list[str]:
    if shutil.which("yq") is None:
        return ["yq v4 is required for R423 ops merge behavioural test"]

    live_fixture = {
        "routingMode": "tunnel",
        "tunnelProtocol": "vxlan",
        "unrelatedCustomKey": "preserve-me",
        "nestedCustom": {"enabled": True, "note": "keep-intact"},
        "kubeProxyReplacement": True,
        "k8sServiceHost": "192.168.56.100",
        "k8sServicePort": 6443,
        "bpf": {"masquerade": True, "autoMount": {"enabled": True}},
        "podSecurityContext": {
            "appArmorProfile": {"type": "Unconfined"},
            "seccompProfile": {"type": "Unconfined"},
        },
        "securityContext": {"seLinuxOptions": {"level": "s0", "type": "spc_t"}},
        "hubble": {"relay": {"enabled": True}, "ui": {"enabled": True}},
        "operator": {"replicas": 2, "resources": {"limits": {"memory": "512Mi"}}},
    }

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ops_path = tmp / "reconcile-cilium-hardening.sh"
        ops_path.write_text(ops_text, encoding="utf-8")
        ops_path.chmod(0o755)

        chart_tgz = tmp / "cilium-1.19.4.tgz"
        _make_dummy_chart_tgz(chart_tgz)

        live_yaml = tmp / "live-fixture.yaml"
        live_yaml.write_text(yaml.safe_dump(live_fixture, sort_keys=False), encoding="utf-8")

        helm_calls = tmp / "helm-calls.log"
        kubectl_calls = tmp / "kubectl-calls.log"
        rollback_calls = tmp / "rollback-called.log"
        captured_values = tmp / "captured-values.yaml"

        stub_helm = tmp / "helm"
        stub_helm.write_text(
            f"""#!/bin/bash
set -euo pipefail
echo "$*" >> "{helm_calls}"
if [ "$1" = "get" ] && [ "$2" = "values" ]; then
  cat "{live_yaml}"
  exit 0
fi
if [ "$1" = "list" ]; then
  echo '[{{"name":"cilium","namespace":"kube-system","revision":"7"}}]'
  exit 0
fi
if [ "$1" = "rollback" ]; then
  echo "$*" >> "{rollback_calls}"
  exit 0
fi
if [ "$1" = "upgrade" ]; then
  shift
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "-f" ]; then
      cp "$2" "{captured_values}"
      shift 2
      continue
    fi
    shift
  done
  exit 0
fi
exit 1
""",
            encoding="utf-8",
        )
        stub_helm.chmod(0o755)

        stub_kubectl = tmp / "kubectl"
        stub_kubectl.write_text(
            f"""#!/bin/bash
set -euo pipefail
echo "$*" >> "{kubectl_calls}"
if [ -n "${{STUB_FAIL_TARGET:-}}" ]; then
  for arg in "$@"; do
    if [ "${{arg}}" = "${{STUB_FAIL_TARGET}}" ]; then
      exit 1
    fi
  done
fi
exit 0
""",
            encoding="utf-8",
        )
        stub_kubectl.chmod(0o755)

        base_env = {
            **os.environ,
            "HELM": str(stub_helm),
            "KUBECTL": str(stub_kubectl),
            "CILIUM_CLI": str(tmp / "no-cilium-cli"),
            "CILIUM_CHART_TGZ": str(chart_tgz),
        }

        errs: list[str] = []

        dry = subprocess.run(
            ["bash", str(ops_path), "--dry-run", "--skip-host-bpf"],
            capture_output=True,
            text=True,
            env=base_env,
        )
        if dry.returncode != 0:
            return [f"reconcile-cilium-hardening.sh --dry-run failed (rc={dry.returncode}): {dry.stderr.strip()}"]
        if "--- " not in dry.stdout or "+++ " not in dry.stdout:
            errs.append("reconcile-cilium-hardening.sh --dry-run must print unified diff of live vs merged values")
        helm_log_dry = helm_calls.read_text(encoding="utf-8") if helm_calls.is_file() else ""
        if "--dry-run=server" not in helm_log_dry:
            errs.append("reconcile-cilium-hardening.sh --dry-run must invoke helm upgrade --dry-run=server")
        if "--reuse-values" in helm_log_dry:
            errs.append("reconcile-cilium-hardening.sh --dry-run must not pass --reuse-values")
        if not captured_values.is_file():
            return [*errs, "reconcile-cilium-hardening.sh --dry-run did not pass -f merged values to helm upgrade"]

        merged = yaml.safe_load(captured_values.read_text(encoding="utf-8")) or {}
        for k, want in (
            ("routingMode", "tunnel"),
            ("tunnelProtocol", "vxlan"),
            ("unrelatedCustomKey", "preserve-me"),
            ("nestedCustom", {"enabled": True, "note": "keep-intact"}),
            ("kubeProxyReplacement", True),
            ("k8sServiceHost", "192.168.56.100"),
            ("k8sServicePort", 6443),
        ):
            if merged.get(k) != want:
                errs.append(f"merged values dropped/altered live key {k}: got={merged.get(k)!r} want={want!r}")

        if merged.get("bpf") != {"masquerade": True, "autoMount": {"enabled": False}}:
            errs.append(f"merged bpf mismatch: {merged.get('bpf')!r}")
        if merged.get("podSecurityContext") != {
            "appArmorProfile": {"type": "Unconfined"},
            "seccompProfile": {"type": "RuntimeDefault"},
        }:
            errs.append(f"merged podSecurityContext mismatch: {merged.get('podSecurityContext')!r}")
        if merged.get("securityContext") != {"seLinuxOptions": None}:
            errs.append(f"merged securityContext mismatch: {merged.get('securityContext')!r}")

        envoy = merged.get("envoy") or {}
        if envoy.get("podSecurityContext") != {"seccompProfile": {"type": "RuntimeDefault"}, "runAsUser": 0, "runAsGroup": 10100}:
            errs.append(f"merged envoy.podSecurityContext mismatch: {envoy.get('podSecurityContext')!r}")
        if envoy.get("securityContext") != {"seLinuxOptions": None}:
            errs.append(f"merged envoy.securityContext mismatch: {envoy.get('securityContext')!r}")
        mounts = envoy.get("extraHostPathMounts") or []
        if not any(m.get("name") == "bpf-maps" and m.get("mountPath") == "/sys/fs/bpf" and m.get("hostPath") == "/sys/fs/bpf" for m in mounts):
            errs.append(f"merged envoy.extraHostPathMounts missing bpf-maps: {mounts!r}")

        hubble = merged.get("hubble") or {}
        if hubble.get("relay") != {"enabled": True}:
            errs.append(f"merged hubble.relay dropped: {hubble.get('relay')!r}")
        ui = hubble.get("ui") or {}
        if ui.get("enabled") is not True:
            errs.append(f"merged hubble.ui.enabled dropped: {ui.get('enabled')!r}")
        if ui.get("securityContext") != {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}}:
            errs.append(f"merged hubble.ui.securityContext mismatch: {ui.get('securityContext')!r}")
        if ((ui.get("frontend") or {}).get("securityContext") or {}).get("capabilities") != {"drop": ["ALL"]}:
            errs.append(f"merged hubble.ui.frontend capabilities mismatch: {ui.get('frontend')!r}")
        if ((ui.get("backend") or {}).get("securityContext") or {}).get("capabilities") != {"drop": ["ALL"]}:
            errs.append(f"merged hubble.ui.backend capabilities mismatch: {ui.get('backend')!r}")

        op = merged.get("operator") or {}
        if op.get("replicas") != 2 or op.get("resources") != {"limits": {"memory": "512Mi"}}:
            errs.append(f"merged operator live keys dropped: {op!r}")
        if op.get("podSecurityContext") != {
            "seccompProfile": {"type": "RuntimeDefault"},
            "runAsNonRoot": True,
            "runAsUser": 10100,
            "runAsGroup": 10100,
        }:
            errs.append(f"merged operator.podSecurityContext mismatch: {op.get('podSecurityContext')!r}")

        helm_calls.unlink(missing_ok=True)
        kubectl_calls.unlink(missing_ok=True)
        apply_ok = subprocess.run(
            ["bash", str(ops_path), "--apply", "--skip-host-bpf"],
            capture_output=True,
            text=True,
            env=base_env,
        )
        if apply_ok.returncode != 0:
            errs.append(f"reconcile-cilium-hardening.sh --apply failed: {apply_ok.stderr.strip()}")
        else:
            helm_log_apply = helm_calls.read_text(encoding="utf-8") if helm_calls.is_file() else ""
            if "--dry-run=server" in helm_log_apply or "--reuse-values" in helm_log_apply:
                errs.append(f"reconcile-cilium-hardening.sh --apply passed forbidden flags: {helm_log_apply!r}")
            k_log = kubectl_calls.read_text(encoding="utf-8") if kubectl_calls.is_file() else ""
            for target in ("ds/cilium", "ds/cilium-envoy", "deploy/cilium-operator", "deploy/hubble-ui"):
                if f"-n kube-system rollout status {target}" not in k_log:
                    errs.append(f"reconcile-cilium-hardening.sh --apply did not wait for {target}")

        fail_env = {**base_env, "STUB_FAIL_TARGET": "deploy/cilium-operator"}
        apply_fail = subprocess.run(
            ["bash", str(ops_path), "--apply", "--skip-host-bpf"],
            capture_output=True,
            text=True,
            env=fail_env,
        )
        if apply_fail.returncode == 0:
            errs.append("reconcile-cilium-hardening.sh --apply must exit non-zero when rollout status fails")
        if "helm rollback cilium 7 -n kube-system --wait" not in apply_fail.stderr:
            errs.append(f"rollout failure must print exact helm rollback command; got stderr={apply_fail.stderr!r}")
        if rollback_calls.is_file():
            errs.append("reconcile-cilium-hardening.sh must NOT auto-execute helm rollback")

        return errs


def check(texts: dict[str, str], *, render_helm: bool = False, run_ops_behavior: bool = False) -> list[str]:
    errors: list[str] = []

    prereq = texts["prereq"]
    for needle in (
        "findmnt -rn -t bpf -T /sys/fs/bpf",
        "exit 1",
    ):
        if needle not in prereq:
            errors.append(f"01-prerequisites.sh missing {needle}")

    cni = texts["cni"]
    if not re.search(r"findmnt -rn -t bpf -T /sys/fs/bpf", cni):
        errors.append("03-cni-install.sh must verify /sys/fs/bpf is mounted as bpf before install")
    for flag in REQUIRED_CNI_FLAGS:
        if flag not in cni:
            errors.append(f"03-cni-install.sh missing {flag}")

    ops = texts["ops"]
    for needle in (
        "get values cilium -n kube-system -o yaml",
        ". as $item ireduce ({}; . * $item)",
        "diff -u",
        "autoMount:",
        "extraHostPathMounts:",
        "seLinuxOptions: null",
        "runAsUser: 10100",
        "runAsGroup: 10100",
        "findmnt -rn -t bpf -T /sys/fs/bpf",
        "rollout status",
        "helm rollback cilium",
    ):
        if needle not in ops:
            errors.append(f"reconcile-cilium-hardening.sh missing {needle}")
    # D-CILIUM-BPFFS-UNIT: systemd refuses a mount unit for the /sys/fs/bpf API file system.
    for label, text in (("01-prerequisites.sh", prereq), ("reconcile-cilium-hardening.sh", ops)):
        if "/etc/systemd/system/sys-fs-bpf.mount" in text or "enable --now sys-fs-bpf.mount" in text:
            errors.append(f"{label} must not create sys-fs-bpf.mount (systemd refuses it; boot mount is built in)")
    if "--reuse-values" in re.sub(r"#[^\n]*", "", ops):
        errors.append("reconcile-cilium-hardening.sh must never pass --reuse-values")

    rules = json.loads(texts["accepted"])["configAudit"]
    found: dict[tuple[str, str, str, str], set[str]] = {}
    for r in rules:
        key = (r["namespace"], r["kind"], r["name"], r["class"])
        if key in EXPECTED_RULES:
            found[key] = set(r["checks"])
    for key, want in EXPECTED_RULES.items():
        got = found.get(key)
        if got is None:
            errors.append(f"compliance-acceptance-rules.json missing rule {key}")
        elif got != want:
            errors.append(f"compliance-acceptance-rules.json {key} mismatch: missing={sorted(want - got)} extra={sorted(got - want)}")

    for k in REASON_KEYS:
        if texts["i18n"].count(f'"compliance.acceptedReason.{k}"') < 2:
            errors.append(f"i18n.ts missing ko/en entries for compliance.acceptedReason.{k}")
    for workload in ("DaemonSet/cilium", "DaemonSet/cilium-envoy", "ReplicaSet/cilium-operator", "ReplicaSet/hubble-ui", "ReplicaSet/coredns"):
        if workload not in texts["doc"]:
            errors.append(f"compliance-hardening.md missing {workload}")

    if "check R423 " not in texts["reg"] or "check-cilium-coredns-hardening.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R423 with --mutation-verify")

    if render_helm:
        errors.extend(_verify_helm_render(cni))
    if run_ops_behavior:
        errors.extend(_verify_ops_merge_behavior(ops))
    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("re-add sys-fs-bpf.mount unit in 01-prerequisites", "prereq", "if ! findmnt -rn -t bpf -T /sys/fs/bpf >/dev/null 2>&1; then", "sudo systemctl enable --now sys-fs-bpf.mount\nif ! findmnt -rn -t bpf -T /sys/fs/bpf >/dev/null 2>&1; then"),
    ("drop bpffs fail-closed check in 01-prerequisites", "prereq", "findmnt -rn -t bpf -T /sys/fs/bpf >/dev/null 2>&1", "true"),
    ("drop bpffs guard in 03-cni-install", "cni", "if ! findmnt -rn -t bpf -T /sys/fs/bpf >/dev/null 2>&1; then", "if false; then"),
    ("re-enable bpf.autoMount in 03-cni-install", "cni", "--set bpf.autoMount.enabled=false", "--set bpf.autoMount.enabled=true"),
    ("drop cilium RuntimeDefault seccomp", "cni", "--set podSecurityContext.seccompProfile.type=RuntimeDefault", "--set podSecurityContext.seccompProfile.type=Unconfined"),
    ("drop cilium seLinuxOptions=null", "cni", "      --set securityContext.seLinuxOptions=null \\\n", ""),
    ("drop envoy RuntimeDefault seccomp", "cni", "--set envoy.podSecurityContext.seccompProfile.type=RuntimeDefault", ""),
    ("drop envoy runAsUser=0", "cni", "      --set envoy.podSecurityContext.runAsUser=0 \\\n", ""),
    ("drop envoy runAsGroup=10100", "cni", "      --set envoy.podSecurityContext.runAsGroup=10100 \\\n", ""),
    ("drop envoy seLinuxOptions=null", "cni", "      --set envoy.securityContext.seLinuxOptions=null \\\n", ""),
    ("drop envoy bpf-maps extraHostPathMounts", "cni", '      --set "envoy.extraHostPathMounts[0].name=bpf-maps" \\\n', ""),
    ("drop hubble-ui runAsNonRoot", "cni", "      --set hubble.ui.securityContext.runAsNonRoot=true \\\n", ""),
    ("drop hubble-ui seccompProfile", "cni", "      --set hubble.ui.securityContext.seccompProfile.type=RuntimeDefault \\\n", ""),
    ("drop hubble-ui frontend drop ALL", "cni", '      --set "hubble.ui.frontend.securityContext.capabilities.drop[0]=ALL" \\\n', ""),
    ("drop hubble-ui backend drop ALL", "cni", '      --set "hubble.ui.backend.securityContext.capabilities.drop[0]=ALL" \\\n', ""),
    ("drop operator runAsNonRoot", "cni", "      --set operator.podSecurityContext.runAsNonRoot=true \\\n", ""),
    ("drop operator runAsUser=10100", "cni", "--set operator.podSecurityContext.runAsUser=10100", "--set operator.podSecurityContext.runAsUser=0"),
    ("drop operator runAsGroup=10100", "cni", "--set operator.podSecurityContext.runAsGroup=10100", "--set operator.podSecurityContext.runAsGroup=0"),
    ("ops helper uses heredoc-only values instead of yq merge", "ops", '"${YQ}" eval-all \'. as $item ireduce ({}; . * $item)\' "${WORK}/live-values.yaml" "${WORK}/hardening-overlay.yaml" > "${WORK}/merged-values.yaml"', 'cp "${WORK}/hardening-overlay.yaml" "${WORK}/merged-values.yaml"'),
    ("ops helper uses --reuse-values", "ops", ' -f "${WORK}/merged-values.yaml"', ' --reuse-values -f "${WORK}/merged-values.yaml"'),
    ("ops helper skips unified diff in dry-run", "ops", 'diff -u "${WORK}/live-values.yaml" "${WORK}/merged-values.yaml"', 'true'),
    ("ops helper drops hubble-ui rollout wait", "ops", 'ds/cilium ds/cilium-envoy deploy/cilium-operator deploy/hubble-ui', 'ds/cilium ds/cilium-envoy deploy/cilium-operator'),
    ("ops helper drops exact helm rollback command", "ops", '  helm rollback cilium ${PREV_REV} -n kube-system --wait', '  echo "rollback manually"'),
    ("drop i18n ciliumOperatorHost", "i18n", '"compliance.acceptedReason.ciliumOperatorHost"', '"compliance.acceptedReason.removed"'),
    ("drop i18n corednsKubeadm", "i18n", '"compliance.acceptedReason.corednsKubeadm"', '"compliance.acceptedReason.removed"'),
    ("drop R423 wiring", "reg", "check R423 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts, render_helm=True, run_ops_behavior=True)
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
            if not check(mutated, run_ops_behavior=(key == "ops")):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1

        data = json.loads(texts["accepted"])
        json_mutations = 0
        # Re-add fixed KSV017/KSV030 to cilium; drop KSV105 from cilium-envoy; drop new rules
        for label, mut_fn in (
            ("re-add KSV017 to cilium", lambda d: next(r for r in d["configAudit"] if r["name"] == "cilium")["checks"].append("KSV017")),
            ("drop KSV105 from cilium-envoy", lambda d: next(r for r in d["configAudit"] if r["name"] == "cilium-envoy")["checks"].remove("KSV105")),
            ("drop cilium-operator rule", lambda d: d["configAudit"].remove(next(r for r in d["configAudit"] if r["name"] == "cilium-operator"))),
            ("drop hubble-ui rule", lambda d: d["configAudit"].remove(next(r for r in d["configAudit"] if r["name"] == "hubble-ui"))),
            ("drop coredns rule", lambda d: d["configAudit"].remove(next(r for r in d["configAudit"] if r["name"] == "coredns"))),
        ):
            changed = copy.deepcopy(data)
            mut_fn(changed)
            if not check({**texts, "accepted": json.dumps(changed)}):
                print(f"FAIL: JSON mutation not rejected: {label}", file=sys.stderr)
                return 1
            json_mutations += 1

        print(f"PASS: R423 Cilium & CoreDNS hardening ({len(MUTATIONS) + json_mutations} mutations rejected)")
        return 0

    print("PASS: R423 Cilium & CoreDNS hardening")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
