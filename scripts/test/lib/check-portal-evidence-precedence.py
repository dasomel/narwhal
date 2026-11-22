#!/usr/bin/env python3
"""R389: Failure-first evidence, report families, auxiliary scope and old-cache safety.

Static seams complement G1-G5 Vitest experiments; mutated copies must be rejected.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
FILES = {
    "comp": "portal/src/lib/compliance.ts",
    "kisa": "portal/src/lib/kisa.ts",
    "keys": "portal/src/lib/cache-keys.ts",
    "grid": "portal/src/components/compliance/frameworks-grid.tsx",
    "ssr": "portal/src/components/compliance/failure-counters.tsx",
    "detail": "portal/src/app/(dashboard)/compliance/frameworks/[id]/page.tsx",
}


def check(texts):
    errors = []
    required = {
        "comp": [
            "for (const failure of controlEvidenceFailures(evidenceSpec, evalCtx))",
            "...ctx.evidenceReports.config,",
            "...ctx.evidenceReports.rbac,",
            "...ctx.evidenceReports.infra,",
            'resolveCanonicalCheckId(c.checkID ?? "") !== canonical',
            "evaluateCheckAgainstCatalogue(id, specControl, evalCtx)",
            'entry.reportFamily === "infra"',
        ],
        "kisa": [
            "if (actionableControls > 0 || actionableFailures > 0 || auditFailures > 0)",
            "getRbacAuditList(), getInfraAuditList()",
            "f.evaluatedPassCount === undefined || f.notEvaluatedCount === undefined",
        ],
        "keys": [
            'complianceFrameworksList: () => "compliance:v2:frameworks:list"',
            'join("compliance", "v2", "frameworks", id)',
            'complianceSummary: () => "compliance:v2:summary"',
            'kisaComplianceList: () => "compliance:v2:kisa:list"',
        ],
    }
    for key, needles in required.items():
        for needle in needles:
            if needle not in texts[key]:
                errors.append(f"{key}: missing {needle}")
    classifier = texts["comp"].split("function classifyNonFailingControl", 1)[1].split("function buildComplianceFrameworkData", 1)[0]
    if "if (evalCtx.hasHttpError)" not in classifier.split("const checks =", 1)[0]:
        errors.append("all scope read failures must precede non-failed control classification")
    for key, var in (("grid", "fw"), ("ssr", "f"), ("detail", "detail")):
        source = texts[key]
        for needle in (f"{var}.evaluatedPassCount ?? 0", f"{var}.notEvaluatedCount ?? Math.max(0, {var}.totalControls - {var}.failCount)"):
            if needle not in source:
                errors.append(f"{key}: missing conservative old-shape default: {needle}")
        if f"{var}.evaluatedPassCount ?? {var}.passCount" in source or f"{var}.notEvaluatedCount ?? 0" in source:
            errors.append(f"{key}: optimistic old-shape fallback")
    return errors


MUTATIONS = [
    ("ignore underlying failures", "comp", "controlEvidenceFailures(evidenceSpec, evalCtx)", "[]"),
    ("drop rbac family from failure join", "comp", "...ctx.evidenceReports.rbac,", ""),
    ("drop infra family from failure join", "comp", "...ctx.evidenceReports.infra,", ""),
    ("bypass canonical alias match in failure join", "comp", 'resolveCanonicalCheckId(c.checkID ?? "") !== canonical', 'c.checkID !== canonical'),
    ("miss auxiliary errors", "comp", "if (evalCtx.hasHttpError)", "if (evalCtx.operatorConfigError)"),
    ("ignore audit-only failures", "kisa", " || actionableFailures > 0 || auditFailures > 0", ""),
    ("old framework cache", "keys", "compliance:v2:frameworks:list", "compliance:frameworks:list"),
    ("old detail cache", "keys", 'join("compliance", "v2", "frameworks", id)', 'join("compliance", "frameworks", id)'),
    ("old summary cache", "keys", "compliance:v2:summary", "compliance:summary"),
    ("old KISA cache", "keys", "compliance:v2:kisa:list", "compliance:kisa:list"),
    ("optimistic grid", "grid", "fw.evaluatedPassCount ?? 0", "fw.evaluatedPassCount ?? fw.passCount"),
    ("optimistic summary", "ssr", "f.evaluatedPassCount ?? 0", "f.evaluatedPassCount ?? f.passCount"),
    ("optimistic detail", "detail", "detail.evaluatedPassCount ?? 0", "detail.evaluatedPassCount ?? detail.passCount"),
]


def main():
    texts = {key: (ROOT / path).read_text() for key, path in FILES.items()}
    errors = check(texts)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if "--mutation-verify" in sys.argv:
        for name, key, old, new in MUTATIONS:
            if old not in texts[key]:
                raise AssertionError(f"mutation target missing: {name}")
            mutated = dict(texts)
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                raise AssertionError(f"mutation survived: {name}")
        print(f"PASS: R389 evidence precedence and old-cache safety ({len(MUTATIONS)} mutations rejected)")
    else:
        print("PASS: R389 evidence precedence and old-cache safety")
    return 0


if __name__ == "__main__":
    sys.exit(main())
