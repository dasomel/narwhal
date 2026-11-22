#!/usr/bin/env python3
"""R393: Portal /compliance round-6 fixes (J1 scopeNamespaces, J2 ClusterConfigAuditReport list/summary/CFG-01, J3 KISA CFG-01 allSettled).

Verifies statically and via --mutation-verify that:
1. J1 (MAJOR 1): `evaluateCheckAgainstCatalogue` enforces both `requiredNamespaces` and
   `scopeNamespaces` via `getApplicableNamespaces`, returning `no_report_in_scope_namespace`
   ("no report in the namespaces this check applies to") when no in-scope report exists in the
   applicable namespaces (e.g. KSV110 / CIS 5.7.4 requires a Workload report in `default`, never
   `dev` or a `default` Service report).
2. J2 (MAJOR 2): `fetchConfigAuditListWithMeta` and `getConfigAuditDetail` fetch both
   `configauditreports` and `clusterconfigauditreports` (throwing `ComplianceDataUnavailableError`
   on either failure), resolve hashed cluster resource names via `ownerReferences`/annotations,
   mark cluster-scoped rows with `scope: "cluster"`, and render a visible Cluster scope badge,
   `__cluster__` Select option, and `_cluster` detail route decoding in UI without adding rules to
   `ACCEPTED_CONFIG_AUDIT_RULES`.
3. J3 (MAJOR 3): `checkCfg01` in `kisa.ts` evaluates all four compliance sources independently
   via `Promise.allSettled`: any known actionable failure returns `fail` (naming failing sources
   and separately listing unreadable sources), unreadable sources with zero known failures return
   `warn` with reason (never reasonless `manual`), and partial KISA results are not cached.
4. Behavioral Vitest cases cover J1, J2, and J3 across backend, KISA, and UI suites.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "kisa": ROOT / "portal/src/lib/kisa.ts",
    "types": ROOT / "portal/src/types/compliance.ts",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "cfg_table": ROOT / "portal/src/components/compliance/config-audit-table.tsx",
    "cfg_detail": ROOT / "portal/src/app/(dashboard)/compliance/config-audit/[ns]/[name]/page.tsx",
    "evidence_test": ROOT / "portal/src/lib/compliance-evidence.test.ts",
    "kisa_test": ROOT / "portal/src/lib/kisa.test.ts",
    "ui_test": ROOT / "portal/src/components/compliance/compliance-ui.test.tsx",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    comp = texts["compliance"]
    kisa = texts["kisa"]

    # J1: scopeNamespaces applied in report filter + dedicated reason & i18n
    for needle in (
        "function getApplicableNamespaces(",
        "entry.scopeNamespaces?.length ? entry.scopeNamespaces : null",
        "const applicableNamespaces = getApplicableNamespaces(entry)",
        "applicableNamespaces.includes(ns) && isNamespaceExcluded(ns, evalCtx) === false",
        "no report in the namespaces this check applies to",
    ):
        if needle not in comp:
            errors.append(f"J1: compliance.ts missing {needle!r}")
    if comp.count('notEvaluatedReason: "no_report_in_scope_namespace"') < 2:
        errors.append("J1: compliance.ts must return no_report_in_scope_namespace in both RBAC and ConfigAudit branches")

    if '"no_report_in_scope_namespace"' not in texts["types"]:
        errors.append("J1: types/compliance.ts missing no_report_in_scope_namespace")
    for needle in (
        '"compliance.framework.notEvaluatedReason.no_report_in_scope_namespace":',
        '"No report in the namespaces this check applies to"',
        'case "no_report_in_scope_namespace":',
    ):
        if needle not in texts["i18n"]:
            errors.append(f"J1: i18n.ts missing {needle!r}")

    # J2: ClusterConfigAuditReport in Config Audit list, detail, summary, and UI
    list_m = re.search(
        r"async function fetchConfigAuditListWithMeta\b[\s\S]*?export async function getConfigAuditList\b",
        comp,
    )
    if not list_m or 'complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports")' not in list_m.group(0):
        errors.append("J2: fetchConfigAuditListWithMeta must fetch /apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports")

    detail_m = re.search(
        r"export async function getConfigAuditDetail\b[\s\S]*?interface RbacAuditListWithMeta\b",
        comp,
    )
    if not detail_m or 'complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports")' not in detail_m.group(0):
        errors.append("J2: getConfigAuditDetail must fetch /apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports")

    for needle in (
        "function resolveConfigAuditResourceName(",
        'scope: "cluster" as const',
    ):
        if needle not in comp:
            errors.append(f"J2: compliance.ts missing {needle!r}")

    if 'scope?: "cluster" | "namespace"' not in texts["types"]:
        errors.append("J2: types/compliance.ts missing ConfigAuditRow.scope")

    for needle in (
        '"compliance.table.clusterScope": "클러스터"',
        '"compliance.table.clusterScope": "Cluster"',
    ):
        if needle not in texts["i18n"]:
            errors.append(f"J2: i18n.ts missing {needle!r}")

    for needle in (
        't("compliance.table.clusterScope")',
        'value="__cluster__"',
        'namespace || "_cluster"',
    ):
        if needle not in texts["cfg_table"]:
            errors.append(f"J2: config-audit-table.tsx missing {needle!r}")

    for needle in (
        'nsDecoded === "_cluster" ? "" : nsDecoded',
        't(locale, "compliance.table.clusterScope")',
    ):
        if needle not in texts["cfg_detail"]:
            errors.append(f"J2: config-audit/[ns]/[name]/page.tsx missing {needle!r}")

    # J3: KISA CFG-01 allSettled semantics (never cancel known failure; never reasonless manual)
    cfg01_m = re.search(
        r"async function checkCfg01\b[\s\S]*?async function checkTls02\b",
        kisa,
    )
    if not cfg01_m:
        errors.append("J3: kisa.ts missing checkCfg01")
    else:
        cfg01_body = cfg01_m.group(0)
        if "Promise.all([" in cfg01_body:
            errors.append("J3 violation: checkCfg01 must not use Promise.all (cancels known failures)")
        for needle in (
            "await Promise.allSettled([",
            "unavailableSources",
            "failingSources",
            "조회 불가 소스:",
            "|| unavailableSources.length > 0)",
        ):
            if needle not in cfg01_body:
                errors.append(f"J3: checkCfg01 missing {needle!r}")

    if "if (!hasPartialCheck)" not in kisa:
        errors.append("J3: getKisaControls must skip caching when checkCfg01 had unavailable sources")

    # Docs + Vitest + R393 wiring
    for needle in ("scopeNamespaces", "ClusterConfigAuditReport", "allSettled"):
        if needle not in texts["doc"]:
            errors.append(f"docs/common/compliance-hardening.md missing {needle!r}")

    for file_key, case_name in (
        ("evidence_test", "J1 (MAJOR 1): enforces check scopeNamespaces (KSV110 / CIS 5.7.4 requires a Workload report in default, not dev or default Service)"),
        ("evidence_test", "J2 (MAJOR 2): ClusterConfigAuditReport ClusterRoleBinding KSV122 appears in Config Audit list, detail, and summary totals and fails closed on 403"),
        ("kisa_test", "J2 KISA CFG-01 fails on cluster-scoped ClusterRoleBinding KSV122 finding in config-audit list"),
        ("kisa_test", "J3 (MAJOR 3): actionable framework failure + infra 403 stays fail and names both failing and unreadable sources"),
        ("ui_test", "J1/J2 UI: renders cluster-scoped ConfigAuditRow with visible Cluster scope, actionable counter, _cluster detail link, and no_report_in_scope_namespace reason"),
    ):
        if case_name not in texts[file_key]:
            errors.append(f"{file_key} missing behavioral case: {case_name!r}")

    if "check R393 " not in texts["reg"] or "check-portal-compliance-round6.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R393 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("J1: ignore scopeNamespaces in getApplicableNamespaces", "compliance", "const scope = entry.scopeNamespaces?.length ? entry.scopeNamespaces : null", "const scope = null"),
    ("J1: drop no_report_in_scope_namespace reason", "compliance", 'notEvaluatedReason: "no_report_in_scope_namespace"', 'notEvaluatedReason: "no_report_for_family"'),
    ("J1: drop i18n no_report_in_scope_namespace en text", "i18n", '"No report in the namespaces this check applies to"', '"Removed"'),
    ("J2: stop fetching clusterconfigauditreports in fetchConfigAuditListWithMeta", "compliance", '    ;[namespaced, clustered] = await Promise.all([\n      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"),\n      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports"),\n    ])', '    namespaced = await complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"); clustered = { items: [] }'),
    ("J2: stop fetching clusterconfigauditreports in getConfigAuditDetail", "compliance", '    ;[namespaced, clustered] = await Promise.all([\n      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"),\n      complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/clusterconfigauditreports"),\n    ])\n  } catch (err) {\n    const msg = err instanceof Error ? err.message : String(err)\n    console.warn("[compliance] getConfigAuditDetail failed:", msg)', '    namespaced = await complianceK8sFetch<RawAuditList>("/apis/aquasecurity.github.io/v1alpha1/configauditreports"); clustered = { items: [] }\n  } catch (err) {\n    const msg = err instanceof Error ? err.message : String(err)\n    console.warn("[compliance] getConfigAuditDetail failed:", msg)'),
    ("J2: drop cluster scope marker on ConfigAuditRow", "compliance", 'scope: "cluster" as const', 'scope: undefined'),
    ("J2: drop visible clusterScope badge in ConfigAuditTable", "cfg_table", 't("compliance.table.clusterScope")', '""'),
    ("J2: drop _cluster decode in ConfigAuditDetailPage", "cfg_detail", 'nsDecoded === "_cluster" ? "" : nsDecoded', "nsDecoded"),
    ("J3: revert checkCfg01 to Promise.all", "kisa", "await Promise.allSettled([", "await Promise.all(["),
    ("J3: drop unavailableSources from warn condition in checkCfg01", "kisa", "if (notEvaluatedControls > 0 || hasUnknownCoverage || unavailableSources.length > 0)", "if (notEvaluatedControls > 0 || hasUnknownCoverage)"),
    ("J3: cache partial KISA result", "kisa", "if (!hasPartialCheck)", "if (true)"),
    ("drop R393 wiring", "reg", "check R393 ", "check R9999 "),
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
        print(f"PASS: R393 portal compliance round-6 fixes J1-J3 ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R393 portal compliance round-6 fixes J1-J3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
