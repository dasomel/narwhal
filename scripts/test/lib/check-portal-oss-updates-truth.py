#!/usr/bin/env python3
"""R394: Portal Settings > OSS Updates truthful running version, ownership/truncation/status-image/digest/CVE invariants.

Verifies without helm or promtool:
1. catalog.ts assigns explicit runningSource (server-version, exact workload/container, static-pod,
   crd-annotation, cli), explicit allowedTagSuffixes (-ubuntu, -distroless, -rootless), sibling
   definitions, and exact imageMatch (incl. registry.istio.io/release/pilot).
2. version.ts exposes explicit repoMatches (no bare substring) and normalizeTag with explicit
   allowedSuffixes allow-list (never blanket-stripping -preview1/-rc1/-custom).
3. status.ts enforces metadata.ownerReferences ownership (Deployment -> ReplicaSet -> Pod;
   DaemonSet/StatefulSet -> Pod; static mirror pod via mirrorAnnotation + nodeName) rather than
   label-only matching, propagates truncated bounded lists as list-truncated unknown, requires
   active Running (!terminating) pods and container running state, dedupes by effective digest,
   never yields in-sync from chart alone or Trivy fallback, flags rollout-mixed as drift, and
   attributes CVEs strictly by exact repo+tag/digest (summarizeCvesForArtifacts).
4. k8s-client.ts reads /api/v1/pods + /apis/apps/v1/replicasets + workloads via
   fetchOssBoundedResource (propagating truncated -> status: "truncated"), extracts runtime image
   and running state from status.containerStatuses[], maps K8sCredentialError -> no-permission,
   and narwhal-portal-k8s.yaml ClusterRole pins apiextensions.k8s.io strictly to get on
   customresourcedefinitions resourceNames [gateways.gateway.networking.k8s.io] (plus
   OSS_UPDATES_UPSTREAM env on Deployment narwhal-portal).
5. oss-updates-table.tsx + i18n.ts render not-applicable CLI state, visible/digest-only siblings,
   rollout-mixed drift, list-truncated unknown, and explicit ko/en reasons for every unknown/no-scan state.
6. scripts/test/regression-check-kakao.sh wires R394 with --mutation-verify.

--mutation-verify proves broken copies fail.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

FILES = {
    "catalog": ROOT / "portal/src/lib/oss-updates/catalog.ts",
    "status": ROOT / "portal/src/lib/oss-updates/status.ts",
    "version": ROOT / "portal/src/lib/oss-updates/version.ts",
    "k8s": ROOT / "portal/src/lib/k8s-client.ts",
    "table": ROOT / "portal/src/components/settings/oss-updates-table.tsx",
    "i18n": ROOT / "portal/src/lib/i18n.ts",
    "test": ROOT / "portal/src/lib/oss-updates/status.test.ts",
    "k8s_test": ROOT / "portal/src/lib/k8s-client.oss-updates.test.ts",
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
    k8s_test = texts["k8s_test"]
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
    if "matchesSelector(" in status:
        errors.append("status.ts must resolve pods by ownerReferences, not matchesSelector(")
    for needle in (
        "repoMatches(",
        "normalizeTag(",
        "getOssClusterInventory()",
        "summarizeCvesForArtifacts(",
        "isOwnedBy(",
        'return pod.phase === "Running" && !pod.terminating',
        "isMirrorStaticPod(",
        "isContainerRunning(",
        "fetchFailureReason(",
        'if (fetch.status === "truncated")',
        'const effectiveDigest = observedDigest ?? (rawImageId ? null : parsed.digest) ?? null',
        'const imageIdentity = observedDigest ?? (rawImageId || parsed.digest || null)',
        'const key = `${normRepo}|${parsed.rawTag ?? ""}|${imageIdentity ?? ""}`',
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
    for needle in (
        'listBounded<RawOssPod>("/api/v1/pods")',
        'listBounded<RawOssReplicaSet>("/apis/apps/v1/replicasets")',
        'if (res.truncated) return { status: "truncated", resource }',
        "const statusImage = typeof entry.image === \"string\" ? entry.image : \"\"",
        "const running = Boolean(entry.state?.running)",
        'if (err instanceof K8sCredentialError) return { status: "no-permission", resource }',
        r'const IMAGE_ID_DIGEST_RE = /(?:^|@|\/\/|\/)((?:sha256:[0-9a-fA-F]{64}|sha512:[0-9a-fA-F]{128}))(?=$|[^0-9a-fA-F])/',
        'const digest = imageIdDigest ?? (!rawImageId && statusImage ? IMAGE_DIGEST_RE.exec(statusImage)?.[1] ?? null : null)',
        'const rawIdentity = !imageIdDigest && rawImageId ? rawImageId : null',
    ):
        # RawOssReplicaSet vs RawOssWorkload check
        if needle == 'listBounded<RawOssReplicaSet>("/apis/apps/v1/replicasets")':
            if 'listBounded<RawOssWorkload>("/apis/apps/v1/replicasets")' not in k8s and needle not in k8s:
                errors.append("k8s-client.ts must read /apis/apps/v1/replicasets for Deployment ownership")
            continue
        if needle not in k8s:
            errors.append(f"k8s-client.ts missing {needle}")

    try:
        rbac_docs = [d for d in yaml.safe_load_all(rbac) if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        errors.append(f"narwhal-portal-k8s.yaml YAML parse failed: {exc}")
        rbac_docs = []
    cr = next((d for d in rbac_docs if d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == "narwhal-portal"), None)
    if cr is None:
        errors.append("narwhal-portal-k8s.yaml missing ClusterRole narwhal-portal")
    else:
        crd_rules = [
            r for r in (cr.get("rules") or [])
            if "apiextensions.k8s.io" in (r.get("apiGroups") or [])
            or "customresourcedefinitions" in (r.get("resources") or [])
        ]
        expected_crd_rule = {
            "apiGroups": ["apiextensions.k8s.io"],
            "resources": ["customresourcedefinitions"],
            "resourceNames": ["gateways.gateway.networking.k8s.io"],
            "verbs": ["get"],
        }
        if crd_rules != [expected_crd_rule]:
            errors.append(f"ClusterRole narwhal-portal CRD rule must be exactly {expected_crd_rule}, got {crd_rules}")
    dep = next((d for d in rbac_docs if d.get("kind") == "Deployment" and (d.get("metadata") or {}).get("name") == "narwhal-portal"), None)
    if dep is None:
        errors.append("narwhal-portal-k8s.yaml missing Deployment narwhal-portal")
    else:
        ctr = next((c for c in dep["spec"]["template"]["spec"]["containers"] if c.get("name") == "narwhal-portal"), None)
        envs = {e["name"]: e.get("value") for e in (ctr or {}).get("env", []) if isinstance(e, dict) and "name" in e}
        if "OSS_UPDATES_UPSTREAM" not in envs:
            errors.append("narwhal-portal Deployment missing OSS_UPDATES_UPSTREAM env")
        elif 'eq .Values.provider "kakao"' not in str(envs["OSS_UPDATES_UPSTREAM"]) and envs["OSS_UPDATES_UPSTREAM"] != "true":
            errors.append(f"narwhal-portal OSS_UPDATES_UPSTREAM has unexpected value: {envs['OSS_UPDATES_UPSTREAM']!r}")

    if 'case "not-applicable":\n      return t("ossUpdates.notApplicableCli")' not in table:
        errors.append("oss-updates-table.tsx missing CLI not-applicable branch")
    for needle in (
        "ossUpdates.notApplicableCli",
        "ossUpdates.reasonNoPermission",
        "ossUpdates.reasonListTruncated",
        "ossUpdates.reasonListTruncatedShort",
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
    for needle in ("docker://", "containerd://", "cri-o://"):
        if needle not in k8s_test:
            errors.append(f"k8s-client.oss-updates.test.ts missing {needle} reproduction")

    if "check R394 " not in reg or "check-portal-oss-updates-truth.py --mutation-verify" not in reg:
        errors.append("scripts/test/regression-check-kakao.sh must wire R394 with --mutation-verify")

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
    ("drop digest from deduplication key", "status", 'const key = `${normRepo}|${parsed.rawTag ?? ""}|${imageIdentity ?? ""}`', 'const key = `${normRepo}|${parsed.rawTag ?? ""}|`'),
    ("prefer status.image digest over observed imageID", "status", 'const imageIdentity = observedDigest ?? (rawImageId || parsed.digest || null)', 'const imageIdentity = parsed.digest ?? observedDigest ?? (rawImageId || null)'),
    ("revert imageID regex to miss runtime-prefixed forms", "k8s", r'const IMAGE_ID_DIGEST_RE = /(?:^|@|\/\/|\/)((?:sha256:[0-9a-fA-F]{64}|sha512:[0-9a-fA-F]{128}))(?=$|[^0-9a-fA-F])/', r'const IMAGE_ID_DIGEST_RE = /(?:^|@)((?:sha256|sha512):[0-9a-fA-F]+)/'),
    ("drop raw non-digest imageID identity preservation", "k8s", 'const rawIdentity = !imageIdDigest && rawImageId ? rawImageId : null', 'const rawIdentity = null'),
    ("let status.image digest override non-empty non-digest imageID", "k8s", 'const digest = imageIdDigest ?? (!rawImageId && statusImage ? IMAGE_DIGEST_RE.exec(statusImage)?.[1] ?? null : null)', 'const digest = imageIdDigest ?? (statusImage ? IMAGE_DIGEST_RE.exec(statusImage)?.[1] ?? null : null)'),
    ("ignore deletionTimestamp in isActiveRunningPod", "status", "return pod.phase === \"Running\" && !pod.terminating", "return pod.phase === \"Running\""),
    ("ignore truncated status in fetchFailureReason", "status", 'if (fetch.status === "truncated") {', 'if (false && fetch.status === "truncated") {'),
    ("drop strict suffix allow-list in normalizeTag", "version", "if (!allowedSuffixes || !allowedSuffixes.includes(suffix)) return null", "if (false && !allowedSuffixes) return null"),
    ("drop cluster-wide pods read in k8s-client.ts", "k8s", 'listBounded<RawOssPod>("/api/v1/pods")', 'listBounded<RawOssPod>("/api/v1/namespaces/kube-system/pods")'),
    ("drop truncated propagation in k8s-client.ts", "k8s", 'if (res.truncated) return { status: "truncated", resource }', 'if (false && res.truncated) return { status: "truncated", resource }'),
    ("drop status.image usage in k8s-client.ts", "k8s", 'const statusImage = typeof entry.image === "string" ? entry.image : ""', 'const statusImage = ""'),
    ("drop 403 no-permission mapping in k8s-client.ts", "k8s", 'if (err instanceof K8sCredentialError) return { status: "no-permission", resource }', 'if (false && err instanceof K8sCredentialError) return { status: "no-permission", resource }'),
    ("widen CRD verb to list in narwhal-portal-k8s.yaml", "rbac", "    resourceNames: [gateways.gateway.networking.k8s.io]\n    verbs: [get]", "    resourceNames: [gateways.gateway.networking.k8s.io]\n    verbs: [get, list]"),
    ("drop resourceNames on CRD rule in narwhal-portal-k8s.yaml", "rbac", "    resourceNames: [gateways.gateway.networking.k8s.io]\n    verbs: [get]", "    verbs: [get]"),
    ("widen RBAC with extra apiextensions rule in narwhal-portal-k8s.yaml", "rbac", "rules:\n", "rules:\n  - apiGroups: [apiextensions.k8s.io]\n    resources: [customresourcedefinitions]\n    verbs: [list]\n"),
    ("drop OSS_UPDATES_UPSTREAM env in narwhal-portal-k8s.yaml", "rbac", "            - name: OSS_UPDATES_UPSTREAM\n", "            - name: OSS_UPDATES_DISABLED\n"),
    ("drop CLI N/A label in UI", "table", 'case "not-applicable":\n      return t("ossUpdates.notApplicableCli")', 'case "not-applicable":\n      return t("ossUpdates.unknown")'),
    ("drop R394 wiring", "reg", "check R394 ", "check R9999 "),
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
        print(f"PASS: R394 OSS Updates truth & sibling isolation ({len(MUTATIONS)} mutations rejected)")
    else:
        print("PASS: R394 OSS Updates truth & sibling isolation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
