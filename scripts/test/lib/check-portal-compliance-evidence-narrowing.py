#!/usr/bin/env python3
"""R392: Portal /compliance round-5 evidence narrowing (H1-H5).

Verifies statically and via --mutation-verify that:
1. H1 (MAJOR 1+2): infra-assessment family (AVD-KCV-*, Node and Pod) NEVER returns `"passed"` from
   report existence in `evaluateCheckAgainstCatalogue`; non-failing infra checks return
   `not_evaluated` with `infra_no_per_check_evidence`, with ko/en wording and page notes pointing
   to `scripts/test/verify-cluster.sh`, and docs state that most CIS node-level controls show as
   not evaluated by the scanner.
2. H2 (MAJOR 3): `trivy-check-catalogue.ts` resolves exact legacy/alias IDs (`KSV0012 -> KCV0012`,
   `KCV0135 -> KCV0035`, `KSV-0124 -> KSV124`) before `normalizeCheckId` without colliding `KSV0012`
   into `KSV012`, and `controlEvidenceFailures` joins across all report families (`config`, `rbac`,
   `infra`) and aliases before deciding any state.
3. H3 (MAJOR 4): `ClusterRole/narwhal-portal` already allows `clusterconfigauditreports`, and
   `fetchFrameworkContext` fetches and joins `/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports`
   into `configItems`, `buildReportTargetMap`, and `failedAuxSources`.
4. H4 (MAJOR 5): `controlEvidenceFailures` does NOT filter by `ctx.nodeRoles` (a failed
   `/api/v1/nodes` read never hides an existing failing infra record).
5. H5 (MAJOR 6): no compliance surface uses "모든 검사 통과" / "All checks passed" or `"Clean"`
   for an empty failure list (Infra list/panel/detail, Config Audit panel/detail, RBAC panel/detail).
6. Behavioral Vitest cases cover H1-H5 and all 10 reviewer expected control states.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "catalogue_ts": ROOT / "portal/src/lib/trivy-check-catalogue.ts",
    "types": ROOT / "portal/src/types/compliance.ts",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "infra_list": ROOT / "portal/src/components/compliance/infra-audit-list.tsx",
    "infra_detail": ROOT / "portal/src/app/(dashboard)/compliance/infra-audit/[node]/page.tsx",
    "cfg_detail": ROOT / "portal/src/app/(dashboard)/compliance/config-audit/[ns]/[name]/page.tsx",
    "rbac_detail": ROOT / "portal/src/app/(dashboard)/compliance/rbac-audit/[ns]/[name]/page.tsx",
    "evidence_test": ROOT / "portal/src/lib/compliance-evidence.test.ts",
    "ui_test": ROOT / "portal/src/components/compliance/compliance-ui.test.tsx",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "rbac_yaml": ROOT / "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    comp = texts["compliance"]

    # H1: infra branch of evaluateCheckAgainstCatalogue must NEVER return passed
    infra_m = re.search(
        r'if \(entry\.reportFamily === "infra"\) \{([\s\S]*?)if \(entry\.reportFamily === "rbac"\)',
        comp,
    )
    if not infra_m:
        errors.append("compliance.ts missing infra branch in evaluateCheckAgainstCatalogue")
    else:
        infra_body = infra_m.group(1)
        if re.search(r'evaluationState:\s*["\']passed["\']', infra_body):
            errors.append("H1 violation: infra branch in evaluateCheckAgainstCatalogue must NEVER return passed")
        if infra_body.count('notEvaluatedReason: "infra_no_per_check_evidence"') < 2:
            errors.append("H1: infra branch must return infra_no_per_check_evidence for both Node and Pod checks")

    if '"infra_no_per_check_evidence"' not in texts["types"]:
        errors.append("types/compliance.ts missing infra_no_per_check_evidence")

    # H2 + H4: alias resolver + cross-family failure join independent of nodeRoles
    cat_ts = texts["catalogue_ts"]
    for needle in (
        "const EXACT_ALIAS_TO_CANONICAL = new Map<string, string>()",
        "const exact = EXACT_ALIAS_TO_CANONICAL.get(upper)",
        "const NORMALIZED_ALIAS_TO_CANONICAL = new Map<string, string>()",
        "export function resolveCanonicalCheckId(",
    ):
        if needle not in cat_ts:
            errors.append(f"trivy-check-catalogue.ts missing {needle!r}")

    fail_m = re.search(
        r"function controlEvidenceFailures\b[\s\S]*?function normalizeNodeType\b",
        comp,
    )
    if not fail_m:
        errors.append("compliance.ts missing controlEvidenceFailures")
    else:
        fail_body = fail_m.group(0)
        for needle in (
            "...ctx.evidenceReports.config,",
            "...ctx.evidenceReports.rbac,",
            "...ctx.evidenceReports.infra,",
            "resolveCanonicalCheckId(",
        ):
            if needle not in fail_body:
                errors.append(f"H2: controlEvidenceFailures missing {needle!r}")
        if "ctx.nodeRoles" in fail_body:
            errors.append("H4 violation: controlEvidenceFailures must not depend on ctx.nodeRoles")

    # H3: ClusterConfigAuditReport fetched & joined (RBAC already grants it)
    if "- clusterconfigauditreports" not in texts["rbac_yaml"]:
        errors.append("ClusterRole narwhal-portal expected to allow clusterconfigauditreports")
    for needle in (
        'tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports")',
        'if (!configClusterRes.ok) failedAuxSources.push("clusterconfigauditreports")',
        'clusterrolebinding: "ClusterRoleBinding"',
    ):
        if needle not in comp:
            errors.append(f"H3: compliance.ts missing {needle!r}")

    # H5: no "모든 검사 통과" / "All checks passed" on compliance keys, no "Clean" in infra-audit-list, H1 note on infra/frameworks
    i18n = texts["i18n"]
    if '"compliance.check.allPassed": "모든 검사 통과"' in i18n or '"compliance.check.allPassed": "All checks passed"' in i18n:
        errors.append("H5 violation: compliance.check.allPassed must not say 모든 검사 통과 / All checks passed")
    for needle in (
        '"compliance.check.allPassed": "실패 기록 없음"',
        '"compliance.check.allPassed": "No failure recorded"',
        '"compliance.framework.notEvaluatedReason.infra_no_per_check_evidence":',
        'case "infra_no_per_check_evidence":',
        "scripts/test/verify-cluster.sh",
    ):
        if needle not in i18n:
            errors.append(f"H1/H5: i18n.ts missing {needle!r}")

    if ': "Clean"' in texts["infra_list"]:
        errors.append("H5 violation: infra-audit-list.tsx must not render hardcoded 'Clean' for zero failures")
    if 't("compliance.infra.emptyChecksCaveat")' not in texts["infra_list"]:
        errors.append("H5/H1: infra-audit-list.tsx must render compliance.infra.emptyChecksCaveat")
    if texts["infra_detail"].count('t(locale, "compliance.infra.emptyChecksCaveat")') < 2:
        errors.append("H5/H1: infra-audit/[node]/page.tsx must render compliance.infra.emptyChecksCaveat in both header and empty-checks state")
    for detail_key in ("cfg_detail", "rbac_detail", "infra_detail"):
        if 't(locale, "compliance.check.allPassed")' not in texts[detail_key]:
            errors.append(f"H5: {detail_key} must render failed-only empty state via compliance.check.allPassed")

    # Docs + Vitest cases + R392 wiring
    if "scripts/test/verify-cluster.sh" not in texts["doc"] or "most CIS node-level" not in texts["doc"]:
        errors.append("H1: docs/common/compliance-hardening.md must state most CIS node-level controls show as not evaluated and point to verify-cluster.sh")
    for case in (
        "H1 (MAJOR 1+2): infra node and control-plane component checks never become passed from clean reports",
        "H2 (MAJOR 3): failure join matches legacy/alias IDs across families without colliding KSV0012 with KSV012",
        "H3 (MAJOR 4): fetches and joins ClusterConfigAuditReport ClusterRoleBinding KSV111 failures and marks 403 as coverage_unknown",
        "H4 (MAJOR 5): failed /api/v1/nodes read never hides existing infra failures even with explicit nodeType",
        "matches all 10 reviewer expected control states on a hand-built live-like fixture",
    ):
        if case not in texts["evidence_test"]:
            errors.append(f"compliance-evidence.test.ts missing behavioral case: {case!r}")
    if "H5/H1: empty failure lists never say All checks passed or 모든 검사 통과" not in texts["ui_test"]:
        errors.append("compliance-ui.test.tsx missing H5/H1 wording test")
    if "check R392 " not in texts["reg"] or "check-portal-compliance-evidence-narrowing.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R392 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("H1: allow Node infra check to return passed", "compliance", 'notEvaluatedReason: "infra_no_per_check_evidence",\n        notEvaluatedDetail: `node/control-plane check (${normId}): the scanner leaves no per-check evidence`,', 'evaluationState: "passed",'),
    ("H1: drop Pod infra_no_per_check_evidence", "compliance", 'notEvaluatedReason: "infra_no_per_check_evidence",\n      notEvaluatedDetail: `node/control-plane check (${normId}): the scanner leaves no per-check evidence`,', 'notEvaluatedReason: "no_report_for_family",'),
    ("H2: bypass exact alias lookup in catalogue resolver", "catalogue_ts", "const exact = EXACT_ALIAS_TO_CANONICAL.get(upper)", "const exact = undefined"),
    ("H2: restrict failure join away from config reports", "compliance", "...ctx.evidenceReports.config,", ""),
    ("H3: stop fetching clusterconfigauditreports", "compliance", 'tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports")', 'tryComplianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports")'),
    ("H4: reintroduce ctx.nodeRoles filter in controlEvidenceFailures", "compliance", "const seenCanonicalIds = new Set<string>()", "const _roles = ctx.nodeRoles; const seenCanonicalIds = new Set<string>()"),
    ("H5: restore Korean 모든 검사 통과", "i18n", '"compliance.check.allPassed": "실패 기록 없음"', '"compliance.check.allPassed": "모든 검사 통과"'),
    ("H5: restore English All checks passed", "i18n", '"compliance.check.allPassed": "No failure recorded"', '"compliance.check.allPassed": "All checks passed"'),
    ("H5: restore hardcoded Clean in InfraAuditList", "infra_list", ': t("compliance.framework.noFailureRecordedShort")', ': "Clean"'),
    ("H5: drop H1 caveat from InfraAuditDetailPage", "infra_detail", 't(locale, "compliance.infra.emptyChecksCaveat")', 't(locale, "compliance.check.allPassedHint")'),
    ("drop 10-control reviewer expectation test", "evidence_test", "matches all 10 reviewer expected control states on a hand-built live-like fixture", "removed test"),
    ("drop R392 wiring", "reg", "check R392 ", "check R9999 "),
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
        print(f"PASS: R392 portal compliance evidence narrowing H1-H5 ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R392 portal compliance evidence narrowing H1-H5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
