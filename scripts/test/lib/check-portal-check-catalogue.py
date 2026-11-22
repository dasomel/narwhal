#!/usr/bin/env python3
"""R391: Portal Trivy check catalogue sync, C2 check-catalogue evaluation rule, and failure-only UI wording.

Verifies offline that:
1. `node portal/scripts/gen-trivy-check-catalogue.mjs --check` succeeds against the pinned
   trivy-operator chart version (`trivy-operator.yaml`), image tag (`scripts/airgap/images.txt`),
   policy mode (`embedded`), and `trivy-checks` module (`v1.7.1`).
2. Behavioural pin-drift verification: mutating the chart version, operator image tag,
   policyMode, or catalogue check schema makes `validateCatalogue` fail loudly.
3. `compliance.ts` classifies non-failing controls via `evaluateCheckAgainstCatalogue` (every
   check ID in catalogue + applicable kind/nodeType/namespace in scope + in-scope report of the
   matching family/kind exists) instead of requiring non-existent `success: true` records.
4. UI wording in `i18n.ts`, `failure-counters.tsx`, `frameworks-grid.tsx`, and
   `frameworks/[id]/page.tsx` explicitly labels the positive state as
   "실패 기록 없음 (스캐너는 실패만 기록)" / "No failure recorded (scanner records failures only)"
   with the one explanatory sentence (`compliance.framework.failureOnlyNote`).
5. `--mutation-verify` proves mutated copies are rejected.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "gen": ROOT / "portal/scripts/gen-trivy-check-catalogue.mjs",
    "cat_json": ROOT / "portal/src/data/trivy-check-catalogue.generated.json",
    "cat_ts": ROOT / "portal/src/lib/trivy-check-catalogue.ts",
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "types": ROOT / "portal/src/types/compliance.ts",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "counters": ROOT / "portal/src/components/compliance/failure-counters.tsx",
    "grid": ROOT / "portal/src/components/compliance/frameworks-grid.tsx",
    "detail": ROOT / "portal/src/app/(dashboard)/compliance/frameworks/[id]/page.tsx",
    "unit_test": ROOT / "portal/src/lib/compliance.test.ts",
    "doc": ROOT / "docs/common/compliance-hardening.md",
    "op_yaml": ROOT / "gitops/charts/narwhal-apps/templates/trivy-operator.yaml",
    "images": ROOT / "scripts/airgap/images.txt",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def check_live_generator_cli() -> list[str]:
    res = subprocess.run(
        ["node", str(FILES["gen"]), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return [f"gen-trivy-check-catalogue.mjs --check failed (rc={res.returncode}): {res.stderr.strip()} {res.stdout.strip()}"]

    # Behavioural drift test via Node ESM import of validateCatalogue:
    probe = r"""
import { readFileSync } from "node:fs";
import { validateCatalogue, readRepoPins, OUT_JSON } from "./portal/scripts/gen-trivy-check-catalogue.mjs";

const pins = readRepoPins();
const base = JSON.parse(readFileSync(OUT_JSON, "utf8"));
if (validateCatalogue(base, pins).length !== 0) {
  console.error("baseline catalogue failed validateCatalogue");
  process.exit(1);
}
const driftCases = [
  ["chart bump", base, { ...pins, trivyOperatorChartVersion: "9.9.9" }],
  ["operator image bump", base, { ...pins, trivyOperatorVersion: "9.9.9", trivyChecksVersion: null }],
  ["policyMode flip", base, { ...pins, policyMode: "bundle" }],
  ["catalogue checksVersion drift", { ...base, source: { ...base.source, trivyChecksVersion: "v9.9.9" } }, pins],
  ["empty catalogue checks", { ...base, checks: {} }, pins],
];
for (const [label, cat, p] of driftCases) {
  if (validateCatalogue(cat, p).length === 0) {
    console.error("drift case was not rejected: " + label);
    process.exit(1);
  }
}
"""
    res2 = subprocess.run(
        ["node", "--input-type=module", "-e", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if res2.returncode != 0:
        return [f"catalogue drift behavioral test failed: {res2.stderr.strip()} {res2.stdout.strip()}"]
    return []


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    # 1. Catalogue JSON invariants
    try:
        cat = json.loads(texts["cat_json"])
    except Exception as exc:
        return [f"trivy-check-catalogue.generated.json invalid JSON: {exc}"]

    src = cat.get("source") or {}
    if src.get("policyMode") != "embedded":
        errors.append(f"catalogue policyMode must be 'embedded', got {src.get('policyMode')!r}")
    if "checks/kubernetes/advanced/optional" not in (src.get("excludedPaths") or []):
        errors.append("catalogue must record checks/kubernetes/advanced/optional in excludedPaths")

    checks_map = cat.get("checks") or {}
    for required_id, expected_family in (
        ("KCV0001", "infra"),
        ("KCV0048", "infra"),
        ("KCV0073", "infra"),
        ("KSV014", "configAudit"),
        ("KSV038", "configAudit"),
        ("KSV041", "rbac"),
        ("KSV103", "configAudit"),
        ("KSV111", "configAudit"),
    ):
        entry = checks_map.get(required_id)
        if not entry or entry.get("reportFamily") != expected_family:
            errors.append(f"catalogue missing or wrong family for {required_id} (expected {expected_family})")
    for forbidden_id in ("KSV004", "KSV029", "KSV039", "KSV040", "KSV9999"):
        if forbidden_id in checks_map:
            errors.append(f"catalogue must not include unexecuted/unknown check {forbidden_id}")
    if checks_map.get("KCV0073", {}).get("nodeTypes") != ["master", "worker"]:
        errors.append("catalogue KCV0073 must apply to ['master', 'worker'] (no worker-only over-correction)")
    if checks_map.get("KCV0048", {}).get("nodeTypes") != ["master"]:
        errors.append("catalogue KCV0048 must apply to ['master']")

    # 2. Compliance evaluator uses check catalogue (not positive success:true records)
    comp = texts["compliance"]
    if "function collectPassedCheckIds(" in comp:
        errors.append("compliance.ts must replace collectPassedCheckIds with the check-catalogue rule")
    for needle in (
        'import { getCatalogueCheck } from "./trivy-check-catalogue"',
        "function evaluateCheckAgainstCatalogue(",
        'notEvaluatedReason: "not_in_catalogue"',
        "excludedNamespaces",
        "matchesSupportedKind(",
        "D-CHECK-CATALOGUE",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing {needle!r}")
    if comp.count('notEvaluatedReason: "no_report_for_family"') < 4:
        errors.append("compliance.ts must enforce no_report_for_family across all 4 report scopes (Node infra, Pod infra, RBAC, ConfigAudit)")

    for needle in ('"not_in_catalogue"', '"no_report_for_family"'):
        if needle not in texts["types"]:
            errors.append(f"types/compliance.ts missing {needle}")

    # 3. C3 wording: explicit failure-only positive label + one explanatory sentence
    i18n = texts["i18n"]
    for needle in (
        "실패 기록 없음 (스캐너는 실패만 기록)",
        "No failure recorded (scanner records failures only)",
        '"compliance.framework.failureOnlyNote"',
        '"compliance.framework.notEvaluatedReason.not_in_catalogue"',
        '"compliance.framework.notEvaluatedReason.no_report_for_family"',
        'case "not_in_catalogue":',
        'case "no_report_for_family":',
    ):
        if needle not in i18n:
            errors.append(f"i18n.ts missing {needle!r}")

    for ui_key in ("counters", "grid", "detail"):
        if "compliance.framework.failureOnlyNote" not in texts[ui_key]:
            errors.append(f"{ui_key} must display compliance.framework.failureOnlyNote")
        if "compliance.framework.noFailureRecordedLabel" not in texts[ui_key]:
            errors.append(f"{ui_key} must reference compliance.framework.noFailureRecordedLabel")

    # 4. C4 tests, docs, and R391 wiring
    if "evaluates every C2 check-catalogue branch on failed-only reports with exact hand-derived counts" not in texts["unit_test"]:
        errors.append("compliance.test.ts missing C2 branch + live-like failed-only test")
    if "trivy-check-catalogue.generated.json" not in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md must document trivy-check-catalogue.generated.json")
    if "check R391 " not in texts["reg"] or "check-portal-check-catalogue.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R391 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("switch catalogue policyMode to bundle", "cat_json", '"policyMode": "embedded"', '"policyMode": "bundle"'),
    ("reintroduce optional KSV039 in catalogue", "cat_json", '"KSV038": {', '"KSV039": {"id":"KSV039","avdId":"AVD-KSV-0039","reportFamily":"configAudit","kinds":["LimitRange"],"severity":"Low","title":"opt"}, "KSV038": {'),
    ("misclassify KCV0073 as worker-only in catalogue", "cat_json", '"KCV0073": {\n      "id": "KCV0073",\n      "avdId": "AVD-KCV-0073",\n      "reportFamily": "infra",\n      "kinds": [\n        "Node"\n      ],\n      "nodeTypes": [\n        "master",\n        "worker"\n      ]', '"KCV0073": {\n      "id": "KCV0073",\n      "avdId": "AVD-KCV-0073",\n      "reportFamily": "infra",\n      "kinds": [\n        "Node"\n      ],\n      "nodeTypes": [\n        "worker"\n      ]'),
    ("drop not_in_catalogue reason in compliance.ts", "compliance", 'notEvaluatedReason: "not_in_catalogue"', 'notEvaluatedReason: "removed" as never'),
    ("drop no_report_for_family reason in compliance.ts", "compliance", 'notEvaluatedReason: "no_report_for_family"', 'notEvaluatedReason: "removed" as never'),
    ("drop Korean failure-only positive label", "i18n", "실패 기록 없음 (스캐너는 실패만 기록)", "평가 통과"),
    ("drop English failure-only positive label", "i18n", "No failure recorded (scanner records failures only)", "Verified pass"),
    ("drop failureOnlyNote from FailureCounters", "counters", "compliance.framework.failureOnlyNote", "compliance.framework.removed"),
    ("drop failureOnlyNote from FrameworksGrid", "grid", "compliance.framework.failureOnlyNote", "compliance.framework.removed"),
    ("drop failureOnlyNote from FrameworkDetailPage", "detail", "compliance.framework.failureOnlyNote", "compliance.framework.removed"),
    ("drop R391 wiring", "reg", "check R391 ", "check R9999 "),
]


def main() -> int:
    cli_errors = check_live_generator_cli()
    if cli_errors:
        print("\n".join(cli_errors), file=sys.stderr)
        return 1

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
        print(f"PASS: R391 portal Trivy check catalogue & failure-only wording ({len(MUTATIONS)} mutations + 5 pin-drift cases rejected)")
        return 0

    print("PASS: R391 portal Trivy check catalogue & failure-only wording")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
