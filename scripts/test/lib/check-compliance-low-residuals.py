#!/usr/bin/env python3
"""R383: Remaining low-severity compliance findings hardened or fail-closed allowlisted.

Verifies:
1. gitops/resources/openbao-unseal.yaml (openbao-auto-unseal) enforces runAsNonRoot: true,
   runAsUser: 10100, runAsGroup: 10100 (UID/GID > 10000) with a D-OPENBAO-UID-10100 comment and
   no container/initContainer override of either id. gitops/resources/openbao-backup.yaml
   (openbao-snapshot, openbao-snapshot-holder) must keep runAsUser: 100 and no runAsGroup:
   existing backup files on the PVC are owned by uid 100 (changing it needs an ownership migration).
2. portal/src/data/compliance-acceptance-rules.json does NOT allowlist openbao-auto-unseal and holds EXACT
   (set-equal) check lists for: database/Pod/narwhal-db-1 (accept), the pooler ReplicaSet
   (upstream: CNPG overwrites its securityContext), the snapshot CronJob and holder (accept) and
   nfs-quota-agent/DaemonSet/nfs-quota-agent (accept).
3. portal/src/lib/i18n.ts, docs/common/compliance-hardening.md (incl. its rule/workload counts,
   derived from the allowlist) and scripts/test/regression-check-kakao.sh stay in sync.
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
    "unseal": ROOT / "gitops/resources/openbao-unseal.yaml",
    "backup": ROOT / "gitops/resources/openbao-backup.yaml",
    "accepted": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def _containers(pod_spec: dict) -> list[dict]:
    return list(pod_spec.get("containers") or []) + list(pod_spec.get("initContainers") or [])


def _check_container_overrides(label: str, pod_spec: dict, errors: list[str]) -> None:
    for c in _containers(pod_spec):
        sc = c.get("securityContext") or {}
        for key in ("runAsUser", "runAsGroup"):
            if key in sc:
                errors.append(f"{label} container {c.get('name')} must not override {key}")


def _check_pod_uid_10100(label: str, pod_spec: dict, errors: list[str]) -> None:
    sc = pod_spec.get("securityContext") or {}
    if sc.get("runAsNonRoot") is not True:
        errors.append(f"{label} must set securityContext.runAsNonRoot: true")
    if sc.get("runAsUser") != 10100:
        errors.append(f"{label} must set securityContext.runAsUser: 10100 (got {sc.get('runAsUser')!r})")
    if sc.get("runAsGroup") != 10100:
        errors.append(f"{label} must set securityContext.runAsGroup: 10100 (got {sc.get('runAsGroup')!r})")
    _check_container_overrides(label, pod_spec, errors)


def _check_pod_uid_100(label: str, pod_spec: dict, errors: list[str]) -> None:
    sc = pod_spec.get("securityContext") or {}
    if sc.get("runAsNonRoot") is not True:
        errors.append(f"{label} must set securityContext.runAsNonRoot: true")
    if sc.get("runAsUser") != 100:
        errors.append(f"{label} must keep securityContext.runAsUser: 100 (got {sc.get('runAsUser')!r}); backup files on the PVC are owned by uid 100")
    if "runAsGroup" in sc:
        errors.append(f"{label} must not set securityContext.runAsGroup (got {sc.get('runAsGroup')!r}); backup files on the PVC are owned by uid 100")
    _check_container_overrides(label, pod_spec, errors)


def _parse_config_rules(src: str) -> list[tuple[tuple[str, str, str, str, str], set[str]]]:
    return [((r["namespace"], r["kind"], r["name"], r["class"], r["id"]), set(r["checks"]))
            for r in json.loads(src)["configAudit"]]


# D-LANE-B-R2-CNPG: Keep KSV036 separate from the previously reviewed UID checks.
# Cost: one additional exact rule and bilingual reason; escape hatch: remove it if CNPG 1.31+ supports disabling the mount safely.


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
        _check_pod_uid_100(
            "openbao-snapshot",
            snap_cj["spec"]["jobTemplate"]["spec"]["template"]["spec"],
            errors,
        )
    if not holder_dep:
        errors.append("openbao-backup.yaml missing holder Deployment")
    else:
        _check_pod_uid_100(
            "openbao-snapshot-holder",
            holder_dep["spec"]["template"]["spec"],
            errors,
        )
    if "10100" in texts["backup"]:
        errors.append("openbao-backup.yaml must not use uid/gid 10100 (existing PVC backup files are owned by uid 100)")

    # 2. Allowlist entries (exact check sets; openbao-auto-unseal is fixed, not allowlisted)
    rules = _parse_config_rules(texts["accepted"])
    if any(k[2] == "openbao-auto-unseal" for k, _ in rules):
        errors.append("compliance-acceptance-rules.json must not allowlist fixed workload openbao-auto-unseal")

    # D-OWN-POD supersedes D-P2-2 for the reviewed UID/GID checks. Use the
    # pooler's exact Deployment identity so a new ReplicaSet hash needs no rule update.
    # Revoke the rule if the operator's UID/GID requirement changes.
    expected_rules = {
        ("database", "Pod", "narwhal-db-1", "accept", "cfg:database:Pod:narwhal-db-1:accept"): {"KSV020", "KSV021"},
        ("database", "Pod", "narwhal-db-1", "upstream", "cfg:database:Pod:narwhal-db-1:ksv036-accept"): {"KSV036"},
        ("database", "ReplicaSet", "narwhal-db-pooler-rw", "accept", "cfg:database:ReplicaSet:narwhal-db-pooler-rw:accept"): {"KSV020", "KSV021"},
        ("storage", "CronJob", "openbao-snapshot", "accept", "cfg:storage:CronJob:openbao-snapshot:accept"): {"KSV020", "KSV021"},
        ("storage", "ReplicaSet", "openbao-snapshot-holder", "accept", "cfg:storage:ReplicaSet:openbao-snapshot-holder:accept"): {"KSV020", "KSV021"},
        ("nfs-quota-agent", "DaemonSet", "nfs-quota-agent", "accept", "cfg:nfs-quota-agent:DaemonSet:nfs-quota-agent:accept"): {
            "KSV001", "KSV003", "KSV004", "KSV012", "KSV017", "KSV020", "KSV021", "KSV023", "KSV121",
        },
    }
    for key, want in expected_rules.items():
        matches = [c for k, c in rules if k == key]
        if len(matches) != 1:
            errors.append(f"compliance-acceptance-rules.json must hold exactly one rule {key} (found {len(matches)})")
        elif matches[0] != want:
            errors.append(
                f"compliance-acceptance-rules.json rule {key} check set differs: "
                f"missing {sorted(want - matches[0])}, extra {sorted(matches[0] - want)}"
            )

    # 3. i18n, docs, and regression wiring
    for key in ("cnpgPostgresUid26", "cnpgPoolerUid", "openbaoSnapshotUid"):
        if texts["i18n"].count(f'"compliance.acceptedReason.{key}"') < 2:
            errors.append(f"i18n.ts must define ko and en translations for compliance.acceptedReason.{key}")
    if "/etc/pgbouncer" in texts["i18n"] or "/etc/pgbouncer" in texts["doc"]:
        errors.append("i18n.ts / compliance-hardening.md must not claim /etc/pgbouncer ownership (CNPG uses /controller)")
    want_summary = f"{len(rules)} Config Audit rules across {len({k[:3] for k, _ in rules})} workloads"
    if want_summary not in texts["doc"]:
        errors.append(f"docs/common/compliance-hardening.md allowlist summary must read '{want_summary}'")
    for row in ("CronJob/openbao-snapshot", "ReplicaSet/openbao-snapshot-holder",
                "Pod/narwhal-db-1", "ReplicaSet/narwhal-db-pooler-rw"):
        if row not in texts["doc"]:
            errors.append(f"docs/common/compliance-hardening.md missing {row} row")
    if "Matching is exact: `database/Pod/narwhal-db-1`" not in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md must document that Pod/narwhal-db-1 is an exact match")
    if "check R383 " not in texts["reg"] or "check-compliance-low-residuals.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R383 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("revert openbao-auto-unseal runAsUser", "unseal", "runAsUser: 10100", "runAsUser: 100"),
    ("drop openbao-auto-unseal runAsGroup", "unseal", "            runAsGroup: 10100\n", ""),
    ("container-level override in openbao-auto-unseal", "unseal", "                allowPrivilegeEscalation: false\n", "                allowPrivilegeEscalation: false\n                runAsUser: 100\n"),
    ("openbao-snapshot switched to 10100", "backup", "            runAsUser: 100", "            runAsUser: 10100"),
    ("openbao-snapshot gains runAsGroup", "backup", "            runAsUser: 100\n", "            runAsUser: 100\n            runAsGroup: 10100\n"),
    ("openbao-snapshot-holder switched to 10100", "backup", "        runAsUser: 100", "        runAsUser: 10100"),
    ("drop i18n cnpgPostgresUid26", "i18n", '"compliance.acceptedReason.cnpgPostgresUid26"', '"compliance.acceptedReason.removed"'),
    ("drop i18n cnpgPoolerUid", "i18n", '"compliance.acceptedReason.cnpgPoolerUid"', '"compliance.acceptedReason.removed"'),
    ("drop i18n openbaoSnapshotUid", "i18n", '"compliance.acceptedReason.openbaoSnapshotUid"', '"compliance.acceptedReason.removed"'),
    ("stale doc count", "doc", "52 Config Audit rules across 51 workloads", "45 Config Audit rules across 45 workloads"),
    ("false /etc/pgbouncer claim", "doc", "### Config Audit allowlist", "### Config Audit allowlist /etc/pgbouncer"),
    ("drop exact-match doc note", "doc", "Matching is exact: `database/Pod/narwhal-db-1`", "Matching is loose: `database/Pod/narwhal-db-1`"),
    ("drop R383 wiring", "reg", "check R383 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    # Exercise the JSON artifact directly; preserve and strengthen the old rule mutations.
    data = json.loads(texts["accepted"])
    changed = copy.deepcopy(data)
    changed["configAudit"][0]["name"] = "openbao-auto-unseal"
    json_mutations = [("allowlist fixed openbao-auto-unseal", changed)]
    for name in ("openbao-snapshot", "openbao-snapshot-holder", "nfs-quota-agent",
                 "narwhal-db-1", "narwhal-db-pooler-rw"):
        for mode in ("drop", "extra check"):
            changed = copy.deepcopy(data)
            rule = next(r for r in changed["configAudit"] if r["name"] == name)
            if mode == "drop":
                changed["configAudit"].remove(rule)
            else:
                rule["checks"].append("KSV014")
            json_mutations.append((f"{mode}: {name}", changed))
    changed = copy.deepcopy(data)
    pooler = next(r for r in changed["configAudit"] if r["name"] == "narwhal-db-pooler-rw")
    pooler["name"] = "narwhal-db-pooler-rw-5b44f8765f"
    json_mutations.append(("replace pooler Deployment identity with a ReplicaSet revision", changed))

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
        for name, data in json_mutations:
            mutated = dict(texts, accepted=json.dumps(data))
            if not check(mutated):
                raise AssertionError(f"mutation not rejected: {name}")
        print(f"PASS: R383 low-severity compliance hardening & allowlist ({len(MUTATIONS) + len(json_mutations)} mutations rejected)")
        return 0

    print("PASS: R383 low-severity compliance hardening & allowlist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
