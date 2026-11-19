#!/usr/bin/env python3
"""R382: Portal framework accepted/actionable split fix-round invariants.

Verifies the follow-up review fixes:
1. parseComplianceTarget uses Object.hasOwn(COMPLIANCE_TARGET_KINDS, rawKind) so
   prototype keys like constructor-foo parse to null.
2. UI surfaces raw failCount whenever failCount !== actionableFail (even when
   acceptedFail === 0 after dropping superseded ReplicaSets), without '+0 accepted'.
3. getComplianceFrameworks / getComplianceFrameworkDetail warn on missing
   ReplicaSets consistently with getConfigAuditList, including empty RS lists.
4. kisa.ts checkCfg01 comment reflects per-check framework attribution without
   altering raw f.passRate grading.
5. Unit/UI tests pin constructor-foo, RS 403/500 fail-safe, empty RS warning,
   unaccountedFail + checks:[], hashed StatefulSet restore, and UI raw-count case.
--mutation-verify proves broken copies fail.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "grid": ROOT / "portal/src/components/compliance/frameworks-grid.tsx",
    "detail": ROOT / "portal/src/app/(dashboard)/compliance/frameworks/[id]/page.tsx",
    "counters": ROOT / "portal/src/components/compliance/failure-counters.tsx",
    "kisa": ROOT / "portal/src/lib/kisa.ts",
    "test": ROOT / "portal/src/lib/compliance.test.ts",
    "ui_test": ROOT / "portal/src/components/compliance/compliance-ui.test.tsx",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    comp = texts["compliance"]
    grid = texts["grid"]
    detail = texts["detail"]
    counters = texts["counters"]
    kisa = texts["kisa"]
    test = texts["test"]
    ui_test = texts["ui_test"]
    reg = texts["reg"]

    if "Object.hasOwn(COMPLIANCE_TARGET_KINDS, rawKind)" not in comp:
        errors.append("compliance.ts must use Object.hasOwn(COMPLIANCE_TARGET_KINDS, rawKind)")

    if "rsMap.size > 0 && missingReplicaSets.size > 0" in comp:
        errors.append("compliance.ts must not suppress missingReplicaSets warning when rsMap is empty")
    if len(re.findall(r"if \(missingReplicaSets\.size > 0\)", comp)) < 2:
        errors.append("compliance.ts must warn on missingReplicaSets.size > 0 in both list and detail")

    for needle in (
        "acceptedFail > 0 || ctrl.failCount !== actionableFail",
        "acceptedFail > 0 || fw.failCount !== actionableFail",
    ):
        if needle not in grid:
            errors.append(f"frameworks-grid.tsx missing {needle!r}")

    for needle in (
        "acceptedFail > 0 || ctrl.failCount !== actionableFail",
        "acceptedControlsFail > 0 && (",
    ):
        if needle not in detail:
            errors.append(f"frameworks/[id]/page.tsx missing {needle!r}")

    for needle in (
        "rawControlsTotal",
        "acceptedControlsTotal > 0 || rawControlsTotal !== actionableControlsTotal",
        "rawControlsTotal !== actionableControlsTotal",
    ):
        if needle not in counters:
            errors.append(f"failure-counters.tsx missing {needle!r}")

    if "only exposes `success`, no resource ref" in kisa:
        errors.append("kisa.ts still contains stale comment claiming checks lack resource refs")
    if "actionableFailCount" not in kisa or "f.passRate" not in kisa:
        errors.append("kisa.ts comment/code must accurately reference actionableFailCount and raw f.passRate")

    for needle in (
        "kube-system/constructor-foo",
        "for (const status of [403, 500])",
        "warns about missing ReplicaSets on the framework path even when replicasets list is empty",
        "checks: []",
        "monitoring/statefulset-78785dbd5c",
    ):
        if needle not in test:
            errors.append(f"compliance.test.ts missing {needle!r}")

    if "0 actionable · 0 accepted (raw 2)" not in ui_test:
        errors.append("compliance-ui.test.tsx missing superseded-RS raw count test")

    if "check R382 " not in reg or "check-portal-framework-accepted-fixes.py --mutation-verify" not in reg:
        errors.append("regression-check-kakao.sh must wire R382 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "revert own-property lookup in parseComplianceTarget",
        "compliance",
        "Object.hasOwn(COMPLIANCE_TARGET_KINDS, rawKind) ? COMPLIANCE_TARGET_KINDS[rawKind] : undefined",
        "COMPLIANCE_TARGET_KINDS[rawKind]",
    ),
    (
        "suppress missingReplicaSets warning on empty rsMap",
        "compliance",
        "if (missingReplicaSets.size > 0) {",
        "if (rsMap !== null && rsMap.size > 0 && missingReplicaSets.size > 0) {",
    ),
    (
        "hide raw failCount on ControlPreviewCard when acceptedFail is 0",
        "grid",
        "acceptedFail > 0 || ctrl.failCount !== actionableFail",
        "acceptedFail > 0",
    ),
    (
        "hide raw failCount on FrameworksGrid card when acceptedFail is 0",
        "grid",
        "acceptedFail > 0 || fw.failCount !== actionableFail",
        "acceptedFail > 0",
    ),
    (
        "hide raw failCount on detail ControlsTable row when acceptedFail is 0",
        "detail",
        "acceptedFail > 0 || ctrl.failCount !== actionableFail",
        "acceptedFail > 0",
    ),
    (
        "hide raw failCount on FailureCounters when acceptedControlsTotal is 0",
        "counters",
        "acceptedControlsTotal > 0 || rawControlsTotal !== actionableControlsTotal",
        "acceptedControlsTotal > 0",
    ),
    (
        "restore stale comment in kisa.ts",
        "kisa",
        "(`checkID` + `target`) when `reportType=all`",
        "only exposes `success`, no resource ref",
    ),
    (
        "drop constructor-foo test",
        "test",
        "kube-system/constructor-foo",
        "kube-system/pod-foo",
    ),
    (
        "drop RS 403/500 framework test",
        "test",
        "for (const status of [403, 500])",
        "for (const status of [])",
    ),
    (
        "drop UI superseded-RS raw count test",
        "ui_test",
        "0 actionable · 0 accepted (raw 2)",
        "removed",
    ),
    (
        "drop R382 wiring",
        "reg",
        "check R382 ",
        "check R9999 ",
    ),
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
        print(f"PASS: R382 portal framework fix-round invariants ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R382 portal framework fix-round invariants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
