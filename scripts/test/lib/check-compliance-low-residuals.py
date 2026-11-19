#!/usr/bin/env python3
"""R383: Remaining low-severity compliance findings hardened or fail-closed allowlisted.

Verifies:
1. gitops/resources/openbao-unseal.yaml (openbao-auto-unseal) and
   gitops/resources/openbao-backup.yaml (openbao-snapshot, openbao-snapshot-holder)
   enforce runAsNonRoot: true, runAsUser: 10100, runAsGroup: 10100 (UID/GID > 10000)
   with D-OPENBAO-UID-10100 decision comments.
2. portal/src/lib/compliance-accepted.ts does NOT allowlist those fixable OpenBao
   workloads, and includes exact fail-closed rules for database/Pod/narwhal-db-1
   (KSV020, KSV021, KSV036), database/ReplicaSet/narwhal-db-pooler-rw (KSV020, KSV021),
   and nfs-quota-agent/DaemonSet/nfs-quota-agent (KSV001, KSV003, KSV012, KSV017,
   KSV020, KSV021, KSV023, KSV121).
3. portal/src/lib/i18n.ts, docs/common/compliance-hardening.md, and
   scripts/test/regression-check-kakao.sh stay in sync.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "unseal": ROOT / "gitops/resources/openbao-unseal.yaml",
    "backup": ROOT / "gitops/resources/openbao-backup.yaml",
    "accepted": ROOT / "portal/src/lib/compliance-accepted.ts",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def _check_pod_uid_10100(label: str, pod_spec: dict, errors: list[str]) -> None:
    sc = pod_spec.get("securityContext") or {}
    if sc.get("runAsNonRoot") is not True:
        errors.append(f"{label} must set securityContext.runAsNonRoot: true")
    if sc.get("runAsUser") != 10100:
        errors.append(f"{label} must set securityContext.runAsUser: 10100 (got {sc.get('runAsUser')!r})")
    if sc.get("runAsGroup") != 10100:
        errors.append(f"{label} must set securityContext.runAsGroup: 10100 (got {sc.get('runAsGroup')!r})")


def _parse_config_rules(src: str) -> dict[tuple[str, str, str, str], set[str]]:
    rules: dict[tuple[str, str, str, str], set[str]] = {}
    pat = re.compile(
        r'configRule\(\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*\[([^\]]*)\],\s*"([^"]*)"',
        re.S,
    )
    for m in pat.finditer(src):
        ns, kind, name, checks_raw, cls = m.groups()
        checks = set(re.findall(r'"([^"]+)"', checks_raw))
        rules[(ns, kind, name, cls)] = checks
    return rules


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    # 1. OpenBao manifests hardened to 10100:10100
    unseal_docs = [d for d in yaml.safe_load_all(texts["unseal"]) if d]
    unseal_cj = next((d for d in unseal_docs if d.get("kind") == "CronJob"), None)
    if not unseal_cj:
        errors.append("openbao-unseal.yaml missing CronJob")
    else:
        _check_pod_uid_10100(
            "openbao-auto-unseal",
            unseal_cj["spec"]["jobTemplate"]["spec"]["template"]["spec"],
            errors,
        )
    if "D-OPENBAO-UID-10100" not in texts["unseal"]:
        errors.append("openbao-unseal.yaml missing D-OPENBAO-UID-10100 comment")

    backup_docs = [d for d in yaml.safe_load_all(texts["backup"]) if d]
    snap_cj = next((d for d in backup_docs if d.get("kind") == "CronJob"), None)
    holder_dep = next((d for d in backup_docs if d.get("kind") == "Deployment"), None)
    if not snap_cj:
        errors.append("openbao-backup.yaml missing CronJob")
    else:
        _check_pod_uid_10100(
            "openbao-snapshot",
            snap_cj["spec"]["jobTemplate"]["spec"]["template"]["spec"],
            errors,
        )
    if not holder_dep:
        errors.append("openbao-backup.yaml missing holder Deployment")
    else:
        _check_pod_uid_10100(
            "openbao-snapshot-holder",
            holder_dep["spec"]["template"]["spec"],
            errors,
        )
    if texts["backup"].count("D-OPENBAO-UID-10100") < 2:
        errors.append("openbao-backup.yaml missing D-OPENBAO-UID-10100 comments on CronJob and holder")

    # 2. Allowlist entries (and fixable workloads NOT allowlisted)
    rules = _parse_config_rules(texts["accepted"])
    for forbidden in ("openbao-auto-unseal", "openbao-snapshot", "openbao-snapshot-holder"):
        if any(k[2] == forbidden for k in rules):
            errors.append(f"compliance-accepted.ts must not allowlist fixable workload {forbidden}")

    expected_rules = {
        ("database", "Pod", "narwhal-db-1", "accept"): {"KSV020", "KSV021", "KSV036"},
        ("database", "ReplicaSet", "narwhal-db-pooler-rw", "accept"): {"KSV020", "KSV021"},
        ("nfs-quota-agent", "DaemonSet", "nfs-quota-agent", "accept"): {
            "KSV001", "KSV003", "KSV012", "KSV017", "KSV020", "KSV021", "KSV023", "KSV121",
        },
    }
    for key, want in expected_rules.items():
        got = rules.get(key)
        if got is None:
            errors.append(f"compliance-accepted.ts missing rule {key}")
        elif not want.issubset(got):
            errors.append(f"compliance-accepted.ts rule {key} missing checks {sorted(want - got)}")

    # 3. i18n, docs, and regression wiring
    if texts["i18n"].count("compliance.acceptedReason.cnpgPoolerUid") < 2:
        errors.append("i18n.ts must define ko and en translations for compliance.acceptedReason.cnpgPoolerUid")
    if "71 Config Audit rules across 59 workloads" not in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md allowlist summary count must reflect 71 rules / 59 workloads")
    if "ReplicaSet/narwhal-db-pooler-rw" not in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md missing ReplicaSet/narwhal-db-pooler-rw row")
    if "check R383 " not in texts["reg"] or "check-compliance-low-residuals.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R383 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("revert openbao-auto-unseal runAsUser", "unseal", "runAsUser: 10100", "runAsUser: 100"),
    ("drop openbao-auto-unseal runAsGroup", "unseal", "            runAsGroup: 10100\n", ""),
    ("revert openbao-snapshot runAsUser", "backup", "            runAsUser: 10100", "            runAsUser: 100"),
    ("drop openbao-snapshot runAsGroup", "backup", "            runAsGroup: 10100\n", ""),
    ("revert openbao-snapshot-holder runAsUser", "backup", "        runAsUser: 10100", "        runAsUser: 100"),
    ("drop openbao-snapshot-holder runAsGroup", "backup", "        runAsGroup: 10100\n", "        # dropped\n"),
    ("drop narwhal-db-1 KSV020/021", "accepted", '["KSV020", "KSV021", "KSV036"]', '["KSV036"]'),
    ("drop narwhal-db-pooler-rw rule", "accepted", '"narwhal-db-pooler-rw"', '"narwhal-db-pooler-other"'),
    ("drop nfs-quota-agent KSV003", "accepted", '"KSV001", "KSV003", "KSV012", "KSV017", "KSV020", "KSV021", "KSV023", "KSV121"', '"KSV001", "KSV012", "KSV017", "KSV020", "KSV021", "KSV023", "KSV121"'),
    ("allowlist openbao-snapshot-holder", "accepted", '"openbao-agent-injector"', '"openbao-snapshot-holder"'),
    ("drop i18n cnpgPoolerUid", "i18n", '"compliance.acceptedReason.cnpgPoolerUid"', '"compliance.acceptedReason.removed"'),
    ("stale doc count", "doc", "71 Config Audit rules across 59 workloads", "70 Config Audit rules across 58 workloads"),
    ("drop R383 wiring", "reg", "check R383 ", "check R9999 "),
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
        print(f"PASS: R383 low-severity compliance hardening & allowlist ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R383 low-severity compliance hardening & allowlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
