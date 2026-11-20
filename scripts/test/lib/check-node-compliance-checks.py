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


def _run_behavioral_verify_tests(verify_text: str) -> list[str]:
    errors: list[str] = []
    fn_src = _extract_node_hardening_fn(verify_text)

    if shutil.which("bash") is None:
        return ["bash binary required for R385 behavioral check"]

    import pwd
    import grp
    cur_user = pwd.getpwuid(os.getuid()).pw_name
    cur_group = grp.getgrgid(os.getgid()).gr_name
    owner = f"{cur_user}:{cur_group}"

    script_tpl = f"""#!/usr/bin/env bash
set -uo pipefail
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
pass() {{ PASS_COUNT=$((PASS_COUNT + 1)); echo "PASS: $1"; }}
fail() {{ FAIL_COUNT=$((FAIL_COUNT + 1)); echo "FAIL: $1"; }}
warn() {{ WARN_COUNT=$((WARN_COUNT + 1)); echo "WARN: $1"; }}
{fn_src}
verify_node_file_hardening "$1" "{owner}" "{owner}"
echo "SUMMARY pass=$PASS_COUNT fail=$FAIL_COUNT warn=$WARN_COUNT"
[ "$FAIL_COUNT" -eq 0 ] && [ "$PASS_COUNT" -ge 2 ]
"""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        runner = tmp_path / "run.sh"
        runner.write_text(script_tpl, encoding="utf-8")

        def make_root(name: str, cni_mode: int | None, etcd_mode: int | None) -> Path:
            r = tmp_path / name
            cni_dir = r / "etc/cni/net.d"
            cni_dir.mkdir(parents=True)
            if cni_mode is not None:
                f = cni_dir / "05-cilium.conflist"
                f.write_text("{}\n", encoding="utf-8")
                os.chmod(f, cni_mode)
            os.chmod(cni_dir, 0o700)
            if etcd_mode is not None:
                etcd_dir = r / "var/lib/etcd"
                etcd_dir.mkdir(parents=True)
                os.chmod(etcd_dir, etcd_mode)
            return r

        # 1. Compliant layout (600 CNI file, 700 etcd dir) must pass
        good = make_root("good", 0o600, 0o700)
        res_good = subprocess.run(["bash", str(runner), str(good)], capture_output=True, text=True)
        if res_good.returncode != 0 or "CIS 1.1.9 CNI file perms: 1 file(s) <=600" not in res_good.stdout:
            errors.append(f"verify_node_file_hardening rejected compliant fixture: {res_good.stdout} {res_good.stderr}")

        # 2. Permissive CNI file (644) must fail CIS 1.1.9
        bad_cni = make_root("bad_cni", 0o644, 0o700)
        res_bad_cni = subprocess.run(["bash", str(runner), str(bad_cni)], capture_output=True, text=True)
        if res_bad_cni.returncode == 0 or "FAIL: CIS 1.1.9 CNI file perms" not in res_bad_cni.stdout:
            errors.append(f"verify_node_file_hardening missed 644 CNI file: {res_bad_cni.stdout}")

        # 3. Empty CNI dir (no config files) must fail CIS 1.1.9
        empty_cni = make_root("empty_cni", None, 0o700)
        res_empty = subprocess.run(["bash", str(runner), str(empty_cni)], capture_output=True, text=True)
        if res_empty.returncode == 0 or "none found" not in res_empty.stdout:
            errors.append(f"verify_node_file_hardening missed empty CNI dir: {res_empty.stdout}")

        # 4. Permissive etcd dir (755) must fail CIS 1.1.12
        bad_etcd = make_root("bad_etcd", 0o600, 0o755)
        res_bad_etcd = subprocess.run(["bash", str(runner), str(bad_etcd)], capture_output=True, text=True)
        if res_bad_etcd.returncode == 0 or "FAIL: CIS 1.1.12 etcd data dir" not in res_bad_etcd.stdout:
            errors.append(f"verify_node_file_hardening missed 755 etcd dir: {res_bad_etcd.stdout}")

        # 5. Wrong etcd owner must fail CIS 1.1.12
        wrong_owner_script = script_tpl.replace(f'"{owner}" "{owner}"', f'"{owner}" "wronguser:wronggroup"')
        wrong_runner = tmp_path / "run_wrong_owner.sh"
        wrong_runner.write_text(wrong_owner_script, encoding="utf-8")
        res_owner = subprocess.run(["bash", str(wrong_runner), str(good)], capture_output=True, text=True)
        if res_owner.returncode == 0 or "FAIL: CIS 1.1.12 etcd data dir" not in res_owner.stdout:
            errors.append(f"verify_node_file_hardening missed wrong etcd owner: {res_owner.stdout}")

    return errors


def check(texts: dict[str, str], run_behavioral: bool = False) -> list[str]:
    errors: list[str] = []

    # 1. trivy-operator.yaml nodeCollector mounts
    trivy_body = HELM_COMMENT_RE.sub("", texts["trivy"], count=1)
    trivy_doc = yaml.safe_load(trivy_body)
    nc = (((trivy_doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject", {}).get("nodeCollector") or {}
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

    # 2. compliance-accepted.ts: KCV normalization + KCV0056 only (never KCV0059)
    acc = texts["accepted"]
    for needle in (
        "AVD-KCV-0*(\\d+)",
        "ACCEPTED_INFRA_RULES",
        "export function matchInfraException(",
        '"compliance.acceptedReason.infraCniDirGlob"',
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

    # 3. compliance.ts & types & i18n
    comp = texts["compliance"]
    for needle in (
        "function infraAuditMatcher(",
        "function classifyInfraAuditItem(",
        'if (resolved.kind === "Node")',
        "acceptedInfraFailures",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing {needle!r}")

    if "acceptedInfraFailures: CheckSummary" not in texts["types"]:
        errors.append("portal/src/types/compliance.ts missing acceptedInfraFailures")

    for key in ("compliance.counter.infraBreakdown", "compliance.table.infraActionableSummary", "compliance.acceptedReason.infraCniDirGlob"):
        if texts["i18n"].count(f'"{key}"') < 2:
            errors.append(f"portal/src/lib/i18n.ts must define ko and en translations for {key}")

    # 4. docs/common/compliance-hardening.md
    doc = texts["doc"]
    for needle in (
        "\n### Infra allowlist\n",
        "| cluster | Node/* | KCV0056 | upstream |",
        "## Node collector & CIS 1.1.9 / 1.1.12 resolution",
        "D-NODE-COLLECTOR-PASSWD",
    ):
        if needle not in doc:
            errors.append(f"docs/common/compliance-hardening.md missing {needle!r}")
    if "CNI dir 0700 not traversable by the scanner" in doc:
        errors.append("docs/common/compliance-hardening.md still contains debunked CNI 0700 traversal claim")

    # 5. verify-cluster.sh + regression wiring
    ver = texts["verify"]
    if "verify_node_file_hardening" not in ver:
        errors.append("scripts/test/verify-cluster.sh missing verify_node_file_hardening")
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
    ("allowlist KCV0059", "accepted", '["KCV0056"]', '["KCV0056", "KCV0059"]'),
    ("reclassify KCV0056 as accept", "accepted", '    ["KCV0056"],\n    "upstream",', '    ["KCV0056"],\n    "accept",'),
    ("drop KCV normalization", "accepted", "AVD-KCV-0*(\\d+)", "AVD-KCV-DISABLED-(\\d+)"),
    ("drop Node matcher in frameworks", "compliance", 'if (resolved.kind === "Node")', 'if (resolved.kind === "NeverNode")'),
    ("drop acceptedInfraFailures type", "types", "acceptedInfraFailures: CheckSummary", "removedInfra: CheckSummary"),
    ("drop i18n infraCniDirGlob", "i18n", '"compliance.acceptedReason.infraCniDirGlob"', '"compliance.acceptedReason.removed"'),
    ("drop Infra allowlist table in docs", "doc", "\n### Infra allowlist\n", "\n### Removed\n"),
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
