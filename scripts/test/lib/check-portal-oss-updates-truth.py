#!/usr/bin/env python3
"""R391: Portal Settings > OSS Updates truthful running version, sibling isolation, and unknown-reason invariants.

Verifies without helm or promtool:
1. catalog.ts assigns explicit runningSource (server-version, exact workload/container, static-pod,
   crd-annotation, cli), sibling definitions (cilium-envoy, apisix-ingress-controller, falcosidekick,
   velero-plugin-for-aws), and exact imageMatch (incl. registry.istio.io/release/pilot).
2. version.ts exposes explicit repoMatches (no bare substring) and normalizeTag (rejecting
   prereleases and non-semver tags like latest/HEAD/8-alpine).
3. status.ts uses getOssClusterInventory + repoMatches (no r.image.includes) and computeDrift
   never marks unparseable tags as in-sync.
4. k8s-client.ts defines getOssClusterInventory mapping K8sCredentialError -> no-permission, and
   narwhal-portal-k8s.yaml ClusterRole is NOT widened (no apiextensions.k8s.io rule).
5. oss-updates-table.tsx + i18n.ts render not-applicable CLI state, sibling sub-rows, and explicit
   ko/en reasons for every unknown/no-scan state.
6. scripts/test/regression-check-kakao.sh wires R391 with --mutation-verify.

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
        'label: "apisix-ingress-controller"',
        'crdName: "gateways.gateway.networking.k8s.io"',
        'kind: "static-pod", namespace: "kube-system", namePrefix: "kube-vip-"',
        'name: "istiod", container: "discovery"',
        'imageMatch: "registry.istio.io/release/pilot"',
        'name: "falco", container: "falco"',
        'label: "falcosidekick"',
        'name: "velero", container: "velero"',
        'label: "velero-plugin-for-aws"',
        'name: "gitea", container: "gitea"',
        'name: "metrics-server", container: "metrics-server"',
        'runningSource: { kind: "cli" }',
    ):
        if needle not in catalog:
            errors.append(f"catalog.ts missing required mapping: {needle}")

    if "r.image.includes(" in status:
        errors.append("status.ts must not use bare substring r.image.includes(")
    for needle in (
        "repoMatches(",
        "normalizeTag(",
        "getOssClusterInventory()",
        'if (hasUnparseableTag) {\n    return chartOk === false ? "drift" : "unknown"\n  }',
    ):
        if needle not in status:
            errors.append(f"status.ts missing {needle}")

    for needle in ("export function repoMatches(", "export function normalizeTag(", "if (!cleaned || PRERELEASE_TAG_RE.test(cleaned)) return null"):
        if needle not in version:
            errors.append(f"version.ts missing {needle}")

    if "export async function getOssClusterInventory()" not in k8s:
        errors.append("k8s-client.ts missing getOssClusterInventory()")
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
        "ossUpdates.reasonTagUnparseable",
        "ossUpdates.reasonUpstreamOff",
        "ossUpdates.reasonNoScanReport",
        "ossUpdates.companionLabel",
    ):
        if needle not in table:
            errors.append(f"oss-updates-table.tsx missing {needle}")
        if f'"{needle}"' not in i18n:
            errors.append(f"i18n.ts missing {needle}")

    if "liveClusterInventoryFixture" not in test:
        errors.append("status.test.ts missing liveClusterInventoryFixture")

    if "check R391 " not in reg or "check-portal-oss-updates-truth.py --mutation-verify" not in reg:
        errors.append("scripts/test/regression-check-kakao.sh must wire R391 with --mutation-verify")

    return errors


MUTATIONS: list[tuple[str, str, str, str]] = [
    ("revert Cilium container pin", "catalog", 'name: "cilium", container: "cilium-agent"', 'name: "cilium", container: "other"'),
    ("revert Istio imageMatch to broken istio/pilot", "catalog", 'imageMatch: "registry.istio.io/release/pilot"', 'imageMatch: "istio/pilot"'),
    ("remove Helm CLI kind", "catalog", 'runningSource: { kind: "cli" }', 'runningSource: { kind: "server-version" }'),
    ("drop cilium-envoy sibling isolation", "catalog", 'label: "cilium-envoy"', 'label: "other"'),
    ("reintroduce substring match in status.ts", "status", "repoMatches(r.image, imageMatch)", "r.image.includes(imageMatch)"),
    ("allow unparseable tag in computeDrift", "status", "if (hasUnparseableTag) {", "if (false && hasUnparseableTag) {"),
    ("drop K8s inventory call", "status", "getOssClusterInventory()", "Promise.resolve({} as never)"),
    ("drop prerelease rejection in normalizeTag", "version", "if (!cleaned || PRERELEASE_TAG_RE.test(cleaned)) return null", "if (!cleaned) return null"),
    ("drop 403 no-permission mapping in k8s-client.ts", "k8s", 'if (err instanceof K8sCredentialError) return { status: "no-permission", resource }', 'if (false && err instanceof K8sCredentialError) return { status: "no-permission", resource }'),
    ("widen RBAC in narwhal-portal-k8s.yaml", "rbac", "rules:\n", "rules:\n  - apiGroups: [apiextensions.k8s.io]\n"),
    ("drop CLI N/A label in UI", "table", 'case "not-applicable":\n      return t("ossUpdates.notApplicableCli")', 'case "not-applicable":\n      return t("ossUpdates.unknown")'),
    ("drop R391 wiring", "reg", "check R391 ", "check R9999 "),
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
        print(f"PASS: R391 OSS Updates truth & sibling isolation ({len(MUTATIONS)} mutations rejected)")
    else:
        print("PASS: R391 OSS Updates truth & sibling isolation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
