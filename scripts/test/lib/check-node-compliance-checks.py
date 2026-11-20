#!/usr/bin/env python3
"""R385: Node compliance resolution for KCV0059 (scanner passwd/group mounts) & KCV0056 (allowlist + live verifier).

Verifies:
1. gitops/charts/narwhal-apps/templates/trivy-operator.yaml preserves all 8 chart 0.27.0 default
   nodeCollector hostPath volumes/mounts AND adds read-only /etc/passwd and /etc/group File mounts
   (and renders cleanly via helm template when available).
2. portal/src/lib/compliance-accepted.ts normalizes KCV IDs and allowlists ONLY KCV0056 (upstream),
   never KCV0059, failing closed on empty/unlisted inputs.
3. portal/src/lib/compliance.ts reuses infraAuditMatcher across Infra list/detail/summary and
   Compliance Frameworks (Node targets) while keeping raw counts visible and failing closed when
   checks[] is omitted.
4. portal/src/types/compliance.ts, portal/src/lib/i18n.ts, and docs/common/compliance-hardening.md
   stay aligned (Infra allowlist table + node-collector root-cause resolution).
5. scripts/test/verify-cluster.sh defines and invokes verify_node_file_hardening under nodes, and
   behavioral fixture tests prove it passes compliant CIS 1.1.9/1.1.12 layouts and catches regressions.
6. scripts/test/regression-check-kakao.sh wires R385 with --mutation-verify.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "trivy": ROOT / "gitops/charts/narwhal-apps/templates/trivy-operator.yaml",
    "accepted": ROOT / "portal/src/lib/compliance-accepted.ts",
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "infra_list": ROOT / "portal/src/components/compliance/infra-audit-list.tsx",
    "types": ROOT / "portal/src/types/compliance.ts",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "verify": ROOT / "scripts/test/verify-cluster.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

HELM_COMMENT_RE = re.compile(r"\A\{\{-\s*/\*.*?\*/\s*-?\}\}\s*", re.S)

DEFAULT_NODE_COLLECTOR_MOUNTS = {
    "var-lib-etcd": "/var/lib/etcd",
    "var-lib-kubelet": "/var/lib/kubelet",
    "var-lib-kube-scheduler": "/var/lib/kube-scheduler",
    "var-lib-kube-controller-manager": "/var/lib/kube-controller-manager",
    "etc-systemd": "/etc/systemd",
    "lib-systemd": "/lib/systemd/",
    "etc-kubernetes": "/etc/kubernetes",
    "etc-cni-netd": "/etc/cni/net.d/",
}

DEFAULT_NODE_COLLECTOR_VOLUMES = {
    "var-lib-etcd": "/var/lib/etcd",
    "var-lib-kubelet": "/var/lib/kubelet",
    "var-lib-kube-scheduler": "/var/lib/kube-scheduler",
    "var-lib-kube-controller-manager": "/var/lib/kube-controller-manager",
    "etc-systemd": "/etc/systemd",
    "lib-systemd": "/lib/systemd",
    "etc-kubernetes": "/etc/kubernetes",
    "etc-cni-netd": "/etc/cni/net.d/",
}


def _extract_node_hardening_fn(verify_text: str) -> str:
    m = re.search(r"verify_node_file_hardening\(\)\s*\{.*?\n\}", verify_text, re.S)
    assert m, "verify_node_file_hardening() not found in verify-cluster.sh"
    return m.group(0)


def _bash_interpreters() -> list[str]:
    candidates: list[str] = []
    seen_real: set[str] = set()
    for raw in (shutil.which("bash"), "/bin/bash"):
        if not raw:
            continue
        p = Path(raw)
        if not p.exists():
            continue
        real = str(p.resolve())
        if real not in seen_real:
            seen_real.add(real)
            candidates.append(str(p))
    return candidates


def _run_behavioral_verify_tests(verify_text: str) -> list[str]:
    errors: list[str] = []
    fn_src = _extract_node_hardening_fn(verify_text)

    interpreters = _bash_interpreters()
    if not interpreters:
        return ["bash binary required for R385 behavioral check"]

    import pwd
    import grp
    cur_user = pwd.getpwuid(os.getuid()).pw_name
    cur_group = grp.getgrgid(os.getgid()).gr_name
    owner = f"{cur_user}:{cur_group}"

    script_tpl = f"""#!/usr/bin/env bash
set -euo pipefail
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
pass() {{ PASS_COUNT=$((PASS_COUNT + 1)); echo "PASS: $1"; }}
fail() {{ FAIL_COUNT=$((FAIL_COUNT + 1)); echo "FAIL: $1"; }}
warn() {{ WARN_COUNT=$((WARN_COUNT + 1)); echo "WARN: $1"; }}
{fn_src}
verify_node_file_hardening "$1" "{owner}" "{owner}"
echo "SUMMARY pass=$PASS_COUNT fail=$FAIL_COUNT warn=$WARN_COUNT"
want_pass="${{2:-2}}"
[ "$FAIL_COUNT" -eq 0 ] && [ "$PASS_COUNT" -eq "$want_pass" ]
"""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        runner = tmp_path / "run.sh"
        runner.write_text(script_tpl, encoding="utf-8")
        wrong_owner_script = script_tpl.replace(f'"{owner}" "{owner}"', f'"{owner}" "wronguser:wronggroup"')
        wrong_runner = tmp_path / "run_wrong_owner.sh"
        wrong_runner.write_text(wrong_owner_script, encoding="utf-8")

        def make_root(
            name: str,
            cni_mode: int | None,
            etcd_mode: int | None,
            *,
            etcd_manifest: bool = False,
            cni_dir_mode: int = 0o700,
            symlink_target_mode: int | None = None,
        ) -> Path:
            r = tmp_path / name
            cni_dir = r / "etc/cni/net.d"
            cni_dir.mkdir(parents=True)
            if cni_mode is not None:
                f = cni_dir / "05-cilium.conflist"
                f.write_text("{}\n", encoding="utf-8")
                os.chmod(f, cni_mode)
            if symlink_target_mode is not None:
                target = r / "opt/custom-cni.conf"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}\n", encoding="utf-8")
                os.chmod(target, symlink_target_mode)
                (cni_dir / "10-symlink.conflist").symlink_to(target)
            os.chmod(cni_dir, cni_dir_mode)
            if etcd_mode is not None:
                etcd_dir = r / "var/lib/etcd"
                etcd_dir.mkdir(parents=True)
                os.chmod(etcd_dir, etcd_mode)
            if etcd_manifest:
                manifest = r / "etc/kubernetes/manifests/etcd.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text("kind: Pod\n", encoding="utf-8")
            return r

        good = make_root("good", 0o600, 0o700)
        worker = make_root("worker", 0o600, None, etcd_manifest=False)
        etcd_missing = make_root("etcd_missing", 0o600, None, etcd_manifest=True)
        bad_cni_644 = make_root("bad_cni_644", 0o644, 0o700)
        bad_cni_700 = make_root("bad_cni_700", 0o700, 0o700)
        bad_cni_suid = make_root("bad_cni_suid", 0o4600, 0o700)
        bad_cni_symlink = make_root("bad_cni_symlink", 0o600, 0o700, symlink_target_mode=0o644)
        empty_cni = make_root("empty_cni", None, 0o700)
        unreadable_cni = make_root("unreadable_cni", 0o600, 0o700, cni_dir_mode=0o000)
        bad_etcd_755 = make_root("bad_etcd_755", 0o600, 0o755)
        bad_etcd_suid = make_root("bad_etcd_suid", 0o600, 0o4700)
        bad_etcd_sgid = make_root("bad_etcd_sgid", 0o600, 0o2700)

        try:
            for bash_bin in interpreters:
                tag = f"[{bash_bin}]"

                # 1. Compliant master layout (600 CNI file, 700 etcd dir)
                res_good = subprocess.run([bash_bin, str(runner), str(good), "2"], capture_output=True, text=True)
                if res_good.returncode != 0 or "CIS 1.1.9 CNI file perms: 1 file(s) <=600" not in res_good.stdout:
                    errors.append(f"{tag} rejected compliant master fixture: {res_good.stdout} {res_good.stderr}")

                # 2. Compliant worker layout (600 CNI file, no etcd dir or manifest)
                res_worker = subprocess.run([bash_bin, str(runner), str(worker), "1"], capture_output=True, text=True)
                if res_worker.returncode != 0 or "SUMMARY pass=1 fail=0" not in res_worker.stdout:
                    errors.append(f"{tag} rejected compliant worker fixture: {res_worker.stdout} {res_worker.stderr}")

                # 3. etcd.yaml present but /var/lib/etcd missing
                res_etcd_missing = subprocess.run([bash_bin, str(runner), str(etcd_missing), "2"], capture_output=True, text=True)
                if res_etcd_missing.returncode == 0 or "FAIL: CIS 1.1.12 etcd data dir missing or symlink" not in res_etcd_missing.stdout:
                    errors.append(f"{tag} missed missing etcd dir when etcd.yaml exists: {res_etcd_missing.stdout} {res_etcd_missing.stderr}")

                # 4. Permissive/special CNI files (644, 0700, 4600, symlink->644)
                for label, fixture in (
                    ("644", bad_cni_644),
                    ("0700", bad_cni_700),
                    ("4600", bad_cni_suid),
                    ("symlink->644", bad_cni_symlink),
                ):
                    res = subprocess.run([bash_bin, str(runner), str(fixture), "2"], capture_output=True, text=True)
                    if res.returncode == 0 or "FAIL: CIS 1.1.9 CNI file perms" not in res.stdout:
                        errors.append(f"{tag} missed {label} CNI file: {res.stdout} {res.stderr}")

                # 5. Empty CNI dir
                res_empty = subprocess.run([bash_bin, str(runner), str(empty_cni), "2"], capture_output=True, text=True)
                if res_empty.returncode == 0 or "none found" not in res_empty.stdout:
                    errors.append(f"{tag} missed empty CNI dir: {res_empty.stdout} {res_empty.stderr}")

                # 6. Unreadable CNI dir (000) must report unreadable, not none found (non-root only)
                if os.geteuid() != 0:
                    res_unreadable = subprocess.run([bash_bin, str(runner), str(unreadable_cni), "2"], capture_output=True, text=True)
                    if (
                        res_unreadable.returncode == 0
                        or "FAIL: CIS 1.1.9 CNI config dir unreadable" not in res_unreadable.stdout
                        or "none found" in res_unreadable.stdout
                    ):
                        errors.append(f"{tag} misreported 000 CNI dir: {res_unreadable.stdout} {res_unreadable.stderr}")

                # 7. Permissive/special etcd dirs (755, 4700, 2700)
                for label, fixture in (("755", bad_etcd_755), ("4700", bad_etcd_suid), ("2700", bad_etcd_sgid)):
                    res = subprocess.run([bash_bin, str(runner), str(fixture), "2"], capture_output=True, text=True)
                    if res.returncode == 0 or "FAIL: CIS 1.1.12 etcd data dir" not in res.stdout:
                        errors.append(f"{tag} missed {label} etcd dir: {res.stdout} {res.stderr}")

                # 8. Wrong etcd owner
                res_owner = subprocess.run([bash_bin, str(wrong_runner), str(good), "2"], capture_output=True, text=True)
                if res_owner.returncode == 0 or "FAIL: CIS 1.1.12 etcd data dir" not in res_owner.stdout:
                    errors.append(f"{tag} missed wrong etcd owner: {res_owner.stdout} {res_owner.stderr}")
        finally:
            unreadable_cni_dir = unreadable_cni / "etc/cni/net.d"
            if unreadable_cni_dir.exists():
                os.chmod(unreadable_cni_dir, 0o700)

    return errors


def check(texts: dict[str, str], run_behavioral: bool = False) -> list[str]:
    errors: list[str] = []

    # 1. trivy-operator.yaml nodeCollector mounts + podAnnotations checksum
    trivy_body = HELM_COMMENT_RE.sub("", texts["trivy"], count=1)
    trivy_doc = yaml.safe_load(trivy_body)
    values_obj = ((trivy_doc.get("spec") or {}).get("source") or {}).get("helm") or {}
    values_obj = values_obj.get("valuesObject") or {}
    nc = values_obj.get("nodeCollector") or {}
    mounts = {m.get("name"): m for m in (nc.get("volumeMounts") or []) if isinstance(m, dict)}
    volumes = {v.get("name"): v for v in (nc.get("volumes") or []) if isinstance(v, dict)}

    for name, mpath in DEFAULT_NODE_COLLECTOR_MOUNTS.items():
        if name not in mounts or mounts[name].get("mountPath") != mpath or mounts[name].get("readOnly") is not True:
            errors.append(f"trivy-operator.yaml missing default nodeCollector.volumeMount {name}:{mpath} readOnly:true")
    for name, hpath in DEFAULT_NODE_COLLECTOR_VOLUMES.items():
        hp = (volumes.get(name) or {}).get("hostPath") or {}
        if hp.get("path") != hpath:
            errors.append(f"trivy-operator.yaml missing default nodeCollector.volume {name}:{hpath}")

    for name, fpath in (("etc-passwd", "/etc/passwd"), ("etc-group", "/etc/group")):
        m = mounts.get(name) or {}
        v = (volumes.get(name) or {}).get("hostPath") or {}
        if m.get("mountPath") != fpath or m.get("readOnly") is not True:
            errors.append(f"trivy-operator.yaml missing read-only nodeCollector.volumeMount {name} -> {fpath}")
        if v.get("path") != fpath or v.get("type") != "File":
            errors.append(f"trivy-operator.yaml missing hostPath File volume {name} -> {fpath}")

    if "etc-shadow" in mounts or any(m.get("mountPath") == "/etc/shadow" for m in mounts.values()) or any(
        ((v.get("hostPath") or {}).get("path") == "/etc/shadow") for v in volumes.values()
    ):
        errors.append("trivy-operator.yaml must never mount /etc/shadow")

    expected_nc_hash = hashlib.sha256(
        json.dumps(
            {"volumes": nc.get("volumes") or [], "volumeMounts": nc.get("volumeMounts") or []},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    pod_ann = values_obj.get("podAnnotations") or {}
    if pod_ann.get("checksum/node-collector-mounts") != expected_nc_hash:
        errors.append(
            f"trivy-operator.yaml podAnnotations.checksum/node-collector-mounts={pod_ann.get('checksum/node-collector-mounts')!r} "
            f"must match sha256({expected_nc_hash})"
        )

    # 2. compliance-accepted.ts: KCV normalization + KCV0056 only (never KCV0059)
    acc = texts["accepted"]
    for needle in (
        "AVD-KCV-0*(\\d+)",
        "ACCEPTED_INFRA_RULES",
        "export function matchInfraException(",
        '"compliance.acceptedReason.infraCniDirGlob"',
        "CMD-0009",
    ):
        if needle not in acc:
            errors.append(f"compliance-accepted.ts missing {needle!r}")

    infra_section_m = re.search(r"export const ACCEPTED_INFRA_RULES.*?\]\n", acc, re.S)
    if not infra_section_m:
        errors.append("compliance-accepted.ts missing ACCEPTED_INFRA_RULES definition")
    else:
        infra_block = infra_section_m.group(0)
        if '"KCV0056"' not in infra_block or '"upstream"' not in infra_block:
            errors.append("ACCEPTED_INFRA_RULES must register KCV0056 as upstream")
        if "KCV0059" in infra_block:
            errors.append("ACCEPTED_INFRA_RULES must NOT allowlist KCV0059 (fixed via /etc/passwd & /etc/group mounts)")

    # 3. compliance.ts & infra-audit-list.tsx & types & i18n
    comp = texts["compliance"]
    for needle in (
        "function infraAuditMatcher(",
        "function classifyInfraAuditItem(",
        'if (resolved.kind === "Node")',
        "acceptedInfraFailures",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing {needle!r}")

    infra_list = texts["infra_list"]
    for needle in ("acceptedFailures", "hasActionable && acceptedFailures > 0", "+{acceptedFailures}"):
        if needle not in infra_list:
            errors.append(f"infra-audit-list.tsx missing mixed-node accepted badge token {needle!r}")

    if "acceptedInfraFailures: CheckSummary" not in texts["types"]:
        errors.append("portal/src/types/compliance.ts missing acceptedInfraFailures")

    for key in ("compliance.counter.infraBreakdown", "compliance.table.infraActionableSummary", "compliance.acceptedReason.infraCniDirGlob"):
        if texts["i18n"].count(f'"{key}"') < 2:
            errors.append(f"portal/src/lib/i18n.ts must define ko and en translations for {key}")
    for needle in ("실행한 단일 로컬 노드만 검사", "CMD-0009", "single local node", "other nodes in a multi-node cluster"):
        if needle not in texts["i18n"]:
            errors.append(f"portal/src/lib/i18n.ts missing KCV0056 single-node/re-evaluation honesty text {needle!r}")

    # 4. docs/common/compliance-hardening.md
    doc = texts["doc"]
    for needle in (
        "\n### Infra allowlist\n",
        "| cluster | Node/* | KCV0056 | upstream |",
        "## Node collector & CIS 1.1.9 / 1.1.12 resolution",
        "D-NODE-COLLECTOR-PASSWD",
        "D-NODE-COLLECTOR-PASSWD-ROLLOUT",
        "rollout restart deploy/trivy-operator",
        "not** covered by `verify-cluster.sh`",
        "must be removed",
    ):
        if needle not in doc:
            errors.append(f"docs/common/compliance-hardening.md missing {needle!r}")
    if "CNI dir 0700 not traversable by the scanner" in doc:
        errors.append("docs/common/compliance-hardening.md still contains debunked CNI 0700 traversal claim")

    # 5. verify-cluster.sh + regression wiring
    ver = texts["verify"]
    if "verify_node_file_hardening" not in ver:
        errors.append("scripts/test/verify-cluster.sh missing verify_node_file_hardening")
    else:
        fn_src = _extract_node_hardening_fn(ver)
        for token in ('${priv[@]+"${priv[@]}"}', "8#7177", "8#7077", "find -L", "stat -L", "CNI config dir unreadable"):
            if token not in fn_src:
                errors.append(f"verify_node_file_hardening missing hardening check token {token!r}")
        if re.search(r'(?<!\+)\"\$\{priv\[@\]\}\"', fn_src):
            errors.append("verify_node_file_hardening must guard every priv expansion with ${priv[@]+\"${priv[@]}\"} for bash 3.2 set -u")
    nodes_block_m = re.search(r'if should_run "nodes"; then[\s\S]*?# 2\. KUBE-VIP', ver)
    if not nodes_block_m or "verify_node_file_hardening" not in nodes_block_m.group(0):
        errors.append("scripts/test/verify-cluster.sh nodes section must call verify_node_file_hardening")

    if "check R385 " not in texts["reg"] or "check-node-compliance-checks.py --mutation-verify" not in texts["reg"]:
        errors.append("scripts/test/regression-check-kakao.sh must wire R385 with --mutation-verify")

    if run_behavioral and not errors:
        errors.extend(_run_behavioral_verify_tests(ver))

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("drop etc-passwd volumeMount", "trivy", "            - name: etc-passwd\n              mountPath: /etc/passwd\n              readOnly: true\n", ""),
    ("drop etc-group volume", "trivy", "            - name: etc-group\n              hostPath:\n                path: /etc/group\n                type: File\n", ""),
    ("drop default etc-cni-netd mount", "trivy", "            - name: etc-cni-netd\n              mountPath: /etc/cni/net.d/\n              readOnly: true\n", ""),
    ("mount /etc/shadow", "trivy", "path: /etc/group", "path: /etc/shadow"),
    ("stale nodeCollector checksum annotation", "trivy", "checksum/node-collector-mounts: \"f18e6e663efa8ebf898e70aad50659110412217842662f0a67e150be8df9156e\"", "checksum/node-collector-mounts: \"stale\""),
    ("allowlist KCV0059", "accepted", '["KCV0056"]', '["KCV0056", "KCV0059"]'),
    ("reclassify KCV0056 as accept", "accepted", '    ["KCV0056"],\n    "upstream",', '    ["KCV0056"],\n    "accept",'),
    ("drop KCV normalization", "accepted", "AVD-KCV-0*(\\d+)", "AVD-KCV-DISABLED-(\\d+)"),
    ("drop Node matcher in frameworks", "compliance", 'if (resolved.kind === "Node")', 'if (resolved.kind === "NeverNode")'),
    ("drop mixed-node accepted badge", "infra_list", "{hasActionable && acceptedFailures > 0 && (", "{false && acceptedFailures > 0 && ("),
    ("drop acceptedInfraFailures type", "types", "acceptedInfraFailures: CheckSummary", "removedInfra: CheckSummary"),
    ("drop i18n infraCniDirGlob", "i18n", '"compliance.acceptedReason.infraCniDirGlob"', '"compliance.acceptedReason.removed"'),
    ("drop i18n single-node honesty text", "i18n", "other nodes in a multi-node cluster", "all cluster nodes"),
    ("drop Infra allowlist table in docs", "doc", "\n### Infra allowlist\n", "\n### Removed\n"),
    ("drop operator rollout restart note in docs", "doc", "rollout restart deploy/trivy-operator", "noop"),
    ("drop bash 3.2 empty-array guard", "verify", '${priv[@]+"${priv[@]}"}', '"${priv[@]}"'),
    ("weaken CNI special-bit mask", "verify", "8#7177", "8#177"),
    ("weaken etcd special-bit mask", "verify", "8#7077", "8#077"),
    ("drop symlink follow in find/stat", "verify", "find -L", "find"),
    ("drop verify_node_file_hardening call", "verify", "  if ${IS_CLUSTER_NODE}; then\n    verify_node_file_hardening", "  if ${IS_CLUSTER_NODE}; then\n    true"),
    ("drop R385 wiring", "reg", "check R385 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts, run_behavioral=True)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    # Optional helm template check on real tree
    if shutil.which("helm") is not None:
        helm_res = subprocess.run(
            ["helm", "template", "narwhal-apps", str(ROOT / "gitops/charts/narwhal-apps"), "--show-only", "templates/trivy-operator.yaml"],
            capture_output=True,
            text=True,
        )
        if helm_res.returncode != 0:
            print(f"FAIL: helm template trivy-operator.yaml failed: {helm_res.stderr}", file=sys.stderr)
            return 1
        rendered = yaml.safe_load(helm_res.stdout)
        nc = ((rendered.get("spec") or {}).get("source") or {}).get("helm", {}).get("valuesObject", {}).get("nodeCollector", {})
        mount_paths = {m.get("mountPath") for m in nc.get("volumeMounts", [])}
        if "/etc/passwd" not in mount_paths or "/etc/group" not in mount_paths:
            print("FAIL: rendered trivy-operator.yaml missing /etc/passwd or /etc/group mount", file=sys.stderr)
            return 1

    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            mutated = copy.deepcopy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target not found for {name}", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated, run_behavioral=False):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R385 node compliance KCV0059/KCV0056 ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R385 node compliance KCV0059/KCV0056")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
