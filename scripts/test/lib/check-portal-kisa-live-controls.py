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

    # 1. Forbid STATIC_STATUS or hard-coded status maps for the 6 controls
    if "STATIC_STATUS" in kisa:
        errors.append("kisa.ts must not contain STATIC_STATUS")
    for cid in LIVE_SIX:
        if re.search(rf'"{cid}"\s*:\s*"(?:pass|fail|warn)"', kisa):
            errors.append(f"kisa.ts hardcodes a static status for {cid}")

    # 2. Wiring in kisa.ts
    for needle in (
        "checkEtcd02()",
        "checkSec01()",
        "checkImg01()",
        "checkTls01()",
        "checkObs01()",
        "checkImg02()",
        "checkRbacLive(entry.id)",
        "if (r.partial) hasPartialCheck = true",
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
        'makeOutcome(\n    "manual",\n    "compliance.kisa.reason.imgNodeManual"',
        "portLevelMtls",
    ):
        if needle not in checks:
            errors.append(f"kisa-checks.ts missing {needle!r}")

    for needle in (
        "routed.has(name)",
        "buildSupersededReplicaSetKeys(inv.replicasets.data)",
        "excludedMissingOrZeroRs",
        "excludedOutOfScope",
        "matchRbacException(name, cid, roleNs, kind) !== null",
        "const hasCoreOrWildcardGroup = apiGroups.includes(\"*\") || apiGroups.includes(\"\")",
        "if (hasWildcardVerb && hasWildcardResource && hasCoreOrWildcardGroup)",
    ):
        if needle not in checks_ops:
            errors.append(f"kisa-checks-ops.ts missing {needle!r}")

    # 4. PartialObjectMetadata on Secret metadata read; no secrets rule in ClusterRole narwhal-portal
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
            if "secrets" in (rule.get("resources") or []):
                errors.append("ClusterRole narwhal-portal must not grant secrets access")

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
