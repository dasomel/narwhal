#!/usr/bin/env python3
"""R388: Compliance scan coverage (Lane S — control plane, bindings, PSS specs, KISA accuracy, node hardening).

Verifies:
1. gitops/charts/narwhal-apps/templates/trivy-operator.yaml:
   - excludes only zero-value namespaces (kube-public, kube-node-lease, trivy-system);
     kube-system and istio-system are scanned in-operator.
   - trivyOperator.excludeImages suppresses vulnerability scan Jobs for system images
     (control-plane, CNI, CSI, kube-vip, Istio pilot/ztunnel/install-cni) using exact
     Go filepath.Match globs that do NOT exclude app waypoint proxyv2 images.
   - trivyOperator.skipResourceByLabels includes Velero keys + ghost-pod-reaper child Jobs.
   - trivy.supportedConfigAuditKinds includes RoleBinding and ClusterRoleBinding (KSV111).
   - compliance.specs restricts chart-rendered specs to k8s-cis-1.23 and k8s-nsa-1.0.
2. gitops/resources/ghost-pod-reaper.yaml labels child Jobs (jobTemplate.metadata.labels)
   with narwhal.dasomel.io/skip-ephemeral-job-scan: "true" without labeling the CronJob itself.
3. gitops/resources/pss-compliance.yaml + gitops/charts/narwhal-apps/templates/pss-compliance.yaml:
   - defines k8s-pss-baseline-0.1 (11 controls) and k8s-pss-restricted-0.1 (17 controls) with
     reportType: all and uppercase AVD-KSV-* IDs (zero lowercase avd-* IDs).
4. gitops/resources/kisa-compliance.yaml:
   - KISA-ETCD-01 checks AVD-KCV-0029/0030/0042/0043 (no mis-mapped AVD-KCV-0004).
   - KISA-RBAC-01 includes AVD-KSV-0111 alongside AVD-KSV-0041/0043.
   - KISA-NET-01 accurately describes selector checking (not cluster-wide default-deny).
5. scripts/common/01-prerequisites.sh persists /etc/sysctl.d/95-kubelet-kernel-defaults.conf;
   scripts/cluster/02-init-cluster.sh sets protectKernelDefaults: true and 6 PFS AEAD
   tlsCipherSuites (without enabling serverTLSBootstrap: true).
6. scripts/cluster/harden-node-files.sh, 11-4b-apiserver-oidc-node.sh, 02-join-control-plane.sh:
   - harden all /etc/kubernetes/pki *.crt/*.key to 0600 root:root and install
     narwhal-harden-node-files.timer for kubeadm certs renew; oidc-ca.crt is fetched via
     sudo cat and installed 0600.
7. Behavioral fixture tests under both bash 3.2 (/bin/bash) and default bash (no helm/promtool):
   - harden-node-files.sh tightens 644 PKI certs/keys, kubelet drop-ins, kubelet config, CNI.
   - verify_node_file_hardening rejects 644 PKI certs/keys, etcd subdir certs, missing ca.crt,
     644 kubelet unit/drop-ins, 644 kubelet config, missing/false protectKernelDefaults,
     empty/weak tlsCipherSuites, and symlink->644 PKI cert.
8. docs/common/compliance-hardening.md (## Scan coverage) + regression-check-kakao.sh R388 wiring.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "trivy": ROOT / "gitops/charts/narwhal-apps/templates/trivy-operator.yaml",
    "reaper": ROOT / "gitops/resources/ghost-pod-reaper.yaml",
    "pss_res": ROOT / "gitops/resources/pss-compliance.yaml",
    "pss_app": ROOT / "gitops/charts/narwhal-apps/templates/pss-compliance.yaml",
    "kisa": ROOT / "gitops/resources/kisa-compliance.yaml",
    "prereq": ROOT / "scripts/common/01-prerequisites.sh",
    "init": ROOT / "scripts/cluster/02-init-cluster.sh",
    "join_cp": ROOT / "scripts/cluster/02-join-control-plane.sh",
    "oidc_node": ROOT / "scripts/cluster/11-4b-apiserver-oidc-node.sh",
    "harden": ROOT / "scripts/cluster/harden-node-files.sh",
    "verify": ROOT / "scripts/test/verify-cluster.sh",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

EXPECTED_SYSTEM_IMAGES = (
    "registry.k8s.io/kube-apiserver:v1.35.7",
    "registry.k8s.io/kube-controller-manager:v1.35.7",
    "registry.k8s.io/kube-scheduler:v1.35.7",
    "registry.k8s.io/etcd:3.6.6-0",
    "registry.k8s.io/coredns/coredns:v1.13.1",
    "registry.k8s.io/metrics-server/metrics-server:v0.8.1",
    "registry.k8s.io/sig-storage/nfsplugin:v4.13.2",
    "ghcr.io/kube-vip/kube-vip:v1.1.2",
    "quay.io/cilium/cilium:v1.19.4",
    "registry.istio.io/release/pilot:1.30.1-distroless",
    "registry.istio.io/release/ztunnel:1.30.1",
    "registry.istio.io/release/install-cni:1.30.1-distroless",
)

MUST_NOT_EXCLUDE_IMAGES = (
    "registry.istio.io/release/proxyv2:1.30.1-distroless",
    "ghcr.io/dasomel/narwhal-portal:1.1.23",
    "docker.io/alpine/k8s:1.31.4",
)

REQUIRED_SYSCTLS = (
    "vm.overcommit_memory = 1",
    "vm.panic_on_oom = 0",
    "kernel.panic = 10",
    "kernel.panic_on_oops = 1",
    "kernel.keys.root_maxkeys = 1000000",
    "kernel.keys.root_maxbytes = 25000000",
)

EXPECTED_CIPHERS = (
    "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
    "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",
    "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384",
    "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384",
    "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305",
    "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305",
)


def _go_filepath_match(pattern: str, name: str) -> bool:
    """Emulate Go's filepath.Match where '*' does not cross '/'."""
    p_parts = pattern.split("/")
    n_parts = name.split("/")
    if len(p_parts) != len(n_parts):
        return False
    return all(fnmatch.fnmatchcase(np, pp) for pp, np in zip(p_parts, n_parts))


def _bash_interpreters() -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in (shutil.which("bash"), "/bin/bash"):
        if not raw:
            continue
        p = Path(raw)
        if not p.exists():
            continue
        real = str(p.resolve())
        if real not in seen:
            seen.add(real)
            candidates.append(str(p))
    return candidates


def _extract_fn(verify_text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\(\)\s*\{{.*?\n\}}", verify_text, re.S | re.M)
    assert m, f"{name}() not found in verify-cluster.sh"
    return m.group(0)


def _run_behavioral_tests(harden_text: str, verify_text: str) -> list[str]:
    errors: list[str] = []
    interpreters = _bash_interpreters()
    if not interpreters:
        return ["bash binary required for R388 behavioral tests"]

    import grp
    import pwd

    owner = f"{pwd.getpwuid(os.getuid()).pw_name}:{grp.getgrgid(os.getgid()).gr_name}"
    fn_src = "\n\n".join(
        _extract_fn(verify_text, n)
        for n in ("_node_priv", "_node_stat", "verify_node_file_hardening")
    )

    verify_runner_tpl = f"""#!/usr/bin/env bash
set -euo pipefail
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
pass() {{ PASS_COUNT=$((PASS_COUNT + 1)); echo "PASS: $1"; }}
fail() {{ FAIL_COUNT=$((FAIL_COUNT + 1)); echo "FAIL: $1"; }}
warn() {{ WARN_COUNT=$((WARN_COUNT + 1)); echo "WARN: $1"; }}
{fn_src}
verify_node_file_hardening "$1" "{owner}" "${{3:-{owner}}}"
echo "SUMMARY pass=$PASS_COUNT fail=$FAIL_COUNT warn=$WARN_COUNT"
want_pass="${{2:-5}}"
[ "$FAIL_COUNT" -eq 0 ] && [ "$PASS_COUNT" -eq "$want_pass" ]
"""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        harden_script = tmp_path / "harden-node-files.sh"
        harden_script.write_text(harden_text, encoding="utf-8")
        verify_runner = tmp_path / "run-verify.sh"
        verify_runner.write_text(verify_runner_tpl, encoding="utf-8")

        def make_node_root(
            name: str,
            *,
            worker: bool = False,
            crt_mode: int = 0o600,
            etcd_crt_mode: int = 0o600,
            key_mode: int = 0o600,
            include_ca: bool = True,
            pki_symlink_644: bool = False,
            unit_mode: int | None = 0o600,
            dropin_usr_mode: int | None = 0o600,
            dropin_etc_mode: int | None = 0o600,
            kcfg_mode: int | None = 0o600,
            kcfg_body: str | None = None,
        ) -> Path:
            r = tmp_path / name
            cni_dir = r / "etc/cni/net.d"
            cni_dir.mkdir(parents=True)
            cni_f = cni_dir / "05-cilium.conflist"
            cni_f.write_text("{}\n", encoding="utf-8")
            os.chmod(cni_f, 0o600)
            os.chmod(cni_dir, 0o700)

            if not worker:
                etcd_dir = r / "var/lib/etcd"
                etcd_dir.mkdir(parents=True)
                os.chmod(etcd_dir, 0o700)
                man = r / "etc/kubernetes/manifests/etcd.yaml"
                man.parent.mkdir(parents=True)
                man.write_text("kind: Pod\n", encoding="utf-8")
                os.chmod(man, 0o600)

            pki = r / "etc/kubernetes/pki"
            pki.mkdir(parents=True)
            if include_ca:
                ca = pki / "ca.crt"
                ca.write_text("ca\n", encoding="utf-8")
                os.chmod(ca, 0o600)
            if not worker:
                for fname, mode in (
                    ("apiserver.crt", crt_mode),
                    ("oidc-ca.crt", crt_mode),
                    ("apiserver.key", key_mode),
                ):
                    p = pki / fname
                    p.write_text("x\n", encoding="utf-8")
                    os.chmod(p, mode)
                epki = pki / "etcd"
                epki.mkdir()
                ec = epki / "server.crt"
                ec.write_text("ec\n", encoding="utf-8")
                os.chmod(ec, etcd_crt_mode)
                ek = epki / "server.key"
                ek.write_text("ek\n", encoding="utf-8")
                os.chmod(ek, key_mode)
            if pki_symlink_644:
                tgt = r / "opt/external.crt"
                tgt.parent.mkdir(parents=True, exist_ok=True)
                tgt.write_text("ext\n", encoding="utf-8")
                os.chmod(tgt, 0o644)
                (pki / "linked.crt").symlink_to(tgt)

            if unit_mode is not None:
                u = r / "usr/lib/systemd/system/kubelet.service"
                u.parent.mkdir(parents=True, exist_ok=True)
                u.write_text("[Service]\n", encoding="utf-8")
                os.chmod(u, unit_mode)
            if dropin_usr_mode is not None:
                d1 = r / "usr/lib/systemd/system/kubelet.service.d/10-kubeadm.conf"
                d1.parent.mkdir(parents=True, exist_ok=True)
                d1.write_text("[Service]\n", encoding="utf-8")
                os.chmod(d1, dropin_usr_mode)
            if dropin_etc_mode is not None:
                d2 = r / "etc/systemd/system/kubelet.service.d/10-after-chrony.conf"
                d2.parent.mkdir(parents=True, exist_ok=True)
                d2.write_text("[Unit]\n", encoding="utf-8")
                os.chmod(d2, dropin_etc_mode)

            if kcfg_mode is not None:
                kc = r / "var/lib/kubelet/config.yaml"
                kc.parent.mkdir(parents=True, exist_ok=True)
                body = kcfg_body if kcfg_body is not None else (
                    "apiVersion: kubelet.config.k8s.io/v1beta1\n"
                    "kind: KubeletConfiguration\n"
                    "protectKernelDefaults: true\n"
                    "tlsCipherSuites:\n"
                    "  - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256\n"
                    "  - TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384\n"
                )
                kc.write_text(body, encoding="utf-8")
                os.chmod(kc, kcfg_mode)
            return r

        # 1. Behavioral test of harden-node-files.sh: unhardened fixture -> hardened -> passes verify
        for idx, bash_bin in enumerate(interpreters):
            unhard = make_node_root(
                f"harden_target_{idx}",
                crt_mode=0o644,
                etcd_crt_mode=0o644,
                key_mode=0o644,
                unit_mode=0o644,
                dropin_usr_mode=0o644,
                dropin_etc_mode=0o644,
                kcfg_mode=0o644,
            )
            os.chmod(unhard / "etc/cni/net.d/05-cilium.conflist", 0o644)
            os.chmod(unhard / "etc/kubernetes/pki/ca.crt", 0o644)
            h_res = subprocess.run(
                [bash_bin, str(harden_script), str(unhard)],
                capture_output=True,
                text=True,
                env={**os.environ, "INSTALL_RENEW_TIMER": "false"},
            )
            if h_res.returncode != 0:
                errors.append(f"[{bash_bin}] harden-node-files.sh failed: {h_res.stdout} {h_res.stderr}")
                continue
            for rel in (
                "etc/cni/net.d/05-cilium.conflist",
                "etc/kubernetes/pki/ca.crt",
                "etc/kubernetes/pki/apiserver.crt",
                "etc/kubernetes/pki/oidc-ca.crt",
                "etc/kubernetes/pki/apiserver.key",
                "etc/kubernetes/pki/etcd/server.crt",
                "etc/kubernetes/pki/etcd/server.key",
                "usr/lib/systemd/system/kubelet.service",
                "usr/lib/systemd/system/kubelet.service.d/10-kubeadm.conf",
                "etc/systemd/system/kubelet.service.d/10-after-chrony.conf",
                "var/lib/kubelet/config.yaml",
            ):
                m = stat.S_IMODE(os.stat(unhard / rel).st_mode)
                if m != 0o600:
                    errors.append(f"[{bash_bin}] harden-node-files.sh left {rel} at {oct(m)} (expected 0o600)")
            v_res = subprocess.run(
                [bash_bin, str(verify_runner), str(unhard), "5", owner],
                capture_output=True,
                text=True,
            )
            if v_res.returncode != 0:
                errors.append(f"[{bash_bin}] post-harden verify failed: {v_res.stdout} {v_res.stderr}")

        # 2. Behavioral boundary cases for verify_node_file_hardening
        cases = [
            ("compliant master", make_node_root("ok_master"), "5", True, "SUMMARY pass=5 fail=0"),
            ("compliant worker", make_node_root("ok_worker", worker=True), "4", True, "SUMMARY pass=4 fail=0"),
            ("644 apiserver.crt", make_node_root("bad_crt", crt_mode=0o644), "5", False, "FAIL: CIS 1.1.19-21/4.1.7 PKI perms/owner"),
            ("644 etcd/server.crt", make_node_root("bad_etcd_crt", etcd_crt_mode=0o644), "5", False, "FAIL: CIS 1.1.19-21/4.1.7 PKI perms/owner"),
            ("644 apiserver.key", make_node_root("bad_key", key_mode=0o644), "5", False, "FAIL: CIS 1.1.19-21/4.1.7 PKI perms/owner"),
            ("missing ca.crt", make_node_root("no_ca", include_ca=False), "5", False, "FAIL: CIS 4.1.7 kubelet CA file missing"),
            ("symlink->644 PKI cert", make_node_root("sym_crt", pki_symlink_644=True), "5", False, "FAIL: CIS 1.1.19-21/4.1.7 PKI perms/owner"),
            ("644 10-kubeadm.conf drop-in", make_node_root("bad_dropin_usr", dropin_usr_mode=0o644), "5", False, "FAIL: CIS 4.1.1-2 kubelet systemd unit/drop-ins"),
            ("644 10-after-chrony.conf drop-in", make_node_root("bad_dropin_etc", dropin_etc_mode=0o644), "5", False, "FAIL: CIS 4.1.1-2 kubelet systemd unit/drop-ins"),
            ("missing kubelet unit+drop-ins", make_node_root("no_unit", unit_mode=None, dropin_usr_mode=None, dropin_etc_mode=None), "5", False, "FAIL: CIS 4.1.1-2 kubelet systemd unit/drop-ins: none found"),
            ("644 kubelet config.yaml", make_node_root("bad_kcfg_mode", kcfg_mode=0o644), "5", False, "FAIL: CIS 4.1.9/4.2.6/4.2.13 kubelet config"),
            ("missing protectKernelDefaults", make_node_root("no_pkd", kcfg_body="tlsCipherSuites:\n  - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256\n"), "5", False, "protectKernelDefaults!=true"),
            ("false protectKernelDefaults", make_node_root("false_pkd", kcfg_body="protectKernelDefaults: false\ntlsCipherSuites:\n  - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256\n"), "5", False, "protectKernelDefaults!=true"),
            ("empty tlsCipherSuites", make_node_root("no_ciphers", kcfg_body="protectKernelDefaults: true\n"), "5", False, "tlsCipherSuites=empty"),
            ("weak tlsCipherSuites", make_node_root("weak_cipher", kcfg_body="protectKernelDefaults: true\ntlsCipherSuites:\n  - TLS_RSA_WITH_3DES_EDE_CBC_SHA\n"), "5", False, "weakCipher=TLS_RSA_WITH_3DES_EDE_CBC_SHA"),
            ("inline flow tlsCipherSuites", make_node_root("inline_ciphers", kcfg_body="protectKernelDefaults: true\ntlsCipherSuites: [TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256]\n"), "5", True, "SUMMARY pass=5 fail=0"),
        ]
        for bash_bin in interpreters:
            for label, fixture, want_pass, expect_ok, required in cases:
                res = subprocess.run(
                    [bash_bin, str(verify_runner), str(fixture), want_pass, owner],
                    capture_output=True,
                    text=True,
                )
                ok = (res.returncode == 0) if expect_ok else (res.returncode != 0)
                if not ok or required not in res.stdout:
                    errors.append(f"[{bash_bin}] {label}: rc={res.returncode} out={res.stdout!r} err={res.stderr!r}")

    return errors


def check(texts: dict[str, str], run_behavioral: bool = False) -> list[str]:
    errors: list[str] = []

    # 1. trivy-operator.yaml
    trivy_doc = yaml.safe_load(texts["trivy"])
    vals = (((trivy_doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}

    excl_ns = [ns.strip() for ns in str(vals.get("excludeNamespaces") or "").split(",") if ns.strip()]
    for must_scan in ("kube-system", "istio-system"):
        if must_scan in excl_ns:
            errors.append(f"trivy-operator.yaml must not exclude {must_scan} from scanning")
    for must_excl in ("kube-public", "kube-node-lease", "trivy-system"):
        if must_excl not in excl_ns:
            errors.append(f"trivy-operator.yaml should keep {must_excl} in excludeNamespaces")

    top_op = vals.get("trivyOperator") or {}
    skip_labels = [k.strip() for k in str(top_op.get("skipResourceByLabels") or "").split(",") if k.strip()]
    for lbl in ("velero.io/repo-name", "velero.io/pod-volume-backup", "velero.io/pod-volume-restore", "narwhal.dasomel.io/skip-ephemeral-job-scan"):
        if lbl not in skip_labels:
            errors.append(f"trivy-operator.yaml skipResourceByLabels missing {lbl}")

    excl_imgs = [p.strip() for p in str(top_op.get("excludeImages") or "").split(",") if p.strip()]
    if not excl_imgs:
        errors.append("trivy-operator.yaml must set trivyOperator.excludeImages to suppress system-image vuln scan Jobs")
    else:
        for img in EXPECTED_SYSTEM_IMAGES:
            if not any(_go_filepath_match(pat, img) for pat in excl_imgs):
                errors.append(f"trivy-operator.yaml excludeImages does not match system image {img}")
        for img in MUST_NOT_EXCLUDE_IMAGES:
            if any(_go_filepath_match(pat, img) for pat in excl_imgs):
                errors.append(f"trivy-operator.yaml excludeImages must NOT match non-system/waypoint image {img}")

    trivy_cfg = vals.get("trivy") or {}
    kinds = [k.strip() for k in str(trivy_cfg.get("supportedConfigAuditKinds") or "").split(",") if k.strip()]
    for kind in ("Workload", "Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding", "NetworkPolicy"):
        if kind not in kinds:
            errors.append(f"trivy-operator.yaml trivy.supportedConfigAuditKinds missing {kind}")

    comp_specs = (vals.get("compliance") or {}).get("specs") or []
    if comp_specs != ["k8s-cis-1.23", "k8s-nsa-1.0"]:
        errors.append(f"trivy-operator.yaml compliance.specs must be ['k8s-cis-1.23', 'k8s-nsa-1.0'] (got {comp_specs!r})")

    # 2. ghost-pod-reaper.yaml
    reaper_docs = [d for d in yaml.safe_load_all(texts["reaper"]) if isinstance(d, dict)]
    cjs = [d for d in reaper_docs if d.get("kind") == "CronJob"]
    if len(cjs) != 1:
        errors.append("ghost-pod-reaper.yaml must contain exactly one CronJob")
    else:
        cj = cjs[0]
        cj_labels = (cj.get("metadata") or {}).get("labels") or {}
        jt_labels = (((cj.get("spec") or {}).get("jobTemplate") or {}).get("metadata") or {}).get("labels") or {}
        if "narwhal.dasomel.io/skip-ephemeral-job-scan" in cj_labels:
            errors.append("ghost-pod-reaper CronJob metadata must NOT carry skip-ephemeral-job-scan (only jobTemplate)")
        if jt_labels.get("narwhal.dasomel.io/skip-ephemeral-job-scan") != "true":
            errors.append("ghost-pod-reaper spec.jobTemplate.metadata.labels must set narwhal.dasomel.io/skip-ephemeral-job-scan: 'true'")

    # 3. pss-compliance.yaml (resource + ArgoCD Application)
    pss_docs = [d for d in yaml.safe_load_all(texts["pss_res"]) if isinstance(d, dict)]
    pss_by_name = {((d.get("metadata") or {}).get("name")): d for d in pss_docs}
    for name, expected_count in (("k8s-pss-baseline-0.1", 11), ("k8s-pss-restricted-0.1", 17)):
        doc = pss_by_name.get(name)
        if not doc or doc.get("kind") != "ClusterComplianceReport":
            errors.append(f"pss-compliance.yaml missing ClusterComplianceReport {name}")
            continue
        spec = doc.get("spec") or {}
        if spec.get("reportType") != "all":
            errors.append(f"{name} must set reportType: all")
        controls = ((spec.get("compliance") or {}).get("controls")) or []
        if len(controls) != expected_count:
            errors.append(f"{name} expected {expected_count} controls, got {len(controls)}")
        for ctrl in controls:
            for chk in ctrl.get("checks") or []:
                cid = str(chk.get("id") or "")
                if not re.match(r"^AVD-KSV-\d{4}$", cid):
                    errors.append(f"{name} control {ctrl.get('id')} has non-uppercase/invalid check id {cid!r}")
    pss_active = "\n".join(l for l in texts["pss_res"].splitlines() if not l.lstrip().startswith("#"))
    if "avd-" in pss_active:
        errors.append("pss-compliance.yaml must contain zero lowercase avd- check IDs on active lines")
    if 'include: "pss-compliance.yaml"' not in texts["pss_app"] or 'sync-wave: "5"' not in texts["pss_app"]:
        errors.append("gitops/charts/narwhal-apps/templates/pss-compliance.yaml must include pss-compliance.yaml at sync-wave 5")

    # 4. kisa-compliance.yaml
    kisa_doc = yaml.safe_load(texts["kisa"])
    kisa_ctrls = {
        c.get("id"): c
        for c in (((kisa_doc.get("spec") or {}).get("compliance") or {}).get("controls") or [])
    }
    etcd_checks = [ch.get("id") for ch in (kisa_ctrls.get("KISA-ETCD-01") or {}).get("checks") or []]
    if "AVD-KCV-0004" in etcd_checks or not {"AVD-KCV-0029", "AVD-KCV-0030", "AVD-KCV-0042", "AVD-KCV-0043"}.issubset(etcd_checks):
        errors.append(f"KISA-ETCD-01 must use AVD-KCV-0029/0030/0042/0043 (no AVD-KCV-0004), got {etcd_checks}")
    rbac1_checks = [ch.get("id") for ch in (kisa_ctrls.get("KISA-RBAC-01") or {}).get("checks") or []]
    if not {"AVD-KSV-0041", "AVD-KSV-0043", "AVD-KSV-0111"}.issubset(rbac1_checks):
        errors.append(f"KISA-RBAC-01 must include AVD-KSV-0041/0043/0111, got {rbac1_checks}")
    net1_name = str((kisa_ctrls.get("KISA-NET-01") or {}).get("name") or "")
    if "default-deny" in net1_name.lower():
        errors.append(f"KISA-NET-01 name must not claim default-deny when only KSV038 selector check runs: {net1_name!r}")

    # 5. Kubelet hardening + sysctl persistence
    prereq_active = {l.strip() for l in texts["prereq"].splitlines() if l.strip() and not l.lstrip().startswith("#")}
    if "/etc/sysctl.d/95-kubelet-kernel-defaults.conf" not in texts["prereq"]:
        errors.append("01-prerequisites.sh must write /etc/sysctl.d/95-kubelet-kernel-defaults.conf")
    for line in REQUIRED_SYSCTLS:
        if line not in prereq_active:
            errors.append(f"01-prerequisites.sh missing active sysctl line {line!r}")

    kubeadm_m = re.search(r"(?ms)^cat <<EOF > /tmp/kubeadm-config\.yaml\s*\n(.*?)^EOF\s*$", texts["init"])
    if not kubeadm_m:
        errors.append("02-init-cluster.sh kubeadm-config heredoc missing")
    else:
        body = re.sub(r"(?m)^(\s*certSANs):\$\{CERT_SANS\}\s*$", r"\1: []", kubeadm_m.group(1))
        docs = [d for d in yaml.safe_load_all(body) if isinstance(d, dict)]
        kc = next((d for d in docs if d.get("kind") == "KubeletConfiguration"), None)
        if not kc:
            errors.append("02-init-cluster.sh missing KubeletConfiguration")
        else:
            if kc.get("protectKernelDefaults") is not True:
                errors.append("02-init-cluster.sh KubeletConfiguration must set protectKernelDefaults: true")
            if kc.get("serverTLSBootstrap") is True:
                errors.append("02-init-cluster.sh must NOT enable serverTLSBootstrap without a CSR approver")
            ciphers = kc.get("tlsCipherSuites") or []
            if tuple(ciphers) != EXPECTED_CIPHERS:
                errors.append(f"02-init-cluster.sh KubeletConfiguration tlsCipherSuites mismatch: {ciphers!r}")

    # 6. PKI 0600 + renewal timer
    harden_active = "\n".join(l for l in texts["harden"].splitlines() if l.strip() and not l.lstrip().startswith("#"))
    for token in ("*.crt", "*.key", "systemctl enable --now narwhal-harden-node-files.timer", "narwhal-harden-node-files.service", "/usr/local/sbin/narwhal-harden-node-files.sh"):
        if token not in harden_active:
            errors.append(f"harden-node-files.sh missing active token {token!r}")
    if "install -m 0600" not in texts["oidc_node"] or "chmod 0600" not in texts["oidc_node"]:
        errors.append("11-4b-apiserver-oidc-node.sh must install/enforce oidc-ca.crt mode 0600")
    if "sudo cat /etc/kubernetes/pki/oidc-ca.crt" not in texts["join_cp"] or "install -o root -g root -m 0600 /tmp/oidc-ca.crt" not in texts["join_cp"]:
        errors.append("02-join-control-plane.sh must fetch oidc-ca.crt via sudo cat and install mode 0600")

    # 7. Docs & regression wiring
    for needle in (
        "\n## Scan coverage\n",
        "D-SCAN-CONTROL-PLANE",
        "D-SCAN-SYSTEM-EXCLUDE-IMAGES",
        "D-SCAN-BINDINGS",
        "D-PSS-SPEC-CASE",
        "D-KISA-CONTROL-ACCURACY",
        "D-NODE-PKI-PERMS",
        "D-NODE-PKI-RENEW",
        "D-KUBELET-KERNEL-DEFAULTS",
        "D-KUBELET-TLS-CIPHERS",
        "D-KUBELET-NO-SERVER-TLS-BOOTSTRAP",
        "ClusterConfigAuditReport",
    ):
        if needle not in texts["doc"]:
            errors.append(f"docs/common/compliance-hardening.md missing {needle!r}")

    if "check R388 " not in texts["reg"] or "check-compliance-scan-coverage.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R388 with --mutation-verify")

    if run_behavioral and not errors:
        errors.extend(_run_behavioral_tests(texts["harden"], texts["verify"]))

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("re-exclude kube-system", "trivy", 'excludeNamespaces: "kube-public,kube-node-lease,trivy-system"', 'excludeNamespaces: "kube-system,kube-public,kube-node-lease,trivy-system"'),
    ("re-exclude istio-system", "trivy", 'excludeNamespaces: "kube-public,kube-node-lease,trivy-system"', 'excludeNamespaces: "kube-public,kube-node-lease,trivy-system,istio-system"'),
    ("drop kube-public exclusion", "trivy", 'excludeNamespaces: "kube-public,kube-node-lease,trivy-system"', 'excludeNamespaces: "kube-node-lease,trivy-system"'),
    ("over-broad istio excludeImages matching proxyv2", "trivy", "registry.istio.io/release/pilot:*,registry.istio.io/release/ztunnel:*,registry.istio.io/release/install-cni:*", "registry.istio.io/release/*"),
    ("drop excludeImages", "trivy", "registry.k8s.io/kube-apiserver:*", "registry.k8s.io/unused:*"),
    ("drop ghost-pod-reaper skip label", "trivy", ",narwhal.dasomel.io/skip-ephemeral-job-scan", ""),
    ("drop RoleBinding from supportedConfigAuditKinds", "trivy", "Role,ClusterRole,RoleBinding,ClusterRoleBinding,NetworkPolicy", "Role,ClusterRole,ClusterRoleBinding,NetworkPolicy"),
    ("drop ClusterRoleBinding from supportedConfigAuditKinds", "trivy", "Role,ClusterRole,RoleBinding,ClusterRoleBinding,NetworkPolicy", "Role,ClusterRole,RoleBinding,NetworkPolicy"),
    ("re-enable broken chart PSS specs", "trivy", "            - k8s-nsa-1.0\n", "            - k8s-nsa-1.0\n            - k8s-pss-restricted-0.1\n"),
    ("drop ghost-pod-reaper jobTemplate label", "reaper", 'narwhal.dasomel.io/skip-ephemeral-job-scan: "true"', 'other-label: "true"'),
    ("lowercase PSS check id", "pss_res", "id: AVD-KSV-0001", "id: avd-ksv-0001"),
    ("PSS reportType summary", "pss_res", "reportType: all", "reportType: summary"),
    ("drop pss-compliance wave 5", "pss_app", 'sync-wave: "5"', 'sync-wave: "1"'),
    ("re-introduce KCV0004 in KISA-ETCD-01", "kisa", "id: AVD-KCV-0042", "id: AVD-KCV-0004"),
    ("drop KSV111 from KISA-RBAC-01", "kisa", "          - id: AVD-KSV-0111\n", ""),
    ("re-claim default-deny in KISA-NET-01", "kisa", "name: NetworkPolicy 셀렉터 적용 점검", "name: Ingress default-deny NetworkPolicy 적용"),
    ("drop 95-kubelet-kernel-defaults.conf", "prereq", "/etc/sysctl.d/95-kubelet-kernel-defaults.conf", "/etc/sysctl.d/95-other.conf"),
    ("drop kernel.panic sysctl", "prereq", "kernel.panic = 10", "# kernel.panic = 10"),
    ("disable protectKernelDefaults", "init", "protectKernelDefaults: true", "protectKernelDefaults: false"),
    ("enable serverTLSBootstrap without approver", "init", "protectKernelDefaults: true", "protectKernelDefaults: true\nserverTLSBootstrap: true"),
    ("drop tlsCipherSuites", "init", "  - TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305\n", ""),
    ("drop PKI *.crt hardening", "harden", "-name '*.crt' -o -name '*.key'", "-name '*.key'"),
    ("drop renewal timer", "harden", "systemctl enable --now narwhal-harden-node-files.timer", "systemctl status narwhal-disabled.timer"),
    ("revert oidc-ca.crt to 0644 in 11-4b", "oidc_node", "install -m 0600", "install -m 0644"),
    ("revert oidc-ca.crt to 0644 in 02-join", "join_cp", "install -o root -g root -m 0600 /tmp/oidc-ca.crt", "install -o root -g root -m 0644 /tmp/oidc-ca.crt"),
    ("drop Scan coverage doc section", "doc", "\n## Scan coverage\n", "\n## Removed\n"),
    ("drop R388 wiring", "reg", "check R388 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts, run_behavioral=True)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = dict(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated, run_behavioral=False):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R388 compliance scan coverage ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R388 compliance scan coverage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
