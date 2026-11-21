#!/usr/bin/env python3
"""R386: KISA-IMG-01 (Harbor containerd CA), KISA-POD-01 (PSA 14/14), KISA-RBAC-02 (dev Role).

Static, behavioral (bash 3.2 + no helm/promtool on PATH), and mutation-verified check for:
  A. KISA-IMG-01 (D-HARBOR-NODE-CA): 02-containerd.sh no longer writes skip_verify=true for
     harbor.<domain>; 08-6b-harbor-containerd-tls-node.sh validates PEM fail-closed and writes
     ca.crt + hosts.toml atomically; 08-6-tls-routes.sh installs locally, rotates, and propagates
     to all nodes (Vagrant sshpass, Kakao via apply-harbor-ca-kakao.sh); join scripts sync CA for
     late-joining nodes; airgap 06-configure-mirrors.sh stays untouched for external mirrors.
  B. KISA-POD-01 (D-PSA-LABELS-14): apply-psa-labels.sh sets audit/warn (never enforce) on all 14
     platform namespaces (7 baseline, 7 privileged incl. chaos-testing); 08-3-security.sh and
     14-gitops-bootstrap.sh both call it; dev-namespace.yaml, narwhal-db.yaml, and chaos-mesh.yaml
     stay in sync without enforce; workload-security-profile.md updated.
  C. KISA-RBAC-02 (D-DEV-ROLE-NO-IMPERSONATE): dev/developer-full-access binds explicit namespaced
     Role/developer-full-access (not ClusterRole/edit) with no impersonate/escalate/bind, no
     serviceaccounts/token or proxy subresources, and no wildcards while retaining dev workload
     and Secret/PVC/Ingress/NetworkPolicy permissions.
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
    "tls_routes": "scripts/cluster/08-6-tls-routes.sh",
    "harbor_node": "scripts/cluster/08-6b-harbor-containerd-tls-node.sh",
    "join_worker": "scripts/cluster/02-join-worker.sh",
    "join_cp": "scripts/cluster/02-join-control-plane.sh",
    "kakao_dist": "scripts/cloud/distribute-join-kakao.sh",
    "kakao_prov": "scripts/cloud/provision-kakao.sh",
    "kakao_harbor": "scripts/cloud/apply-harbor-ca-kakao.sh",
    "mirrors": "scripts/airgap/06-configure-mirrors.sh",
    "ops_doc": "docs/vagrant/operations.md",
    "psa_helper": "scripts/cluster/lib/apply-psa-labels.sh",
    "sec_script": "scripts/cluster/08-3-security.sh",
    "gitops_boot": "scripts/cluster/14-gitops-bootstrap.sh",
    "dev_ns": "gitops/resources/dev-namespace.yaml",
    "narwhal_db": "gitops/resources/narwhal-db.yaml",
    "chaos_mesh": "gitops/charts/narwhal-apps/templates/chaos-mesh.yaml",
    "psa_doc": "docs/common/workload-security-profile.md",
    "rbac": "gitops/resources/rbac-policies.yaml",
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

    harbor_node = texts["harbor_node"]
    harbor_node_active = "\n".join(non_comment_lines(harbor_node))
    if re.search(r"skip_verify\s*=", harbor_node_active):
        errs.append("08-6b-harbor-containerd-tls-node.sh must never emit skip_verify")
    for token in ("openssl x509", 'ca = "${CA_DEST}"', "BEGIN CERTIFICATE", "D-HARBOR-NODE-CA"):
        if token not in harbor_node:
            errs.append(f"08-6b-harbor-containerd-tls-node.sh missing {token!r}")

    tls = texts["tls_routes"]
    for token in (
        "08-6b-harbor-containerd-tls-node.sh",
        "apply-harbor-ca-kakao.sh",
        'if [ "${HARBOR_PROP_FAILED}" -gt 0 ]; then',
        "refusing skip_verify fallback",
    ):
        if token not in tls:
            errs.append(f"08-6-tls-routes.sh missing {token!r}")
    if 'if [ -z "${CA_CERT}" ]; then' not in tls:
        errs.append("08-6-tls-routes.sh must fail closed when CA_CERT is empty")

    for key in ("join_worker", "join_cp"):
        if "sync_harbor_containerd_ca() {" not in texts[key] or "\nsync_harbor_containerd_ca\n" not in texts[key] or "08-6b-harbor-containerd-tls-node.sh" not in texts[key]:
            errs.append(f"{FILES[key]} must define and invoke sync_harbor_containerd_ca")
    if "HARBOR_CA_PRESENT=true" not in texts["kakao_dist"] or "openssl x509" not in texts["kakao_dist"]:
        errs.append("distribute-join-kakao.sh must validate and stage harbor-ca.crt when present on master-1")
    if "Harbor containerd CA propagation to other nodes FAILED" not in texts["kakao_prov"] or "stage_harbor_ca" not in texts["kakao_prov"]:
        errs.append("provision-kakao.sh must wire apply-harbor-ca-kakao.sh in phase2 and harbor-ca stage")
    if "08-6b-harbor-containerd-tls-node.sh" not in texts["kakao_harbor"]:
        errs.append("apply-harbor-ca-kakao.sh must invoke 08-6b-harbor-containerd-tls-node.sh")
    if "harbor." in "\n".join(non_comment_lines(texts["mirrors"])):
        errs.append("06-configure-mirrors.sh must remain scoped to external airgap mirrors, not harbor.<domain>")
    if "D-HARBOR-NODE-CA" not in texts["ops_doc"] or "08-6b-harbor-containerd-tls-node.sh" not in texts["ops_doc"]:
        errs.append("docs/vagrant/operations.md must document D-HARBOR-NODE-CA and 08-6b-harbor-containerd-tls-node.sh")

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

    if "chaos-testing" not in texts["psa_doc"] or "apply-psa-labels.sh" not in texts["psa_doc"]:
        errs.append("docs/common/workload-security-profile.md must document chaos-testing and apply-psa-labels.sh")

    # --- Part C: KISA-RBAC-02 ---
    rbac_docs = [d for d in yaml.safe_load_all(texts["rbac"]) if isinstance(d, dict)]
    dev_roles = [
        d for d in rbac_docs
        if d.get("kind") == "Role"
        and (d.get("metadata") or {}).get("name") == "developer-full-access"
        and (d.get("metadata") or {}).get("namespace") == "dev"
    ]
    dev_rbs = [
        d for d in rbac_docs
        if d.get("kind") == "RoleBinding"
        and (d.get("metadata") or {}).get("name") == "developer-full-access"
        and (d.get("metadata") or {}).get("namespace") == "dev"
    ]
    if len(dev_roles) != 1:
        errs.append("rbac-policies.yaml must define Role/dev/developer-full-access")
    if len(dev_rbs) != 1:
        errs.append("rbac-policies.yaml must define RoleBinding/dev/developer-full-access")
    else:
        rr = dev_rbs[0].get("roleRef") or {}
        if rr.get("kind") != "Role" or rr.get("name") != "developer-full-access":
            errs.append(f"RoleBinding/dev/developer-full-access must bind Role/developer-full-access, got {rr!r}")
        subs = dev_rbs[0].get("subjects") or []
        if not any(s.get("kind") == "Group" and s.get("name") == "oidc:developer" for s in subs):
            errs.append("RoleBinding/dev/developer-full-access must bind Group oidc:developer")

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
                errs.append("Role/dev/developer-full-access must not use wildcard apiGroups")
            bad_v = verbs & FORBIDDEN_VERBS
            if bad_v:
                errs.append(f"Role/dev/developer-full-access has forbidden verb(s): {sorted(bad_v)}")
            bad_r = resources & FORBIDDEN_RESOURCES
            if bad_r:
                errs.append(f"Role/dev/developer-full-access has forbidden resource(s): {sorted(bad_r)}")
            seen_resources.update(resources)
        missing_res = REQUIRED_DEV_RESOURCES - seen_resources
        if missing_res:
            errs.append(f"Role/dev/developer-full-access missing required dev resources: {sorted(missing_res)}")

    # --- Regression wiring ---
    if "check R386 " not in texts["reg"] or "check-kisa-node-trust-psa-rbac.py --mutation-verify" not in texts["reg"]:
        errs.append("regression-check-kakao.sh must wire R386 with --mutation-verify")

    return errs


def make_clean_path(extra_dir: Path) -> str:
    """Build a PATH containing standard tools + extra_dir, excluding helm and promtool."""
    clean_bin = extra_dir / "clean-bin"
    clean_bin.mkdir(parents=True, exist_ok=True)
    needed = (
        "bash", "sh", "cat", "chmod", "cmp", "cp", "cut", "dirname", "env",
        "grep", "id", "mkdir", "mktemp", "mv", "openssl", "printf", "rm", "sed",
        "base64", "tr", "wc", "hostname",
    )
    for tool in needed:
        found = shutil.which(tool)
        if found:
            link = clean_bin / tool
            if not link.exists():
                link.symlink_to(found)
    return f"{extra_dir}:{clean_bin}"


def generate_self_signed_ca(dest: Path, cn: str) -> None:
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-keyout", str(dest.with_suffix(".key")),
            "-out", str(dest),
            "-days", "30", "-nodes",
            "-subj", f"/CN={cn}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    dest.with_suffix(".key").unlink(missing_ok=True)


def check_behavior(texts: dict[str, str]) -> list[str]:
    errs: list[str] = []
    shells = ["/bin/bash"]
    default_bash = shutil.which("bash")
    if default_bash and os.path.realpath(default_bash) != os.path.realpath("/bin/bash"):
        shells.append(default_bash)

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        ca1 = t / "ca1.crt"
        ca2 = t / "ca2.crt"
        generate_self_signed_ca(ca1, "Narwhal Test CA 1")
        generate_self_signed_ca(ca2, "Narwhal Test CA 2")

        node_script = t / "08-6b-harbor-containerd-tls-node.sh"
        node_script.write_text(texts["harbor_node"], encoding="utf-8")
        node_script.chmod(0o755)

        psa_script = t / "apply-psa-labels.sh"
        psa_script.write_text(texts["psa_helper"], encoding="utf-8")
        psa_script.chmod(0o755)

        for idx, sh in enumerate(shells):
            run_dir = t / f"run-{idx}"
            run_dir.mkdir()
            certs_dir = run_dir / "certs.d"
            ctr_cfg = run_dir / "config.toml"
            ctr_cfg.write_text('config_path = "/etc/containerd/certs.d"\n', encoding="utf-8")
            fake_bin = run_dir / "bin"
            fake_bin.mkdir()
            clean_path = make_clean_path(fake_bin)
            base_env = {
                "PATH": clean_path,
                "HOME": str(run_dir),
                "CERTS_DIR": str(certs_dir),
                "CONTAINERD_CONFIG": str(ctr_cfg),
                "SUDO": "",
            }

            # 1) Legacy skip_verify hosts.toml is replaced with verified CA1 hosts.toml
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
            if "skip_verify" in hosts_text:
                errs.append(f"[{sh}] skip_verify survived migration")
            if f'ca = "{ca_path}"' not in hosts_text:
                errs.append(f"[{sh}] hosts.toml missing expected ca path: {hosts_text!r}")
            if not ca_path.exists() or ca_path.read_text(encoding="utf-8") != ca1.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] ca.crt does not match CA1")
            if stat.S_IMODE(ca_path.stat().st_mode) != 0o644:
                errs.append(f"[{sh}] ca.crt mode expected 0644")

            # 2) Idempotent re-run reports already up to date
            p2 = subprocess.run(
                [sh, str(node_script), str(ca1), "test.internal"],
                env=base_env, capture_output=True, text=True, timeout=30,
            )
            if p2.returncode != 0 or "already up to date" not in p2.stdout:
                errs.append(f"[{sh}] 08-6b re-run not idempotent: rc={p2.returncode} out={p2.stdout!r}")

            # 3) CA rotation replaces ca.crt atomically
            p3 = subprocess.run(
                [sh, str(node_script), str(ca2), "test.internal"],
                env=base_env, capture_output=True, text=True, timeout=30,
            )
            if p3.returncode != 0 or "ca_changed=1" not in p3.stdout:
                errs.append(f"[{sh}] 08-6b CA rotation failed: rc={p3.returncode} out={p3.stdout!r} err={p3.stderr!r}")
            if ca_path.read_text(encoding="utf-8") != ca2.read_text(encoding="utf-8"):
                errs.append(f"[{sh}] ca.crt was not rotated to CA2")

            # 4) Bad CA inputs (empty, non-PEM, corrupt PEM) and bad domain fail closed without clobbering CA2
            empty_f = run_dir / "empty.crt"
            empty_f.write_text("", encoding="utf-8")
            nonpem_f = run_dir / "nonpem.crt"
            nonpem_f.write_text("not a cert\n", encoding="utf-8")
            corrupt_f = run_dir / "corrupt.crt"
            corrupt_f.write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", encoding="utf-8")
            for bad_label, bad_args in (
                ("empty CA", [str(empty_f), "test.internal"]),
                ("non-PEM CA", [str(nonpem_f), "test.internal"]),
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
                errs.append(f"[{sh}] 08-6b clobbered valid CA2 after bad inputs")

            # 5) Standalone kubectl extraction mode (orchestrator live migration path)
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

            # 6) Behavioral test for apply-psa-labels.sh (partial 9/14 at 08-3, full 14/14 at 14)
            psa_log = run_dir / "psa.log"
            fake_kubectl.write_text(
                "#!/bin/sh\n"
                'cmd="$1"; shift\n'
                'if [ "$cmd" = "get" ] && [ "$1" = "ns" ]; then\n'
                '  case " ${EXISTING_NS:-} " in\n'
                '    *" $2 "*) exit 0 ;;\n'
                '    *) exit 1 ;;\n'
                '  esac\n'
                'fi\n'
                'if [ "$cmd" = "label" ] && [ "$1" = "ns" ]; then\n'
                f'  printf "%s\\n" "$*" >> "{psa_log}"\n'
                '  exit 0\n'
                'fi\n'
                'exit 1\n',
                encoding="utf-8",
            )
            fake_kubectl.chmod(0o755)
            # Partial set (9 namespaces existing at 08-3)
            partial_ns = "devtools monitoring database default cilium-secrets kube-system platform-system storage nfs-quota-agent"
            psa_log.write_text("", encoding="utf-8")
            pp1 = subprocess.run(
                [sh, str(psa_script)],
                env={**base_env, "EXISTING_NS": partial_ns},
                capture_output=True, text=True, timeout=30,
            )
            if pp1.returncode != 0:
                errs.append(f"[{sh}] apply-psa-labels.sh partial run failed: {pp1.stderr}")
            lines1 = [l for l in psa_log.read_text(encoding="utf-8").splitlines() if l.strip()]
            if len(lines1) != 9:
                errs.append(f"[{sh}] apply-psa-labels.sh partial expected 9 label calls, got {len(lines1)}")

            # Full set (all 14 namespaces existing at step 14 / live convergence)
            all_14 = " ".join(sorted(EXPECTED_BASELINE | EXPECTED_PRIVILEGED))
            psa_log.write_text("", encoding="utf-8")
            pp2 = subprocess.run(
                [sh, str(psa_script)],
                env={**base_env, "EXISTING_NS": all_14},
                capture_output=True, text=True, timeout=30,
            )
            if pp2.returncode != 0:
                errs.append(f"[{sh}] apply-psa-labels.sh full run failed: {pp2.stderr}")
            applied = {}
            for line in psa_log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                if "pod-security.kubernetes.io/enforce" in line:
                    errs.append(f"[{sh}] apply-psa-labels.sh set enforce: {line}")
                parts = line.split()
                ns_name = parts[1]
                audit_m = re.search(r"pod-security\.kubernetes\.io/audit=(\w+)", line)
                warn_m = re.search(r"pod-security\.kubernetes\.io/warn=(\w+)", line)
                if audit_m and warn_m:
                    applied[ns_name] = (audit_m.group(1), warn_m.group(1))
            expected_map = {n: ("baseline", "baseline") for n in EXPECTED_BASELINE}
            expected_map.update({n: ("privileged", "privileged") for n in EXPECTED_PRIVILEGED})
            if applied != expected_map:
                errs.append(f"[{sh}] apply-psa-labels.sh full 14 mismatch: got {applied!r}")

            # 7) Join script sync_harbor_containerd_ca behavioral test (Phase 1 skip vs late-join sync)
            for jkey, ip_var in (("join_worker", "MASTER_IP"), ("join_cp", "MASTER1_IP")):
                m_fn = re.search(r"(sync_harbor_containerd_ca\(\) \{.*?\n\})", texts[jkey], re.S)
                if not m_fn:
                    errs.append(f"[{sh}] could not extract sync_harbor_containerd_ca from {jkey}")
                    continue
                stage_dir = run_dir / f"stage-{jkey}"
                (stage_dir / "scripts/cluster").mkdir(parents=True, exist_ok=True)
                shutil.copy2(node_script, stage_dir / "scripts/cluster/08-6b-harbor-containerd-tls-node.sh")
                j_certs = run_dir / f"jcerts-{jkey}"
                # Fake sshpass + sudo
                (fake_bin / "sudo").write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
                (fake_bin / "sudo").chmod(0o755)
                (fake_bin / "sshpass").write_text(
                    "#!/bin/sh\n"
                    'shift 2\n'  # -p vagrant
                    'sub="$1"; shift\n'
                    'if [ "$sub" = "ssh" ]; then\n'
                    '  [ "${MASTER_HAS_CA:-0}" = "1" ] && exit 0 || exit 1\n'
                    'fi\n'
                    'if [ "$sub" = "scp" ]; then\n'
                    '  for last in "$@"; do dst="$last"; done\n'
                    f'  cp "{ca1}" "$dst"\n'
                    '  exit 0\n'
                    'fi\n'
                    'exit 1\n',
                    encoding="utf-8",
                )
                (fake_bin / "sshpass").chmod(0o755)
                wrapper = (
                    "set -euo pipefail\n"
                    f'{ip_var}="192.168.56.10"\n'
                    'DOMAIN="join.test"\n'
                    f'STAGE_DIR="{stage_dir}"\n'
                    f'export CERTS_DIR="{j_certs}" CONTAINERD_CONFIG="{ctr_cfg}" SUDO=""\n'
                    + m_fn.group(1)
                    + "\nsync_harbor_containerd_ca\n"
                )
                # Phase 1: master has no CA -> skips cleanly
                pj0 = subprocess.run(
                    [sh, "-c", wrapper],
                    env={**base_env, "MASTER_HAS_CA": "0"},
                    capture_output=True, text=True, timeout=30,
                )
                if pj0.returncode != 0 or (j_certs / "harbor.join.test/hosts.toml").exists():
                    errs.append(f"[{sh}] {jkey} Phase 1 skip failed: rc={pj0.returncode} err={pj0.stderr!r}")
                # Late join: master has CA -> installs verified hosts.toml
                pj1 = subprocess.run(
                    [sh, "-c", wrapper],
                    env={**base_env, "MASTER_HAS_CA": "1"},
                    capture_output=True, text=True, timeout=30,
                )
                j_hosts = j_certs / "harbor.join.test/hosts.toml"
                if pj1.returncode != 0 or not j_hosts.exists() or "skip_verify" in j_hosts.read_text(encoding="utf-8"):
                    errs.append(f"[{sh}] {jkey} late-join sync failed: rc={pj1.returncode} err={pj1.stderr!r}")

    return errs


MUTATIONS = [
    ("02-containerd re-introduces skip_verify", "containerd", "# D-HARBOR-NODE-CA", "  skip_verify = true\n# D-HARBOR-NODE-CA"),
    (
        "08-6b drops openssl x509 validation",
        "harbor_node",
        'if ! openssl x509 -in "${CA_SRC}" -noout -subject >/dev/null 2>&1; then\n  echo "ERROR: Harbor CA input is not a valid X.509 certificate: ${CA_SRC}" >&2\n  exit 1\nfi\nCA_FP="$(openssl x509 -in "${CA_SRC}" -noout -fingerprint -sha256 | sed \'s/^.*=//\')"',
        'CA_FP="unchecked"',
    ),
    ("08-6b emits skip_verify", "harbor_node", '  ca = "${CA_DEST}"', "  skip_verify = true"),
    ("08-6 drops empty-CA fail-closed", "tls_routes", 'if [ -z "${CA_CERT}" ]; then', 'if false && [ -z "${CA_CERT}" ]; then'),
    ("08-6 drops propagation error gate", "tls_routes", 'if [ "${HARBOR_PROP_FAILED}" -gt 0 ]; then', "if false; then"),
    ("02-join-worker drops Harbor CA sync", "join_worker", "sync_harbor_containerd_ca() {", "noop_harbor_sync() {"),
    ("02-join-control-plane drops Harbor CA sync", "join_cp", "sync_harbor_containerd_ca() {", "noop_harbor_sync() {"),
    ("distribute-join-kakao drops harbor-ca staging", "kakao_dist", "HARBOR_CA_PRESENT=true", "HARBOR_CA_PRESENT=false"),
    ("provision-kakao drops apply-harbor-ca-kakao in phase2", "kakao_prov", "Harbor containerd CA propagation to other nodes FAILED", "ignored"),
    ("apply-psa-labels drops chaos-testing", "psa_helper", "nfs-quota-agent chaos-testing;", "nfs-quota-agent;"),
    ("apply-psa-labels adds enforce", "psa_helper", "pod-security.kubernetes.io/warn=baseline", "pod-security.kubernetes.io/enforce=baseline"),
    ("08-3-security drops apply-psa-labels call", "sec_script", 'PSA_HELPER="/home/vagrant/scripts/cluster/lib/apply-psa-labels.sh"', 'PSA_HELPER="/bin/true"'),
    ("14-gitops-bootstrap drops apply-psa-labels call", "gitops_boot", 'PSA_HELPER="/home/vagrant/scripts/cluster/lib/apply-psa-labels.sh"', 'PSA_HELPER="/bin/true"'),
    ("dev-namespace drops PSA label", "dev_ns", "pod-security.kubernetes.io/audit: baseline", "example.com/audit: baseline"),
    ("narwhal-db drops PSA label", "narwhal_db", "pod-security.kubernetes.io/warn: baseline", "example.com/warn: baseline"),
    ("chaos-mesh drops managedNamespaceMetadata PSA", "chaos_mesh", "pod-security.kubernetes.io/audit: privileged", "example.com/audit: privileged"),
    ("rbac re-binds ClusterRole/edit", "rbac", "  kind: Role\n  name: developer-full-access", "  kind: ClusterRole\n  name: edit"),
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
        print(f"PASS: R386 KISA-IMG-01/POD-01/RBAC-02 ({len(MUTATIONS)} mutations rejected, bash 3.2 + no-helm/promtool behavioral verified)")
    else:
        print("PASS: R386 KISA-IMG-01/POD-01/RBAC-02")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
