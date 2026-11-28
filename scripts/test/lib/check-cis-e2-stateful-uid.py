#!/usr/bin/env python3
"""R429 (lane E2): CIS 5.7.3 KSV020/KSV021 stateful workload hardening & exact acceptances.

Verifies:
1. Prometheus & Alertmanager (D-E2-PROM-UID): pod securityContext 10100:10100 (runAsNonRoot,
   runAsUser, runAsGroup, fsGroup, seccompProfile RuntimeDefault) in both GitOps
   (gitops/charts/narwhal-apps/templates/prometheus-stack.yaml) and bootstrap
   (scripts/cluster/08-2-monitoring.sh); neither is accepted for KSV020/KSV021.
2. Keycloak CR (D-E2-KC-GID): runAsGroup 10100 alongside runAsUser 1000 in both GitOps
   (gitops/charts/narwhal-platform/templates/keycloak-cr.yaml) and bootstrap
   (scripts/cluster/11-keycloak.sh); iam/StatefulSet/keycloak accepts ONLY KSV020 (KSV021 fixed).
3. Exact Config Audit acceptances for Harbor (6 workloads: core/jobservice/registry/nginx/portal
   + harbor-redis), OpenBao server (storage/StatefulSet/openbao), and Keycloak (KSV020 only),
   plus bilingual en/ko i18n keys and docs/common/compliance-hardening.md parity.
4. Behavioral verification of scripts/ops/migrate-pv-ownership.sh via stubbed kubectl/ssh/chown:
   dry-run read-only, --apply operator-CR pause/scale/mindepth-1 chown/verify/unpause/rollout,
   non-NFS PV refusal, operator-owned refusal without --operator-cr, path traversal refusal,
   and UID 0 refusal.
5. R429 registration in scripts/test/regression-check-kakao.sh.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "prom_gitops": ROOT / "gitops/charts/narwhal-apps/templates/prometheus-stack.yaml",
    "mon_boot": ROOT / "scripts/cluster/08-2-monitoring.sh",
    "kc_gitops": ROOT / "gitops/charts/narwhal-platform/templates/keycloak-cr.yaml",
    "kc_boot": ROOT / "scripts/cluster/11-keycloak.sh",
    "migrate": ROOT / "scripts/ops/migrate-pv-ownership.sh",
    "rules": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

EXPECTED_PROM_SC = {
    "runAsNonRoot": True,
    "runAsUser": 10100,
    "runAsGroup": 10100,
    "fsGroup": 10100,
    "seccompProfile": {"type": "RuntimeDefault"},
}

EXPECTED_RULES = {
    ("devtools", "ReplicaSet", "harbor-core", "upstream", "cfg:devtools:ReplicaSet:harbor-core:uid-accept"): {"KSV020", "KSV021"},
    ("devtools", "ReplicaSet", "harbor-jobservice", "upstream", "cfg:devtools:ReplicaSet:harbor-jobservice:uid-accept"): {"KSV020", "KSV021"},
    ("devtools", "ReplicaSet", "harbor-registry", "upstream", "cfg:devtools:ReplicaSet:harbor-registry:uid-accept"): {"KSV020", "KSV021"},
    ("devtools", "ReplicaSet", "harbor-nginx", "upstream", "cfg:devtools:ReplicaSet:harbor-nginx:accept"): {"KSV020", "KSV021"},
    ("devtools", "ReplicaSet", "harbor-portal", "upstream", "cfg:devtools:ReplicaSet:harbor-portal:accept"): {"KSV020", "KSV021"},
    ("devtools", "StatefulSet", "harbor-redis", "upstream", "cfg:devtools:StatefulSet:harbor-redis:accept"): {"KSV020", "KSV021"},
    ("iam", "StatefulSet", "keycloak", "upstream", "cfg:iam:StatefulSet:keycloak:accept"): {"KSV020"},
    ("storage", "StatefulSet", "openbao", "accept", "cfg:storage:StatefulSet:openbao:accept"): {"KSV020", "KSV021"},
}

EXPECTED_I18N_KEYS = (
    "harborChartUid10000",
    "harborJobserviceUid10000",
    "harborRegistryUid10000",
    "harborRedisUid999",
    "keycloakUid1000",
    "openbaoServerUid100",
)


def _load_values_object(text: str) -> dict:
    cleaned = text.replace("{{ .Values.baseDomain }}", "local.narwhal.internal")
    cleaned = re.sub(r"\{\{[^}]+\}\}", "stub", cleaned)
    doc = yaml.safe_load(cleaned) or {}
    return (((doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def _extract_set_json(script_text: str, key: str) -> dict | None:
    m = re.search(
        r"if helm upgrade --install prometheus-stack\b.*?; then",
        script_text,
        re.S,
    )
    if not m:
        return None
    block = m.group(0)
    pat = re.compile(r"--set-json\s+'" + re.escape(key) + r"=([^']+)'")
    hit = pat.search(block)
    if not hit:
        return None
    return json.loads(hit.group(1))


def _load_kc_gitops_sc(text: str) -> dict | None:
    cleaned = text.replace("{{ .Values.baseDomain }}", "local.narwhal.internal")
    cleaned = re.sub(r"\{\{[^}]+\}\}", "stub", cleaned)
    doc = yaml.safe_load(cleaned) or {}
    containers = (
        (((doc.get("spec") or {}).get("unsupported") or {}).get("podTemplate") or {})
        .get("spec", {})
        .get("containers", [])
    )
    return containers[0].get("securityContext") if containers else None


def _load_kc_boot_sc(text: str) -> dict | None:
    m = re.search(r"kubectl apply -f - <<EOF\n(.*?)\nEOF", text, re.S)
    if not m:
        return None
    doc = yaml.safe_load(m.group(1).replace("${DOMAIN}", "local.narwhal.internal")) or {}
    containers = (
        (((doc.get("spec") or {}).get("unsupported") or {}).get("podTemplate") or {})
        .get("spec", {})
        .get("containers", [])
    )
    return containers[0].get("securityContext") if containers else None


def _run_migrate_behavior_tests(migrate_text: str, errors: list[str]) -> None:
    uid = os.getuid()
    gid = os.getgid()
    if uid == 0 or gid == 0:
        # Fallback numeric non-zero check only if executed as root (unlikely in CI).
        uid, gid = 10100, 10100
    target = f"{uid}:{gid}"

    stub_kubectl = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

log_path = Path(os.environ["E2_LOG"])
state_path = Path(os.environ["E2_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
# Strip --kubeconfig and --request-timeout
clean = []
i = 0
while i < len(args):
    if args[i] in ("--kubeconfig", "--request-timeout"):
        i += 2
        continue
    if args[i].startswith("--kubeconfig=") or args[i].startswith("--request-timeout="):
        i += 1
        continue
    clean.append(args[i])
    i += 1

with log_path.open("a") as f:
    f.write("kubectl " + " ".join(clean) + "\n")

ns = None
if len(clean) >= 2 and clean[0] == "-n":
    ns = clean[1]
    clean = clean[2:]

cmd = clean[0] if clean else ""
if cmd == "get":
    kind = clean[1]
    if kind == "pods":
        if "-A" in clean:
            items = state.get("all_pods", [])
        else:
            if state.get("fail_pod_list"):
                sys.exit(9)
            items = [] if state.get("replicas", 1) == 0 else [{"metadata": {"name": "prom-0"}}]
        print(json.dumps({"items": items}))
        sys.exit(0)
    name = clean[2] if len(clean) > 2 else ""
    if kind.startswith("statefulset/"):
        print(json.dumps(state["workload"]))
        sys.exit(0)
    if kind == "pvc":
        print(json.dumps(state["pvcs"][name]))
        sys.exit(0)
    if kind == "pv":
        print(json.dumps(state["pvs"][name]))
        sys.exit(0)
    if kind.startswith("prometheus/"):
        print(json.dumps(state["cr"]))
        sys.exit(0)
elif cmd == "patch":
    patch_str = clean[clean.index("-p") + 1]
    patch_obj = json.loads(patch_str)
    if "paused" in patch_obj.get("spec", {}):
        state["cr"]["spec"]["paused"] = patch_obj["spec"]["paused"]
    if "replicas" in patch_obj.get("spec", {}):
        state["replicas"] = patch_obj["spec"]["replicas"]
    state_path.write_text(json.dumps(state))
    print("patched")
    sys.exit(0)
elif cmd == "scale":
    for tok in clean:
        if tok.startswith("--replicas="):
            state["replicas"] = int(tok.split("=", 1)[1])
    state_path.write_text(json.dumps(state))
    print("scaled")
    sys.exit(0)
elif cmd == "rollout":
    print("rollout complete")
    sys.exit(0)

print(f"unexpected kubectl args: {args}", file=sys.stderr)
sys.exit(1)
'''

    stub_ssh = r'''#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path

log_path = Path(os.environ["E2_LOG"])
args = sys.argv[1:]
# Find "sudo" "-n" "bash" "-s" "--" ...
idx = args.index("bash")
remote_args = args[idx:]
with log_path.open("a") as f:
    f.write("ssh " + " ".join(remote_args) + "\n")
script_stdin = sys.stdin.read()
state = __import__("json").loads(Path(os.environ["E2_STATE"]).read_text())
if state.get("fail_chown") and "chown" in remote_args:
    sys.exit(9)
proc = subprocess.run(remote_args, input=script_stdin, text=True, capture_output=True)
sys.stdout.write(proc.stdout)
sys.stderr.write(proc.stderr)
sys.exit(proc.returncode)
'''

    stub_chown = r'''#!/usr/bin/env python3
import os, sys
from pathlib import Path

log_path = Path(os.environ["E2_CHOWN_LOG"])
with log_path.open("a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
sys.exit(0)
'''

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        script_path = td_path / "migrate-pv-ownership.sh"
        script_path.write_text(migrate_text, encoding="utf-8")
        script_path.chmod(0o755)

        bin_dir = td_path / "bin"
        bin_dir.mkdir()
        for name, content in (("kubectl", stub_kubectl), ("ssh", stub_ssh), ("chown", stub_chown)):
            p = bin_dir / name
            p.write_text(content, encoding="utf-8")
            p.chmod(0o755)

        kubeconfig = td_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")

        nfs_share = td_path / "nfs"
        pv_dir = nfs_share / "monitoring" / "prom-pvc-prometheus-prometheus-stack-kube-prom-prometheus-0"
        (pv_dir / "wal").mkdir(parents=True)
        (pv_dir / "wal" / "000001").write_text("tsdb", encoding="utf-8")
        (pv_dir / "lock").write_text("", encoding="utf-8")

        def make_state(driver="nfs.csi.k8s.io", subdir="monitoring/prom-pvc-prometheus-prometheus-stack-kube-prom-prometheus-0", owned=True):
            wl = {
                "metadata": {
                    "name": "prometheus-prometheus-stack-kube-prom-prometheus",
                    "ownerReferences": [{"kind": "Prometheus", "name": "prometheus-stack-kube-prom-prometheus"}] if owned else [],
                },
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app.kubernetes.io/name": "prometheus"}},
                    "volumeClaimTemplates": [{"metadata": {"name": "prom-pvc"}}],
                    "template": {"spec": {"volumes": []}},
                },
            }
            return {
                "replicas": 1,
                "workload": wl,
                "cr": {"spec": {"replicas": 1, "paused": False}},
                "pvcs": {
                    "prom-pvc-prometheus-prometheus-stack-kube-prom-prometheus-0": {"status": {"phase": "Bound"}, "spec": {"volumeName": "pv-prom-0"}},
                },
                "pvs": {
                    "pv-prom-0": {
                        "spec": {
                            "storageClassName": "nfs-csi" if driver == "nfs.csi.k8s.io" else "cinder",
                            "csi": {"driver": driver, "volumeAttributes": {"subDir": subdir}},
                        }
                    }
                },
                "all_pods": [],
            }

        def run_case(extra_args: list[str], state_obj: dict) -> tuple[subprocess.CompletedProcess, str, str]:
            log_file = td_path / "run.log"
            chown_log = td_path / "chown.log"
            state_file = td_path / "state.json"
            log_file.write_text("", encoding="utf-8")
            chown_log.write_text("", encoding="utf-8")
            state_file.write_text(json.dumps(state_obj), encoding="utf-8")
            env = dict(
                os.environ,
                PATH=f"{bin_dir}:{os.environ['PATH']}",
                E2_LOG=str(log_file),
                E2_CHOWN_LOG=str(chown_log),
                E2_STATE=str(state_file),
            )
            cmd = [
                "bash", str(script_path),
                "--kubeconfig", str(kubeconfig),
                "--node", "master-1=vagrant@192.0.2.10",
                "--nfs-share", str(nfs_share),
                "--namespace", "monitoring",
                "--workload", "statefulset/prometheus-prometheus-stack-kube-prom-prometheus",
                "--pvc", "prom-pvc-prometheus-prometheus-stack-kube-prom-prometheus-0",
                "--target", target,
            ] + extra_args
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=20)
            return proc, log_file.read_text(encoding="utf-8"), chown_log.read_text(encoding="utf-8")

        # Case A: Dry-run default (read-only)
        p, log_txt, chown_txt = run_case(
            ["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus"],
            make_state(),
        )
        if p.returncode != 0:
            errors.append(f"migrate-pv-ownership dry-run failed (rc={p.returncode}): {p.stderr}")
        else:
            if "kubectl patch" in log_txt or "kubectl scale" in log_txt or chown_txt.strip():
                errors.append(f"migrate-pv-ownership dry-run mutated state: log={log_txt!r} chown={chown_txt!r}")
            if "Rollback steps" not in p.stdout or "DRY-RUN" not in p.stdout:
                errors.append("migrate-pv-ownership dry-run must print DRY-RUN plan and Rollback steps")

        # Case B: --apply with operator CR pause/scale/mindepth-1 chown/unpause
        p, log_txt, chown_txt = run_case(
            ["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus", "--apply"],
            make_state(),
        )
        if p.returncode != 0:
            errors.append(f"migrate-pv-ownership --apply failed (rc={p.returncode}): {p.stderr}")
        else:
            lines = [ln.strip() for ln in log_txt.splitlines() if ln.strip()]
            must_order = [
                'kubectl -n monitoring patch prometheus/prometheus-stack-kube-prom-prometheus --type=merge -p {"spec":{"paused":true}}',
                "kubectl -n monitoring scale statefulset/prometheus-prometheus-stack-kube-prom-prometheus --replicas=0",
                "ssh bash -s -- chown",
                "kubectl -n monitoring scale statefulset/prometheus-prometheus-stack-kube-prom-prometheus --replicas=1",
                'kubectl -n monitoring patch prometheus/prometheus-stack-kube-prom-prometheus --type=merge -p {"spec":{"paused":false}}',
                "kubectl -n monitoring rollout status statefulset/prometheus-prometheus-stack-kube-prom-prometheus",
            ]
            pos = -1
            for needle in must_order:
                nxt = next((i for i, ln in enumerate(lines) if i > pos and needle in ln), -1)
                if nxt == -1:
                    errors.append(f"migrate-pv-ownership --apply missing/out-of-order step: {needle!r} in {lines}")
                    break
                pos = nxt
            # PV root dir must NEVER be passed to chown; nested entries MUST be passed
            chown_tokens = chown_txt.split()
            pv_real = str(pv_dir.resolve())
            if pv_real in chown_tokens:
                errors.append(f"migrate-pv-ownership --apply chowned PV root directory {pv_real}")
            if str((pv_dir / "wal" / "000001").resolve()) not in chown_tokens:
                errors.append(f"migrate-pv-ownership --apply did not chown nested file under PV dir: {chown_txt!r}")

        # Case C: Refuse non-NFS PV
        p, log_txt, chown_txt = run_case(
            ["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus", "--apply"],
            make_state(driver="cinder.csi.openstack.org"),
        )
        if p.returncode == 0 or "REFUSE non-NFS PV" not in p.stderr:
            errors.append(f"migrate-pv-ownership must refuse non-NFS PV (rc={p.returncode}, stderr={p.stderr!r})")
        if "kubectl scale" in log_txt or chown_txt.strip():
            errors.append("migrate-pv-ownership mutated state on non-NFS PV refusal")

        # Case D: Refuse operator-owned StatefulSet without --operator-cr
        p, _, _ = run_case(["--apply"], make_state(owned=True))
        if p.returncode == 0 or "--operator-cr" not in p.stderr:
            errors.append(f"migrate-pv-ownership must refuse operator-owned workload without --operator-cr (rc={p.returncode})")

        # Case E: Refuse path traversal subDir
        p, _, _ = run_case(
            ["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus", "--apply"],
            make_state(subdir="../etc"),
        )
        if p.returncode == 0 or "unsafe" not in p.stderr:
            errors.append(f"migrate-pv-ownership must refuse traversal subDir (rc={p.returncode}, stderr={p.stderr!r})")

        # Case F: Refuse UID 0 target
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        p = subprocess.run(
            [
                "bash", str(script_path),
                "--kubeconfig", str(kubeconfig),
                "--local",
                "--nfs-share", str(nfs_share),
                "--namespace", "monitoring",
                "--workload", "statefulset/prometheus-prometheus-stack-kube-prom-prometheus",
                "--pvc", "prom-pvc-prometheus-prometheus-stack-kube-prom-prometheus-0",
                "--target", "0:10100",
            ],
            env=env, capture_output=True, text=True, timeout=10,
        )
        if p.returncode == 0:
            errors.append("migrate-pv-ownership must refuse UID 0 target")

        wrong = make_state()
        wrong["workload"]["spec"]["volumeClaimTemplates"] = [{"metadata": {"name": "other"}}]
        p, _, _ = run_case(["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus"], wrong)
        if p.returncode == 0 or "not mounted" not in p.stderr:
            errors.append("migrate-pv-ownership must reject PVC not mounted by target workload")
        other = make_state()
        other["all_pods"] = [{"metadata": {"namespace": "other", "name": "consumer", "labels": {"app": "else"}}, "status": {"phase": "Running"}, "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "prom-pvc-prometheus-prometheus-stack-kube-prom-prometheus-0"}}]}}]
        p, _, _ = run_case(["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus"], other)
        if p.returncode == 0 or "also mounted" not in p.stderr:
            errors.append("migrate-pv-ownership must reject PVC mounted by another running pod")

        before = make_state()
        before["fail_pod_list"] = True
        p, _, _ = run_case(["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus", "--apply"], before)
        if p.returncode == 0 or "replicas=1" not in p.stderr or "unpaused" not in p.stderr:
            errors.append("pre-chown failure must restore replicas and unpause operator")
        after = make_state()
        after["fail_chown"] = True
        p, _, _ = run_case(["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus", "--apply"], after)
        if p.returncode == 0 or "no automatic restart" not in p.stderr or "Manual rollback" not in p.stderr:
            errors.append("post-chown failure must avoid restart and print rollback instructions")

        accepted = make_state(subdir="monitoring/storage..old")
        (nfs_share / "monitoring" / "storage..old").mkdir(parents=True, exist_ok=True)
        p, _, _ = run_case(["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus"], accepted)
        if p.returncode != 0:
            errors.append("subDir storage..old must be accepted")
        rejected = make_state(subdir="a/../b")
        p, _, _ = run_case(["--operator-cr", "prometheus/prometheus-stack-kube-prom-prometheus"], rejected)
        if p.returncode == 0 or "unsafe" not in p.stderr:
            errors.append("subDir path component .. must be rejected")


def check(texts: dict[str, str], run_behavior: bool = True) -> list[str]:
    errors: list[str] = []
    _check_other_mount_filter(texts, errors)

    # 1. Prometheus & Alertmanager securityContext (GitOps + bootstrap parity)
    prom_vals = _load_values_object(texts["prom_gitops"])
    p_sc = ((prom_vals.get("prometheus") or {}).get("prometheusSpec") or {}).get("securityContext")
    a_sc = ((prom_vals.get("alertmanager") or {}).get("alertmanagerSpec") or {}).get("securityContext")
    if p_sc != EXPECTED_PROM_SC:
        errors.append(f"prometheus-stack.yaml prometheusSpec.securityContext mismatch: {p_sc!r}")
    if a_sc != EXPECTED_PROM_SC:
        errors.append(f"prometheus-stack.yaml alertmanagerSpec.securityContext mismatch: {a_sc!r}")
    if "D-E2-PROM-UID" not in texts["prom_gitops"]:
        errors.append("prometheus-stack.yaml missing D-E2-PROM-UID decision comment")

    boot_p_sc = _extract_set_json(texts["mon_boot"], "prometheus.prometheusSpec.securityContext")
    boot_a_sc = _extract_set_json(texts["mon_boot"], "alertmanager.alertmanagerSpec.securityContext")
    if boot_p_sc != EXPECTED_PROM_SC:
        errors.append(f"08-2-monitoring.sh prometheus.prometheusSpec.securityContext mismatch: {boot_p_sc!r}")
    if boot_a_sc != EXPECTED_PROM_SC:
        errors.append(f"08-2-monitoring.sh alertmanager.alertmanagerSpec.securityContext mismatch: {boot_a_sc!r}")

    # 2. Keycloak CR runAsGroup 10100 parity (keeping runAsUser 1000)
    kc_gitops_sc = _load_kc_gitops_sc(texts["kc_gitops"])
    kc_boot_sc = _load_kc_boot_sc(texts["kc_boot"])
    for label, sc in (("keycloak-cr.yaml", kc_gitops_sc), ("11-keycloak.sh", kc_boot_sc)):
        if not isinstance(sc, dict):
            errors.append(f"{label}: Keycloak CR securityContext missing")
            continue
        if sc.get("runAsUser") != 1000:
            errors.append(f"{label}: Keycloak CR runAsUser must remain 1000, got {sc.get('runAsUser')!r}")
        if sc.get("runAsGroup") != 10100:
            errors.append(f"{label}: Keycloak CR runAsGroup must be 10100 (D-E2-KC-GID), got {sc.get('runAsGroup')!r}")
    if kc_gitops_sc != kc_boot_sc:
        errors.append(f"Keycloak CR securityContext parity mismatch: {kc_gitops_sc!r} vs {kc_boot_sc!r}")
    if "D-E2-KC-GID" not in texts["kc_gitops"] or "D-E2-KC-GID" not in texts["kc_boot"]:
        errors.append("keycloak-cr.yaml and 11-keycloak.sh must both carry D-E2-KC-GID comment")

    # 3. Exact Config Audit acceptances (Harbor 6, OpenBao server, Keycloak KSV020 only; Prometheus/Alertmanager unaccepted)
    rules_data = json.loads(texts["rules"])
    cfg_rules = rules_data["configAudit"]
    parsed = [
        ((r["namespace"], r["kind"], r["name"], r["class"], r["id"]), set(r["checks"]))
        for r in cfg_rules
    ]
    for key, want_checks in EXPECTED_RULES.items():
        matches = [c for k, c in parsed if k == key]
        if len(matches) != 1:
            errors.append(f"compliance-acceptance-rules.json must hold exactly one rule {key} (found {len(matches)})")
        elif matches[0] != want_checks:
            errors.append(f"rule {key} check set mismatch: expected {sorted(want_checks)}, got {sorted(matches[0])}")

    # Fixed workloads must NOT have KSV020/KSV021 accepted; Keycloak must NOT have KSV021 accepted
    for r in cfg_rules:
        checks = set(r["checks"])
        if r["namespace"] == "monitoring" and r["name"] in (
            "prometheus-prometheus-stack-kube-prom-prometheus",
            "alertmanager-prometheus-stack-kube-prom-alertmanager",
        ) and (checks & {"KSV020", "KSV021"}):
            errors.append(f"fixed workload {r['name']} must not accept KSV020/KSV021")
        if r["namespace"] == "iam" and r["kind"] == "StatefulSet" and r["name"] == "keycloak" and "KSV021" in checks:
            errors.append("iam/StatefulSet/keycloak must not accept KSV021 (fixed by runAsGroup 10100)")

    for key in EXPECTED_I18N_KEYS:
        if texts["i18n"].count(f'"compliance.acceptedReason.{key}"') != 2:
            errors.append(f"i18n.ts must define ko and en entries for compliance.acceptedReason.{key}")

    for row_marker in (
        "ReplicaSet/harbor-nginx",
        "ReplicaSet/harbor-portal",
        "StatefulSet/harbor-redis",
        "StatefulSet/keycloak",
        "StatefulSet/openbao",
    ):
        if row_marker not in texts["doc"]:
            errors.append(f"docs/common/compliance-hardening.md missing {row_marker}")

    # 4. Migration helper invariants + behavior tests
    if "-mindepth 1" not in texts["migrate"]:
        errors.append("migrate-pv-ownership.sh must use -mindepth 1 so PV root dir (65534) is never chowned")
    if "nfs.csi.k8s.io" not in texts["migrate"]:
        errors.append("migrate-pv-ownership.sh must enforce nfs.csi.k8s.io PV driver")
    if "D-E2-PV-MIGRATE-OP" not in texts["migrate"]:
        errors.append("migrate-pv-ownership.sh missing D-E2-PV-MIGRATE-OP operator-managed StatefulSet guard")
    if run_behavior and not errors:
        _run_migrate_behavior_tests(texts["migrate"], errors)

    # 5. Regression wiring
    if "check R429 " not in texts["reg"] or "check-cis-e2-stateful-uid.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R429 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("prometheus gitops runAsUser reverted to 1000", "prom_gitops", "              runAsUser: 10100\n              runAsGroup: 10100\n              fsGroup: 10100\n              seccompProfile:\n                type: RuntimeDefault\n            podMetadata:", "              runAsUser: 1000\n              runAsGroup: 10100\n              fsGroup: 10100\n              seccompProfile:\n                type: RuntimeDefault\n            podMetadata:"),
    ("prometheus gitops fsGroup reverted to 2000", "prom_gitops", "              fsGroup: 10100\n              seccompProfile:\n                type: RuntimeDefault\n            podMetadata:", "              fsGroup: 2000\n              seccompProfile:\n                type: RuntimeDefault\n            podMetadata:"),
    ("alertmanager gitops runAsGroup reverted to 2000", "prom_gitops", "              runAsUser: 10100\n              runAsGroup: 10100\n              fsGroup: 10100\n              seccompProfile:\n                type: RuntimeDefault\n            resources:", "              runAsUser: 10100\n              runAsGroup: 2000\n              fsGroup: 10100\n              seccompProfile:\n                type: RuntimeDefault\n            resources:"),
    ("prometheus bootstrap securityContext dropped", "mon_boot", '    --set-json \'prometheus.prometheusSpec.securityContext={"runAsNonRoot":true,"runAsUser":10100,"runAsGroup":10100,"fsGroup":10100,"seccompProfile":{"type":"RuntimeDefault"}}\' \\\n', ""),
    ("alertmanager bootstrap runAsGroup drifted", "mon_boot", 'alertmanager.alertmanagerSpec.securityContext={"runAsNonRoot":true,"runAsUser":10100,"runAsGroup":10100', 'alertmanager.alertmanagerSpec.securityContext={"runAsNonRoot":true,"runAsUser":10100,"runAsGroup":2000'),
    ("keycloak gitops runAsGroup dropped", "kc_gitops", "              runAsUser: 1000\n              runAsGroup: 10100\n", "              runAsUser: 1000\n"),
    ("keycloak bootstrap runAsGroup dropped", "kc_boot", "              runAsUser: 1000\n              runAsGroup: 10100\n", "              runAsUser: 1000\n"),
    ("keycloak acceptance wrongly adds KSV021", "rules", '"id": "cfg:iam:StatefulSet:keycloak:accept",\n      "scope": "namespace",\n      "namespace": "iam",\n      "kind": "StatefulSet",\n      "name": "keycloak",\n      "checks": [\n        "KSV020"\n      ],', '"id": "cfg:iam:StatefulSet:keycloak:accept",\n      "scope": "namespace",\n      "namespace": "iam",\n      "kind": "StatefulSet",\n      "name": "keycloak",\n      "checks": [\n        "KSV020",\n        "KSV021"\n      ],'),
    ("openbao server acceptance dropped", "rules", '"id": "cfg:storage:StatefulSet:openbao:accept"', '"id": "cfg:storage:StatefulSet:openbao:removed"'),
    ("harbor-redis acceptance dropped", "rules", '"id": "cfg:devtools:StatefulSet:harbor-redis:accept"', '"id": "cfg:devtools:StatefulSet:harbor-redis:removed"'),
    ("prometheus wrongly allowlisted for KSV020", "rules", '"id": "cfg:monitoring:StatefulSet:prometheus-prometheus-stack-kube-prom-prometheus:accept",\n      "scope": "namespace",\n      "namespace": "monitoring",\n      "kind": "StatefulSet",\n      "name": "prometheus-prometheus-stack-kube-prom-prometheus",\n      "checks": [\n        "KSV011"\n      ],', '"id": "cfg:monitoring:StatefulSet:prometheus-prometheus-stack-kube-prom-prometheus:accept",\n      "scope": "namespace",\n      "namespace": "monitoring",\n      "kind": "StatefulSet",\n      "name": "prometheus-prometheus-stack-kube-prom-prometheus",\n      "checks": [\n        "KSV011",\n        "KSV020"\n      ],'),
    ("i18n keycloakUid1000 dropped", "i18n", '"compliance.acceptedReason.keycloakUid1000"', '"compliance.acceptedReason.removed"'),
    ("migrate script removes -mindepth 1 on chown", "migrate", 'find "${DIR_REAL}" -xdev -mindepth 1 -exec chown -h "${T_PAIR}" -- {} +', 'find "${DIR_REAL}" -xdev -exec chown -h "${T_PAIR}" -- {} +'),
    ("migrate script removes non-NFS PV guard", "migrate", 'if [[ "${CSI_DRIVER}" != "nfs.csi.k8s.io" ]]; then', 'if [[ "${CSI_DRIVER}" == "never-match" ]]; then'),
    ("migrate script removes operator-owned guard", "migrate", 'if [[ -n "${OWNER_REF}" && -z "${OPERATOR_CR}" && "${ALLOW_OWNED}" == false ]]; then', 'if false; then'),
    ("migrate script removes PVC ownership guard", "migrate", '[[ "${mounted}" == true ]] || die "PVC ${NAMESPACE}/${pvc} is not mounted by ${WORKLOAD_CANON}"', ': # removed'),
    ("migrate jq filter uses the selector string as the pod", "migrate", '. as $p | select($p.metadata.namespace != $ns or ([ $selector | split(",")[] | split("=") as $pair | ($p.metadata.labels // {})[$pair[0]] == $pair[1] ]', 'select(.metadata.namespace != $ns or ([ $selector | split(",")[] | split("=") as $pair | .metadata.labels[$pair[0]] == $pair[1] ]'),
    ("migrate script removes external pod mount guard", "migrate", '[[ -z "${other_mount}" ]] || die "PVC ${NAMESPACE}/${pvc} is also mounted by running pod outside ${WORKLOAD_CANON}: ${other_mount}"', ': # removed'),
    ("migrate script removes pre-chown recovery", "migrate", 'if [[ "${CHOWN_STARTED}" == false ]]; then', 'if false; then'),
    ("migrate script removes post-chown rollback report", "migrate", 'echo "Failure during/after chown; no automatic restart.', 'echo "Failure during/after chown; automatic restart.'),
    ("migrate script accepts parent path component", "migrate", '*"/../"*', '*"/./"*'),
    ("R429 wiring dropped", "reg", "check R429 ", "check R9999 "),
]



def _check_other_mount_filter(texts, errs):
    """Run the script's real jq filter on realistic pod JSON (a bug here passed the stub fixtures)."""
    import json as _json, re as _re, shutil as _sh, subprocess as _sp
    if not _sh.which("jq"):
        errs.append("jq not installed: cannot verify the external-mount filter"); return
    m = _re.search(r"other_mount=\"\$\(printf '%s' \"\$\{ALL_PODS\}\" \| jq -r --arg ns \"\$\{NAMESPACE\}\" --arg pvc \"\$\{pvc\}\" --arg selector \"\$\{SELECTOR\}\" '(.*?)'\)\"", texts["migrate"])
    if not m:
        errs.append("migrate-pv-ownership.sh external-mount jq filter not found"); return
    flt = m.group(1)
    def pod(ns, name, labels, pvc):
        return {"metadata": {"namespace": ns, "name": name, "labels": labels}, "status": {"phase": "Running"},
                "spec": {"volumes": [{"name": "d", "persistentVolumeClaim": {"claimName": pvc}}]}}
    pods = {"items": [pod("monitoring", "own-0", {"app": "x", "tier": "db"}, "data-own-0"),
                      pod("monitoring", "intruder", {"app": "y"}, "data-own-0"),
                      pod("other", "far", {"app": "x", "tier": "db"}, "data-own-0"),
                      {"metadata": {"namespace": "monitoring", "name": "nolabels"}, "status": {"phase": "Running"}, "spec": {}}]}
    try:
        out = _sp.run(["jq", "-r", "--arg", "ns", "monitoring", "--arg", "pvc", "data-own-0", "--arg", "selector", "app=x,tier=db", flt],
                      input=_json.dumps(pods), capture_output=True, text=True, check=True).stdout.split()
    except _sp.CalledProcessError as exc:
        errs.append(f"external-mount jq filter fails on realistic pod JSON: {exc.stderr.strip()[:160]}"); return
    if sorted(out) != ["monitoring/intruder", "other/far"]:
        errs.append(f"external-mount jq filter reported {out}, expected monitoring/intruder and other/far only")

def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts, run_behavior=True)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new)
            run_beh = key == "migrate"
            if not check(mutated, run_behavior=run_beh):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R429 CIS E2 stateful UID/GID hardening & acceptances ({len(MUTATIONS)} mutations rejected)")
        return 0
    print("PASS: R429 CIS E2 stateful UID/GID hardening & acceptances")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
