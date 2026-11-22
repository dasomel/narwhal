#!/usr/bin/env python3
"""R386: KISA-IMG-01 (Harbor containerd CA), KISA-POD-01 (PSA 14/14), KISA-RBAC-02 (dev Role).

Static, behavioral (bash 3.2 + default bash), and mutation-verified check for:
  A. KISA-IMG-01 (D-HARBOR-NODE-CA):
     - 02-containerd.sh never emits skip_verify for harbor.<domain>; 06-configure-mirrors.sh stays
       scoped to external airgap mirrors.
     - scripts/common/lib-harbor-ca.sh validates every PEM block (rejects expired, not-yet-valid,
       trailing private key, foreign PEM blocks, trailing garbage), exports shared
       probe_remote_harbor_ca / sync_harbor_containerd_ca (distinguishing SSH/sudo/probe errors
       from explicit HARBOR_CA_ABSENT), and checks local Harbor CA state.
     - 08-6b-harbor-containerd-tls-node.sh installs ca.crt (mode 0644) and hosts.toml atomically.
     - 08-6-tls-routes.sh fails loudly on node-enumeration or remote-apply errors, 06-phase2-start.sh
       records 08-6/Harbor CA failures in PHASE2_DEGRADED (even when executed from /tmp/vagrant-shell),
       apply-harbor-ca-kakao.sh propagates tofu/JSON/SSH failures and delivers CA to all target nodes,
       and distribute-join-kakao.sh uses the shared three-way Harbor CA probe (SSH 255 fails,
       HARBOR_CA_ABSENT skips cleanly, HARBOR_CA_PRESENT validates & stages /tmp/harbor-ca.crt).
  B. KISA-POD-01 (D-PSA-LABELS-14):
     - apply-psa-labels.sh matches only exact per-namespace API NotFound (rejecting exec-plugin
       "not found" text, Forbidden, API timeout, and wrong-namespace NotFound), ends with a 14/14
       convergence check, and never prints "applied" for skipped namespaces.
     - 08-3-security.sh stays tolerant (no --require-all) while 14-gitops-bootstrap.sh waits
       boundedly for GitOps-created namespaces (dev, chaos-testing), fails loudly if the helper is
       missing, and invokes apply-psa-labels.sh --require-all.
     - dev-namespace.yaml, narwhal-db.yaml, and chaos-mesh.yaml stay in sync (audit/warn only).
  C. KISA-RBAC-02 (D-DEV-ROLE-NO-IMPERSONATE):
     - RoleBinding/dev/developer-dev-access binds Role/dev/developer-dev-access (old binding
       developer-full-access absent, no ClusterRole/edit binding anywhere).
     - Role/dev/developer-dev-access has no impersonate/escalate/bind, no serviceaccounts/token or
       proxy subresources, and no wildcards while retaining developer resources.
"""

import base64
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
    "containerd": "scripts/common/02-containerd.sh",
    "harbor_lib": "scripts/common/lib-harbor-ca.sh",
    "tls_routes": "scripts/cluster/08-6-tls-routes.sh",
    "harbor_node": "scripts/cluster/08-6b-harbor-containerd-tls-node.sh",
    "phase2": "scripts/cluster/06-phase2-start.sh",
    "join_worker": "scripts/cluster/02-join-worker.sh",
    "join_cp": "scripts/cluster/02-join-control-plane.sh",
    "kakao_dist": "scripts/cloud/distribute-join-kakao.sh",
    "kakao_prov": "scripts/cloud/provision-kakao.sh",
    "kakao_harbor": "scripts/cloud/apply-harbor-ca-kakao.sh",
    "mirrors": "scripts/airgap/06-configure-mirrors.sh",
    "psa_helper": "scripts/cluster/lib/apply-psa-labels.sh",
    "sec_script": "scripts/cluster/08-3-security.sh",
    "gitops_boot": "scripts/cluster/14-gitops-bootstrap.sh",
    "dev_ns": "gitops/resources/dev-namespace.yaml",
    "narwhal_db": "gitops/resources/narwhal-db.yaml",
    "chaos_mesh": "gitops/charts/narwhal-apps/templates/chaos-mesh.yaml",
    "rbac": "gitops/resources/rbac-policies.yaml",
    "rbac_app": "gitops/charts/narwhal-apps/templates/rbac-policies.yaml",
    "reg": "scripts/test/regression-check-kakao.sh",
}

EXPECTED_BASELINE = {
    "iam", "devtools", "monitoring", "database", "dev", "default", "cilium-secrets",
}
EXPECTED_PRIVILEGED = {
    "kube-system", "istio-system", "platform-system", "storage",
    "security-system", "nfs-quota-agent", "chaos-testing",
}
FORBIDDEN_VERBS = {"impersonate", "escalate", "bind", "*"}
FORBIDDEN_RESOURCES = {"serviceaccounts/token", "pods/proxy", "services/proxy", "*"}
REQUIRED_DEV_RESOURCES = {
    "pods", "pods/exec", "secrets", "persistentvolumeclaims", "configmaps",
    "services", "serviceaccounts", "deployments", "jobs", "ingresses", "networkpolicies",
}


def non_comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def check_static(texts: dict[str, str]) -> list[str]:
    errs: list[str] = []

    # --- Part A: KISA-IMG-01 ---
    ctr_active = "\n".join(non_comment_lines(texts["containerd"]))
    if "skip_verify" in ctr_active:
        errs.append("02-containerd.sh must not write skip_verify in non-comment lines")
    if "HARBOR_CERTS_DIR" in ctr_active:
        errs.append("02-containerd.sh must not pre-create harbor hosts.toml")
    if '"/etc/containerd/certs.d"' not in texts["containerd"]:
        errs.append("02-containerd.sh must keep config_path=/etc/containerd/certs.d")

    for k in ("harbor_node", "harbor_lib"):
        active = "\n".join(non_comment_lines(texts[k]))
        if re.search(r"skip_verify\s*=", active):
            errs.append(f"{FILES[k]} must never emit skip_verify")

    # Join scripts must source the shared library rather than duplicating sync_harbor_containerd_ca.
    for key in ("join_worker", "join_cp"):
        if "sync_harbor_containerd_ca() {" in texts[key]:
            errs.append(f"{FILES[key]} must not duplicate sync_harbor_containerd_ca(); source lib-harbor-ca.sh")
        if "/scripts/common/lib-harbor-ca.sh" not in texts[key] or 'source "${HARBOR_CA_LIB}"' not in texts[key]:
            errs.append(f"{FILES[key]} must resolve and source guest path /scripts/common/lib-harbor-ca.sh")
        if "sync_harbor_containerd_ca " not in texts[key]:
            errs.append(f"{FILES[key]} must invoke sync_harbor_containerd_ca")

    if "harbor." in "\n".join(non_comment_lines(texts["mirrors"])):
        errs.append("06-configure-mirrors.sh must remain scoped to external airgap mirrors, not harbor.<domain>")

    if "stage_harbor_ca" not in texts["kakao_prov"] or "apply-harbor-ca-kakao.sh" not in texts["kakao_prov"]:
        errs.append("provision-kakao.sh must wire apply-harbor-ca-kakao.sh in phase2 and harbor-ca stage")
    if "validate_harbor_ca_bundle" not in texts["kakao_dist"] or "probe_remote_harbor_ca" not in texts["kakao_dist"]:
        errs.append("distribute-join-kakao.sh must use probe_remote_harbor_ca and validate_harbor_ca_bundle")

    # --- Part B: KISA-POD-01 ---
    psa = texts["psa_helper"]
    if "pod-security.kubernetes.io/enforce" in psa:
        errs.append("apply-psa-labels.sh must NOT set pod-security.kubernetes.io/enforce")
    loops = {}
    for m in re.finditer(r"for ns in ([^;]+); do(.*?)done", psa, re.S):
        names = set(m.group(1).split())
        vals = dict(re.findall(r"pod-security\.kubernetes\.io/(enforce|audit|warn)=([a-z]+)", m.group(2)))
        if vals == {"audit": "baseline", "warn": "baseline"}:
            loops["baseline"] = names
        elif vals == {"audit": "privileged", "warn": "privileged"}:
            loops["privileged"] = names
    if loops.get("baseline") != EXPECTED_BASELINE:
        errs.append(f"apply-psa-labels.sh baseline set mismatch: {loops.get('baseline')!r}")
    if loops.get("privileged") != EXPECTED_PRIVILEGED:
        errs.append(f"apply-psa-labels.sh privileged set mismatch: {loops.get('privileged')!r}")
    for key in ("sec_script", "gitops_boot"):
        if 'PSA_HELPER="/home/vagrant/scripts/cluster/lib/apply-psa-labels.sh"' not in texts[key]:
            errs.append(f"{FILES[key]} must invoke /home/vagrant/scripts/cluster/lib/apply-psa-labels.sh")
    if '--require-all' in "\n".join(non_comment_lines(texts["sec_script"])):
        errs.append("08-3-security.sh early PSA pass must stay tolerant (must not pass --require-all)")
    if 'bash "${PSA_HELPER}" --require-all' not in texts["gitops_boot"]:
        errs.append("14-gitops-bootstrap.sh final PSA pass must invoke apply-psa-labels.sh with --require-all")
    if '"14-gitops-bootstrap.sh"' not in texts["phase2"]:
        errs.append("06-phase2-start.sh must keep 14-gitops-bootstrap.sh in CRITICAL_SCRIPTS so strict PSA failure aborts Phase 2")

    for key, ns_name, level in (("dev_ns", "dev", "baseline"), ("narwhal_db", "database", "baseline")):
        docs = [d for d in yaml.safe_load_all(texts[key]) if isinstance(d, dict) and d.get("kind") == "Namespace"]
        match = [d for d in docs if (d.get("metadata") or {}).get("name") == ns_name]
        if len(match) != 1:
            errs.append(f"{FILES[key]} must declare Namespace/{ns_name}")
        else:
            lbls = (match[0].get("metadata") or {}).get("labels") or {}
            if lbls.get("pod-security.kubernetes.io/audit") != level or lbls.get("pod-security.kubernetes.io/warn") != level:
                errs.append(f"{FILES[key]} Namespace/{ns_name} must set audit/warn={level}")
            if "pod-security.kubernetes.io/enforce" in lbls:
                errs.append(f"{FILES[key]} Namespace/{ns_name} must not set enforce")

    chaos_doc = yaml.safe_load(texts["chaos_mesh"])
    cm_labels = (
        ((chaos_doc or {}).get("spec") or {})
        .get("syncPolicy", {})
        .get("managedNamespaceMetadata", {})
        .get("labels", {})
    )
    if cm_labels.get("pod-security.kubernetes.io/audit") != "privileged" or cm_labels.get("pod-security.kubernetes.io/warn") != "privileged":
        errs.append("chaos-mesh.yaml managedNamespaceMetadata.labels must set audit/warn=privileged")
    if "pod-security.kubernetes.io/enforce" in cm_labels:
        errs.append("chaos-mesh.yaml must not set pod-security.kubernetes.io/enforce")

    # --- Part C: KISA-RBAC-02 ---
    rbac_app = yaml.safe_load(re.sub(r"\{\{[^}]+\}\}", "placeholder", texts["rbac_app"])) or {}
    prune_on = (((rbac_app.get("spec") or {}).get("syncPolicy") or {}).get("automated") or {}).get("prune")
    if prune_on is not True:
        errs.append("rbac-policies ArgoCD Application must keep automated.prune: true for legacy binding cleanup")

    rbac_docs = [d for d in yaml.safe_load_all(texts["rbac"]) if isinstance(d, dict)]
    legacy_rbs = [
        d for d in rbac_docs
        if d.get("kind") == "RoleBinding"
        and (d.get("metadata") or {}).get("name") == "developer-full-access"
        and (d.get("metadata") or {}).get("namespace") == "dev"
    ]
    if legacy_rbs:
        errs.append("rbac-policies.yaml must remove legacy RoleBinding/dev/developer-full-access (immutable roleRef)")

    dev_roles = [
        d for d in rbac_docs
        if d.get("kind") == "Role"
        and (d.get("metadata") or {}).get("name") == "developer-dev-access"
        and (d.get("metadata") or {}).get("namespace") == "dev"
    ]
    dev_rbs = [
        d for d in rbac_docs
        if d.get("kind") == "RoleBinding"
        and (d.get("metadata") or {}).get("name") == "developer-dev-access"
        and (d.get("metadata") or {}).get("namespace") == "dev"
    ]
    if len(dev_roles) != 1:
        errs.append("rbac-policies.yaml must define Role/dev/developer-dev-access")
    if len(dev_rbs) != 1:
        errs.append("rbac-policies.yaml must define RoleBinding/dev/developer-dev-access")
    else:
        rr = dev_rbs[0].get("roleRef") or {}
        if rr.get("kind") != "Role" or rr.get("name") != "developer-dev-access":
            errs.append(f"RoleBinding/dev/developer-dev-access must bind Role/developer-dev-access, got {rr!r}")
        subs = dev_rbs[0].get("subjects") or []
        if not any(s.get("kind") == "Group" and s.get("name") == "oidc:developer" for s in subs):
            errs.append("RoleBinding/dev/developer-dev-access must bind Group oidc:developer")

    for d in rbac_docs:
        if d.get("kind") in {"RoleBinding", "ClusterRoleBinding"}:
            rr = d.get("roleRef") or {}
            if rr.get("kind") == "ClusterRole" and rr.get("name") == "edit":
                errs.append(f"{d.get('kind')}/{(d.get('metadata') or {}).get('name')} must not bind built-in ClusterRole/edit")

    if len(dev_roles) == 1:
        seen_resources = set()
        for rule in dev_roles[0].get("rules") or []:
            groups = set(rule.get("apiGroups") or [])
            resources = set(rule.get("resources") or [])
            verbs = {v.lower() for v in (rule.get("verbs") or [])}
            if "*" in groups:
                errs.append("Role/dev/developer-dev-access must not use wildcard apiGroups")
            bad_v = verbs & FORBIDDEN_VERBS
            if bad_v:
                errs.append(f"Role/dev/developer-dev-access has forbidden verb(s): {sorted(bad_v)}")
            bad_r = resources & FORBIDDEN_RESOURCES
            if bad_r:
                errs.append(f"Role/dev/developer-dev-access has forbidden resource(s): {sorted(bad_r)}")
            seen_resources.update(resources)
        missing_res = REQUIRED_DEV_RESOURCES - seen_resources
        if missing_res:
            errs.append(f"Role/dev/developer-dev-access missing required dev resources: {sorted(missing_res)}")

    if "check R386 " not in texts["reg"] or "check-kisa-node-trust-psa-rbac.py --mutation-verify" not in texts["reg"]:
        errs.append("regression-check-kakao.sh must wire R386 with --mutation-verify")

    return errs


def generate_cert(dest: Path, cn: str, not_before: str | None = None, not_after: str | None = None, keep_key: bool = False) -> Path | None:
    key_path = dest.with_suffix(".key")
    cmd = [
        "openssl", "req", "-x509", "-newkey", "ec",
        "-pkeyopt", "ec_paramgen_curve:prime256v1",
        "-keyout", str(key_path),
        "-out", str(dest),
        "-nodes",
        "-subj", f"/CN={cn}",
    ]
    if not_before and not_after:
        cmd.extend(["-not_before", not_before, "-not_after", not_after])
    else:
        cmd.extend(["-days", "30"])
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    if keep_key:
        return key_path
    key_path.unlink(missing_ok=True)
    return None


def _extract_tls_prop_block(tls_text: str) -> str:
    start_marker = "# Configure containerd TLS trust for Harbor (KISA-IMG-01, D-HARBOR-NODE-CA)"
    end_marker = 'echo "=== TLS Routes and APISIX Routes Installation Complete ==="'
    assert start_marker in tls_text and end_marker in tls_text, "08-6-tls-routes.sh missing Harbor CA block markers"
    return tls_text[tls_text.index(start_marker):tls_text.index(end_marker)]


def _extract_phase2_harbor_block(p2_text: str) -> str:
    start_marker = "for script in "
    end_marker = "# Check OpenBao encrypted snapshot backup activation"
    assert start_marker in p2_text and end_marker in p2_text, "06-phase2-start.sh missing expected loop/Harbor markers"
    return p2_text[p2_text.index(start_marker):p2_text.index(end_marker)]


def _extract_gitops_psa_block(gitops_text: str) -> str:
    start_marker = "# D-PSA-LABELS-14 (KISA-POD-01): strict final PSA convergence pass (--require-all)."
    end_marker = 'echo "=== GitOps Bootstrap Done ==="'
    assert start_marker in gitops_text and end_marker in gitops_text, "14-gitops-bootstrap.sh missing PSA convergence block markers"
    return gitops_text[gitops_text.index(start_marker):gitops_text.index(end_marker)]


def check_behavior(texts: dict[str, str]) -> list[str]:
    errs: list[str] = []
    shells = ["/bin/bash"]
    default_bash = shutil.which("bash")
    if default_bash and os.path.realpath(default_bash) != os.path.realpath("/bin/bash"):
        shells.append(default_bash)

    inherited_path = os.environ.get("PATH", "/usr/bin:/bin")

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        ca1 = t / "ca1.crt"
        ca2 = t / "ca2.crt"
        expired_ca = t / "expired.crt"
        future_ca = t / "future.crt"
        key1 = generate_cert(ca1, "Narwhal Test CA 1", keep_key=True)
        generate_cert(ca2, "Narwhal Test CA 2")
        generate_cert(expired_ca, "Narwhal Expired CA", "20190101000000Z", "20200101000000Z")
        generate_cert(future_ca, "Narwhal Future CA", "20980101000000Z", "20990101000000Z")
        assert key1 is not None

        for idx, sh in enumerate(shells):
            run_dir = t / f"run-{idx}"
            run_dir.mkdir()
            stage_dir = run_dir / "stage"
            (stage_dir / "scripts/common").mkdir(parents=True)
            (stage_dir / "scripts/cluster/lib").mkdir(parents=True)
            (stage_dir / "scripts/cloud").mkdir(parents=True)

            lib_path = stage_dir / "scripts/common/lib-harbor-ca.sh"
            lib_path.write_text(texts["harbor_lib"], encoding="utf-8")
            lib_path.chmod(0o755)

            node_script = stage_dir / "scripts/cluster/08-6b-harbor-containerd-tls-node.sh"
            node_script.write_text(texts["harbor_node"], encoding="utf-8")
            node_script.chmod(0o755)

            psa_script = stage_dir / "scripts/cluster/lib/apply-psa-labels.sh"
            psa_script.write_text(texts["psa_helper"], encoding="utf-8")
            psa_script.chmod(0o755)

            certs_dir = run_dir / "certs.d"
            ctr_cfg = run_dir / "config.toml"
            ctr_cfg.write_text('config_path = "/etc/containerd/certs.d"\n', encoding="utf-8")
            fake_bin = run_dir / "bin"
            fake_bin.mkdir()
            base_env = {
                "PATH": f"{fake_bin}:{inherited_path}",
                "HOME": str(run_dir),
                "STAGE_DIR": str(stage_dir),
                "CERTS_DIR": str(certs_dir),
                "CONTAINERD_CONFIG": str(ctr_cfg),
                "SUDO": "",
                "TMPDIR": str(run_dir),
            }

            # 1) Legacy skip_verify hosts.toml is migrated to verified CA1 + 0644 ca.crt
            legacy_dir = certs_dir / "harbor.test.internal"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "hosts.toml").write_text(
                'server = "https://harbor.test.internal"\n\n'
                '[host."https://harbor.test.internal"]\n'
                '  capabilities = ["pull", "resolve"]\n'
                '  skip_verify = true\n',
                encoding="utf-8",
            )
            p = subprocess.run(
                [sh, str(node_script), str(ca1), "test.internal"],
                env=base_env, capture_output=True, text=True, timeout=30,
            )
            if p.returncode != 0:
                errs.append(f"[{sh}] 08-6b migration failed: {p.stderr}")
                continue
            hosts_text = (legacy_dir / "hosts.toml").read_text(encoding="utf-8")
            ca_path = legacy_dir / "ca.crt"
            if "skip_verify" in hosts_text or f'ca = "{ca_path}"' not in hosts_text:
                errs.append(f"[{sh}] hosts.toml migration wrong: {hosts_text!r}")
            if not ca_path.exists() or ca_path.read_text(encoding="utf-8") != ca1.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] ca.crt does not match CA1")
            if stat.S_IMODE(ca_path.stat().st_mode) != 0o644:
                errs.append(f"[{sh}] ca.crt mode expected 0644, got {oct(stat.S_IMODE(ca_path.stat().st_mode))}")

            # 2) Idempotent re-run & CA rotation to CA2 & multi-cert valid bundle
            p2 = subprocess.run(
                [sh, str(node_script), str(ca1), "test.internal"],
                env=base_env, capture_output=True, text=True, timeout=30,
            )
            if p2.returncode != 0 or "already up to date" not in p2.stdout:
                errs.append(f"[{sh}] 08-6b re-run not idempotent: rc={p2.returncode} out={p2.stdout!r}")

            bundle_ok = run_dir / "bundle-ok.crt"
            bundle_ok.write_text(ca1.read_text() + "\n" + ca2.read_text(), encoding="utf-8")
            pb_ok = subprocess.run(
                [sh, str(node_script), str(bundle_ok), "test.internal"],
                env=base_env, capture_output=True, text=True, timeout=30,
            )
            if pb_ok.returncode != 0:
                errs.append(f"[{sh}] 08-6b rejected valid multi-cert bundle: {pb_ok.stderr}")

            p3 = subprocess.run(
                [sh, str(node_script), str(ca2), "test.internal"],
                env=base_env, capture_output=True, text=True, timeout=30,
            )
            if p3.returncode != 0 or "ca_changed=1" not in p3.stdout or ca_path.read_text(encoding="utf-8") != ca2.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] 08-6b CA rotation to CA2 failed: rc={p3.returncode} out={p3.stdout!r}")

            # 3) Bad CA fixtures must fail closed without clobbering CA2
            empty_f = run_dir / "empty.crt"
            empty_f.write_text("", encoding="utf-8")
            corrupt_f = run_dir / "corrupt.crt"
            corrupt_f.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", encoding="utf-8")
            cert_plus_key = run_dir / "cert-plus-key.crt"
            cert_plus_key.write_text(ca1.read_text() + "\n" + key1.read_text(), encoding="utf-8")
            cert_plus_garbage = run_dir / "cert-plus-garbage.crt"
            cert_plus_garbage.write_text(ca1.read_text() + "\nTRAILING_GARBAGE_NOT_PEM\n", encoding="utf-8")
            valid_plus_expired = run_dir / "valid-plus-expired.crt"
            valid_plus_expired.write_text(ca1.read_text() + "\n" + expired_ca.read_text(), encoding="utf-8")
            unclosed_plus_garbage = run_dir / "smuggled.crt"
            unclosed_plus_garbage.write_text(
                ca1.read_text().replace("-----END CERTIFICATE-----", "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END CERTIFICATE-----"),
                encoding="utf-8",
            )

            for bad_label, bad_args in (
                ("empty CA", [str(empty_f), "test.internal"]),
                ("expired CA", [str(expired_ca), "test.internal"]),
                ("not-yet-valid CA", [str(future_ca), "test.internal"]),
                ("cert + trailing private key", [str(cert_plus_key), "test.internal"]),
                ("garbage after valid cert", [str(cert_plus_garbage), "test.internal"]),
                ("valid + expired sibling", [str(valid_plus_expired), "test.internal"]),
                ("smuggled header in cert block", [str(unclosed_plus_garbage), "test.internal"]),
                ("corrupt PEM", [str(corrupt_f), "test.internal"]),
                ("invalid domain", [str(ca1), "bad;domain"]),
            ):
                pb = subprocess.run(
                    [sh, str(node_script), *bad_args],
                    env=base_env, capture_output=True, text=True, timeout=30,
                )
                if pb.returncode == 0:
                    errs.append(f"[{sh}] 08-6b accepted {bad_label}")
            if ca_path.read_text(encoding="utf-8") != ca2.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] 08-6b clobbered CA2 after bad inputs")

            # 4) Standalone kubectl extraction mode
            ca1_b64 = base64.b64encode(ca1.read_bytes()).decode("ascii")
            fake_kubectl = fake_bin / "kubectl"
            fake_kubectl.write_text(
                "#!/bin/sh\n"
                'if [ "${FAKE_EMPTY_CA:-0}" = "1" ]; then exit 0; fi\n'
                f'printf "%s" "{ca1_b64}"\n',
                encoding="utf-8",
            )
            fake_kubectl.chmod(0o755)
            pk = subprocess.run(
                [sh, str(node_script)],
                env={**base_env, "DOMAIN": "test.internal"},
                capture_output=True, text=True, timeout=30,
            )
            if pk.returncode != 0 or ca_path.read_text(encoding="utf-8") != ca1.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] 08-6b standalone kubectl mode failed: rc={pk.returncode} err={pk.stderr!r}")
            pk_empty = subprocess.run(
                [sh, str(node_script)],
                env={**base_env, "DOMAIN": "test.internal", "FAKE_EMPTY_CA": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if pk_empty.returncode == 0:
                errs.append(f"[{sh}] 08-6b standalone empty-secret mode must fail closed")

            # 5) Shared lib sync_harbor_containerd_ca: SSH failure vs HARBOR_CA_ABSENT vs HARBOR_CA_PRESENT vs unexpected output
            (fake_bin / "sudo").write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
            (fake_bin / "sudo").chmod(0o755)
            (fake_bin / "sshpass").write_text(
                "#!/bin/sh\n"
                'shift 2\n'
                'sub="$1"; shift\n'
                'if [ "$sub" = "ssh" ]; then\n'
                '  if [ "${FAKE_SSH_FAIL:-0}" = "1" ]; then echo "ssh: connect to host timed out" >&2; exit 255; fi\n'
                '  if [ "${FAKE_SSH_UNEXPECTED:-0}" = "1" ]; then echo "Welcome to Ubuntu"; exit 0; fi\n'
                '  if [ "${MASTER_HAS_CA:-0}" = "1" ]; then printf "HARBOR_CA_PRESENT\\n"; else printf "HARBOR_CA_ABSENT\\n"; fi\n'
                '  exit 0\n'
                'fi\n'
                'if [ "$sub" = "scp" ]; then\n'
                '  if [ "${FAKE_SCP_FAIL:-0}" = "1" ]; then exit 1; fi\n'
                '  for last in "$@"; do dst="$last"; done\n'
                f'  cp "{ca1}" "$dst"\n'
                '  exit 0\n'
                'fi\n'
                'exit 1\n',
                encoding="utf-8",
            )
            (fake_bin / "sshpass").chmod(0o755)

            j_certs = run_dir / "jcerts"
            join_cmd = (
                f'set -euo pipefail\nsource "{lib_path}"\n'
                f'export CERTS_DIR="{j_certs}" CONTAINERD_CONFIG="{ctr_cfg}" SUDO=""\n'
                'sync_harbor_containerd_ca "192.168.56.10" "join.test"\n'
            )
            pj_absent = subprocess.run(
                [sh, "-c", join_cmd], env={**base_env, "MASTER_HAS_CA": "0"},
                capture_output=True, text=True, timeout=30,
            )
            if pj_absent.returncode != 0 or (j_certs / "harbor.join.test/hosts.toml").exists():
                errs.append(f"[{sh}] sync_harbor_containerd_ca ABSENT should skip cleanly: rc={pj_absent.returncode}")

            pj_ssh_fail = subprocess.run(
                [sh, "-c", join_cmd], env={**base_env, "FAKE_SSH_FAIL": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if pj_ssh_fail.returncode == 0 or "HARBOR_CA_ABSENT" in pj_ssh_fail.stdout or (j_certs / "harbor.join.test/hosts.toml").exists():
                errs.append(f"[{sh}] sync_harbor_containerd_ca SSH failure must fail loudly, got rc={pj_ssh_fail.returncode}")

            pj_unexp = subprocess.run(
                [sh, "-c", join_cmd], env={**base_env, "FAKE_SSH_UNEXPECTED": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if pj_unexp.returncode == 0 or (j_certs / "harbor.join.test/hosts.toml").exists():
                errs.append(f"[{sh}] sync_harbor_containerd_ca unexpected probe output must fail loudly, got rc={pj_unexp.returncode}")

            pj_present = subprocess.run(
                [sh, "-c", join_cmd], env={**base_env, "MASTER_HAS_CA": "1"},
                capture_output=True, text=True, timeout=30,
            )
            j_hosts = j_certs / "harbor.join.test/hosts.toml"
            if pj_present.returncode != 0 or not j_hosts.exists() or "skip_verify" in j_hosts.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] sync_harbor_containerd_ca PRESENT failed: rc={pj_present.returncode} err={pj_present.stderr!r}")

            # 6) 08-6-tls-routes.sh node enumeration failure & remote apply failure
            (fake_bin / "hostname").write_text('#!/bin/sh\necho "192.168.56.10"\n', encoding="utf-8")
            (fake_bin / "hostname").chmod(0o755)
            fake_kubectl.write_text(
                "#!/bin/sh\n"
                'if [ "${FAKE_NODES_FAIL:-0}" = "1" ]; then echo "192.168.56.10"; echo "The connection to the server was refused" >&2; exit 1; fi\n'
                'if [ "${FAKE_NODES_EMPTY:-0}" = "1" ]; then exit 0; fi\n'
                'printf "192.168.56.10\\n192.168.56.20\\n"\n',
                encoding="utf-8",
            )
            fake_kubectl.chmod(0o755)
            tls_block = _extract_tls_prop_block(texts["tls_routes"]).replace(
                "/home/vagrant/scripts/cluster/08-6b-harbor-containerd-tls-node.sh", str(node_script),
            )
            tls_runner = run_dir / "run-08-6-prop.sh"
            tls_runner.write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                f'DOMAIN="test.internal"\nCA_CERT="{ca1_b64}"\n'
                + tls_block,
                encoding="utf-8",
            )
            tls_runner.chmod(0o755)
            pt_nfail = subprocess.run(
                [sh, str(tls_runner)], env={**base_env, "FAKE_NODES_FAIL": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if pt_nfail.returncode == 0:
                errs.append(f"[{sh}] 08-6 must fail loudly when kubectl get nodes fails")
            pt_nempty = subprocess.run(
                [sh, str(tls_runner)], env={**base_env, "FAKE_NODES_EMPTY": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if pt_nempty.returncode == 0:
                errs.append(f"[{sh}] 08-6 must fail loudly when kubectl get nodes returns empty")
            pt_rfail = subprocess.run(
                [sh, str(tls_runner)], env={**base_env, "FAKE_SSH_FAIL": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if pt_rfail.returncode == 0:
                errs.append(f"[{sh}] 08-6 must fail loudly when remote node apply fails")

            # 7) 06-phase2-start.sh running from /tmp/.../vagrant-shell records 08-6/Harbor failures in PHASE2_DEGRADED
            p2_stage = run_dir / "p2-scripts"
            (p2_stage / "cluster").mkdir(parents=True)
            for sname in ("07-cnpg.sh", "08-1-networking.sh", "08-2-monitoring.sh", "08-3-security.sh",
                          "08-4-storage.sh", "08-5-registry.sh", "09-istio-ambient.sh", "10-dnsmasq.sh",
                          "11-keycloak.sh", "11-1-keycloak-admin.sh", "11-2-keycloak-config.sh",
                          "11-3-keycloak-clients.sh", "11-4-keycloak-apiserver.sh", "12-gitea.sh",
                          "13-argocd.sh", "13-2-narwhal-portal-bindings.sh", "14-gitops-bootstrap.sh",
                          "15-narwhal-portal.sh"):
                (p2_stage / "cluster" / sname).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (p2_stage / "cluster" / "08-6-tls-routes.sh").write_text(
                '#!/bin/sh\nexit "${FAKE_086_RC:-0}"\n', encoding="utf-8",
            )
            p2_degraded = run_dir / "phase2-degraded"
            p2_block = _extract_phase2_harbor_block(texts["phase2"]).replace(
                "/home/vagrant/scripts/common/lib-harbor-ca.sh", str(lib_path),
            )
            p2_tail = texts["phase2"][texts["phase2"].index('if [[ "${#DEGRADED_REASONS[@]}" -gt 0 ]]; then'):
                                      texts["phase2"].index('date -u')]
            vagrant_shell = run_dir / "vagrant-shell"  # NO sibling scripts/ dir
            vagrant_shell.write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                f'SCRIPT_DIR="{p2_stage}/cluster"\n'
                f'PHASE2_DEGRADED="{p2_degraded}"\n'
                'rm -f "${PHASE2_DEGRADED}"\n'
                'DOMAIN="test.internal"\n'
                'CRITICAL_SCRIPTS=("07-cnpg.sh" "14-gitops-bootstrap.sh")\n'
                'PORTAL_BINDINGS_DEGRADED_RC=3\n'
                'DEGRADED_REASONS=()\n'
                + p2_block + "\n" + p2_tail,
                encoding="utf-8",
            )
            vagrant_shell.chmod(0o755)
            p2_fail = subprocess.run(
                [sh, str(vagrant_shell)],
                env={**base_env, "FAKE_086_RC": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if p2_fail.returncode != 0 or not p2_degraded.exists() or "08-6-tls-routes.sh failed" not in p2_degraded.read_text():
                errs.append(f"[{sh}] 06-phase2-start.sh did not record 08-6 failure in PHASE2_DEGRADED: out={p2_fail.stdout!r}")
            p2_ok = subprocess.run(
                [sh, str(vagrant_shell)],
                env={**base_env, "FAKE_086_RC": "0"},
                capture_output=True, text=True, timeout=30,
            )
            if p2_ok.returncode != 0 or p2_degraded.exists():
                errs.append(f"[{sh}] 06-phase2-start.sh healthy run produced PHASE2_DEGRADED: {p2_degraded.read_text() if p2_degraded.exists() else ''}")

            # 8) Kakao apply-harbor-ca-kakao.sh: tofu/JSON errors, SSH transport failure, and multi-node success
            kakao_apply = stage_dir / "scripts/cloud/apply-harbor-ca-kakao.sh"
            kakao_apply.write_text(texts["kakao_harbor"], encoding="utf-8")
            kakao_apply.chmod(0o755)
            kakao_dist = stage_dir / "scripts/cloud/distribute-join-kakao.sh"
            kakao_dist.write_text(texts["kakao_dist"], encoding="utf-8")
            kakao_dist.chmod(0o755)
            (stage_dir / "scripts/cloud/lib-ssh.sh").write_text("KAKAO_SSH_OPTS=(-o BatchMode=yes)\n", encoding="utf-8")

            tf_dir = run_dir / "tf"
            tf_dir.mkdir()
            (tf_dir / "key.pem").write_text("dummy\n", encoding="utf-8")
            (fake_bin / "tofu").write_text(
                "#!/bin/sh\n"
                'if [ "$2" = "-raw" ] && [ "$3" = "bastion_public_ip" ]; then echo "1.2.3.4"; exit 0; fi\n'
                'if [ "$2" = "-raw" ] && [ "$3" = "ssh_key_path" ]; then echo "key.pem"; exit 0; fi\n'
                'if [ "$2" = "-json" ] && [ "$3" = "master_private_ips" ]; then\n'
                '  printf "%s\\n" "${FAKE_MASTERS_JSON:-[\\"10.0.0.10\\",\\"10.0.0.11\\"]}"\n'
                '  exit 0\n'
                'fi\n'
                'if [ "$2" = "-json" ] && [ "$3" = "worker_private_ips" ]; then\n'
                '  if [ "${FAKE_TOFU_FAIL:-0}" = "1" ]; then echo "Error: state locked" >&2; exit 1; fi\n'
                '  if [ "${FAKE_TOFU_BAD_JSON:-0}" = "1" ]; then echo "not-json"; exit 0; fi\n'
                '  printf "%s\\n" "${FAKE_WORKERS_JSON:-[\\"10.0.0.20\\"]}"\n'
                '  exit 0\n'
                'fi\n'
                'exit 1\n',
                encoding="utf-8",
            )
            (fake_bin / "tofu").chmod(0o755)

            kakao_nodes_dir = run_dir / "kakao-nodes"
            fake_ssh = fake_bin / "ssh"
            fake_ssh.write_text(
                "#!/bin/sh\n"
                'target=""; cmd=""\n'
                'while [ "$#" -gt 0 ]; do\n'
                '  case "$1" in\n'
                '    -i|-o) shift 2 ;;\n'
                '    *@*) target="${1#*@}"; shift; cmd="$*"; break ;;\n'
                '    *) shift ;;\n'
                '  esac\n'
                'done\n'
                'if [ "${FAKE_SSH_TARGET_FAIL:-}" = "$target" ]; then\n'
                '  echo "ssh: connect to host $target port 22: Connection timed out" >&2\n'
                '  exit 255\n'
                'fi\n'
                # Harbor CA probe on master-1 (supports both shared probe_remote_harbor_ca and mutated sudo test -s)
                'case "$cmd" in\n'
                '  *HARBOR_CA_PRESENT*|*"sudo test -s"*"/etc/containerd/certs.d/"*)\n'
                '    case "${FAKE_KAKAO_HARBOR_MODE:-present}" in\n'
                '      ssh_fail) echo "ssh: connect to host $target port 22: Connection timed out" >&2; exit 255 ;;\n'
                '      unexpected) echo "Welcome banner without sentinel"; exit 0 ;;\n'
                '      absent)\n'
                '        case "$cmd" in\n'
                '          *HARBOR_CA_PRESENT*) printf "HARBOR_CA_ABSENT\\n"; exit 0 ;;\n'
                '          *) exit 1 ;;\n'
                '        esac ;;\n'
                '      present)\n'
                '        case "$cmd" in\n'
                '          *HARBOR_CA_PRESENT*) printf "HARBOR_CA_PRESENT\\n"; exit 0 ;;\n'
                '          *) exit 0 ;;\n'
                '        esac ;;\n'
                '    esac ;;\n'
                '  *"sudo cat"*"/etc/containerd/certs.d/"*)\n'
                f'    if [ "${{FAKE_KAKAO_HARBOR_BAD:-0}}" = "1" ]; then cat "{expired_ca}"; else cat "{ca1}"; fi\n'
                '    exit 0 ;;\n'
                '  *"sudo cat /home/vagrant/"*)\n'
                '    echo "dummy-join-artifact"\n'
                '    exit 0 ;;\n'
                '  *"get cm kubeadm-config"*)\n'
                '    echo "apiVersion: kubeadm.k8s.io/v1beta4"\n'
                '    exit 0 ;;\n'
                '  *"sudo test -f"*)\n'
                '    exit 0 ;;\n'
                '  *"mktemp /tmp/harbor-ca.XXXXXX"*)\n'
                f'    ndir="{kakao_nodes_dir}/$target"; mkdir -p "$ndir"\n'
                '    cat > "$ndir/staged-ca.crt"\n'
                '    printf "/tmp/harbor-ca.123456"\n'
                '    exit 0 ;;\n'
                '  *"08-6b-harbor-containerd-tls-node.sh"*)\n'
                f'    ndir="{kakao_nodes_dir}/$target"; mkdir -p "$ndir/certs.d"\n'
                f'    CERTS_DIR="$ndir/certs.d" CONTAINERD_CONFIG="{ctr_cfg}" SUDO="" "{sh}" "{node_script}" "$ndir/staged-ca.crt" "${{DOMAIN:-kakao.test}}"\n'
                '    exit $? ;;\n'
                '  *"cat >"*)\n'
                '    dest=$(printf "%s" "$cmd" | sed -n "s/.*cat > \'\([^\']*\)\'.*/\\1/p")\n'
                f'    ndir="{kakao_nodes_dir}/$target"; mkdir -p "$ndir$(dirname "$dest")"\n'
                '    cat > "$ndir$dest"\n'
                '    exit 0 ;;\n'
                '  *"ls -l "*)\n'
                '    echo "    42 /tmp/ok"\n'
                '    exit 0 ;;\n'
                'esac\n'
                'echo "UNEXPECTED SSH CMD on $target: $cmd" >&2\n'
                'exit 1\n',
                encoding="utf-8",
            )
            fake_ssh.chmod(0o755)

            for klabel, kenv in (
                ("tofu error", {"FAKE_TOFU_FAIL": "1"}),
                ("malformed JSON", {"FAKE_TOFU_BAD_JSON": "1"}),
                ("SSH transport failure on target", {"FAKE_SSH_TARGET_FAIL": "10.0.0.20"}),
            ):
                pk_fail = subprocess.run(
                    [sh, str(kakao_apply)],
                    env={**base_env, **kenv, "TF_DIR": str(tf_dir), "DOMAIN": "kakao.test"},
                    capture_output=True, text=True, timeout=30,
                )
                if pk_fail.returncode == 0:
                    errs.append(f"[{sh}] apply-harbor-ca-kakao.sh swallowed {klabel}")

            if kakao_nodes_dir.exists():
                shutil.rmtree(kakao_nodes_dir)
            pk_ok = subprocess.run(
                [sh, str(kakao_apply)],
                env={**base_env, "TF_DIR": str(tf_dir), "DOMAIN": "kakao.test"},
                capture_output=True, text=True, timeout=30,
            )
            if pk_ok.returncode != 0:
                errs.append(f"[{sh}] apply-harbor-ca-kakao.sh success fixture failed: rc={pk_ok.returncode} err={pk_ok.stderr!r}")
            else:
                for target_ip in ("10.0.0.11", "10.0.0.20"):
                    t_ca = kakao_nodes_dir / target_ip / "certs.d/harbor.kakao.test/ca.crt"
                    t_hosts = kakao_nodes_dir / target_ip / "certs.d/harbor.kakao.test/hosts.toml"
                    if not t_ca.exists() or t_ca.read_text(encoding="utf-8") != ca1.read_text(encoding="utf-8"):
                        errs.append(f"[{sh}] apply-harbor-ca-kakao.sh did not install CA on {target_ip}")
                    if not t_hosts.exists() or "skip_verify" in t_hosts.read_text(encoding="utf-8"):
                        errs.append(f"[{sh}] apply-harbor-ca-kakao.sh hosts.toml invalid on {target_ip}")

            # 8b) Kakao distribute-join-kakao.sh: SSH rc 255 fails, unexpected output fails,
            #     HARBOR_CA_ABSENT continues without CA, HARBOR_CA_PRESENT delivers CA, bad CA fails
            for mode_label, kenv in (
                ("SSH rc 255 during Harbor CA probe", {"FAKE_KAKAO_HARBOR_MODE": "ssh_fail"}),
                ("unexpected Harbor CA probe output", {"FAKE_KAKAO_HARBOR_MODE": "unexpected"}),
                ("expired Harbor CA on master-1", {"FAKE_KAKAO_HARBOR_MODE": "present", "FAKE_KAKAO_HARBOR_BAD": "1"}),
            ):
                if kakao_nodes_dir.exists():
                    shutil.rmtree(kakao_nodes_dir)
                pd_fail = subprocess.run(
                    [sh, str(kakao_dist)],
                    env={**base_env, **kenv, "TF_DIR": str(tf_dir), "DOMAIN": "kakao.test"},
                    capture_output=True, text=True, timeout=30,
                )
                if pd_fail.returncode == 0:
                    errs.append(f"[{sh}] distribute-join-kakao.sh must fail on {mode_label}")
                if kakao_nodes_dir.exists():
                    errs.append(f"[{sh}] distribute-join-kakao.sh distributed files after {mode_label}")

            # HARBOR_CA_ABSENT ("not issued yet") -> continues and distributes join files without harbor-ca.crt
            if kakao_nodes_dir.exists():
                shutil.rmtree(kakao_nodes_dir)
            pd_abs = subprocess.run(
                [sh, str(kakao_dist)],
                env={**base_env, "FAKE_KAKAO_HARBOR_MODE": "absent", "TF_DIR": str(tf_dir), "DOMAIN": "kakao.test"},
                capture_output=True, text=True, timeout=30,
            )
            if pd_abs.returncode != 0:
                errs.append(f"[{sh}] distribute-join-kakao.sh ABSENT should continue cleanly: rc={pd_abs.returncode} err={pd_abs.stderr!r}")
            else:
                if not (kakao_nodes_dir / "10.0.0.11/tmp/join-control-plane.sh").exists() or not (kakao_nodes_dir / "10.0.0.20/tmp/join-command.sh").exists():
                    errs.append(f"[{sh}] distribute-join-kakao.sh ABSENT did not deliver join artifacts")
                if (kakao_nodes_dir / "10.0.0.11/tmp/harbor-ca.crt").exists() or (kakao_nodes_dir / "10.0.0.20/tmp/harbor-ca.crt").exists():
                    errs.append(f"[{sh}] distribute-join-kakao.sh ABSENT unexpectedly staged harbor-ca.crt")

            # HARBOR_CA_PRESENT -> delivers validated harbor-ca.crt to control-plane and worker targets
            if kakao_nodes_dir.exists():
                shutil.rmtree(kakao_nodes_dir)
            pd_ok = subprocess.run(
                [sh, str(kakao_dist)],
                env={**base_env, "FAKE_KAKAO_HARBOR_MODE": "present", "TF_DIR": str(tf_dir), "DOMAIN": "kakao.test"},
                capture_output=True, text=True, timeout=30,
            )
            if pd_ok.returncode != 0:
                errs.append(f"[{sh}] distribute-join-kakao.sh PRESENT failed: rc={pd_ok.returncode} err={pd_ok.stderr!r}")
            else:
                for target_ip in ("10.0.0.11", "10.0.0.20"):
                    staged = kakao_nodes_dir / target_ip / "tmp/harbor-ca.crt"
                    if not staged.exists() or staged.read_text(encoding="utf-8") != ca1.read_text(encoding="utf-8"):
                        errs.append(f"[{sh}] distribute-join-kakao.sh PRESENT did not deliver harbor-ca.crt to {target_ip}")

            # 9) apply-psa-labels.sh: exact per-ns NotFound vs exec-plugin "not found" / Forbidden / timeout / wrong-ns NotFound
            psa_log = run_dir / "psa.log"
            fake_kubectl.write_text(
                "#!/bin/sh\n"
                'cmd="$1"; shift\n'
                'if [ "$cmd" = "get" ] && { [ "$1" = "namespace" ] || [ "$1" = "ns" ]; }; then\n'
                '  ns="$2"\n'
                '  if [ "${FAKE_EXEC_PLUGIN_NOT_FOUND:-0}" = "1" ]; then\n'
                '    echo "Unable to connect to the server: getting credentials: exec: executable oidc-login not found" >&2\n'
                '    exit 1\n'
                '  fi\n'
                '  if [ "${FAKE_API_TIMEOUT:-0}" = "1" ]; then\n'
                '    echo "Unable to connect to the server: dial tcp 192.168.56.10:6443: i/o timeout" >&2\n'
                '    exit 1\n'
                '  fi\n'
                '  if [ "${FAKE_WRONG_NS_NOT_FOUND:-}" = "$ns" ]; then\n'
                '    echo "Error from server (NotFound): namespaces \\"other-ns\\" not found" >&2\n'
                '    exit 1\n'
                '  fi\n'
                '  if [ "${FAKE_FORBIDDEN_NS:-}" = "$ns" ]; then\n'
                '    echo "Error from server (Forbidden): namespaces \\"$ns\\" is forbidden" >&2\n'
                '    exit 1\n'
                '  fi\n'
                '  if [ -n "${FAKE_APPEAR_FILE:-}" ] && [ "$ns" = "chaos-testing" ]; then\n'
                '    c=$(cat "${FAKE_APPEAR_FILE}" 2>/dev/null || echo 0)\n'
                '    c=$((c + 1))\n'
                '    printf "%s" "$c" > "${FAKE_APPEAR_FILE}"\n'
                '    if [ "$c" -lt 2 ]; then\n'
                '      echo "Error from server (NotFound): namespaces \\"$ns\\" not found" >&2\n'
                '      exit 1\n'
                '    fi\n'
                '    case " $* " in\n'
                '      *-o\\ jsonpath=*|*-ojsonpath=*)\n'
                f'        if grep -q "namespace chaos-testing " "{psa_log}" 2>/dev/null; then printf "privileged:privileged"; else printf ":"; fi\n'
                '        exit 0 ;;\n'
                '      *) printf "namespace/%s\\n" "$ns"; exit 0 ;;\n'
                '    esac\n'
                '  fi\n'
                '  case " ${EXISTING_NS:-} " in\n'
                '    *" $ns "*)\n'
                '      case " $* " in\n'
                '        *-o\\ jsonpath=*|*-ojsonpath=*)\n'
                '          if [ "${FAKE_MISMATCH_NS:-}" = "$ns" ]; then printf ":"; exit 0; fi\n'
                '          case " iam devtools monitoring database dev default cilium-secrets " in\n'
                '            *" $ns "*) printf "baseline:baseline" ;;\n'
                '            *) printf "privileged:privileged" ;;\n'
                '          esac\n'
                '          exit 0 ;;\n'
                '        *) printf "namespace/%s\\n" "$ns"; exit 0 ;;\n'
                '      esac ;;\n'
                '    *) echo "Error from server (NotFound): namespaces \\"$ns\\" not found" >&2; exit 1 ;;\n'
                '  esac\n'
                'fi\n'
                'if [ "$cmd" = "label" ] && { [ "$1" = "namespace" ] || [ "$1" = "ns" ]; }; then\n'
                f'  printf "%s\\n" "$*" >> "{psa_log}"\n'
                '  exit 0\n'
                'fi\n'
                'exit 1\n',
                encoding="utf-8",
            )
            fake_kubectl.chmod(0o755)
            partial_ns = "devtools monitoring database default cilium-secrets kube-system platform-system storage nfs-quota-agent"
            psa_log.write_text("", encoding="utf-8")
            pp1 = subprocess.run(
                [sh, str(psa_script)], env={**base_env, "EXISTING_NS": partial_ns},
                capture_output=True, text=True, timeout=30,
            )
            if pp1.returncode != 0:
                errs.append(f"[{sh}] apply-psa-labels.sh partial run failed: {pp1.stderr}")
            if "9/14" not in pp1.stdout or "5/14 namespaces do not exist yet" not in pp1.stdout:
                errs.append(f"[{sh}] apply-psa-labels.sh partial convergence output missing counts: {pp1.stdout!r}")
            for skipped in ("iam", "istio-system", "security-system", "dev", "chaos-testing"):
                for line in pp1.stdout.splitlines():
                    if "applied" in line and re.search(rf"\b{re.escape(skipped)}\b", line):
                        errs.append(f"[{sh}] apply-psa-labels.sh printed 'applied' for skipped ns {skipped}: {line!r}")

            pp_req_partial = subprocess.run(
                [sh, str(psa_script), "--require-all"], env={**base_env, "EXISTING_NS": partial_ns},
                capture_output=True, text=True, timeout=30,
            )
            if pp_req_partial.returncode == 0:
                errs.append(f"[{sh}] apply-psa-labels.sh --require-all must fail when namespaces are missing")

            all_14 = " ".join(sorted(EXPECTED_BASELINE | EXPECTED_PRIVILEGED))
            psa_log.write_text("", encoding="utf-8")
            pp2 = subprocess.run(
                [sh, str(psa_script), "--require-all"], env={**base_env, "EXISTING_NS": all_14},
                capture_output=True, text=True, timeout=30,
            )
            if pp2.returncode != 0 or "14/14" not in pp2.stdout:
                errs.append(f"[{sh}] apply-psa-labels.sh 14/14 --require-all run failed: rc={pp2.returncode} out={pp2.stdout!r}")
            applied = {}
            for line in psa_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                if "pod-security.kubernetes.io/enforce" in line:
                    errs.append(f"[{sh}] apply-psa-labels.sh set enforce: {line}")
                ns_name = line.split()[1]
                audit_m = re.search(r"pod-security\.kubernetes\.io/audit=(\w+)", line)
                warn_m = re.search(r"pod-security\.kubernetes\.io/warn=(\w+)", line)
                if audit_m and warn_m:
                    applied[ns_name] = (audit_m.group(1), warn_m.group(1))
            expected_map = {n: ("baseline", "baseline") for n in EXPECTED_BASELINE}
            expected_map.update({n: ("privileged", "privileged") for n in EXPECTED_PRIVILEGED})
            if applied != expected_map:
                errs.append(f"[{sh}] apply-psa-labels.sh 14/14 label map mismatch: {applied!r}")

            for err_label, err_env in (
                ("exec-plugin 'not found' text", {"FAKE_EXEC_PLUGIN_NOT_FOUND": "1"}),
                ("Forbidden error", {"FAKE_FORBIDDEN_NS": "iam"}),
                ("API timeout", {"FAKE_API_TIMEOUT": "1"}),
                ("wrong-namespace NotFound", {"FAKE_WRONG_NS_NOT_FOUND": "iam"}),
                ("convergence mismatch", {"FAKE_MISMATCH_NS": "dev"}),
            ):
                pp_err = subprocess.run(
                    [sh, str(psa_script)], env={**base_env, "EXISTING_NS": all_14, **err_env},
                    capture_output=True, text=True, timeout=30,
                )
                if pp_err.returncode == 0:
                    errs.append(f"[{sh}] apply-psa-labels.sh swallowed {err_label}")

            # 10) 14-gitops-bootstrap.sh bounded wait for GitOps namespaces + strict --require-all
            gitops_psa_block = _extract_gitops_psa_block(texts["gitops_boot"]).replace(
                "/home/vagrant/scripts/cluster/lib/apply-psa-labels.sh", str(psa_script),
            )
            gitops_runner = run_dir / "run-14-psa.sh"
            gitops_runner.write_text(
                "#!/bin/bash\nset -euo pipefail\n" + gitops_psa_block,
                encoding="utf-8",
            )
            gitops_runner.chmod(0o755)

            # Case A: chaos-testing appears on poll 2 -> bounded wait converges & --require-all succeeds 14/14
            appear_file = run_dir / "appear-count"
            appear_file.write_text("0", encoding="utf-8")
            psa_log.write_text("", encoding="utf-8")
            pg_wait_ok = subprocess.run(
                [sh, str(gitops_runner)],
                env={
                    **base_env,
                    "EXISTING_NS": all_14,
                    "FAKE_APPEAR_FILE": str(appear_file),
                    "PSA_NS_WAIT_TIMEOUT": "5",
                    "PSA_NS_WAIT_INTERVAL": "1",
                },
                capture_output=True, text=True, timeout=30,
            )
            if pg_wait_ok.returncode != 0 or "14/14" not in pg_wait_ok.stdout or "namespace chaos-testing " not in psa_log.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] 14-gitops-bootstrap PSA wait+strict failed on delayed chaos-testing: rc={pg_wait_ok.returncode} err={pg_wait_ok.stderr!r}")

            # Case B: chaos-testing never created -> strict --require-all fails non-zero
            missing_chaos = " ".join(sorted((EXPECTED_BASELINE | EXPECTED_PRIVILEGED) - {"chaos-testing"}))
            pg_miss = subprocess.run(
                [sh, str(gitops_runner)],
                env={
                    **base_env,
                    "EXISTING_NS": missing_chaos,
                    "PSA_NS_WAIT_TIMEOUT": "0",
                    "PSA_NS_WAIT_INTERVAL": "1",
                },
                capture_output=True, text=True, timeout=30,
            )
            if pg_miss.returncode == 0:
                errs.append(f"[{sh}] 14-gitops-bootstrap must fail loudly when a namespace is missing after wait")

            # Case C: missing apply-psa-labels.sh helper -> fails non-zero (not WARN + exit 0)
            missing_helper_runner = run_dir / "run-14-psa-missing.sh"
            missing_helper_runner.write_text(
                "#!/bin/bash\nset -euo pipefail\n"
                + _extract_gitops_psa_block(texts["gitops_boot"]).replace(
                    "/home/vagrant/scripts/cluster/lib/apply-psa-labels.sh",
                    str(run_dir / "no-such-apply-psa-labels.sh"),
                ),
                encoding="utf-8",
            )
            missing_helper_runner.chmod(0o755)
            pg_no_helper = subprocess.run(
                [sh, str(missing_helper_runner)],
                env={**base_env, "EXISTING_NS": all_14, "PSA_NS_WAIT_TIMEOUT": "0", "PSA_NS_WAIT_INTERVAL": "1"},
                capture_output=True, text=True, timeout=30,
            )
            if pg_no_helper.returncode == 0:
                errs.append(f"[{sh}] 14-gitops-bootstrap must fail loudly when apply-psa-labels.sh is missing")

    return errs


MUTATIONS = [
    ("02-containerd re-introduces skip_verify", "containerd", "# D-HARBOR-NODE-CA", "  skip_verify = true\n# D-HARBOR-NODE-CA"),
    ("lib-harbor-ca drops expiry check (-checkend 0)", "harbor_lib", 'if ! openssl x509 -in "${b}" -noout -checkend 0 >/dev/null 2>&1; then', "if false; then"),
    ("lib-harbor-ca allows trailing garbage/private keys", "harbor_lib", "    /^[[:space:]]*$/ { next }\n    {\n      bad = 1\n      exit 2\n    }", "    /^[[:space:]]*$/ { next }\n    { next }"),
    ("lib-harbor-ca treats SSH probe failure as CA-absent", "harbor_lib", '  if [ "${probe_rc}" -ne 0 ]; then\n    echo "ERROR: remote probe failed while checking Harbor CA (${remote_ca}, rc=${probe_rc}): ${probe_out}" >&2\n    return 1\n  fi', '  if [ "${probe_rc}" -ne 0 ]; then return 2; fi'),
    ("distribute-join-kakao treats SSH failure as CA-absent", "kakao_dist", 'harbor_probe_rc=0\nprobe_remote_harbor_ca "${HARBOR_CA_REMOTE}" 1 ssh_node "${MASTER1}" || harbor_probe_rc=$?', 'harbor_probe_rc=2\nif ssh_node "${MASTER1}" "sudo test -s \'${HARBOR_CA_REMOTE}\'" 2>/dev/null; then harbor_probe_rc=0; fi'),
    ("08-6b drops validate_harbor_ca_bundle call", "harbor_node", 'if ! validate_harbor_ca_bundle "${CA_SRC}"; then\n  exit 1\nfi', "true"),
    ("08-6b emits skip_verify", "harbor_node", '  ca = "${CA_DEST}"', "  skip_verify = true"),
    ("08-6 swallows kubectl get nodes failure", "tls_routes", 'if ! CLUSTER_NODE_IPS="$(kubectl get nodes -o jsonpath=\'{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\\n"}{end}\')"; then\n    echo "ERROR: failed to enumerate cluster nodes for Harbor containerd CA propagation." >&2\n    exit 1\n  fi', 'CLUSTER_NODE_IPS="$(kubectl get nodes -o jsonpath=\'{range .items[*]}{.status.addresses[?(@.type=="InternalIP")].address}{"\\n"}{end}\' || true)"'),
    ("08-6 drops propagation error gate", "tls_routes", 'if [ "${HARBOR_PROP_FAILED}" -gt 0 ]; then', "if false; then"),
    ("06-phase2-start drops 08-6 failure DEGRADED recording", "phase2", 'if [[ "${script}" == "08-6-tls-routes.sh" ]]; then', "if false; then"),
    ("02-join-worker re-duplicates sync_harbor_containerd_ca", "join_worker", 'sync_harbor_containerd_ca "${MASTER_IP}"', 'sync_harbor_containerd_ca() { :; }\nsync_harbor_containerd_ca "${MASTER_IP}"'),
    ("02-join-control-plane drops Harbor CA lib source", "join_cp", 'source "${HARBOR_CA_LIB}"', ":"),
    ("apply-harbor-ca-kakao reverts to unpropagated process substitution", "kakao_harbor", 'workers_json="$(cd "${TF_DIR}" && tofu output -json worker_private_ips)"\nworkers_lines="$(read_ips "${workers_json}")"', 'workers_lines="$(read_ips "$(cd "${TF_DIR}" && tofu output -json worker_private_ips || true)" || true)"'),
    ("apply-psa-labels uses loose not-found substring match", "psa_helper", '    if [ "${line}" = "${expected}" ]; then\n      return 0\n    fi', '    case "${line}" in *[Nn]ot\\ [Ff]ound*) return 0 ;; esac'),
    ("apply-psa-labels treats Forbidden as NotFound", "psa_helper", '    if [ "${line}" = "${expected}" ]; then\n      return 0\n    fi', '    return 0'),
    ("apply-psa-labels drops chaos-testing", "psa_helper", "nfs-quota-agent chaos-testing;", "nfs-quota-agent;"),
    ("apply-psa-labels adds enforce", "psa_helper", "pod-security.kubernetes.io/warn=baseline", "pod-security.kubernetes.io/enforce=baseline"),
    ("08-3-security drops apply-psa-labels call", "sec_script", 'PSA_HELPER="/home/vagrant/scripts/cluster/lib/apply-psa-labels.sh"', 'PSA_HELPER="/bin/true"'),
    ("08-3-security makes early PSA pass strict", "sec_script", 'bash "${PSA_HELPER}"', 'bash "${PSA_HELPER}" --require-all'),
    ("14-gitops-bootstrap drops --require-all on final PSA pass", "gitops_boot", 'bash "${PSA_HELPER}" --require-all', 'bash "${PSA_HELPER}"'),
    ("14-gitops-bootstrap drops bounded wait for GitOps namespaces", "gitops_boot", 'for ns in dev chaos-testing; do', 'for ns in ; do'),
    ("14-gitops-bootstrap swallows missing PSA helper", "gitops_boot", 'if [ ! -f "${PSA_HELPER}" ]; then\n  echo "ERROR: ${PSA_HELPER} not found; cannot enforce required 14/14 PSA audit/warn labels" >&2\n  exit 1\nfi\nbash "${PSA_HELPER}" --require-all', 'if [ -f "${PSA_HELPER}" ]; then bash "${PSA_HELPER}" --require-all; else echo "WARN: missing" >&2; fi'),
    ("dev-namespace drops PSA label", "dev_ns", "pod-security.kubernetes.io/audit: baseline", "example.com/audit: baseline"),
    ("narwhal-db drops PSA label", "narwhal_db", "pod-security.kubernetes.io/warn: baseline", "example.com/warn: baseline"),
    ("chaos-mesh drops managedNamespaceMetadata PSA", "chaos_mesh", "pod-security.kubernetes.io/audit: privileged", "example.com/audit: privileged"),
    ("rbac re-binds ClusterRole/edit", "rbac", "  kind: Role\n  name: developer-dev-access", "  kind: ClusterRole\n  name: edit"),
    ("rbac keeps legacy developer-full-access RoleBinding", "rbac", "  name: developer-dev-access\n  namespace: dev", "  name: developer-full-access\n  namespace: dev"),
    ("rbac adds impersonate verb to dev Role", "rbac", '    resources: ["pods/eviction"]\n    verbs: ["create"]', '    resources: ["pods/eviction"]\n    verbs: ["create", "impersonate"]'),
    ("rbac adds serviceaccounts/token to dev Role", "rbac", "      - secrets\n      - serviceaccounts\n      - services\n", "      - secrets\n      - serviceaccounts\n      - serviceaccounts/token\n      - services\n"),
    ("rbac adds wildcard verb to dev Role", "rbac", '    resources: ["leases"]\n    verbs: ["get"', '    resources: ["leases"]\n    verbs: ["*", "get"'),
    ("rbac drops secrets from dev Role", "rbac", "      - persistentvolumeclaims\n      - secrets\n      - serviceaccounts\n", "      - persistentvolumeclaims\n      - serviceaccounts\n"),
    ("regression-check-kakao drops R386 wiring", "reg", "check R386 ", "check R9999 "),
]


def load_texts() -> dict[str, str]:
    return {k: (ROOT / rel).read_text(encoding="utf-8") for k, rel in FILES.items()}


def main() -> int:
    texts = load_texts()
    errs = check_static(texts) + check_behavior(texts)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            if old not in texts[key]:
                print(f"FAIL: mutation target not found for {name!r} in {FILES[key]}", file=sys.stderr)
                return 1
            mutated = dict(texts)
            mutated[key] = texts[key].replace(old, new, 1)
            m_errs = check_static(mutated)
            if not m_errs:
                m_errs = check_behavior(mutated)
            if not m_errs:
                print(f"FAIL: mutation NOT caught: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R386 KISA-IMG-01/POD-01/RBAC-02 ({len(MUTATIONS)} mutations rejected, bash 3.2 + default bash behavioral verified)")
    else:
        print("PASS: R386 KISA-IMG-01/POD-01/RBAC-02")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
