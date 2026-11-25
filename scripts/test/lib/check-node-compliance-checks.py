#!/usr/bin/env python3
"""R385: Node compliance resolution for KCV0059 (passwd/group mounts) & KCV0056 (/etc/cni mount, no acceptance).

Verifies:
1. gitops/charts/narwhal-apps/templates/trivy-operator.yaml preserves all 8 chart 0.27.0 default
   nodeCollector hostPath volumes/mounts (remounting host /etc/cni/net.d/ at container /etc/cni so
   CMD-0009/CMD-0010 /*/cni/* globs CNI files instead of the 700 dir) AND adds read-only
   /etc/passwd and /etc/group File mounts (and renders cleanly via helm template when available).
2. portal/src/lib/compliance-accepted.ts normalizes KCV IDs and keeps ACCEPTED_INFRA_RULES exact
   (neither KCV0056 nor KCV0059 is allowlisted), failing closed on empty/unlisted inputs.
3. portal/src/lib/compliance.ts reuses infraAuditMatcher across Infra list/detail/summary and
   Compliance Frameworks (Node targets) while keeping raw counts visible and failing closed when
   checks[] is omitted.
4. portal/src/types/compliance.ts, portal/src/lib/i18n.ts, and docs/common/compliance-hardening.md
   stay aligned (exact Infra allowlist table + node-collector root-cause resolution).
5. scripts/test/verify-cluster.sh defines and invokes verify_node_file_hardening under nodes, and
   behavioral fixture tests prove it passes compliant CIS 1.1.9/1.1.12 layouts and catches regressions.
6. scripts/test/regression-check-kakao.sh wires R385 with --mutation-verify.
"""

from __future__ import annotations

from functools import lru_cache
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
    "rules": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "acceptance": ROOT / "portal/src/lib/compliance-acceptance.ts",
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "infra_list": ROOT / "portal/src/components/compliance/infra-audit-list.tsx",
    "types": ROOT / "portal/src/types/compliance.ts",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "verify": ROOT / "scripts/test/verify-cluster.sh",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

HELM_COMMENT_RE = re.compile(r"\A\{\{-\s*/\*.*?\*/\s*-?\}\}\s*", re.S)

# (name, mountPath, hostPath) — chart 0.27.0 default mounts, except etc-cni-netd mounts host
# /etc/cni/net.d/ at container /etc/cni so /*/cni/* matches CNI files (D-NODE-COLLECTOR-CNI-MOUNT).
DEFAULT_NODE_COLLECTOR_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("var-lib-etcd", "/var/lib/etcd", "/var/lib/etcd"),
    ("var-lib-kubelet", "/var/lib/kubelet", "/var/lib/kubelet"),
    ("var-lib-kube-scheduler", "/var/lib/kube-scheduler", "/var/lib/kube-scheduler"),
    ("var-lib-kube-controller-manager", "/var/lib/kube-controller-manager", "/var/lib/kube-controller-manager"),
    ("etc-systemd", "/etc/systemd", "/etc/systemd"),
    ("lib-systemd", "/lib/systemd/", "/lib/systemd"),
    ("etc-kubernetes", "/etc/kubernetes", "/etc/kubernetes"),
    ("etc-cni-netd", "/etc/cni", "/etc/cni/net.d/"),
)


def _extract_fn(verify_text: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\(\)\s*\{{.*?\n\}}", verify_text, re.S | re.M)
    assert m, f"{name}() not found in verify-cluster.sh"
    return m.group(0)


def _extract_node_hardening_fn(verify_text: str) -> str:
    return "\n\n".join(_extract_fn(verify_text, n) for n in ("_node_priv", "_node_stat", "verify_node_file_hardening"))


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
verify_node_file_hardening "$1" "{owner}" "${{3:-{owner}}}"
echo "SUMMARY pass=$PASS_COUNT fail=$FAIL_COUNT warn=$WARN_COUNT"
want_pass="${{2:-2}}"
[ "$FAIL_COUNT" -eq 0 ] && [ "$PASS_COUNT" -eq "$want_pass" ]
"""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        runner = tmp_path / "run.sh"
        runner.write_text(script_tpl, encoding="utf-8")

        def make_root(
            name: str,
            cni_mode: int | None,
            etcd_mode: int | None,
            *,
            etcd_manifest: bool = False,
            cni_dir_mode: int = 0o700,
            create_cni_dir: bool = True,
            cni_subdir: bool = False,
            cni_dotfile_mode: int | None = None,
            symlink_target_mode: int | None = None,
        ) -> Path:
            r = tmp_path / name
            cni_dir = r / "etc/cni/net.d"
            if create_cni_dir:
                cni_dir.mkdir(parents=True)
                if cni_mode is not None:
                    f = cni_dir / "05-cilium.conflist"
                    f.write_text("{}\n", encoding="utf-8")
                    os.chmod(f, cni_mode)
                if cni_dotfile_mode is not None:
                    df = cni_dir / ".kubernetes-cni-keep"
                    df.write_text("", encoding="utf-8")
                    os.chmod(df, cni_dotfile_mode)
                if cni_subdir:
                    sd = cni_dir / "nested"
                    sd.mkdir()
                    os.chmod(sd, 0o700)
                if symlink_target_mode is not None:
                    target = r / "opt/custom-cni.conf"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("{}\n", encoding="utf-8")
                    os.chmod(target, symlink_target_mode)
                    (cni_dir / "10-symlink.conflist").symlink_to(target)
                os.chmod(cni_dir, cni_dir_mode)
            else:
                (r / "etc/cni").mkdir(parents=True)
            if etcd_mode is not None:
                etcd_dir = r / "var/lib/etcd"
                etcd_dir.mkdir(parents=True)
                os.chmod(etcd_dir, etcd_mode)
            if etcd_manifest:
                manifest = r / "etc/kubernetes/manifests/etcd.yaml"
                manifest.parent.mkdir(parents=True)
                manifest.write_text("kind: Pod\n", encoding="utf-8")
            pki_dir = r / "etc/kubernetes/pki"
            pki_dir.mkdir(parents=True, exist_ok=True)
            ca = pki_dir / "ca.crt"
            ca.write_text("cert\n", encoding="utf-8")
            os.chmod(ca, 0o600)
            svc = r / "usr/lib/systemd/system/kubelet.service"
            svc.parent.mkdir(parents=True, exist_ok=True)
            svc.write_text("[Service]\n", encoding="utf-8")
            os.chmod(svc, 0o600)
            dropin = r / "usr/lib/systemd/system/kubelet.service.d/10-kubeadm.conf"
            dropin.parent.mkdir(parents=True, exist_ok=True)
            dropin.write_text("[Service]\n", encoding="utf-8")
            os.chmod(dropin, 0o600)
            kcfg = r / "var/lib/kubelet/config.yaml"
            kcfg.parent.mkdir(parents=True, exist_ok=True)
            kcfg.write_text(
                "apiVersion: kubelet.config.k8s.io/v1beta1\nkind: KubeletConfiguration\n",
                encoding="utf-8",
            )
            os.chmod(kcfg, 0o600)
            return r

        good = make_root("good", 0o600, 0o700, cni_dotfile_mode=0o600)
        worker = make_root("worker", 0o600, None, etcd_manifest=False)
        etcd_missing = make_root("etcd_missing", 0o600, None, etcd_manifest=True)
        bad_cni_444 = make_root("bad_cni_444", 0o444, 0o700)
        bad_cni_644 = make_root("bad_cni_644", 0o644, 0o700)
        bad_cni_700 = make_root("bad_cni_700", 0o700, 0o700)
        bad_cni_suid = make_root("bad_cni_suid", 0o4600, 0o700)
        bad_cni_symlink = make_root("bad_cni_symlink", 0o600, 0o700, symlink_target_mode=0o644)
        missing_cni_dir = make_root("missing_cni_dir", None, 0o700, create_cni_dir=False)
        empty_cni = make_root("empty_cni", None, 0o700)
        subdir_only_cni = make_root("subdir_only_cni", None, 0o700, cni_subdir=True)
        dotfile_only_cni = make_root("dotfile_only_cni", None, 0o700, cni_dotfile_mode=0o600)
        bad_dotfile_cni = make_root("bad_dotfile_cni", 0o600, 0o700, cni_dotfile_mode=0o644)
        unreadable_cni = make_root("unreadable_cni", 0o600, 0o700, cni_dir_mode=0o000)
        bad_etcd_755 = make_root("bad_etcd_755", 0o600, 0o755)
        bad_etcd_suid = make_root("bad_etcd_suid", 0o600, 0o4700)
        bad_etcd_sgid = make_root("bad_etcd_sgid", 0o600, 0o2700)

        cases: list[tuple[str, Path, str, str, bool, str, str | None]] = [
            ("compliant master fixture", good, "5", owner, True, "CIS 1.1.9 CNI file perms: 2 file(s) <=600", None),
            ("compliant worker fixture", worker, "4", owner, True, "SUMMARY pass=4 fail=0", None),
            ("missing etcd dir when etcd.yaml exists", etcd_missing, "5", owner, False, "FAIL: CIS 1.1.12 etcd data dir missing or symlink", None),
            ("444 CNI file", bad_cni_444, "5", owner, False, "FAIL: CIS 1.1.9 CNI file perms", None),
            ("644 CNI file", bad_cni_644, "5", owner, False, "FAIL: CIS 1.1.9 CNI file perms", None),
            ("0700 CNI file", bad_cni_700, "5", owner, False, "FAIL: CIS 1.1.9 CNI file perms", None),
            ("4600 CNI file", bad_cni_suid, "5", owner, False, "FAIL: CIS 1.1.9 CNI file perms", None),
            ("symlink->644 CNI file", bad_cni_symlink, "5", owner, False, "FAIL: CIS 1.1.9 CNI file perms", None),
            ("missing CNI dir", missing_cni_dir, "5", owner, False, "FAIL: CIS 1.1.9 CNI config dir missing or symlink", None),
            ("empty CNI dir", empty_cni, "5", owner, False, "FAIL: CIS 1.1.9 CNI config files: none found", None),
            ("subdir-only CNI dir", subdir_only_cni, "5", owner, False, "FAIL: CIS 1.1.9 CNI config files: none found", None),
            ("dotfile-only CNI dir", dotfile_only_cni, "5", owner, False, "FAIL: CIS 1.1.9 CNI config files: none found", None),
            ("644 dotfile with 600 conflist", bad_dotfile_cni, "5", owner, False, "FAIL: CIS 1.1.9 CNI file perms", None),
            ("755 etcd dir", bad_etcd_755, "5", owner, False, "FAIL: CIS 1.1.12 etcd data dir", None),
            ("4700 etcd dir", bad_etcd_suid, "5", owner, False, "FAIL: CIS 1.1.12 etcd data dir", None),
            ("2700 etcd dir", bad_etcd_sgid, "5", owner, False, "FAIL: CIS 1.1.12 etcd data dir", None),
            ("wrong etcd owner", good, "5", "wronguser:wronggroup", False, "FAIL: CIS 1.1.12 etcd data dir", None),
        ]
        if os.geteuid() != 0:
            cases.append(("000 CNI dir", unreadable_cni, "5", owner, False, "FAIL: CIS 1.1.9 CNI config dir unreadable", "none found"))

        def run_case(
            bash_bin: str,
            label: str,
            fixture: Path,
            want_pass: str,
            etcd_owner: str,
            expect_ok: bool,
            required: str,
            forbidden: str | None,
        ) -> None:
            res = subprocess.run([bash_bin, str(runner), str(fixture), want_pass, etcd_owner], capture_output=True, text=True)
            ok = (res.returncode == 0) if expect_ok else (res.returncode != 0)
            if not ok or required not in res.stdout or (forbidden is not None and forbidden in res.stdout):
                errors.append(f"[{bash_bin}] {label}: {res.stdout} {res.stderr}")

        try:
            for bash_bin in interpreters:
                for label, fixture, want_pass, etcd_owner, expect_ok, required, forbidden in cases:
                    run_case(bash_bin, label, fixture, want_pass, etcd_owner, expect_ok, required, forbidden)
        finally:
            unreadable_cni_dir = unreadable_cni / "etc/cni/net.d"
            if unreadable_cni_dir.exists():
                os.chmod(unreadable_cni_dir, 0o700)

    return errors


@lru_cache(maxsize=4)
def _parse_trivy_values(trivy_text: str) -> dict:
    trivy_body = HELM_COMMENT_RE.sub("", trivy_text, count=1)
    trivy_doc = yaml.safe_load(trivy_body)
    return (((trivy_doc.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}


def check(texts: dict[str, str], run_behavioral: bool = False) -> list[str]:
    errors: list[str] = []

    # 1. trivy-operator.yaml nodeCollector mounts + podAnnotations checksum
    values_obj = _parse_trivy_values(texts["trivy"])
    nc = values_obj.get("nodeCollector") or {}
    mounts = {m.get("name"): m for m in (nc.get("volumeMounts") or []) if isinstance(m, dict)}
    volumes = {v.get("name"): v for v in (nc.get("volumes") or []) if isinstance(v, dict)}

    for name, mpath, hpath in DEFAULT_NODE_COLLECTOR_ENTRIES:
        if name not in mounts or mounts[name].get("mountPath") != mpath or mounts[name].get("readOnly") is not True:
            errors.append(f"trivy-operator.yaml missing default nodeCollector.volumeMount {name}:{mpath} readOnly:true")
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

    # 2. compliance-accepted.ts: KCV normalization + exact ACCEPTED_INFRA_RULES (neither KCV0056 nor KCV0059)
    acc = texts["accepted"] + texts["acceptance"]
    for needle in (
        "CHECK_ID_PAD_WIDTH",
        "(?:AVD-(KSV|KCV)-|(KSV|KCV))0*(\\d+)",
        "ACCEPTED_INFRA_RULES",
        "export function matchInfraException(",
    ):
        if needle not in acc:
            errors.append(f"compliance-accepted.ts missing {needle!r}")

    if "infraCniDirGlob" in acc:
        errors.append("compliance-accepted.ts must remove stale infraCniDirGlob reason key")

    # D6: retired empty-table assertion must preserve the original negative checks
    # while allowing only reviewed control-plane Pod triples. Cost: explicit set;
    # escape: extend after evidence review, never blanket node acceptance.
    rules = json.loads(texts["rules"])["infra"]
    targets = {(r.get("kind"), r.get("namespace"), r.get("name"), c)
               for r in rules for c in r.get("checks", [])}
    expected = {
        ("Pod", "kube-system", "kube-controller-manager-narwhal-master-1", "KCV0039"),
        ("Pod", "kube-system", "kube-scheduler-narwhal-master-1", "KCV0041"),
        # D-OWN-CP: measured on this Pod. Require fresh review for another node.
        ("Pod", "kube-system", "kube-apiserver-narwhal-master-1", "KCV0001"),
        ("Pod", "kube-system", "kube-apiserver-narwhal-master-1", "KCV0012"),
    }
    if targets != expected:
        errors.append("Infra rules must match reviewed exact control-plane Pod targets; KCV0056/KCV0059 stay actionable")
    if "= loaded.infra" not in acc:
        errors.append("ACCEPTED_INFRA_RULES must load validated infra data")

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

    for key in ("compliance.counter.infraBreakdown", "compliance.table.infraActionableSummary"):
        if texts["i18n"].count(f'"{key}"') < 2:
            errors.append(f"portal/src/lib/i18n.ts must define ko and en translations for {key}")
    if "compliance.acceptedReason.infraCniDirGlob" in texts["i18n"]:
        errors.append("portal/src/lib/i18n.ts must remove stale compliance.acceptedReason.infraCniDirGlob key")

    # 4. docs/common/compliance-hardening.md
    doc = texts["doc"]
    for needle in (
        "\n### Infra allowlist\n",
        f"**{len(rules)} Infra rules**",
        "## Node collector & CIS 1.1.9 / 1.1.12 resolution",
        "D-NODE-COLLECTOR-PASSWD",
        "D-NODE-COLLECTOR-PASSWD-ROLLOUT",
        "D-NODE-COLLECTOR-CNI-MOUNT",
        "rollout restart deploy/trivy-operator",
        "nodeType: master",
        "does **not** report a worker node's `644` CNI file",
        "values: []",
    ):
        if needle not in doc:
            errors.append(f"docs/common/compliance-hardening.md missing {needle!r}")
    for forbidden in (
        "| cluster | Node/* | KCV0056 |",
        "CNI dir 0700 not traversable by the scanner",
        "cannot pass for a compliant control-plane node by design",
        "real regressions on any node remain visible and actionable",
    ):
        if forbidden in doc:
            errors.append(f"docs/common/compliance-hardening.md still contains debunked/removed text {forbidden!r}")

    # 5. verify-cluster.sh + regression wiring
    ver = texts["verify"]
    if "verify_node_file_hardening" not in ver or "_node_priv" not in ver:
        errors.append("scripts/test/verify-cluster.sh missing verify_node_file_hardening or _node_priv wrapper")
    else:
        priv_fn = _extract_fn(ver, "_node_priv")
        stat_fn = _extract_fn(ver, "_node_stat")
        hard_fn = _extract_fn(ver, "verify_node_file_hardening")
        fn_src = "\n\n".join((priv_fn, stat_fn, hard_fn))
        if '${priv[@]+"${priv[@]}"}' not in priv_fn:
            errors.append("_node_priv must guard priv expansion with ${priv[@]+\"${priv[@]}\"} for bash 3.2 set -u")
        for token in ("_node_priv", "((8#${mode} & 8#7177))", "8#7077", 'find -L "${cni_dir}"', "stat -L", "CNI config dir unreadable", "*.conf|*.conflist|*.json", 'if [ "${cni_conf_count}" -eq 0 ]'):
            if token not in fn_src:
                errors.append(f"verify_node_file_hardening missing hardening check token {token!r}")
        if "_node_priv" not in stat_fn or "_node_priv" not in hard_fn:
            errors.append("_node_stat and verify_node_file_hardening must route privileged commands through _node_priv")
        if re.search(r'(?<!\+)\"\$\{priv\[@\]\}\"', fn_src):
            errors.append("verify_node_file_hardening/_node_priv must guard every priv expansion with ${priv[@]+\"${priv[@]}\"} for bash 3.2 set -u")
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
    ("revert etc-cni-netd mountPath", "trivy", "            - name: etc-cni-netd\n              mountPath: /etc/cni\n              readOnly: true\n", "            - name: etc-cni-netd\n              mountPath: /etc/cni/net.d/\n              readOnly: true\n"),
    ("mount /etc/shadow", "trivy", "path: /etc/group", "path: /etc/shadow"),
    ("stale nodeCollector checksum annotation", "trivy", "checksum/node-collector-mounts: \"eca6d129ecc1594eb2d9bcc63e2d5b2ea63cc89909a235fa591134bc555d4a70\"", "checksum/node-collector-mounts: \"stale\""),
    ("allowlist KCV0056", "rules", '"KCV0039"', '"KCV0056"'),
    ("allowlist KCV0059", "rules", '"KCV0039"', '"KCV0059"'),
    ("drop KCV normalization", "acceptance", "(?:AVD-(KSV|KCV)-|(KSV|KCV))0*(\\d+)", "(?:AVD-(KSV)-|(KSV))0*(\\d+)"),
    ("drop Node matcher in frameworks", "compliance", 'if (resolved.kind === "Node")', 'if (resolved.kind === "NeverNode")'),
    ("drop mixed-node accepted badge", "infra_list", "{hasActionable && acceptedFailures > 0 && (", "{false && acceptedFailures > 0 && ("),
    ("drop acceptedInfraFailures type", "types", "acceptedInfraFailures: CheckSummary", "removedInfra: CheckSummary"),
    ("drop i18n infraActionableSummary", "i18n", '"compliance.table.infraActionableSummary"', '"compliance.table.removed"'),
    ("drop Infra allowlist table in docs", "doc", "\n### Infra allowlist\n", "\n### Removed\n"),
    ("drop D-NODE-COLLECTOR-CNI-MOUNT in docs", "doc", "D-NODE-COLLECTOR-CNI-MOUNT", "D-REMOVED"),
    ("drop master-only KCV0056 scope in docs", "doc", "does **not** report a worker node's `644` CNI file", "reports every node's `644` CNI file"),
    ("drop operator rollout restart note in docs", "doc", "rollout restart deploy/trivy-operator", "noop"),
    ("drop bash 3.2 empty-array guard", "verify", '${priv[@]+"${priv[@]}"}', '"${priv[@]}"'),
    ("weaken CNI special-bit mask", "verify", "8#7177", "8#177"),
    ("weaken etcd special-bit mask", "verify", "8#7077", "8#077"),
    ("drop CNI config presence check", "verify", 'if [ "${cni_conf_count}" -eq 0 ]', 'if [ "${cni_count}" -eq 0 ]'),
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
            mutated = dict(texts)
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
