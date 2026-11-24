#!/usr/bin/env python3
"""R402: Portal CIS kube-bench test record validation, failure-first precedence, exact acceptance, cache versioning, and UI separation.

Forbids:
1. Treating missing, stale (> 26h or future), error status, wrong benchmark, duplicate controlId,
   totals-mismatched, unknown-node, truncated, 403-forbidden, or oversized records as passed.
2. Benchmark PASS overriding a Trivy failure (failure-first across sources; sourceDisagreement kept).
3. Pattern-based acceptance of benchmark findings (must use exact benchmarkId+controlId+optional node).
4. Conflating tested (benchmark) passes with failure-only 'no failure recorded' in UI.
5. Stale v3 framework/summary cache keys or caching when benchmark ConfigMap list failed.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "kube_bench": ROOT / "portal/src/lib/kube-bench.ts",
    "compliance": ROOT / "portal/src/lib/compliance.ts",
    "types": ROOT / "portal/src/types/compliance.ts",
    "rules": ROOT / "portal/src/data/compliance-acceptance-rules.json",
    "loader": ROOT / "portal/src/lib/compliance-acceptance.ts",
    "matcher": ROOT / "portal/src/lib/compliance-accepted.ts",
    "keys": ROOT / "portal/src/lib/cache-keys.ts",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "counters": ROOT / "portal/src/components/compliance/failure-counters.tsx",
    "grid": ROOT / "portal/src/components/compliance/frameworks-grid.tsx",
    "detail": ROOT / "portal/src/app/(dashboard)/compliance/frameworks/[id]/page.tsx",
    "rbac_yaml": ROOT / "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml",
    "kb_test": ROOT / "portal/src/lib/kube-bench.test.ts",
    "kisa": ROOT / "portal/src/lib/kisa.ts",
    "kisa_test": ROOT / "portal/src/lib/kisa.test.ts",
    "ui_test": ROOT / "portal/src/components/compliance/compliance-ui.test.tsx",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    kb = texts["kube_bench"]
    comp = texts["compliance"]

    # 1. Fail-closed record validation in kube-bench.ts
    for needle in (
        "export const BENCHMARK_MAX_AGE_HOURS = 26",
        "export const MAX_BENCHMARK_RECORD_BYTES =",
        "Buffer.byteLength(rawJson, \"utf8\") > MAX_BENCHMARK_RECORD_BYTES",
        "parsed.schemaVersion !== BENCHMARK_EXPECTED_SCHEMA_VERSION",
        "parsed.tool !== BENCHMARK_EXPECTED_TOOL",
        "parsed.benchmarkId !== BENCHMARK_EXPECTED_ID",
        'parsed.status === "error"',
        'parsed.status !== "complete"',
        "nodesAvailable && !clusterNodes.has(rawNode)",
        "finishedMs > nowMs",
        "nowMs - finishedMs > BENCHMARK_MAX_AGE_MS",
        "resultsByControlId.has(controlId)",
        "totals.pass !== counts.pass",
        "if (validByNode.has(node))",
        "validByNode.delete(node)",
        "`duplicate kube-bench record for node ${node}`",
        'if (top === "4") return "all-nodes"',
        'if (scope === "control-plane" && role !== "master") continue',
        "configMapRes.data.metadata?.continue",
        '"benchmark_list_forbidden"',
        '"benchmark_stale"',
        '"benchmark_error"',
        '"benchmark_malformed"',
        '"benchmark_missing"',
        '"benchmark_info"',
        "getCisControlNodeScope",
        "D-KUBE-BENCH-SECTION-SCOPE",
    ):
        if needle not in kb:
            errors.append(f"kube-bench.ts missing fail-closed check: {needle!r}")

    # Failure-first merge ordering: failure branch must precede passed branch in mergeCisControlWithBenchmark
    merge_m = re.search(
        r"export function mergeCisControlWithBenchmark\b[\s\S]*$",
        kb,
    )
    if not merge_m:
        errors.append("kube-bench.ts missing mergeCisControlWithBenchmark")
    else:
        merge_body = merge_m.group(0)
        fail_cond = 'if (trivyState === "failed" || bench.verdict === "failed")'
        pass_cond = 'if (bench.verdict === "passed")'
        if fail_cond not in merge_body or pass_cond not in merge_body:
            errors.append("mergeCisControlWithBenchmark missing failure-first or passed branch")
        elif merge_body.index(fail_cond) > merge_body.index(pass_cond):
            errors.append("mergeCisControlWithBenchmark must evaluate failure-first before passed")
        if "sourceDisagreement" not in merge_body:
            errors.append("mergeCisControlWithBenchmark must record sourceDisagreement when tools disagree")

    # 2. Compliance integration & partial non-caching & one-source-of-truth summary
    for needle in (
        "BENCHMARK_CONFIGMAP_LIST_PATH",
        "buildBenchmarkStore(",
        'item.spec.compliance.id === "k8s-cis-1.23"',
        "mergeCisControlWithBenchmark(ctrl, evalCtx.benchmarkStore)",
        "export function summarizeComplianceControls(",
        "summarizeComplianceControls(controls,",
        "partial: evalCtx.hasHttpError || evalCtx.benchmarkStore.listFailed",
        "if (!evalCtx.hasHttpError && !evalCtx.benchmarkStore.listFailed)",
    ):
        if needle not in comp:
            errors.append(f"compliance.ts missing benchmark integration: {needle!r}")
    if "benchmarkTestedFailCount = 0" in comp:
        errors.append("compliance.ts must not hardcode benchmarkTestedFailCount = 0 in summary-only branch")

    # 3. Exact benchmark acceptance schema & matcher (empty in this lane, no pattern matching)
    try:
        rules_data = json.loads(texts["rules"])
    except Exception as exc:
        errors.append(f"compliance-acceptance-rules.json invalid JSON: {exc}")
        rules_data = {}
    if rules_data.get("benchmark") != []:
        errors.append("compliance-acceptance-rules.json benchmark[] must exist and stay empty in this lane")

    for needle in (
        "BENCHMARK_CONTROL_ID_RE",
        "benchmarkAllowedFields",
        "seenBenchmarkTargets",
        "benchmarkRuleIndex",
    ):
        if needle not in texts["loader"]:
            errors.append(f"compliance-acceptance.ts missing exact benchmark loader validation: {needle!r}")

    matcher = texts["matcher"]
    if "export function matchBenchmarkException(" not in matcher:
        errors.append("compliance-accepted.ts missing matchBenchmarkException")
    else:
        mb_body = matcher.split("export function matchBenchmarkException", 1)[1].split("\nexport ", 1)[0]
        if "benchmarkRuleIndex.get(`${benchmarkId}\\0${controlId}`)" not in mb_body:
            errors.append("matchBenchmarkException must use exact benchmarkRuleIndex lookup")
        if re.search(r"\.(?:includes|startsWith|endsWith|test|exec|match)\(", mb_body):
            errors.append("matchBenchmarkException must not use pattern/substring/regex matching")

    # 4. Cache keys v4 & RBAC unchanged
    for needle in (
        'complianceFrameworksList: () => "compliance:v4:frameworks:list"',
        'join("compliance", "v4", "frameworks", id)',
        'complianceSummary: () => "compliance:v4:summary"',
    ):
        if needle not in texts["keys"]:
            errors.append(f"cache-keys.ts missing v4 key: {needle!r}")

    if "resources: [nodes, pods, persistentvolumeclaims, namespaces, services, events, configmaps]" not in texts["rbac_yaml"]:
        errors.append("narwhal-portal-k8s.yaml must retain existing read-only configmaps/nodes permission")

    # 5. UI & KISA CFG-01 separation & i18n
    for ui_key in ("counters", "grid", "detail"):
        if "benchmarkTestedPassCount" not in texts[ui_key]:
            errors.append(f"{ui_key} must display benchmarkTestedPassCount separately from noFailureRecordedPassCount")
    if "(acc, f) => acc + (f.benchmarkTestedPassCount ?? 0)" not in texts["counters"]:
        errors.append("failure-counters.tsx must compute benchmarkTestedPassTotal from f.benchmarkTestedPassCount ?? 0")
    if "compliance.counter.frameworksBreakdownWithBenchmark" not in texts["counters"]:
        errors.append("failure-counters.tsx must use compliance.counter.frameworksBreakdownWithBenchmark when benchmark passes exist")
    if "passed: String(evaluatedPassTotal)" in texts["counters"]:
        errors.append("failure-counters.tsx must not pass evaluatedPassTotal into frameworksBreakdown")
    for needle in (
        "benchmarkTestedPassControls",
        "noFailureRecordedControls",
        "(acc, f) => acc + (f.benchmarkTestedPassCount ?? 0)",
    ):
        if needle not in texts["kisa"]:
            errors.append(f"kisa.ts missing separate benchmark vs no-failure count: {needle!r}")
    for needle in (
        "compliance.framework.benchmarkSourceLine",
        "compliance.framework.sourceDisagreement",
        "compliance.framework.benchmarkActual",
        "compliance.framework.benchmarkExpected",
        "compliance.counter.frameworksBreakdownWithBenchmark",
        "compliance.counter.frameworksPassRateWithBenchmark",
        "compliance.framework.evaluatedPassRateWithBenchmark",
    ):
        if needle not in texts["i18n"]:
            errors.append(f"i18n missing {needle!r}")
    for needle in (
        "compliance.framework.benchmarkSourceLine",
        "compliance.framework.sourceDisagreement",
        "compliance.framework.benchmarkActual",
        "compliance.framework.benchmarkExpected",
    ):
        if needle not in texts["detail"]:
            errors.append(f"detail missing {needle!r}")

    # 6. Behavioral tests & R402 wiring
    for needle in (
        "fails closed (never passed) on $name",
        "handles two nodes where one lacks a record: control-plane passes when worker lacks record, node-scoped 4.x stays not_evaluated; missing control-plane node fails both",
        "rejects duplicate kube-bench ConfigMaps for the same node as benchmark_malformed (never passed)",
        "derives summary counters from merged controls even when CCR detailReport.results is absent (1.1.12 FAIL)",
        "maintains counter partition invariant across real fixture and randomized Trivy/benchmark combinations",
        "handles ConfigMap list 403 by failing closed without caching while keeping Trivy failures failed",
        "keeps control failed with sourceDisagreement when Trivy passed (no failure recorded) but kube-bench failed",
    ):
        if needle not in texts["kb_test"]:
            errors.append(f"kube-bench.test.ts missing behavioral test: {needle!r}")

    if "keeps benchmarkTestedPassCount separate from noFailureRecordedPassCount in CFG-01 ko and en detail text" not in texts["kisa_test"]:
        errors.append("kisa.test.ts missing CFG-01 split test")
    if "70 tested (benchmark) · 4 no failure recorded" not in texts["ui_test"]:
        errors.append("compliance-ui.test.tsx missing benchmark UI separation assertion")

    if "check R402 " not in texts["reg"] or "check-portal-cis-test-records.py --mutation-verify" not in texts["reg"]:
        errors.append("regression-check-kakao.sh must wire R402 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("allow stale records (>26h) as valid", "kube_bench", "if (nowMs - finishedMs > BENCHMARK_MAX_AGE_MS) {", "if (false) {"),
    ("allow future finishedAt as valid", "kube_bench", "if (finishedMs > nowMs) {", "if (false) {"),
    ("treat error status as valid", "kube_bench", 'if (parsed.status === "error") {', "if (false) {"),
    ("ignore wrong benchmarkId", "kube_bench", "if (parsed.benchmarkId !== BENCHMARK_EXPECTED_ID) {", "if (false) {"),
    ("ignore duplicate controlId", "kube_bench", "if (resultsByControlId.has(controlId)) {", "if (false) {"),
    ("ignore totals mismatch", "kube_bench", "totals.pass !== counts.pass ||", "false ||"),
    ("ignore unknown cluster node", "kube_bench", "if (nodesAvailable && !clusterNodes.has(rawNode)) {", "if (false) {"),
    ("drop JSON byte size cap", "kube_bench", 'if (Buffer.byteLength(rawJson, "utf8") > MAX_BENCHMARK_RECORD_BYTES) {', "if (false) {"),
    ("benchmark PASS overrides Trivy failure", "kube_bench", 'if (trivyState === "failed" || bench.verdict === "failed")', 'if (bench.verdict === "failed")'),
    ("pattern-based benchmark acceptance", "matcher", "const exactNodeRule = node !== undefined ? rules.find((r) => r.node === node) : undefined", "const exactNodeRule = node !== undefined ? rules.find((r) => node.includes(r.node ?? \"\")) : undefined"),
    ("add benchmark acceptance rule in this lane", "rules", '"benchmark": []', '"benchmark": [{"id":"x"}]'),
    ("cache response on kube-bench ConfigMap list 403", "compliance", "if (!evalCtx.hasHttpError && !evalCtx.benchmarkStore.listFailed)", "if (!evalCtx.hasHttpError)"),
    ("remove duplicate-node ConfigMap validation", "kube_bench", "if (validByNode.has(node)) {", "if (false) {"),
    ("exclude workers from section 4.x coverage", "kube_bench", 'if (top === "4") return "all-nodes"', 'if (top === "4") return "control-plane"'),
    ("exclude non-master nodes unconditionally in node loop", "kube_bench", 'if (scope === "control-plane" && role !== "master") continue', 'if (role !== "master") continue'),
    ("conflate tested and no-failure-recorded in FailureCounters", "counters", "f.benchmarkTestedPassCount ?? 0", "0"),
    ("conflate tested and no-failure-recorded in FailureCounters breakdown", "counters", "compliance.counter.frameworksBreakdownWithBenchmark", "compliance.counter.frameworksBreakdown"),
    ("conflate tested and no-failure-recorded in KISA CFG-01", "kisa", "f.benchmarkTestedPassCount ?? 0", "0"),
    ("revert frameworks cache key to v3", "keys", 'complianceFrameworksList: () => "compliance:v4:frameworks:list"', 'complianceFrameworksList: () => "compliance:v3:frameworks:list"'),
    ("drop R402 wiring in regression-check-kakao.sh", "reg", "check R402 ", "check R9999 "),
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
        print(f"PASS: R402 CIS kube-bench test records ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R402 CIS kube-bench test records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
