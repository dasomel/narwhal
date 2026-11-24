#!/usr/bin/env python3
"""R397: Portal Compliance > KISA live controls (no hard-coded static statuses, truthful signals, exact RBAC acceptance).

Verifies without helm or promtool:
1. portal/src/lib/kisa.ts has no STATIC_STATUS map or hard-coded fail/warn/pass statuses for
   KISA-SEC-01, KISA-ETCD-02, KISA-IMG-01, KISA-TLS-01, KISA-OBS-01, KISA-IMG-02.
2. Each control is wired to its live check (and KISA-IMG-01 explicitly reports manual with the
   host verification command, never pass or fail).
3. KISA-SEC-01 uses PartialObjectMetadata (no Secret .data read, no secrets rule in ClusterRole
   narwhal-portal) and degrades forbidden Secret metadata to not evaluated.
4. KISA-RBAC-02 evaluates apiGroups inside the same rule (no cross-rule '*' combination) and
   reuses matchRbacException from compliance-accepted.ts (no second acceptance list).
5. Cache key kisaComplianceList is bumped to compliance:v4:kisa:list, every KisaReasonKey has
   both ko and en entries in portal/src/lib/i18n.ts, and KisaChecklist renders detailLocalized
   and reasonKey.
6. scripts/test/regression-check-kakao.sh wires R397 with --mutation-verify.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "kisa": ROOT / "portal/src/lib/kisa.ts",
    "checks": ROOT / "portal/src/lib/kisa-checks.ts",
    "checks_ops": ROOT / "portal/src/lib/kisa-checks-ops.ts",
    "controls": ROOT / "portal/src/lib/kisa-controls.ts",
    "types": ROOT / "portal/src/types/kisa.ts",
    "k8s": ROOT / "portal/src/lib/k8s-client.ts",
    "ui": ROOT / "portal/src/components/compliance/kisa-checklist.tsx",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "keys": ROOT / "portal/src/lib/cache-keys.ts",
    "rbac": ROOT / "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml",
    "test": ROOT / "portal/src/lib/kisa-live-controls.test.ts",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

LIVE_SIX = ("KISA-SEC-01", "KISA-ETCD-02", "KISA-IMG-01", "KISA-TLS-01", "KISA-OBS-01", "KISA-IMG-02")


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    kisa = texts["kisa"]
    checks = texts["checks"]
    checks_ops = texts["checks_ops"]
    controls = texts["controls"]
    types = texts["types"]
    k8s = texts["k8s"]
    ui = texts["ui"]
    i18n = texts["i18n"]
    keys = texts["keys"]
    rbac = texts["rbac"]
    test = texts["test"]
    reg = texts["reg"]

    # 1. Forbid STATIC_STATUS or hard-coded status maps / overrides for the live controls
    if "STATIC_STATUS" in kisa:
        errors.append("kisa.ts must not contain STATIC_STATUS")
    for cid in (*LIVE_SIX, "KISA-RBAC-01", "KISA-RBAC-02"):
        if re.search(rf'"{cid}"\s*:\s*"(?:pass|fail|warn)"', kisa):
            errors.append(f"kisa.ts hardcodes a static status for {cid}")

    # Parse getKisaControls() to forbid any constant override (e.g. `applyRich(await checkEtcd02()); status = "pass"`)
    # or post-branch `status = ...` reassignment outside the check functions.
    if "export async function getKisaControls()" not in kisa:
        errors.append("kisa.ts missing export async function getKisaControls()")
    else:
        gkc = kisa.split("export async function getKisaControls()", 1)[1]
        if re.search(r'\bstatus\s*=\s*["\'`](?:pass|fail|warn)["\'`]', gkc):
            errors.append("kisa.ts getKisaControls() assigns a constant pass/fail/warn status override")
        branch_specs = [
            ("KISA-SEC-01", r"applyRich\(await checkSec01\(\)\)"),
            ("KISA-ETCD-02", r"applyRich\(await checkEtcd02\(\)\)"),
            ("KISA-TLS-01", r"applyRich\(await checkTls01\(\)\)"),
            ("KISA-OBS-01", r"applyRich\(await checkObs01\(\)\)"),
            ("KISA-IMG-02", r"applyRich\(await checkImg02\(\)\)"),
        ]
        for cid, expected_stmt in branch_specs:
            m = re.search(
                rf'else\s+if\s*\(\s*entry\.id\s*===\s*"{re.escape(cid)}"\s*\)\s*\{{(.*?)\}}',
                gkc,
                flags=re.DOTALL,
            )
            if not m:
                errors.append(f"kisa.ts missing exact branch for {cid}")
            else:
                body = re.sub(r"//[^\n]*", "", m.group(1)).strip()
                if not re.fullmatch(expected_stmt + r";?", body):
                    errors.append(f"kisa.ts {cid} branch must contain only {expected_stmt}, got {body!r}")
        m_rbac = re.search(
            r'else\s+if\s*\(\s*entry\.id\s*===\s*"KISA-RBAC-01"\s*\|\|\s*entry\.id\s*===\s*"KISA-RBAC-02"\s*\)\s*\{(.*?)\}',
            gkc,
            flags=re.DOTALL,
        )
        if not m_rbac:
            errors.append("kisa.ts missing exact branch for KISA-RBAC-01/02")
        else:
            body_rbac = re.sub(r"//[^\n]*", "", m_rbac.group(1)).strip()
            if not re.fullmatch(r"applyRich\(await checkRbacLive\(entry\.id\)\);?", body_rbac):
                errors.append(f"kisa.ts RBAC branch must contain only applyRich(await checkRbacLive(entry.id)), got {body_rbac!r}")
        if "} catch (err) {" in gkc and "controls.push({" in gkc:
            post_try = gkc.split("} catch (err) {", 1)[1].split("controls.push({", 1)[0]
            # Inside catch, only the initial `status = "manual"` is permitted; no conditional/post-catch status overrides.
            other_assigns = [ln.strip() for ln in post_try.splitlines() if re.search(r"\bstatus\s*=", ln) and 'status = "manual"' not in ln]
            if other_assigns:
                errors.append(f"kisa.ts has forbidden post-try status reassignment: {other_assigns}")

    # 2. Wiring in kisa.ts
    for needle in (
        "checkEtcd02()",
        "checkSec01()",
        "checkImg01()",
        "checkTls01()",
        "checkObs01()",
        "checkImg02()",
        "checkRbacLive(entry.id)",
        "if (r.partial) {",
        "if (!hasPartialCheck)",
    ):
        if needle not in kisa:
            errors.append(f"kisa.ts missing {needle!r}")

    # 3. Live signal invariants
    for needle in (
        "isApisixGatewayPeer",
        "--listen-client-urls",
        "--client-cert-auth",
        "velero.io/exclude-from-backup",
        "kubectl.kubernetes.io/last-applied-configuration",
        "compliance.kisa.reason.secSecretMetaNotEvaluated",
        "compliance.kisa.reason.secCronJobNotEvaluated",
        'makeOutcome(\n    "manual",\n    "compliance.kisa.reason.imgNodeManual"',
        "portLevelMtls",
    ):
        if needle not in checks:
            errors.append(f"kisa-checks.ts missing {needle!r}")

    if "EXTERNAL_KMS_ENV_KEYS" in checks or "externalKms" in checks:
        errors.append("kisa-checks.ts must not infer external KMS from env vars")
    if "export async function checkSec01()" not in checks or "export function checkImg01()" not in checks:
        errors.append("kisa-checks.ts missing checkSec01/checkImg01 boundaries")
    else:
        sec01_body = checks.split("export async function checkSec01()", 1)[1].split("export function checkImg01()", 1)[0]
        if re.search(r'["\'`]pass["\'`]', sec01_body):
            errors.append("kisa-checks.ts checkSec01() must never return pass")

    if "failed to verify image ([^\\s:]+" in checks_ops:
        errors.append("kisa-checks-ops.ts must not use the broken FAILED_IMAGE_RE port-truncating regex")

    if "res === `${parent}/*`" in checks_ops:
        errors.append("kisa-checks-ops.ts must not match resource/* as a subresource wildcard (Kubernetes ResourceMatches allows only *, exact, */sub)")

    if 'res !== `${parent}/*`' in checks_ops or 'res !== "*/*"' in checks_ops:
        errors.append("kisa-checks-ops.ts must allow userextras/* and */* for literal '*' extra key impersonation per upstream impersonation.go")

    if "export async function checkImg02()" not in checks_ops or "// --- KISA-RBAC-01" not in checks_ops:
        errors.append("kisa-checks-ops.ts missing checkImg02 boundaries")
    else:
        img02_body = checks_ops.split("export async function checkImg02()", 1)[1].split("// --- KISA-RBAC-01", 1)[0]
        if "if (failedInvSources.length > 0)" not in img02_body or "makeOutcome(\"pass\"" not in img02_body:
            errors.append("kisa-checks-ops.ts checkImg02 must guard pass behind failedInvSources.length === 0")
        elif img02_body.index("if (failedInvSources.length > 0)") > img02_body.index("makeOutcome(\"pass\""):
            errors.append("kisa-checks-ops.ts checkImg02 failedInvSources guard must precede pass return")

    for needle in (
        "routed.has(name)",
        "parseImageRefStrict",
        "return parseImageRefStrict(candidate) ? candidate : null",
        "buildSupersededReplicaSetKeys(inv.replicasets.data)",
        "ruleHasLatestTagValidation",
        "isValidVerifyImageEntry",
        "resolveRuleEffectiveAction",
        "if (hasAuditOverride(entry?.failureActionOverrides) || hasAuditOverride(policy.spec?.validationFailureActionOverrides))",
        "evaluateLatestPolicies",
        "evaluateVerifyPolicies",
        "excludedMissingOrZeroRs",
        "excludedOutOfScope",
        "failedInvSources",
        "if (failedInvSources.length > 0) {",
        "compliance.kisa.reason.imgInventoryNotEvaluated",
        "partial: true, partialSources: failedInvSources",
        "matchRbacException(name, cid, roleNs, kind) !== null",
        "export function ruleGrants(",
        "const hasCoreOrWildcardGroup = hasWildcardGroup || apiGroupsLower.includes(\"\")",
        "if (hasWildcardVerb && hasWildcardResource && hasCoreOrWildcardGroup)",
        "if (!verbs.includes(\"*\") && !verbs.includes(targetVerb)) return false",
        'const RBAC_ESCALATION_RESOURCES = ["roles", "clusterroles"] as const',
        'const RBAC_WRITE_RESOURCES = ["roles", "clusterroles", "rolebindings", "clusterrolebindings"] as const',
        'if (apiGroupsLower.length === 0 || resourcesLower.length === 0) continue',
        'grantsAnyVerb(r, "rbac.authorization.k8s.io", RBAC_ESCALATION_RESOURCES, ["bind", "escalate"])',
        'const impersonatesCore = grantsAnyVerb(r, "", CORE_IMPERSONATE_RESOURCES, ["impersonate"])',
        'const impersonatesAuth = grantsAnyVerb(r, "authentication.k8s.io", ["uids", "userextras/*"], ["impersonate"])',
    ):
        if needle not in checks_ops:
            errors.append(f"kisa-checks-ops.ts missing {needle!r}")

    # 4. PartialObjectMetadata on Secret metadata read; no secrets rule in ClusterRole narwhal-portal;
    # minimal verbs on peerauthentications (list), policyreports (list), cronjobs (get, list).
    if 'Accept: "application/json;as=PartialObjectMetadata;g=meta.k8s.io;v=v1"' not in k8s:
        errors.append("k8s-client.ts getKisaSecretMetadata must request PartialObjectMetadata")

    try:
        rbac_docs = [d for d in yaml.safe_load_all(rbac) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        errors.append(f"narwhal-portal-k8s.yaml YAML parse failed: {exc}")
        rbac_docs = []
    cr = next((d for d in rbac_docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal"), None)
    if cr is None:
        errors.append("narwhal-portal-k8s.yaml missing ClusterRole narwhal-portal")
    else:
        for rule in cr.get("rules") or []:
            resources = rule.get("resources") or []
            verbs = set(rule.get("verbs") or [])
            if "secrets" in resources:
                errors.append("ClusterRole narwhal-portal must not grant secrets access")
            if "peerauthentications" in resources and verbs != {"list"}:
                errors.append(f"ClusterRole narwhal-portal peerauthentications verbs must be ['list'], got {sorted(verbs)}")
            if "policyreports" in resources and verbs != {"list"}:
                errors.append(f"ClusterRole narwhal-portal policyreports verbs must be ['list'], got {sorted(verbs)}")
            if "cronjobs" in resources and verbs != {"get", "list"}:
                errors.append(f"ClusterRole narwhal-portal cronjobs verbs must be ['get', 'list'], got {sorted(verbs)}")

    # 5. Stale catalog wording removed
    for stale in (
        "skip_verify=true(02-containerd.sh)",
        "남은 cluster-admin 바인딩은 velero-server",
        "포털(1.0.4)",
        "어느 네임스페이스에도 없어",
    ):
        if stale in controls:
            errors.append(f"kisa-controls.ts still contains stale wording: {stale!r}")

    # 6. Cache key versioned + i18n bilingual parity for every KisaReasonKey + UI renders them
    if 'kisaComplianceList: () => "compliance:v4:kisa:list"' not in keys:
        errors.append("cache-keys.ts must version kisaComplianceList to compliance:v4:kisa:list")

    reason_keys = re.findall(r'"(compliance\.kisa\.reason\.[A-Za-z0-9_]+)"', types)
    if len(reason_keys) < 20:
        errors.append(f"types/kisa.ts expected >=20 KisaReasonKey entries, got {len(reason_keys)}")
    for rk in reason_keys:
        if len(re.findall(r'"' + re.escape(rk) + r'": "[^"\n]+"', i18n)) != 2:
            errors.append(f"i18n.ts must define both ko and en entries for {rk}")

    for needle in ("pick(control.detailLocalized ?? control.detail, locale)", "control.reasonKey"):
        if needle not in ui:
            errors.append(f"kisa-checklist.tsx missing {needle!r}")

    # 7. Behavioral tests & R397 wiring
    for suite in ("KISA-ETCD-02", "KISA-SEC-01", "KISA-IMG-01", "KISA-TLS-01", "KISA-OBS-01", "KISA-IMG-02", "KISA-RBAC-02"):
        if suite not in test:
            errors.append(f"kisa-live-controls.test.ts missing {suite} coverage")

    if "check R397 " not in reg or "check-portal-kisa-live-controls.py --mutation-verify" not in reg:
        errors.append("regression-check-kakao.sh must wire R397 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("reintroduce STATIC_STATUS map in kisa.ts", "kisa", "const KISA_NET01_NAMESPACES", 'const STATIC_STATUS = { "KISA-ETCD-02": "fail" }\nconst KISA_NET01_NAMESPACES'),
    ("hardcode KISA-SEC-01 fail in kisa.ts", "kisa", "applyRich(await checkSec01())", 'status = ({ "KISA-SEC-01": "fail" }["KISA-SEC-01"] as KisaStatus)'),
    ("drop checkEtcd02 call", "kisa", "applyRich(await checkEtcd02())", 'status = "warn"'),
    ("drop checkTls01 call", "kisa", "applyRich(await checkTls01())", 'status = "warn"'),
    ("drop checkObs01 call", "kisa", "applyRich(await checkObs01())", 'status = "warn"'),
    ("drop checkImg02 call", "kisa", "applyRich(await checkImg02())", 'status = "warn"'),
    ("mark KISA-IMG-01 pass instead of manual", "checks", 'makeOutcome(\n    "manual",\n    "compliance.kisa.reason.imgNodeManual"', 'makeOutcome(\n    "pass",\n    "compliance.kisa.reason.imgNodeManual"'),
    ("ignore portLevelMtls in KISA-TLS-01", "checks", "portLevelMtls", "ignoredField"),
    ("allow unrouted receiver in KISA-OBS-01", "checks_ops", "routed.has(name)", "true"),
    ("drop superseded ReplicaSet filter in KISA-IMG-02", "checks_ops", "buildSupersededReplicaSetKeys(inv.replicasets.data)", "new Set<string>()"),
    ("ignore apiGroups in KISA-RBAC-02", "checks_ops", "if (hasWildcardVerb && hasWildcardResource && hasCoreOrWildcardGroup)", "if (hasWildcardVerb && hasWildcardResource)"),
    ("drop exact matchRbacException in KISA-RBAC-02", "checks_ops", "matchRbacException(name, cid, roleNs, kind) !== null", "true"),
    ("drop PartialObjectMetadata header on Secret read", "k8s", 'Accept: "application/json;as=PartialObjectMetadata;g=meta.k8s.io;v=v1"', 'Accept: "application/json"'),
    ("grant secrets read in ClusterRole narwhal-portal", "rbac", "    resources: [cronjobs]", "    resources: [cronjobs, secrets]"),
    ("revert KISA cache key to v3", "keys", 'kisaComplianceList: () => "compliance:v4:kisa:list"', 'kisaComplianceList: () => "compliance:v3:kisa:list"'),
    ("drop English i18n entry for obsNoChannel", "i18n", '"compliance.kisa.reason.obsNoChannel": "No notification channel configured (null receiver only)"', '"compliance.kisa.reason.removed": "x"'),
    ("drop detailLocalized rendering in KisaChecklist", "ui", "pick(control.detailLocalized ?? control.detail, locale)", "control.detail"),
    ("override checkEtcd02 status with constant pass (R397 bypass)", "kisa", "applyRich(await checkEtcd02())", 'applyRich(await checkEtcd02()); status = "pass"'),
    ("reassign status for KISA-SEC-01 after try/catch", "kisa", "    controls.push({", '    if (entry.id === "KISA-SEC-01") status = "pass"\n    controls.push({'),
    ("drop parseImageRefStrict in extractFailedImageRef", "checks_ops", "return parseImageRefStrict(candidate) ? candidate : null", "return candidate"),
    ("ignore failed IMG-02 inventory lists and fall through to pass", "checks_ops", "  if (failedInvSources.length > 0) {\n    return makeOutcome(\n      \"warn\",\n      \"compliance.kisa.reason.imgInventoryNotEvaluated\",\n      baseKo,\n      baseEn,\n      { partial: true, partialSources: failedInvSources },\n    )\n  }", ""),
    ("return pass from checkSec01 when CronJob is missing", "checks", '      "manual",\n      "compliance.kisa.reason.secCronJobNotEvaluated"', '      "pass",\n      "compliance.kisa.reason.secCronJobNotEvaluated"'),
    ("ignore verbs:['*'] for bind/escalate/impersonate in RBAC-02", "checks_ops", 'if (!verbs.includes("*") && !verbs.includes(targetVerb)) return false', 'if (!verbs.includes(targetVerb)) return false'),
    ("ignore rbac.authorization.k8s.io apiGroup on bind/escalate", "checks_ops", 'grantsAnyVerb(r, "rbac.authorization.k8s.io", RBAC_ESCALATION_RESOURCES, ["bind", "escalate"])', 'grantsAnyVerb(r, "*", RBAC_ESCALATION_RESOURCES, ["bind", "escalate"])'),
    ("bypass impersonate verb check in core impersonate branch", "checks_ops", 'const impersonatesCore = grantsAnyVerb(r, "", CORE_IMPERSONATE_RESOURCES, ["impersonate"])', 'const impersonatesCore = grantsAnyVerb(r, "", [...CORE_IMPERSONATE_RESOURCES, "impersonate"], ["get", "impersonate"])'),
    ("drop uids or userextras/* authentication.k8s.io impersonate check", "checks_ops", 'const impersonatesAuth = grantsAnyVerb(r, "authentication.k8s.io", ["uids", "userextras/*"], ["impersonate"])', 'const impersonatesAuth = grantsAnyVerb(r, "authentication.k8s.io", ["userextras"], ["impersonate"])'),
    ("allow bind/escalate escalation on rolebindings/clusterrolebindings", "checks_ops", 'const RBAC_ESCALATION_RESOURCES = ["roles", "clusterroles"] as const', 'const RBAC_ESCALATION_RESOURCES = ["roles", "clusterroles", "rolebindings", "clusterrolebindings"] as const'),
    ("allow res/* subresource wildcard in ruleGrants", "checks_ops", "res === `*/${sub}`", "res === `${parent}/*` || res === `*/${sub}`"),
    ("exclude literal * extra key userextras/* from sub === * query", "checks_ops", "(res.startsWith(`${parent}/`) && res.length > parent.length + 1)", "(res.startsWith(`${parent}/`) && res.length > parent.length + 1 && res !== `${parent}/*`)"),
    ("ignore rule-level Audit override in resolveRuleEffectiveAction", "checks_ops", "if (baseAction !== \"Enforce\") return \"Audit\"\n  if (hasAuditOverride(entry?.failureActionOverrides) || hasAuditOverride(policy.spec?.validationFailureActionOverrides)) {", "if (baseAction !== \"Enforce\") return \"Audit\"\n  if (false) {"),
    ("let nonResourceURLs-only rules feed writeAccess/anyWildcard", "checks_ops", "if (apiGroupsLower.length === 0 || resourcesLower.length === 0) continue", "// no-op"),
    ("reintroduce unused get verb on peerauthentications", "rbac", "    resources: [peerauthentications]\n    verbs: [list]", "    resources: [peerauthentications]\n    verbs: [get, list]"),
    ("reintroduce unused get verb on policyreports", "rbac", "    resources: [policyreports]\n    verbs: [list]", "    resources: [policyreports]\n    verbs: [get, list]"),
    ("drop R397 wiring in regression-check-kakao.sh", "reg", "check R397 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv[1:]:
        for name, key, old, new in MUTATIONS:
            if old not in texts[key]:
                print(f"FAIL: mutation target missing for '{name}' ({key})", file=sys.stderr)
                return 1
            mutated = copy.copy(texts)
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation not rejected: {name}", file=sys.stderr)
                return 1
        print(f"PASS: R397 portal KISA live controls ({len(MUTATIONS)} mutations rejected)")
        return 0

    print("PASS: R397 portal KISA live controls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
