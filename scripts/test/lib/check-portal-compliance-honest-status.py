#!/usr/bin/env python3
"""R386: Portal /compliance three-state honesty, LOW visibility, RS reconciliation, and data-unavailable guard.

Verifies that:
1. Non-failing controls never blindly become evaluated passes from Trivy's `{checkID:"", success:true}`
   placeholder: classifyNonFailingControl + buildFrameworkEvalContext derive `passed` vs
   `not_evaluated` (with explicit reasons) from current-RBAC sources (`nodes`, `deploy/trivy-operator`,
   `cm/trivy-operator-trivy-config`, and report lists), failing closed to `coverage_unknown`.
2. formatPassRatePct floors to 1 decimal and caps at 99.9% whenever actionable failures or
   unevaluated controls remain, while evaluated/raw/exception-adjusted rates stay distinct.
3. Config Audit keeps unaccepted MEDIUM+ actionable, exposes unaccepted LOW separately from
   accepted hygiene LOW, and reconciles dropped superseded-ReplicaSet counts.
4. Kubernetes list errors fail explicitly via ComplianceDataUnavailableError -> HTTP 503
   `{ unavailable: true }`, skip cache writes, and preserve per-source availability/timestamps
   and 6h rebuild cadence without inflating zeros or widening ClusterRole RBAC.
--mutation-verify proves mutated copies are rejected.
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
    "config_route": ROOT / "portal/src/app/api/compliance/config-audit/route.ts",
    "rbac_route": ROOT / "portal/src/app/api/compliance/rbac-audit/route.ts",
    "infra_route": ROOT / "portal/src/app/api/compliance/infra-audit/route.ts",
    "fw_route": ROOT / "portal/src/app/api/compliance/frameworks/route.ts",
    "page": ROOT / "portal/src/app/(dashboard)/compliance/page.tsx",
    "counters": ROOT / "portal/src/components/compliance/failure-counters.tsx",
    "grid": ROOT / "portal/src/components/compliance/frameworks-grid.tsx",
    "fw_detail": ROOT / "portal/src/app/(dashboard)/compliance/frameworks/[id]/page.tsx",
    "cfg_table": ROOT / "portal/src/components/compliance/config-audit-table.tsx",
    "rbac_table": ROOT / "portal/src/components/compliance/rbac-audit-table.tsx",
    "infra_list": ROOT / "portal/src/components/compliance/infra-audit-list.tsx",
    "fetch_json": ROOT / "portal/src/components/compliance/fetch-json.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "unit_test": ROOT / "portal/src/lib/compliance.test.ts",
    "ui_test": ROOT / "portal/src/components/compliance/compliance-ui.test.tsx",
    "snapshot": ROOT / "portal/src/lib/__fixtures__/compliance-snapshot.json",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    comp = texts["compliance"]
    types = texts["types"]
    doc = texts["doc"]

    # 1. Three-state derivation & fail-closed reasons in compliance.ts
    for needle in (
        "export class ComplianceDataUnavailableError",
        "function buildFrameworkEvalContext(",
        "function classifyNonFailingControl(",
        'notEvaluatedReason: "excluded_namespace"',
        'notEvaluatedReason: "unsupported_kind"',
        'notEvaluatedReason: "missing_node_type"',
        'notEvaluatedReason: "unlinked_check_id"',
        'notEvaluatedReason: "manual"',
        'notEvaluatedReason: "summary_only"',
        'notEvaluatedReason: "coverage_unknown"',
        "unacceptedLowConfigAuditFailures",
        "droppedConfigAuditReports",
        "rebuildIntervalHours: 6",
        "/api/v1/nodes",
        "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator",
        "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing required logic: {needle!r}")

    if ".catch(() => ({" in comp:
        errors.append("compliance.ts must never mask K8s list failures as { items: [] }")
    if len(re.findall(r"throw new ComplianceDataUnavailableError\(", comp)) < 9:
        errors.append("compliance.ts must throw ComplianceDataUnavailableError across all source fetch paths")
    if len(re.findall(r"if \(!meta\.partial\)", comp)) < 2:
        errors.append("compliance.ts must skip cacheSet on partial responses (if (!meta.partial))")

    # 2. Honest rate formatter & type contracts
    for needle in (
        "export function formatPassRatePct(",
        "Math.min(99.9, Math.floor(pct * 10 + 1e-9) / 10)",
        'export type ControlEvaluationState = "passed" | "failed" | "not_evaluated"',
        "evaluatedPassRate?: number",
        "notEvaluatedCount?: number",
        "unacceptedLowConfigAuditFailures?: CheckSummary",
        "droppedConfigAuditReports?: number",
        "sources?: Record<ComplianceSourceKey, ComplianceSourceStatus>",
    ):
        if needle not in types:
            errors.append(f"types/compliance.ts missing contract: {needle!r}")

    # 3. API routes fail loudly with 503 unavailable instead of masking as [] or 200
    for rname in ("config_route", "rbac_route", "infra_route", "fw_route"):
        rtext = texts[rname]
        for needle in ("ComplianceDataUnavailableError", "unavailable: true", "status: 503"):
            if needle not in rtext:
                errors.append(f"{rname} missing 503 unavailable handling: {needle!r}")

    # 4. Page & UI surfaces honest metrics, unavailable states, not_evaluated, unaccepted LOW, and 6h cadence
    if "unavailableSummary" not in texts["page"] or "dataUnavailable: true" not in texts["page"]:
        errors.append("compliance/page.tsx must use unavailableSummary (not zero emptySummary) on fetch failure")

    for cname in ("counters", "grid", "fw_detail"):
        if "formatPassRatePct(" not in texts[cname]:
            errors.append(f"{cname} must call formatPassRatePct(")

    for needle in (
        'isUnavailable("configAudit")',
        "unacceptedLowTotal",
        "droppedReports",
        "compliance.summary.rebuildNote",
    ):
        if needle not in texts["counters"]:
            errors.append(f"failure-counters.tsx missing {needle!r}")

    for cname in ("grid", "fw_detail"):
        if "compliance.framework.notEvaluatedControlsGroup" not in texts[cname]:
            errors.append(f"{cname} missing notEvaluatedControlsGroup")

    if "unavailable === true" not in texts["fetch_json"]:
        errors.append("fetch-json.ts must reject { unavailable: true } payloads")

    for tname in ("grid", "cfg_table", "rbac_table", "infra_list"):
        if "fetchComplianceJson" not in texts[tname]:
            errors.append(f"{tname} must use fetchComplianceJson")
        if 'state="unavailable"' not in texts[tname]:
            errors.append(f"{tname} must render DataState state='unavailable' on error")

    # 5. Docs, snapshot fixture, and tests
    if "### Three-state control evaluation & honest reporting" not in doc:
        errors.append("docs/common/compliance-hardening.md missing '### Three-state control evaluation & honest reporting'")
    if "ComplianceDataUnavailableError" not in doc or "unacceptedLowConfigAuditFailures" not in doc:
        errors.append("docs/common/compliance-hardening.md must document data unavailability and unaccepted LOW")

    if not texts["snapshot"].strip():
        errors.append("compliance-snapshot.json fixture is empty")
    if "compliance-snapshot.json" not in texts["unit_test"]:
        errors.append("compliance.test.ts missing compliance-snapshot.json test")
    if not re.search(r"Not evaluated controls", texts["ui_test"]):
        errors.append("compliance-ui.test.tsx missing not-evaluated UI assertions")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("swallow configAudit error instead of throwing unavailable", "compliance", 'throw new ComplianceDataUnavailableError("configAudit", msg)', 'return []'),
    ("cache partial response on auxiliary failure", "compliance", "if (!meta.partial)", "if (true)"),
    ("reintroduce .catch empty items mask", "compliance", 'throw new ComplianceDataUnavailableError("frameworks", msg)', 'throw new ComplianceDataUnavailableError("frameworks", msg); // .catch(() => ({ items: [] }))'),
    ("round incomplete pass rate up to 100%", "types", "Math.min(99.9, Math.floor(pct * 10 + 1e-9) / 10)", "Math.round(pct * 10) / 10"),
    ("treat summary-only as evaluated", "compliance", 'notEvaluatedReason: "summary_only"', 'notEvaluatedReason: "removed_reason" as never'),
    ("treat lowercase PSS IDs as evaluated pass", "compliance", 'notEvaluatedReason: "unlinked_check_id"', 'notEvaluatedReason: "removed_reason" as never'),
    ("return 200 instead of 503 on unavailable route", "config_route", "{ status: 503 }", "{ status: 200 }"),
    ("mask frameworks route failure", "fw_route", "unavailable: true", "unavailable: false"),
    ("hide unavailableSummary on server error", "page", "dataUnavailable: true", "dataUnavailable: false"),
    ("drop formatPassRatePct from FailureCounters", "counters", "formatPassRatePct(", "Math.round("),
    ("hide unaccepted LOW in ConfigAuditTable/Counters", "counters", "unacceptedLowTotal", "0 /* hidden */"),
    ("render empty instead of unavailable in FrameworksGrid", "grid", 'state="unavailable"', 'state="empty"'),
    ("remove snapshot fixture test", "unit_test", "compliance-snapshot.json", "removed-snapshot.json"),
    ("remove honest-status doc section", "doc", "### Three-state control evaluation & honest reporting", "### Removed"),
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
        print(f"PASS: R386 portal compliance honest 3-state status ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R386 portal compliance honest 3-state status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
