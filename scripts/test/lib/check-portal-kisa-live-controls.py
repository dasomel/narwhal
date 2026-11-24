#!/usr/bin/env python3
"""R397: Portal Compliance > KISA live controls (closed-world baseline, truthful signals, exact RBAC acceptance).

Verifies without helm or promtool:
1. portal/src/lib/kisa.ts has no STATIC_STATUS map or hard-coded fail/warn/pass statuses for
   KISA-SEC-01, KISA-ETCD-02, KISA-IMG-01, KISA-TLS-01, KISA-OBS-01, KISA-IMG-02, RBAC-01/02.
2. Each control is wired to its live check (and KISA-IMG-01 explicitly reports manual with the
   host verification command, never pass or fail).
3. D-KISA-CLOSED-WORLD invariants:
   - KISA-IMG-02 compares ClusterPolicy canonical spec SHA-256 against kisa-policy-baseline.json
     (after stripping only documented Kyverno admission defaults), verifies Ready=True, checks
     enforceEligible, forbids arbitrary Kyverno rule/CEL/deny/match semantic interpretation helpers,
     and refuses caching on partial inventory.
   - KISA-ETCD-02 inspects ONLY the exact "etcd" container in apisix-etcd (no containers.flatMap).
   - KISA-CP-01 and KISA-ETCD-01 inspect ONLY the exact "kube-apiserver" container (no flatMap).
   - KISA-OBS-01 matches Falco/falcosidekick strictly by label, never by pod name prefixes/substrings.
   - KISA-RBAC-01/02 remove isUnavoidableRbacBuiltin and system:/kubeadm: prefix exclusions, evaluate
     single-rule apiGroups/resources/verbs + exact role & bootstrap ClusterRoleBinding acceptance.
   - Sweep invariants across NET-01, POD-01, ADM-01, TLS-01, TLS-02, IMG-03, CFG-01.
4. PartialObjectMetadata on Secret metadata read; no secrets rule in ClusterRole narwhal-portal.
5. Cache key compliance:v4:kisa:list, bilingual i18n keys, and R397 wiring in regression-check-kakao.sh.
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
    "unit_test": ROOT / "portal/src/lib/kisa.test.ts",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}

LIVE_SIX = ("KISA-SEC-01", "KISA-ETCD-02", "KISA-IMG-01", "KISA-TLS-01", "KISA-OBS-01", "KISA-IMG-02")

FORBIDDEN_KYVERNO_SEMANTIC_HELPERS = (
    "ruleMatchesPod",
    "ruleHasLatestTagValidation",
    "denyConditionsRejectLatest",
    "celRejectsLatest",
    "imagePatternRejectsLatest",
    "patternNodeChecksContainersImage",
    "resolveRuleEffectiveAction",
    "hasAuditOverride",
    "isValidVerifyImageEntry",
)

FORBIDDEN_RBAC_PREFIX_HELPERS = (
    "isUnavoidableRbacBuiltin",
    'startsWith("system:")',
    "startsWith('system:')",
    'startsWith("kubeadm:")',
    "startsWith('kubeadm:')",
)


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
    unit_test = texts["unit_test"]
    reg = texts["reg"]

    # 1. Forbid STATIC_STATUS or hard-coded status maps / overrides for the live controls
    if "STATIC_STATUS" in kisa:
        errors.append("kisa.ts must not contain STATIC_STATUS")
    for cid in (*LIVE_SIX, "KISA-RBAC-01", "KISA-RBAC-02"):
        if re.search(rf'"{cid}"\s*:\s*"(?:pass|fail|warn)"', kisa):
            errors.append(f"kisa.ts hardcodes a static status for {cid}")

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
            other_assigns = [ln.strip() for ln in post_try.splitlines() if re.search(r"\bstatus\s*=", ln) and 'status = "manual"' not in ln]
            if other_assigns:
                errors.append(f"kisa.ts has forbidden post-try status reassignment: {other_assigns}")

    # 2. Wiring & sweep invariants in kisa.ts
    if checks_ops.count("matchRbacBindingException({") < 2:
        errors.append("kisa-checks-ops.ts must enforce matchRbacBindingException in both KISA-RBAC-01 and KISA-RBAC-02")

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
        "function isIngressDefaultDenyPolicy(",
        "if (!hasPodSel && !hasNsSel && !hasRestrictedIpBlock && !sameNsAllPods) {",
        "PSA_ENFORCED_LEVELS",
        "if (summary.failed) {",
        "if (summary.partial) {",
        "if (totalControls === 0) {",
        "if (missingExpiry.length > 0) {",
        'p.effectiveEnforcement === "Disabled"',
        "p.hasValidateOrVerifyRules !== false",
        "p.hasApiserverContainer === false",
        "hasCliFlagWithValue(",
    ):
        if needle not in kisa:
            errors.append(f"kisa.ts missing {needle!r}")

    # 3. Container isolation + closed-world checks in kisa-checks.ts & k8s-client.ts
    if "function checkEtcd02(" not in checks:
        errors.append("kisa-checks.ts missing checkEtcd02")
    else:
        etcd_body = checks.split("export async function checkEtcd02()", 1)[1].split("export async function checkSec01()", 1)[0]
        if 'containers.find((c) => c.name === "etcd")' not in etcd_body:
            errors.append("kisa-checks.ts checkEtcd02 must select strictly container name === 'etcd'")
        if "containers.flatMap" in etcd_body:
            errors.append("kisa-checks.ts checkEtcd02 must never merge args with containers.flatMap across containers")
        if "compliance.kisa.reason.etcdContainerMissing" not in etcd_body:
            errors.append("kisa-checks.ts checkEtcd02 must return etcdContainerMissing when etcd container absent")

    if "function mapApiServerPod(" not in k8s:
        errors.append("k8s-client.ts missing mapApiServerPod")
    else:
        apiserver_body = k8s.split("function mapApiServerPod(", 1)[1].split("export async function getApiServerPods()", 1)[0]
        if '(p.spec.containers ?? []).find((c) => c.name === "kube-apiserver")' not in apiserver_body:
            errors.append("k8s-client.ts mapApiServerPod must isolate strictly container name === 'kube-apiserver'")
        if "flatMap" in apiserver_body:
            errors.append("k8s-client.ts mapApiServerPod must not merge container args via flatMap")

    if checks_ops.count("matchRbacBindingException({") < 2:
        errors.append("kisa-checks-ops.ts must enforce matchRbacBindingException in both KISA-RBAC-01 and KISA-RBAC-02")

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
        "sel.matchExpressions",
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

    # 4. kisa-checks-ops.ts: OBS-01 label-only, IMG-02 baseline-digest closed world, RBAC-01/02 exact acceptance
    if "failed to verify image ([^\\s:]+" in checks_ops:
        errors.append("kisa-checks-ops.ts must not use the broken FAILED_IMAGE_RE port-truncating regex")
    if 'startsWith("falco-")' in checks_ops or 'includes("falcosidekick")' in checks_ops:
        errors.append("kisa-checks-ops.ts checkObs01 must match strictly by labels, not pod name prefixes/substrings")

    for forbidden in FORBIDDEN_KYVERNO_SEMANTIC_HELPERS:
        if forbidden in checks_ops:
            errors.append(f"kisa-checks-ops.ts must not reintroduce Kyverno semantic interpreter {forbidden!r} (D-KISA-CLOSED-WORLD)")
    for forbidden in FORBIDDEN_RBAC_PREFIX_HELPERS:
        for mod_name, text in (("kisa-checks-ops.ts", checks_ops), ("kisa-checks.ts", checks), ("kisa.ts", kisa)):
            if forbidden in text:
                errors.append(f"{mod_name} must not exclude RBAC bindings by prefix {forbidden!r} (D-KISA-CLOSED-WORLD)")

    if "res === `${parent}/*`" in checks_ops:
        errors.append("kisa-checks-ops.ts must not match resource/* as a subresource wildcard (Kubernetes ResourceMatches allows only *, exact, */sub)")
    if 'res !== `${parent}/*`' in checks_ops or 'res !== "*/*"' in checks_ops:
        errors.append("kisa-checks-ops.ts must allow userextras/* and */* for literal '*' extra key impersonation per upstream impersonation.go")

    if "export async function checkImg02(" not in checks_ops or "// --- KISA-RBAC-01" not in checks_ops:
        errors.append("kisa-checks-ops.ts missing checkImg02 boundaries")
    else:
        img02_body = checks_ops.split("export async function checkImg02(", 1)[1].split("// --- KISA-RBAC-01", 1)[0]
        for guard in (
            "if (failedInvSources.length > 0)",
            'latestEval.state === "not-ready" || verifyEval.state === "not-ready"',
            'latestEval.state === "differs" ||',
            'latestEval.state !== "enforced" || verifyEval.state !== "enforced"',
        ):
            if guard not in img02_body or 'makeOutcome("pass"' not in img02_body:
                errors.append(f"kisa-checks-ops.ts checkImg02 missing closed-world guard {guard!r}")
            elif img02_body.index(guard) > img02_body.index('makeOutcome("pass"'):
                errors.append(f"kisa-checks-ops.ts checkImg02 guard {guard!r} must precede pass return")

    if checks_ops.count("matchRbacBindingException({") < 2:
        errors.append("kisa-checks-ops.ts must enforce matchRbacBindingException in both KISA-RBAC-01 and KISA-RBAC-02")

    for needle in (
        "D-KISA-CLOSED-WORLD",
        "KISA_POLICY_BASELINE",
        "computeKyvernoSpecDigest",
        "specHasOverrides",
        "function isPolicyReady(",
        "if (!isPolicyReady(pol)) {",
        "function evaluatePolicyAgainstBaseline(",
        "if (liveDigest !== baselineEntry.specSha256 || extraOverrides)",
        "if (!baselineEntry.enforceEligible)",
        "routed.has(name)",
        "parseImageRefStrict",
        "return parseImageRefStrict(candidate) ? candidate : null",
        "buildSupersededReplicaSetKeys(inv.replicasets.data)",
        "excludedMissingOrZeroRs",
        "excludedOutOfScope",
        "failedInvSources",
        "compliance.kisa.reason.imgInventoryNotEvaluated",
        "compliance.kisa.reason.imgPolicyBaselineDiff",
        "compliance.kisa.reason.imgPolicyNotReady",
        "partial: true, partialSources: failedInvSources",
        "matchRbacBindingException({",
        "matchRbacException(name, cid, roleNs, kind) !== null",
        "export function ruleGrants(",
        "if (!groups.includes(\"*\") && !groups.includes(targetGroup)) return false",
        "if (!verbs.includes(\"*\") && !verbs.includes(targetVerb)) return false",
        'const RBAC_ESCALATION_RESOURCES = ["roles", "clusterroles"] as const',
        'const RBAC_WRITE_RESOURCES = ["roles", "clusterroles", "rolebindings", "clusterrolebindings"] as const',
        'grantsAnyVerb(r, "rbac.authorization.k8s.io", RBAC_ESCALATION_RESOURCES, ["bind", "escalate"])',
        'const impersonatesCore = grantsAnyVerb(r, "", CORE_IMPERSONATE_RESOURCES, ["impersonate"])',
        'const impersonatesAuth = grantsAnyVerb(r, "authentication.k8s.io", ["uids", "userextras/*"], ["impersonate"])',
    ):
        if needle not in checks_ops:
            errors.append(f"kisa-checks-ops.ts missing {needle!r}")

    # 5. PartialObjectMetadata on Secret metadata read; no secrets rule in ClusterRole narwhal-portal
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

    # 6. Stale catalog wording removed
    for stale in (
        "skip_verify=true(02-containerd.sh)",
        "남은 cluster-admin 바인딩은 velero-server",
        "포털(1.0.4)",
        "어느 네임스페이스에도 없어",
    ):
        if stale in controls:
            errors.append(f"kisa-controls.ts still contains stale wording: {stale!r}")

    # 7. Cache key versioned + i18n bilingual parity for every KisaReasonKey + UI renders them
    if 'kisaComplianceList: () => "compliance:v4:kisa:list"' not in keys:
        errors.append("cache-keys.ts must version kisaComplianceList to compliance:v4:kisa:list")

    reason_keys = re.findall(r'"(compliance\.kisa\.reason\.[A-Za-z0-9_]+)"', types)
    if len(reason_keys) < 23:
        errors.append(f"types/kisa.ts expected >=23 KisaReasonKey entries, got {len(reason_keys)}")
    for rk in reason_keys:
        if len(re.findall(r'"' + re.escape(rk) + r'": "[^"\n]+"', i18n)) != 2:
            errors.append(f"i18n.ts must define both ko and en entries for {rk}")

    if checks_ops.count("matchRbacBindingException({") < 2:
        errors.append("kisa-checks-ops.ts must enforce matchRbacBindingException in both KISA-RBAC-01 and KISA-RBAC-02")

    for needle in ("pick(control.detailLocalized ?? control.detail, locale)", "control.reasonKey"):
        if needle not in ui:
            errors.append(f"kisa-checklist.tsx missing {needle!r}")

    # 8. Behavioral tests & R397 wiring
    for suite in ("KISA-ETCD-02", "KISA-SEC-01", "KISA-IMG-01", "KISA-TLS-01", "KISA-OBS-01", "KISA-IMG-02", "KISA-RBAC-01", "KISA-RBAC-02"):
        if suite not in test:
            errors.append(f"kisa-live-controls.test.ts missing {suite} coverage")
    for r5_needle in (
        "reproduction (R5):",
        "system:custom-dangerous",
        "sidecar",
    ):
        if r5_needle not in test:
            errors.append(f"kisa-live-controls.test.ts missing R5 closed-world test marker {r5_needle!r}")
    for sweep_needle in ("IMG-03 never passes", "POD-01 does not pass", "NET-01 rejects", "ADM-01 warns", "TLS-02 warns", "CP-01/ETCD-01 reject"):
        if sweep_needle not in unit_test:
            errors.append(f"kisa.test.ts missing sweep regression marker {sweep_needle!r}")

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
    ("merge containers with flatMap in checkEtcd02", "checks", 'const etcdContainer = containers.find((c) => c.name === "etcd")', 'const etcdContainer = { command: containers.flatMap((c) => c.command ?? []), args: containers.flatMap((c) => c.args ?? []) }'),
    ("merge containers with flatMap in mapApiServerPod", "k8s", 'const apiserverContainer = (p.spec.containers ?? []).find((c) => c.name === "kube-apiserver")', 'const apiserverContainer = { command: (p.spec.containers ?? []).flatMap((c) => c.command ?? []), args: [] }'),
    ("reintroduce pod name prefix falco match in checkObs01", "checks_ops", 'const appName = p.metadata?.labels?.["app.kubernetes.io/name"] ?? p.metadata?.labels?.["app"] ?? ""', 'const appName = p.metadata?.name?.startsWith("falco-") ? "falco" : ""'),
    ("allow unrouted receiver in KISA-OBS-01", "checks_ops", "routed.has(name)", "true"),
    ("reintroduce ruleHasLatestTagValidation semantic helper in kisa-checks-ops.ts", "checks_ops", "function evaluatePolicyAgainstBaseline(", "function ruleHasLatestTagValidation() { return true }\nfunction evaluatePolicyAgainstBaseline("),
    ("bypass Ready=True check in evaluatePolicyAgainstBaseline", "checks_ops", "if (!isPolicyReady(pol)) {", "if (false && !isPolicyReady(pol)) {"),
    ("bypass canonical spec SHA-256 comparison in evaluatePolicyAgainstBaseline", "checks_ops", "if (liveDigest !== baselineEntry.specSha256 || extraOverrides) {", "if (false) {"),
    ("bypass enforceEligible guard in evaluatePolicyAgainstBaseline", "checks_ops", "if (!baselineEntry.enforceEligible) {", "if (false && !baselineEntry.enforceEligible) {"),
    ("drop superseded ReplicaSet filter in KISA-IMG-02", "checks_ops", "buildSupersededReplicaSetKeys(inv.replicasets.data)", "new Set<string>()"),
    ("reintroduce system: prefix RBAC exclusion in kisa-checks-ops.ts", "checks_ops", "const remaining = bindings.filter(", 'const remaining = bindings.filter((b) => !b.name.startsWith("system:")).filter('),
    ("drop exact matchRbacBindingException in KISA-RBAC-01/02", "checks_ops", "matchRbacBindingException({", "({ ...{} }) ? null : (() => { const x = {"),
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
    ("allow res/* subresource wildcard in ruleGrants", "checks_ops", "res === `*/${sub}`", "res === `${parent}/*` || res === `*/${sub}`"),
    ("exclude literal * extra key userextras/* from sub === * query", "checks_ops", '(res.startsWith(`${parent}/`) && res.length > parent.length + 1)', '(res.startsWith(`${parent}/`) && res.length > parent.length + 1 && res !== `${parent}/*`)'),
    ("allow NET-01 ingress:[{}] pass", "kisa", "if (!hasPodSel && !hasNsSel && !hasRestrictedIpBlock && !sameNsAllPods) {", "if (false) {"),
    ("ignore summary.failed in IMG-03", "kisa", "if (summary.failed) {", "if (false && summary.failed) {"),
    ("ignore missing notAfter in TLS-02", "kisa", "if (missingExpiry.length > 0) {", "if (false && missingExpiry.length > 0) {"),
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
