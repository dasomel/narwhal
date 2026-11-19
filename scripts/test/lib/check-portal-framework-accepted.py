#!/usr/bin/env python3
"""R381: Compliance frameworks separate accepted exceptions from actionable failures.

Verifies that portal/src/lib/compliance.ts reuses existing Config Audit, RBAC,
ReplicaSet ownership/superseded, and Infra rules fail-closed without a second
allowlist, UI surfaces actionable counts + collapsed accepted groups + doc link,
docs/common/compliance-hardening.md documents framework derivation, and the
trimmed live fixture test exists. --mutation-verify proves broken copies fail.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "types": ROOT / "portal/src/types/compliance.ts",
    "grid": ROOT / "portal/src/components/compliance/frameworks-grid.tsx",
    "detail": ROOT / "portal/src/app/(dashboard)/compliance/frameworks/[id]/page.tsx",
    "counters": ROOT / "portal/src/components/compliance/failure-counters.tsx",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "test": ROOT / "portal/src/lib/compliance.test.ts",
    "fixture": ROOT / "portal/src/lib/__fixtures__/ccr-live-trimmed.json",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    comp = texts["compliance"]
    types = texts["types"]
    grid = texts["grid"]
    detail = texts["detail"]
    counters = texts["counters"]
    doc = texts["doc"]
    test = texts["test"]

    # 1. Fail-closed target parser and existing matcher reuse
    for needle in (
        "export function parseComplianceTarget(",
        "if (CLUSTER_SCOPED_TARGET_KINDS.has(kind)) {",
        "if (namespace !== \"\") return null",
        "else if (namespace.length === 0)",
        "configAuditMatcher(resolved.namespace, resolved.kind, resolved.name, deploymentOwners)",
        "rbacAuditMatcher(resolved.namespace, resolved.kind, resolved.name)",
        "resolveReplicaSetByName(resolved.namespace, resolved.name, resolved.name, rsMap)",
        "fetchReplicaSetMap()",
        "buildReportTargetMap(",
        "buildComplianceFrameworkData(",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing required logic: {needle!r}")

    # 2. Control & framework fail-closed accounting
    for needle in (
        "const accepted = ctrlFail > 0 && actionableFailCount === 0 && acceptedFailCount > 0",
        "const unaccountedFail = Math.max(0, failCount - detailFailControls)",
        "actionableFailCount = failCount",
        "effectivePassRate",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing fail-closed accounting: {needle!r}")

    # 3. Types expose actionable/accepted/effectivePassRate alongside raw failCount/passRate
    for needle in (
        "actionableFailCount?: number",
        "acceptedFailCount?: number",
        "effectivePassRate?: number",
        "accepted?: boolean",
    ):
        if needle not in types:
            errors.append(f"types/compliance.ts missing field: {needle!r}")

    # 4. UI surfaces actionable headline, collapsed accepted controls, and doc anchor link
    for label, src in (("frameworks-grid.tsx", grid), ("frameworks/[id]/page.tsx", detail)):
        for needle in (
            "COMPLIANCE_EXCEPTIONS_DOC_URL",
            "actionableFailCount",
            "acceptedControls",
            "<details",
            "compliance.framework.acceptedControlsGroup",
        ):
            if needle not in src:
                errors.append(f"{label} missing {needle!r}")
    if "f.effectivePassRate ?? f.passRate" not in counters:
        errors.append("failure-counters.tsx missing effectivePassRate handling")

    # 5. Docs & live fixture test
    if "### Framework actionable vs. accepted controls" not in doc:
        errors.append("docs/common/compliance-hardening.md missing '### Framework actionable vs. accepted controls'")
    for needle in ("parseComplianceTarget", "configAuditMatcher", "rbacAuditMatcher", "reportType=summary"):
        if needle not in doc:
            errors.append(f"docs/common/compliance-hardening.md missing {needle!r}")

    if not texts["fixture"]:
        errors.append("trimmed live CCR fixture is empty")
    if "ccr-live-trimmed.json" not in test:
        errors.append("compliance.test.ts missing ccr-live-trimmed.json fixture test")

    # Ensure both getComplianceFrameworks and getComplianceFrameworkDetail call buildComplianceFrameworkData
    if len(re.findall(r"buildComplianceFrameworkData\(", comp)) < 3:
        errors.append("compliance.ts must share buildComplianceFrameworkData across list and detail")

    fw_ctx_m = re.search(
        r"async function fetchFrameworkContext\b[\s\S]*?// --- getComplianceFrameworks ---",
        comp,
    )
    if not fw_ctx_m or "fetchReplicaSetMap()" not in fw_ctx_m.group(0):
        errors.append("fetchFrameworkContext must call fetchReplicaSetMap()")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("allow clusterrole with namespace", "compliance", 'if (namespace !== "") return null', "if (false) return null"),
    ("bypass configAuditMatcher", "compliance", "configAuditMatcher(resolved.namespace, resolved.kind, resolved.name, deploymentOwners)", "(() => ({ accepted: true }))"),
    ("bypass rbacAuditMatcher", "compliance", "rbacAuditMatcher(resolved.namespace, resolved.kind, resolved.name)", "(() => ({ accepted: true }))"),
    ("drop ReplicaSet lookup", "compliance", "complianceK8sFetch<RawComplianceList>(\"/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports\"),\n    fetchReplicaSetMap(),", "complianceK8sFetch<RawComplianceList>(\"/apis/aquasecurity.github.io/v1alpha1/clustercompliancereports\"),\n    Promise.resolve(null),"),
    ("mark mixed control accepted", "compliance", "const accepted = ctrlFail > 0 && actionableFailCount === 0 && acceptedFailCount > 0", "const accepted = acceptedFailCount > 0"),
    ("fail open on summary-only report", "compliance", "actionableFailCount = failCount", "actionableFailCount = 0"),
    ("remove doc URL from FrameworksGrid", "grid", "COMPLIANCE_EXCEPTIONS_DOC_URL", "REMOVED_DOC_URL"),
    ("remove collapsed group from detail page", "detail", "<details", "<div"),
    ("remove framework section from docs", "doc", "### Framework actionable vs. accepted controls", "### Removed"),
    ("remove live fixture test", "test", "ccr-live-trimmed.json", "removed-fixture.json"),
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
            mutated[key] = mutated[key].replace(old, new)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R381 framework accepted/actionable split ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R381 framework accepted/actionable split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
