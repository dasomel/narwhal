#!/usr/bin/env python3
"""R392: Portal Settings > OSS Updates truthful running version, rollout/suffix/CVE/sibling invariants.

Verifies without helm or promtool:
1. catalog.ts assigns explicit runningSource (server-version, exact workload/container, static-pod,
   crd-annotation, cli), explicit allowedTagSuffixes (-ubuntu, -distroless, -rootless), sibling
   definitions, and exact imageMatch (incl. registry.istio.io/release/pilot).
2. version.ts exposes explicit repoMatches (no bare substring) and normalizeTag with explicit
   allowedSuffixes allow-list (never blanket-stripping -preview1/-rc1/-custom).
3. status.ts reads Running pods selected by workload selector (never template-only, never first
   static pod only), never yields in-sync from chart alone or Trivy fallback, flags rollout-mixed
   as drift, and attributes CVEs strictly by exact repo+tag/digest (summarizeCvesForArtifacts).
4. k8s-client.ts reads /api/v1/pods + workload selectors with K8sCredentialError -> no-permission,
   and narwhal-portal-k8s.yaml ClusterRole is NOT widened (no apiextensions.k8s.io rule).
5. oss-updates-table.tsx + i18n.ts render not-applicable CLI state, visible/digest-only siblings,
   rollout-mixed drift, and explicit ko/en reasons for every unknown/no-scan state.
6. scripts/test/regression-check-kakao.sh wires R392 with --mutation-verify.

--mutation-verify proves broken copies fail.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "catalog": ROOT / "portal/src/lib/oss-updates/catalog.ts",
    "status": ROOT / "portal/src/lib/oss-updates/status.ts",
    "version": ROOT / "portal/src/lib/oss-updates/version.ts",
    "k8s": ROOT / "portal/src/lib/k8s-client.ts",
    "table": ROOT / "portal/src/components/settings/oss-updates-table.tsx",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "test": ROOT / "portal/src/lib/oss-updates/status.test.ts",
    "rbac": ROOT / "gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml",
    "reg": ROOT / "scripts/test/regression-check-kakao.sh",
}


def check(texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    catalog = texts["catalog"]
    status = texts["status"]
    version = texts["version"]
    k8s = texts["k8s"]
    table = texts["table"]
    i18n = texts["i18n"]
    test = texts["test"]
    rbac = texts["rbac"]
    reg = texts["reg"]

    for needle in (
        'runningSource: { kind: "server-version" }',
        'name: "cilium", container: "cilium-agent"',
        'label: "cilium-envoy"',
        'name: "apisix", container: "apisix"',
        'allowedTagSuffixes: ["-ubuntu"]',
        'label: "apisix-ingress-controller"',
        'crdName: "gateways.gateway.networking.k8s.io"',
        'kind: "static-pod", namespace: "kube-system", namePrefix: "kube-vip-"',
        'name: "istiod", container: "discovery"',
        'allowedTagSuffixes: ["-distroless"]',
        'imageMatch: "registry.istio.io/release/pilot"',
        'name: "falco", container: "falco"',
        'label: "falcosidekick"',
        'name: "velero", container: "velero"',
        'label: "velero-plugin-for-aws"',
        'name: "gitea", container: "gitea"',
        'allowedTagSuffixes: ["-rootless"]',
        'name: "metrics-server", container: "metrics-server"',
        'runningSource: { kind: "cli" }',
    ):
        if needle not in catalog:
            errors.append(f"catalog.ts missing required mapping: {needle}")

    if "r.image.includes(" in status:
        errors.append("status.ts must not use bare substring r.image.includes(")
    if 'source: "trivy"' in status:
        errors.append("status.ts must never substitute Trivy as an authoritative running version")
    for needle in (
        "repoMatches(",
        "normalizeTag(",
        "getOssClusterInventory()",
        "summarizeCvesForArtifacts(",
        "matchesSelector(",
        "inspectContainerAcrossRunningPods(",
        'if (rolloutMixed) return "drift"',
        'if (imageTags.length === 0) return "unknown"',
        'if (hasUnparseableTag) {\n    return chartOk === false ? "drift" : "unknown"\n  }',
    ):
        if needle not in status:
            errors.append(f"status.ts missing {needle}")

    for needle in (
        "export function repoMatches(",
        "export function normalizeTag(",
        "export function shortenDigest(",
        "if (!allowedSuffixes || !allowedSuffixes.includes(suffix)) return null",
    ):
        if needle not in version:
            errors.append(f"version.ts missing {needle}")

    if "export async function getOssClusterInventory()" not in k8s:
        errors.append("k8s-client.ts missing getOssClusterInventory()")
    if 'listBounded<RawOssPod>("/api/v1/pods")' not in k8s:
        errors.append("k8s-client.ts must read /api/v1/pods for running pod images")
    if 'if (err instanceof K8sCredentialError) return { status: "no-permission", resource }' not in k8s:
        errors.append("k8s-client.ts must map K8sCredentialError to no-permission")

    if "apiextensions.k8s.io" in rbac:
        errors.append("narwhal-portal-k8s.yaml must not widen ClusterRole with apiextensions.k8s.io in this lane")

    if 'case "not-applicable":\n      return t("ossUpdates.notApplicableCli")' not in table:
        errors.append("oss-updates-table.tsx missing CLI not-applicable branch")
    for needle in (
        "ossUpdates.notApplicableCli",
        "ossUpdates.reasonNoPermission",
        "ossUpdates.reasonWorkloadNotFound",
        "ossUpdates.reasonContainerNotFound",
        "ossUpdates.reasonNoRunningPod",
        "ossUpdates.reasonRolloutMixed",
        "ossUpdates.reasonDigestOnly",
        "ossUpdates.reasonTagUnparseable",
        "ossUpdates.reasonUpstreamOff",
        "ossUpdates.reasonNoScanForRunning",
        "ossUpdates.reasonNoRunningImage",
        "ossUpdates.companionLabel",
    ):
        if needle not in table:
            errors.append(f"oss-updates-table.tsx missing {needle}")
        if f'"{needle}"' not in i18n:
            errors.append(f"i18n.ts missing {needle}")

    if "liveClusterInventoryFixture" not in test:
        errors.append("status.test.ts missing liveClusterInventoryFixture")

    if "check R392 " not in reg or "check-portal-oss-updates-truth.py --mutation-verify" not in reg:
        errors.append("scripts/test/regression-check-kakao.sh must wire R392 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("revert Cilium container pin", "catalog", 'name: "cilium", container: "cilium-agent"', 'name: "cilium", container: "other"'),
    ("revert Istio imageMatch to broken istio/pilot", "catalog", 'imageMatch: "registry.istio.io/release/pilot"', 'imageMatch: "istio/pilot"'),
    ("remove Helm CLI kind", "catalog", 'runningSource: { kind: "cli" }', 'runningSource: { kind: "server-version" }'),
    ("drop cilium-envoy sibling isolation", "catalog", 'label: "cilium-envoy"', 'label: "other"'),
    ("drop APISIX -ubuntu suffix allow-list", "catalog", 'allowedTagSuffixes: ["-ubuntu"]', 'allowedTagSuffixes: []'),
    ("allow chart-only match when imageTags empty", "status", 'if (imageTags.length === 0) return "unknown"', 'if (false && imageTags.length === 0) return "unknown"'),
    ("drop rollout-mixed drift", "status", 'if (rolloutMixed) return "drift"', 'if (false && rolloutMixed) return "drift"'),
    ("allow unparseable tag in computeDrift", "status", "if (hasUnparseableTag) {", "if (false && hasUnparseableTag) {"),
    ("drop K8s inventory call", "status", "getOssClusterInventory()", "Promise.resolve({} as never)"),
    ("drop strict suffix allow-list in normalizeTag", "version", "if (!allowedSuffixes || !allowedSuffixes.includes(suffix)) return null", "if (false && !allowedSuffixes) return null"),
    ("drop cluster-wide pods read in k8s-client.ts", "k8s", 'listBounded<RawOssPod>("/api/v1/pods")', 'listBounded<RawOssPod>("/api/v1/namespaces/kube-system/pods")'),
    ("drop 403 no-permission mapping in k8s-client.ts", "k8s", 'if (err instanceof K8sCredentialError) return { status: "no-permission", resource }', 'if (false && err instanceof K8sCredentialError) return { status: "no-permission", resource }'),
    ("widen RBAC in narwhal-portal-k8s.yaml", "rbac", "rules:\n", "rules:\n  - apiGroups: [apiextensions.k8s.io]\n"),
    ("drop CLI N/A label in UI", "table", 'case "not-applicable":\n      return t("ossUpdates.notApplicableCli")', 'case "not-applicable":\n      return t("ossUpdates.unknown")'),
    ("drop R392 wiring", "reg", "check R392 ", "check R9999 "),
]


def main() -> int:
    texts = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    errors = check(texts)
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if "--mutation-verify" in sys.argv[1:]:
        for desc, key, old, new in MUTATIONS:
            mutated = copy.copy(texts)
            if old not in mutated[key]:
                print(f"FAIL: mutation target missing for '{desc}' ({key})", file=sys.stderr)
                return 1
            mutated[key] = mutated[key].replace(old, new, 1)
            if not check(mutated):
                print(f"FAIL: mutation '{desc}' was not caught!", file=sys.stderr)
                return 1
        print(f"PASS: R392 OSS Updates truth & sibling isolation ({len(MUTATIONS)} mutations rejected)")
    else:
        print("PASS: R392 OSS Updates truth & sibling isolation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
