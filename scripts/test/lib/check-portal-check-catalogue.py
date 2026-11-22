#!/usr/bin/env python3
"""R391: Portal Trivy check catalogue integrity (canonical SHA-256 digest + pins), aliases, and failure-only UI wording.

Verifies offline that:
1. `node portal/scripts/gen-trivy-check-catalogue.mjs --check` succeeds against the pinned
   trivy-operator chart version (`trivy-operator.yaml`), image tag (`scripts/airgap/images.txt`),
   policy mode (`embedded`), `trivy-checks` module (`v1.7.1`), AND the committed canonical SHA-256
   digest manifest (`portal/src/data/trivy-check-catalogue.generated.sha256.json`).
2. Behavioural drift & tamper verification via `validateCatalogue`: mutating chart version,
   operator image tag, policyMode, checksVersion, empty checks, an edited entry (removing `KSV110`
   `scopeNamespaces`), a removed alias (`KCV0012` `KSV0012`), a changed `nodeType` (`KCV0066` ->
   `worker`), or a stale digest ALL fail loudly.
3. Python independently recomputes the canonical SHA-256 of `trivy-check-catalogue.generated.json`
   and compares it against `trivy-check-catalogue.generated.sha256.json`.
4. `compliance.ts` classifies non-failing controls via `evaluateCheckAgainstCatalogue` +
   `resolveCanonicalCheckId` and UI wording explicitly labels the positive state as
   "실패 기록 없음 (스캐너는 실패만 기록)" / "No failure recorded (scanner records failures only)".
5. `--mutation-verify` proves mutated copies (including edited entry, removed alias, changed
   nodeType, and stale digest) are rejected.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "gen": ROOT / "portal/scripts/gen-trivy-check-catalogue.mjs",
    "cat_json": ROOT / "portal/src/data/trivy-check-catalogue.generated.json",
    "cat_digest": ROOT / "portal/src/data/trivy-check-catalogue.generated.sha256.json",
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


def canonical_sha256(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def check_live_generator_cli() -> list[str]:
    res = subprocess.run(
        ["node", str(FILES["gen"]), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return [f"gen-trivy-check-catalogue.mjs --check failed (rc={res.returncode}): {res.stderr.strip()} {res.stdout.strip()}"]

    # Behavioural drift + tamper test via Node ESM import of validateCatalogue:
    probe = r"""
import { readFileSync } from "node:fs";
import { validateCatalogue, readRepoPins, OUT_JSON, OUT_DIGEST_JSON } from "./portal/scripts/gen-trivy-check-catalogue.mjs";

const pins = readRepoPins();
const base = JSON.parse(readFileSync(OUT_JSON, "utf8"));
const digest = JSON.parse(readFileSync(OUT_DIGEST_JSON, "utf8"));
if (validateCatalogue(base, pins, digest).length !== 0) {
  console.error("baseline catalogue failed validateCatalogue");
  process.exit(1);
}
const clone = (o) => JSON.parse(JSON.stringify(o));

const editedEntry = clone(base);
delete editedEntry.checks.KSV110.scopeNamespaces;

const removedAlias = clone(base);
delete removedAlias.checks.KCV0012.aliases;
delete removedAlias.checks.KCV0012.legacyId;

const changedNodeType = clone(base);
changedNodeType.checks.KCV0066.nodeTypes = ["worker"];

const staleDigest = clone(digest);
staleDigest.canonicalDigest = "0".repeat(64);

const driftCases = [
  ["chart bump", base, { ...pins, trivyOperatorChartVersion: "9.9.9" }, digest],
  ["operator image bump", base, { ...pins, trivyOperatorVersion: "9.9.9", trivyChecksVersion: null }, digest],
  ["policyMode flip", base, { ...pins, policyMode: "bundle" }, digest],
  ["catalogue checksVersion drift", { ...base, source: { ...base.source, trivyChecksVersion: "v9.9.9" } }, pins, digest],
  ["empty catalogue checks", { ...base, checks: {} }, pins, digest],
  ["edited entry (KSV110 scopeNamespaces removed)", editedEntry, pins, digest],
  ["removed alias (KCV0012 KSV0012)", removedAlias, pins, digest],
  ["changed nodeType (KCV0066 worker)", changedNodeType, pins, digest],
  ["stale digest manifest", base, pins, staleDigest],
];
for (const [label, cat, p, d] of driftCases) {
  if (validateCatalogue(cat, p, d).length === 0) {
    console.error("drift/tamper case was not rejected: " + label);
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
        return [f"catalogue drift/tamper behavioral test failed: {res2.stderr.strip()} {res2.stdout.strip()}"]
    return []


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []

    # 1. Catalogue JSON + canonical SHA-256 digest manifest invariants (offline tamper detection)
    try:
        cat = json.loads(texts["cat_json"])
    except Exception as exc:
        return [f"trivy-check-catalogue.generated.json invalid JSON: {exc}"]
    try:
        dig = json.loads(texts["cat_digest"])
    except Exception as exc:
        return [f"trivy-check-catalogue.generated.sha256.json invalid JSON: {exc}"]

    if dig.get("algorithm") != "sha256":
        errors.append(f"digest algorithm must be 'sha256', got {dig.get('algorithm')!r}")
    expected_digest = dig.get("canonicalDigest")
    actual_digest = canonical_sha256(cat)
    if expected_digest != actual_digest:
        errors.append(
            f"trivy-check-catalogue.generated.json canonical sha256 mismatch "
            f"(manifest={expected_digest!r} != computed={actual_digest!r}); regenerate from upstream source"
        )

    src = cat.get("source") or {}
    dig_src = dig.get("source") or {}
    if src != dig_src:
        errors.append("digest manifest source versions do not match catalogue source")
    if src.get("policyMode") != "embedded":
        errors.append(f"catalogue policyMode must be 'embedded', got {src.get('policyMode')!r}")
    if "checks/kubernetes/advanced/optional" not in (src.get("excludedPaths") or []):
        errors.append("catalogue must record checks/kubernetes/advanced/optional in excludedPaths")

    checks_map = cat.get("checks") or {}
    if dig.get("checkCount") != len(checks_map):
        errors.append(f"digest checkCount={dig.get('checkCount')} != catalogue checks count {len(checks_map)}")

    for required_id, expected_family in (
        ("KCV0001", "infra"),
        ("KCV0012", "infra"),
        ("KCV0035", "infra"),
        ("KCV0048", "infra"),
        ("KCV0066", "infra"),
        ("KCV0073", "infra"),
        ("KSV014", "configAudit"),
        ("KSV038", "configAudit"),
        ("KSV041", "rbac"),
        ("KSV103", "configAudit"),
        ("KSV110", "configAudit"),
        ("KSV111", "configAudit"),
        ("KSV124", "configAudit"),
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
    if checks_map.get("KCV0066", {}).get("nodeTypes") != ["master"]:
        errors.append("catalogue KCV0066 must apply to ['master']")
    if checks_map.get("KSV110", {}).get("scopeNamespaces") != ["default"]:
        errors.append("catalogue KSV110 must retain scopeNamespaces=['default']")
    for check_id, required_alias in (("KCV0012", "KSV0012"), ("KCV0035", "KCV0135"), ("KSV124", "KSV-0124")):
        aliases = checks_map.get(check_id, {}).get("aliases") or []
        if required_alias not in aliases:
            errors.append(f"catalogue {check_id} missing upstream alias {required_alias!r}")

    # 2. Compliance evaluator uses check catalogue + canonical alias resolver
    comp = texts["compliance"]
    if "function collectPassedCheckIds(" in comp:
        errors.append("compliance.ts must replace collectPassedCheckIds with the check-catalogue rule")
    for needle in (
        'from "./trivy-check-catalogue"',
        "getCatalogueCheck",
        "resolveCanonicalCheckId",
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
    if "trivy-check-catalogue.generated.json" not in texts["doc"] or "trivy-check-catalogue.generated.sha256.json" not in texts["doc"]:
        errors.append("docs/common/compliance-hardening.md must document trivy-check-catalogue.generated.json and its sha256 digest manifest")
    if "check R391 " not in texts["reg"] or "check-portal-check-catalogue.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R391 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("switch catalogue policyMode to bundle", "cat_json", '"policyMode": "embedded"', '"policyMode": "bundle"'),
    ("reintroduce optional KSV039 in catalogue", "cat_json", '"KSV038": {', '"KSV039": {"id":"KSV039","avdId":"AVD-KSV-0039","reportFamily":"configAudit","kinds":["LimitRange"],"severity":"Low","title":"opt"}, "KSV038": {'),
    ("edited entry: remove KSV110 scopeNamespaces", "cat_json", '"scopeNamespaces": [\n        "default"\n      ],\n      "severity": "Low",\n      "title": "Workloads in the default namespace"', '"severity": "Low",\n      "title": "Workloads in the default namespace"'),
    ("removed alias: drop KSV0012 from KCV0012", "cat_json", '"aliases": [\n        "KSV0012"\n      ]', '"aliases": []'),
    ("changed nodeType: KCV0066 master to worker", "cat_json", '"KCV0066": {\n      "id": "KCV0066",\n      "avdId": "AVD-KCV-0066",\n      "reportFamily": "infra",\n      "kinds": [\n        "Node"\n      ],\n      "nodeTypes": [\n        "master"\n      ]', '"KCV0066": {\n      "id": "KCV0066",\n      "avdId": "AVD-KCV-0066",\n      "reportFamily": "infra",\n      "kinds": [\n        "Node"\n      ],\n      "nodeTypes": [\n        "worker"\n      ]'),
    ("stale digest: alter canonicalDigest in sha256 manifest", "cat_digest", '"algorithm": "sha256",\n  "canonicalDigest": "', '"algorithm": "sha256",\n  "canonicalDigest": "deadbeef'),
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
        print(f"PASS: R391 portal Trivy check catalogue integrity & failure-only wording ({len(MUTATIONS)} mutations + 9 drift/tamper cases rejected)")
        return 0

    print("PASS: R391 portal Trivy check catalogue integrity & failure-only wording")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
