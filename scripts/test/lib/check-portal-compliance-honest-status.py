#!/usr/bin/env python3
"""R387: Portal /compliance honest 3-state evaluation, pooled rates, KISA CFG-01, partial/503 guard, and behavioral test coverage.

Verifies that:
1. Positive per-check-ID evidence is required before any non-failing control becomes `passed`
   (collectPassedCheckIds per report kind/scope; clean `checks: []` infra reports and unknown IDs
   like AVD-KSV-9999 become `no_evidence_in_report`; 403/500 auxiliary reads become `coverage_unknown`).
2. Node-type scoping uses spec/collector `nodeType` metadata rather than hardcoded CIS section/ID
   ranges (no WORKER_NODE_CHECK_IDS / WORKER_COMMAND_RE over-correction on 4.1.5 / KCV0073).
3. Controls present in `spec.compliance.controls` but absent from `status.detailReport.results`
   are materialised as `not_evaluated` (`absent_from_report`).
4. Aggregate framework pass rates in FailureCounters are pooled ratios with numerator/denominator
   labels (never unweighted averages), and EVERY `formatPassRatePct(` call site passes
   `hasNotEvaluated` so `100%` never leaks when any control is unevaluated or actionable.
5. KISA CFG-01 (`portal/src/lib/kisa.ts`) evaluates `actionableControls` / `notEvaluatedControls`
   rather than passing on raw `f.passRate` when controls are unevaluated.
6. `GET /api/compliance/summary` returns HTTP 503 `{ unavailable: true }` when every primary source
   fails; partial auxiliary failures reach the UI (`partialSourceLabels` / `partialNotice`) and are
   never cached.
7. Behavioral vitest cases exist for unknown ID, forbidden auxiliary read, 4.1.5 node applicability,
   missing detail control, pooled rate, 100% leak, KISA CFG-01, summary 503, and partial UI notice,
   and `compliance-snapshot.json` is trimmed (<= 600 lines).
--mutation-verify proves mutated copies are rejected (runs without helm/promtool).
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
    "kisa": ROOT / "portal/src/lib/kisa.ts",
    "config_route": ROOT / "portal/src/app/api/compliance/config-audit/route.ts",
    "rbac_route": ROOT / "portal/src/app/api/compliance/rbac-audit/route.ts",
    "infra_route": ROOT / "portal/src/app/api/compliance/infra-audit/route.ts",
    "fw_route": ROOT / "portal/src/app/api/compliance/frameworks/route.ts",
    "summary_route": ROOT / "portal/src/app/api/compliance/summary/route.ts",
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
    "kisa_test": ROOT / "portal/src/lib/kisa.test.ts",
    "summary_route_test": ROOT / "portal/src/app/api/compliance/summary/route.test.ts",
    "evidence_test": ROOT / "portal/src/lib/compliance-evidence.test.ts",
    "snapshot": ROOT / "portal/src/lib/__fixtures__/compliance-snapshot.json",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

REQUIRED_VITEST_CASES: list[tuple[str, str]] = [
    ("evidence_test", "G1 failure evidence wins over mixed positive evidence and stale CCR placeholder pass"),
    ("evidence_test", "G2 KCV0007 requires InfraAssessmentReport evidence and rejects ConfigAuditReport evidence"),
    ("evidence_test", "G2 maps snapshot RBAC KSV041 and KSV048 to RBAC evidence, including mixed-family controls"),
    ("evidence_test", "G3 auxiliary read failure %s makes positive workload evidence coverage_unknown"),
    ("kisa_test", "G4 KISA CFG-01 fails on actionable %s findings without actionable framework controls"),
    ("kisa_test", "G5 old-shape framework and KISA cache never produce CFG-01 pass"),
    ("ui_test", "G5 old-shape object renders unknown in grid, summary SSR and framework detail"),
    ("unit_test", "never classifies unknown check ID (AVD-KSV-9999) or uncatalogued optional IDs as passed while allowing clean in-scope catalogue checks"),
    ("unit_test", "evaluates every C2 check-catalogue branch on failed-only reports with exact hand-derived counts"),
    ("unit_test", "fails closed to coverage_unknown and surfaces partial without caching when auxiliary Deployment/ConfigMap read returns 403"),
    ("unit_test", "evaluates 4.1.5 (KCV0073) on control-plane nodes from positive infra report evidence without worker-node over-correction"),
    ("unit_test", "materialises spec.compliance.controls absent from status.detailReport.results as not_evaluated (absent_from_report)"),
    ("ui_test", "computes pooled aggregate pass rates with numerator/denominator labels and never prints 100% when any control is not evaluated or actionable"),
    ("ui_test", "renders visible partial-data notice naming failed auxiliary sources"),
    ("kisa_test", "prevents KISA CFG-01 from passing when controls are unevaluated and fails on actionable controls"),
    ("summary_route_test", "returns HTTP 503 unavailable from /api/compliance/summary when every primary source fails"),
]


def check_format_pass_rate_calls(label: str, text: str) -> list[str]:
    """Every formatPassRatePct(...) call in UI files must pass hasNotEvaluated."""
    errs: list[str] = []
    calls = list(re.finditer(r"formatPassRatePct\(([\s\S]*?)\)", text))
    if not calls:
        errs.append(f"{label} must call formatPassRatePct(")
        return errs
    for idx, m in enumerate(calls, 1):
        if "hasNotEvaluated" not in m.group(1):
            errs.append(f"{label} formatPassRatePct() call #{idx} omits hasNotEvaluated (100% leak)")
    return errs


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    comp = texts["compliance"]
    types = texts["types"]
    kisa = texts["kisa"]
    doc = texts["doc"]

    # 1. Check-catalogue evidence, no CIS-section worker over-correction, missing-spec materialisation
    for needle in (
        "export class ComplianceDataUnavailableError",
        "function evaluateCheckAgainstCatalogue(",
        "function buildFrameworkEvalContext(",
        "function classifyNonFailingControl(",
        "getCatalogueCheck(",
        'notEvaluatedReason: "not_in_catalogue"',
        'notEvaluatedReason: "no_report_for_family"',
        'notEvaluatedReason: "excluded_namespace"',
        'notEvaluatedReason: "unsupported_kind"',
        'notEvaluatedReason: "missing_node_type"',
        'notEvaluatedReason: "unlinked_check_id"',
        'notEvaluatedReason: "manual"',
        'notEvaluatedReason: "summary_only"',
        'notEvaluatedReason: "absent_from_report"',
        'notEvaluatedReason: "coverage_unknown"',
        "operatorConfigError",
        "failedAuxSources",
        "unacceptedLowConfigAuditFailures",
        "droppedConfigAuditReports",
        "rebuildIntervalHours: 6",
        "/api/v1/nodes",
        "/apis/apps/v1/namespaces/security-system/deployments/trivy-operator",
        "/api/v1/namespaces/security-system/configmaps/trivy-operator-trivy-config",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing required logic: {needle!r}")

    for forbidden in ("WORKER_NODE_CHECK_IDS", "WORKER_COMMAND_RE", "MASTER_NODE_CHECK_IDS", "MASTER_COMMAND_RE"):
        if forbidden in comp:
            errors.append(f"compliance.ts must not hardcode CIS section/ID node-type mapping: {forbidden!r}")

    # Reject short-circuiting classifyNonFailingControl to passed before check-catalogue evaluation
    cls_m = re.search(
        r"function classifyNonFailingControl\b[\s\S]*?function buildComplianceFrameworkData\b",
        comp,
    )
    if not cls_m:
        errors.append("compliance.ts missing classifyNonFailingControl body")
    else:
        cls_body = cls_m.group(0)
        if re.search(r"if\s*\(\s*true\s*\)\s*(?:\{\s*)?return\s*\{\s*evaluationState:\s*['\"]passed['\"]", cls_body):
            errors.append("classifyNonFailingControl must never unconditionally return passed")
        if "evaluateCheckAgainstCatalogue(id, specControl, evalCtx)" not in cls_body:
            errors.append("classifyNonFailingControl must evaluate every check ID via evaluateCheckAgainstCatalogue()")

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
        '"no_evidence_in_report"',
        '"absent_from_report"',
        "evaluatedPassRate?: number",
        "notEvaluatedCount?: number",
        "partialSources?: string[]",
        "partial?: boolean",
        "unacceptedLowConfigAuditFailures?: CheckSummary",
        "droppedConfigAuditReports?: number",
        "sources?: Record<ComplianceSourceKey, ComplianceSourceStatus>",
    ):
        if needle not in types:
            errors.append(f"types/compliance.ts missing contract: {needle!r}")

    # 3. API routes fail loudly with 503 unavailable (including summary route when all sources fail)
    for rname in ("config_route", "rbac_route", "infra_route", "fw_route", "summary_route"):
        rtext = texts[rname]
        for needle in ("ComplianceDataUnavailableError", "unavailable: true", "status: 503"):
            if needle not in rtext:
                errors.append(f"{rname} missing 503 unavailable handling: {needle!r}")
    if "if (allUnavailable)" not in texts["summary_route"]:
        errors.append("summary/route.ts must return 503 when every primary source is unavailable")

    # 4. KISA CFG-01 must not pass on raw rate when controls are unevaluated
    if "f.passRate < 0.6" in kisa or "f.passRate < 0.9" in kisa:
        errors.append("kisa.ts checkCfg01 must not grade CFG-01 on raw f.passRate thresholds")
    for needle in ("actionableControls", "notEvaluatedControls", "if (actionableControls > 0 || actionableFailures > 0 || auditFailures > 0)", "if (notEvaluatedControls > 0"):
        if needle not in kisa:
            errors.append(f"kisa.ts checkCfg01 missing evaluated verdict logic: {needle!r}")

    # 5. Page & UI: pooled rates, zero 100% leaks, partial notice, unavailable states
    if "unavailableSummary" not in texts["page"] or "dataUnavailable: true" not in texts["page"]:
        errors.append("compliance/page.tsx must use unavailableSummary (not zero emptySummary) on fetch failure")

    for cname in ("counters", "grid", "fw_detail"):
        errors.extend(check_format_pass_rate_calls(cname, texts[cname]))

    if "function avgRate(" in texts["counters"]:
        errors.append("failure-counters.tsx must use pooled ratios, not unweighted avgRate()")
    for needle in (
        "function pooledRatio(",
        'isUnavailable("configAudit")',
        "unacceptedLowTotal",
        "droppedReports",
        "compliance.summary.rebuildNote",
        "compliance.summary.partialNotice",
        "partialSourceLabels",
    ):
        if needle not in texts["counters"]:
            errors.append(f"failure-counters.tsx missing {needle!r}")

    for cname in ("grid", "fw_detail"):
        if "compliance.framework.notEvaluatedControlsGroup" not in texts[cname]:
            errors.append(f"{cname} missing notEvaluatedControlsGroup")
        if "compliance.framework.coverageUnknownNotice" not in texts[cname]:
            errors.append(f"{cname} missing coverageUnknownNotice")

    if "unavailable === true" not in texts["fetch_json"]:
        errors.append("fetch-json.ts must reject { unavailable: true } payloads")

    for tname in ("grid", "cfg_table", "rbac_table", "infra_list"):
        if "fetchComplianceJson" not in texts[tname]:
            errors.append(f"{tname} must use fetchComplianceJson")
        if 'state="unavailable"' not in texts[tname]:
            errors.append(f"{tname} must render DataState state='unavailable' on error")

    # 6. Docs, trimmed fixture size, behavioral vitest cases, and R387 wiring
    if "### Three-state control evaluation & honest reporting" not in doc:
        errors.append("docs/common/compliance-hardening.md missing '### Three-state control evaluation & honest reporting'")
    if "ComplianceDataUnavailableError" not in doc or "unacceptedLowConfigAuditFailures" not in doc:
        errors.append("docs/common/compliance-hardening.md must document data unavailability and unaccepted LOW")

    snap_lines = texts["snapshot"].splitlines()
    if not texts["snapshot"].strip():
        errors.append("compliance-snapshot.json fixture is empty")
    elif len(snap_lines) > 600:
        errors.append(f"compliance-snapshot.json must stay trimmed (<= 600 lines, got {len(snap_lines)})")
    if "compliance-snapshot.json" not in texts["unit_test"]:
        errors.append("compliance.test.ts missing compliance-snapshot.json test")

    for file_key, case_name in REQUIRED_VITEST_CASES:
        if case_name not in texts[file_key]:
            errors.append(f"{file_key} missing required behavioral vitest case: {case_name!r}")

    if "check R387 " not in texts["reg"] or "check-portal-compliance-honest-status.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R387 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("force classifyNonFailingControl to passed", "compliance", "const checks = resultControl.checks ?? []", 'if (true) return { evaluationState: "passed" }\n  const checks = resultControl.checks ?? []'),
    ("bypass evaluateCheckAgainstCatalogue in classifyNonFailingControl", "compliance", "const outcome = evaluateCheckAgainstCatalogue(id, specControl, evalCtx)", 'const outcome = { evaluationState: "passed" as const }'),
    ("reintroduce hardcoded WORKER_COMMAND_RE over-correction", "compliance", "const CONTROL_PLANE_CHECK_IDS", "const WORKER_COMMAND_RE = /^CMD-0022$/\nconst CONTROL_PLANE_CHECK_IDS"),
    ("drop missing-spec absent_from_report materialisation", "compliance", 'notEvaluatedReason: "absent_from_report"', 'notEvaluatedReason: "removed" as never'),
    ("swallow configAudit error instead of throwing unavailable", "compliance", 'throw new ComplianceDataUnavailableError("configAudit", msg)', "return []"),
    ("cache partial response on auxiliary failure", "compliance", "if (!meta.partial)", "if (true)"),
    ("round incomplete pass rate up to 100%", "types", "Math.min(99.9, Math.floor(pct * 10 + 1e-9) / 10)", "Math.round(pct * 10) / 10"),
    ("leak 100% on raw formatter in FrameworksGrid", "grid", "const rawPctText = formatPassRatePct(fw.passRate, {\n            hasActionableFail: fw.failCount > 0 || hasActionable,\n            hasNotEvaluated: hasNotEval,\n          })", "const rawPctText = formatPassRatePct(fw.passRate, {\n            hasActionableFail: fw.failCount > 0,\n          })"),
    ("revert pooled ratio to unweighted avgRate", "counters", "function pooledRatio(", "function avgRate("),
    ("drop partial notice from FailureCounters", "counters", "compliance.summary.partialNotice", "compliance.summary.removed"),
    ("grade KISA CFG-01 on raw passRate thresholds", "kisa", "if (actionableControls > 0 || actionableFailures > 0 || auditFailures > 0) {", "if (frameworks.some((f) => f.passRate < 0.6)) {"),
    ("return 200 instead of 503 on summary route when all sources fail", "summary_route", "if (allUnavailable) {", "if (false) {"),
    ("return 200 instead of 503 on unavailable config route", "config_route", "{ status: 503 }", "{ status: 200 }"),
    ("hide unavailableSummary on server error", "page", "dataUnavailable: true", "dataUnavailable: false"),
    ("drop unknown-ID behavioral vitest case", "unit_test", "never classifies unknown check ID (AVD-KSV-9999) or uncatalogued optional IDs as passed while allowing clean in-scope catalogue checks", "removed test"),
    ("drop R387 wiring in regression-check-kakao.sh", "reg", "check R387 ", "check R9999 "),
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
        print(f"PASS: R387 portal compliance honest 3-state status ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R387 portal compliance honest 3-state status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
