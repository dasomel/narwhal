import { beforeEach, describe, expect, it, vi } from "vitest"
import type { WorkloadVulnRow } from "@/types/security"
import type { OssClusterInventory, OssOwnerRef, OssPodSpecItem, OssReplicaSetItem, OssWorkloadSpecItem } from "../k8s-client"
import type { UpstreamRelease } from "./upstream"

vi.mock("server-only", () => ({}))
const getArgoAppsOrThrow = vi.fn()
vi.mock("../argocd", () => ({ getArgoAppsOrThrow: () => getArgoAppsOrThrow() }))
const getWorkloadVulnerabilitiesWithMeta = vi.fn()
vi.mock("../trivy", () => ({ getWorkloadVulnerabilitiesWithMeta: () => getWorkloadVulnerabilitiesWithMeta() }))
const getOssClusterInventory = vi.fn()
vi.mock("../k8s-client", () => ({ getOssClusterInventory: () => getOssClusterInventory() }))
const getUpstreamReleases = vi.fn()
vi.mock("./upstream", () => ({
  isUpstreamLookupEnabled: () => process.env.OSS_UPDATES_UPSTREAM === "true",
  getUpstreamReleases: (r: string) => getUpstreamReleases(r),
}))

const { computeDrift, getOssUpdateStatus, summarizeCvesForArtifacts, summarizeUpstream } = await import("./status")

const NOW = Date.parse("2026-11-23T00:00:00Z")
const rel = (tag: string, ageDaysAgo: number): UpstreamRelease => ({
  tag, publishedAt: new Date(NOW - ageDaysAgo * 86_400_000).toISOString(), url: `https://example.test/${tag}`,
})
const sev = (critical = 0, high = 0) => ({ Critical: critical, High: high, Medium: 0, Low: 0, Unknown: 0 })
const vuln = (image: string, critical = 0, high = 0, namespace = "n", digest?: string): WorkloadVulnRow => ({
  namespace, kind: "Deployment", name: "w", image, ...(digest ? { digest } : {}), summary: sev(critical, high),
})
const wl = (
  namespace: string,
  name: string,
  containers: Array<[string, string]>,
  initContainers: Array<[string, string]> = [],
  selector: Record<string, string> = { app: name },
  uid = `${namespace}-${name}-uid`,
): OssWorkloadSpecItem => ({
  namespace,
  name,
  uid,
  selector: { matchLabels: selector },
  containers: containers.map(([cname, image]) => ({ name: cname, image })),
  initContainers: initContainers.map(([cname, image]) => ({ name: cname, image })),
})
const rs = (namespace: string, name: string, deploymentName: string, deploymentUid?: string): OssReplicaSetItem => ({
  namespace,
  name,
  uid: `${namespace}-${name}-uid`,
  ownerReferences: [{ kind: "Deployment", name: deploymentName, ...(deploymentUid ? { uid: deploymentUid } : {}), controller: true }],
})
const pod = (
  namespace: string,
  name: string,
  labels: Record<string, string>,
  containers: Array<[string, string, string?, boolean?]>,
  initContainers: Array<[string, string, string?, boolean?, boolean?]> = [],
  opts: {
    phase?: string
    terminating?: boolean
    ownerReferences?: OssOwnerRef[]
    nodeName?: string
    mirrorAnnotation?: string
  } | string = {},
): OssPodSpecItem => {
  const o = typeof opts === "string" ? { phase: opts } : opts
  return {
    namespace,
    name,
    labels,
    phase: o.phase ?? "Running",
    ...(o.terminating ? { terminating: true } : {}),
    ...(o.nodeName ? { nodeName: o.nodeName } : {}),
    ...(o.mirrorAnnotation ? { mirrorAnnotation: o.mirrorAnnotation } : {}),
    ...(o.ownerReferences ? { ownerReferences: o.ownerReferences } : {}),
    containers: containers.map(([cname, image, digest, running]) => ({
      name: cname,
      image,
      ...(digest ? { digest } : {}),
      running: running ?? true,
    })),
    initContainers: initContainers.map(([cname, image, digest, running, completed]) => ({
      name: cname,
      image,
      ...(digest ? { digest } : {}),
      running: running ?? false,
      completed: completed ?? true,
    })),
  }
}

function emptyUnavailableInventory(): OssClusterInventory {
  return {
    serverVersion: { status: "error" },
    deployments: { status: "error" },
    daemonsets: { status: "error" },
    statefulsets: { status: "error" },
    replicasets: { status: "error" },
    pods: { status: "error" },
    gatewayApiCrdAnnotations: { status: "error" },
  }
}

function liveClusterInventoryFixture(): OssClusterInventory {
  const deployments: OssWorkloadSpecItem[] = [
    wl("kube-system", "cilium-operator", [["cilium-operator", "quay.io/cilium/operator-generic:v1.19.4"]]),
    wl("kube-system", "csi-nfs-controller", [
      ["csi-provisioner", "registry.k8s.io/sig-storage/csi-provisioner:v6.1.0"],
      ["csi-resizer", "registry.k8s.io/sig-storage/csi-resizer:v2.0.0"],
      ["csi-snapshotter", "registry.k8s.io/sig-storage/csi-snapshotter:v8.4.0"],
      ["liveness-probe", "registry.k8s.io/sig-storage/livenessprobe:v2.17.0"],
      ["nfs", "registry.k8s.io/sig-storage/nfsplugin:v4.13.2"],
    ]),
    wl("kube-system", "metrics-server", [["metrics-server", "registry.k8s.io/metrics-server/metrics-server:v0.8.1"]]),
    wl("platform-system", "apisix", [["apisix", "apache/apisix:3.15.0-ubuntu"]], [["init-conf", "apache/apisix:3.15.0-ubuntu"]]),
    wl("platform-system", "apisix-ingress-controller", [["apisix-ingress-controller", "apache/apisix-ingress-controller:1.8.0"]], [["wait-apisix-admin", "busybox:1.28"]]),
    wl("platform-system", "metallb-controller", [["controller", "quay.io/metallb/controller:v0.16.1"]]),
    wl("istio-system", "istiod", [["discovery", "registry.istio.io/release/pilot:1.30.1-distroless"]]),
    wl("platform-system", "cnpg-cloudnative-pg", [["manager", "ghcr.io/cloudnative-pg/cloudnative-pg:1.29.1"]]),
    wl("iam", "keycloak-operator", [["keycloak-operator", "quay.io/keycloak/keycloak-operator:26.5.7"]]),
    wl("platform-system", "cert-manager", [["cert-manager-controller", "quay.io/jetstack/cert-manager-controller:v1.20.2"]]),
    wl("platform-system", "cert-manager-cainjector", [["cert-manager-cainjector", "quay.io/jetstack/cert-manager-cainjector:v1.20.2"]]),
    wl("platform-system", "cert-manager-webhook", [["cert-manager-webhook", "quay.io/jetstack/cert-manager-webhook:v1.20.2"]]),
    wl("platform-system", "kyverno-admission-controller", [["kyverno", "reg.kyverno.io/kyverno/kyverno:v1.18.1"]], [["kyverno-pre", "reg.kyverno.io/kyverno/kyvernopre:v1.18.1"]]),
    wl("security-system", "falco-falcosidekick", [["falcosidekick", "docker.io/falcosecurity/falcosidekick:2.35.0"]]),
    wl("storage", "velero", [["velero", "docker.io/velero/velero:v1.18.1"]], [["velero-plugin-for-aws", "velero/velero-plugin-for-aws:v1.14.1"]]),
    wl("storage", "velero-ui", [["velero-ui", "docker.io/otwld/velero-ui:0.10.1"]]),
    wl("devtools", "gitea", [["gitea", "docker.gitea.com/gitea:1.26.2-rootless"]], [["configure-gitea", "docker.gitea.com/gitea:1.26.2-rootless"]]),
    wl("devtools", "argocd-server", [["argocd-server", "quay.io/argoproj/argocd:v3.4.4"]]),
    wl("devtools", "harbor-core", [["core", "ghcr.io/dasomel/goharbor/harbor-core:v2.15.1"]]),
    wl("devtools", "headlamp", [["headlamp", "ghcr.io/headlamp-k8s/headlamp:v0.46.0"]], [["ca-bundle", "ghcr.io/headlamp-k8s/headlamp:v0.46.0"]]),
  ]
  const replicasets: OssReplicaSetItem[] = deployments.map((d) => rs(d.namespace, `${d.name}-rs`, d.name, d.uid))
  const daemonsets: OssWorkloadSpecItem[] = [
    wl("kube-system", "cilium", [["cilium-agent", "quay.io/cilium/cilium:v1.19.4"]]),
    wl("kube-system", "cilium-envoy", [["cilium-envoy", "quay.io/cilium/cilium-envoy:v1.36.6-1778235340-b87d1e32f522b33bd51701c6476d199326f01496"]]),
    wl("platform-system", "metallb-speaker", [["speaker", "quay.io/metallb/speaker:v0.16.1"]]),
    wl("istio-system", "istio-cni-node", [["install-cni", "registry.istio.io/release/install-cni:1.30.1-distroless"]]),
    wl("istio-system", "ztunnel", [["istio-proxy", "registry.istio.io/release/ztunnel:1.30.1"]]),
    wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]]),
  ]
  const statefulsets: OssWorkloadSpecItem[] = [
    wl("storage", "seaweedfs-master", [["seaweedfs", "chrislusf/seaweedfs:4.34"]]),
    wl("iam", "keycloak", [["keycloak", "quay.io/keycloak/keycloak:26.5.7"]]),
    wl("storage", "openbao", [["openbao", "quay.io/openbao/openbao:2.5.4"]]),
    wl("monitoring", "loki", [["loki", "docker.io/grafana/loki:3.7.3"], ["loki-sc-rules", "docker.io/kiwigrid/k8s-sidecar:2.8.1"]]),
    wl("monitoring", "tempo", [["tempo", "docker.io/grafana/tempo:2.9.0"]]),
  ]
  const pods: OssPodSpecItem[] = [
    ...deployments.map((w, idx) =>
      pod(
        w.namespace,
        `${w.name}-pod-1`,
        w.selector?.matchLabels ?? { app: w.name },
        w.containers.map((c) => [c.name, c.image]),
        w.initContainers.map((c) => [c.name, c.image]),
        { ownerReferences: [{ kind: "ReplicaSet", name: replicasets[idx].name, uid: replicasets[idx].uid, controller: true }] },
      ),
    ),
    ...daemonsets.map((w) =>
      pod(
        w.namespace,
        `${w.name}-pod-1`,
        w.selector?.matchLabels ?? { app: w.name },
        w.containers.map((c) => [c.name, c.image]),
        w.initContainers.map((c) => [c.name, c.image]),
        { ownerReferences: [{ kind: "DaemonSet", name: w.name, uid: w.uid, controller: true }] },
      ),
    ),
    ...statefulsets.map((w) =>
      pod(
        w.namespace,
        `${w.name}-0`,
        w.selector?.matchLabels ?? { app: w.name },
        w.containers.map((c) => [c.name, c.image]),
        w.initContainers.map((c) => [c.name, c.image]),
        { ownerReferences: [{ kind: "StatefulSet", name: w.name, uid: w.uid, controller: true }] },
      ),
    ),
    pod(
      "kube-system",
      "kube-vip-narwhal-master-1",
      { "k8s-app": "kube-vip" },
      [["kube-vip", "ghcr.io/kube-vip/kube-vip:v1.1.2"]],
      [],
      {
        nodeName: "narwhal-master-1",
        mirrorAnnotation: "1ca7f49e21c8f451169e05eb2bfd10b1",
        ownerReferences: [{ kind: "Node", name: "narwhal-master-1", controller: true }],
      },
    ),
  ]

  return {
    serverVersion: { status: "ok", data: "v1.35.7" },
    deployments: { status: "ok", data: deployments },
    daemonsets: { status: "ok", data: daemonsets },
    statefulsets: { status: "ok", data: statefulsets },
    replicasets: { status: "ok", data: replicasets },
    pods: { status: "ok", data: pods },
    gatewayApiCrdAnnotations: { status: "no-permission", resource: "customresourcedefinitions.apiextensions.k8s.io" },
  }
}

function liveArgoApps() {
  return [
    ["apisix", "2.13.0"],
    ["metallb", "0.16.1"],
    ["istiod", "1.30.1"],
    ["seaweedfs", "4.34.0"],
    ["cert-manager", "v1.20.2"],
    ["openbao", "0.28.3"],
    ["kyverno", "3.8.1"],
    ["falco", "9.2.1"],
    ["velero", "12.0.3"],
    ["loki", "18.4.0"],
    ["tempo", "2.2.3"],
    ["harbor", "1.19.1"],
    ["headlamp", "0.46.0"],
  ].map(([name, targetRevision]) => ({ metadata: { name }, spec: { source: { targetRevision } } }))
}

function liveTrivyRows(): WorkloadVulnRow[] {
  return [
    vuln("quay.io/cilium/cilium-envoy:v1.36.6-1778235340-b87d1e32f522b33bd51701c6476d199326f01496", 0, 6, "kube-system"),
    vuln("index.docker.io/apache/apisix:3.15.0-ubuntu", 0, 6, "platform-system"),
    vuln("index.docker.io/apache/apisix-ingress-controller:1.8.0", 3, 56, "platform-system"),
    vuln("quay.io/metallb/controller:v0.16.1", 1, 32, "platform-system"),
    vuln("registry.istio.io/release/pilot:1.30.1-distroless", 0, 42, "istio-system"),
    vuln("index.docker.io/chrislusf/seaweedfs:4.34", 1, 53, "storage"),
    vuln("ghcr.io/cloudnative-pg/cloudnative-pg:1.29.1", 0, 52, "platform-system"),
    vuln("quay.io/keycloak/keycloak:26.5.7", 8, 75, "iam"),
    vuln("quay.io/keycloak/keycloak-operator:26.5.7", 2, 51, "iam"),
    vuln("quay.io/jetstack/cert-manager-controller:v1.20.2", 0, 43, "platform-system"),
    vuln("quay.io/openbao/openbao:2.5.4", 1, 52, "storage"),
    vuln("reg.kyverno.io/kyverno/kyverno:v1.18.1", 1, 50, "platform-system"),
    vuln("index.docker.io/falcosecurity/falco:0.45.1", 1, 1, "security-system"),
    vuln("index.docker.io/falcosecurity/falcosidekick:2.35.0", 3, 25, "security-system"),
    vuln("index.docker.io/velero/velero:v1.18.1", 0, 109, "storage"),
    vuln("index.docker.io/velero/velero-plugin-for-aws:v1.14.1", 0, 40, "storage"),
    vuln("index.docker.io/otwld/velero-ui:0.10.1", 11, 126, "storage"),
    vuln("index.docker.io/grafana/loki:3.7.3", 0, 26, "monitoring"),
    vuln("index.docker.io/grafana/tempo:2.9.0", 2, 63, "monitoring"),
    vuln("docker.gitea.com/gitea:1.26.2-rootless", 6, 99, "devtools"),
    vuln("quay.io/argoproj/argocd:v3.4.4", 5, 183, "devtools"),
    vuln("ghcr.io/dasomel/goharbor/harbor-core:v2.15.1", 7, 94, "devtools"),
    vuln("ghcr.io/headlamp-k8s/headlamp:v0.46.0", 0, 11, "devtools"),
  ]
}

beforeEach(() => {
  delete process.env.OSS_UPDATES_UPSTREAM
  getArgoAppsOrThrow.mockReset().mockResolvedValue([])
  getWorkloadVulnerabilitiesWithMeta.mockReset().mockResolvedValue({ rows: [], partial: false })
  getOssClusterInventory.mockReset().mockResolvedValue(emptyUnavailableInventory())
  getUpstreamReleases.mockReset().mockResolvedValue(null)
})

describe("summarizeUpstream", () => {
  const releases = [rel("v1.36.0", 2), rel("v1.35.9", 20), rel("v1.35.7", 60)]
  it("flags a cooling latest and proposes the newest cooled release as target", () => {
    const u = summarizeUpstream(releases, "1.35.7", false, NOW)!
    expect(u).toMatchObject({ latest: "v1.36.0", cooling: true, target: "v1.35.9", gap: "minor", daysBehind: 58 })
  })
  it("holds the minor line when asked (branch-latest)", () => {
    const u = summarizeUpstream(releases, "1.35.7", true, NOW)!
    expect(u).toMatchObject({ latest: "v1.35.9", cooling: false, target: "v1.35.9", gap: "patch" })
  })
  it("has no target when declared is already newest; daysBehind null when declared tag unseen", () => {
    expect(summarizeUpstream(releases, "1.36.0", false, NOW)).toMatchObject({ target: null, gap: "none" })
    expect(summarizeUpstream(releases, "1.20.0", false, NOW)?.daysBehind).toBeNull()
  })
  it("returns null for an empty pool", () => {
    expect(summarizeUpstream([], "1.0.0", false, NOW)).toBeNull()
    expect(summarizeUpstream([rel("v2.0.0", 30)], "1.0.0", true, NOW)).toBeNull()
  })
})

describe("summarizeCvesForArtifacts / computeDrift", () => {
  it("attributes CVEs strictly by exact repo + tag (or digest), never counting older tags", () => {
    const rows = [
      vuln("index.docker.io/falcosecurity/falco:0.45.0", 9, 9, "security-system"),
      vuln("index.docker.io/falcosecurity/falco:0.45.1", 1, 2, "security-system"),
      vuln("index.docker.io/falcosecurity/falco:0.45.1", 1, 2, "security-system"),
      vuln("index.docker.io/falcosecurity/falcosidekick:2.35.0", 5, 5, "security-system"),
    ]
    expect(
      summarizeCvesForArtifacts(
        rows,
        [{ repo: "docker.io/falcosecurity/falco", rawTag: "0.45.1", pinnedDigest: null, effectiveDigest: null }],
        "security-system",
      ),
    ).toEqual({ critical: 1, high: 2 })

    // Running 0.45.2 which has no report: older 0.45.0 / 0.45.1 reports must NOT match!
    expect(
      summarizeCvesForArtifacts(
        rows,
        [{ repo: "docker.io/falcosecurity/falco", rawTag: "0.45.2", pinnedDigest: null, effectiveDigest: null }],
        "security-system",
      ),
    ).toBeNull()

    // Digest-only image matches strictly by digest
    const dig = "sha256:" + "d".repeat(64)
    expect(
      summarizeCvesForArtifacts(
        [vuln("quay.io/cilium/cilium:latest", 2, 4, "kube-system", dig)],
        [{ repo: "quay.io/cilium/cilium", rawTag: null, pinnedDigest: dig, effectiveDigest: dig }],
        "kube-system",
      ),
    ).toEqual({ critical: 2, high: 4 })
  })

  it("drift: never returns in-sync from chart alone, mixed rollout, or disallowed suffix", () => {
    const d = { version: "0.45.1", chartVersion: "9.2.1" }
    expect(computeDrift(d, "9.2.1", ["0.45.1"])).toBe("in-sync")
    expect(computeDrift(d, "9.2.0", ["0.45.1"])).toBe("drift")
    expect(computeDrift(d, null, ["0.45.0"])).toBe("drift")
    expect(computeDrift(d, "HEAD", [])).toBe("unknown")
    expect(computeDrift(d, "9.2.1", ["latest"])).toBe("unknown")
    expect(computeDrift({ version: "latest", chartVersion: null }, null, ["latest"])).toBe("unknown")
    // K1: chart alone must NEVER yield in-sync when running tags are empty
    expect(computeDrift({ version: "1.20.2", chartVersion: null }, "v1.20.2", [])).toBe("unknown")
    expect(computeDrift(d, "9.2.1", [])).toBe("unknown")
    // K2: mixed rollout is always drift
    expect(computeDrift(d, "9.2.1", ["0.45.1", "0.45.0"], undefined, true)).toBe("drift")
    // K3: component suffix allow-list
    const apisix = { version: "3.15.0", chartVersion: "2.13.0" }
    expect(computeDrift(apisix, "2.13.0", ["3.15.0-ubuntu"], ["-ubuntu"])).toBe("in-sync")
    expect(computeDrift(apisix, "2.13.0", ["3.15.0-rc1"], ["-ubuntu"])).toBe("unknown")
    expect(computeDrift(apisix, "2.13.0", ["3.15.0-preview1"], ["-ubuntu"])).toBe("unknown")
    expect(computeDrift(apisix, "2.13.0", ["3.15.0-custom"], ["-ubuntu"])).toBe("unknown")
  })
})

describe("getOssUpdateStatus — live cluster facts & reviewer reproductions", () => {
  it("reproduces all live facts truthfully: no false drift, real running versions, sibling CVEs, explicit reasons", async () => {
    getArgoAppsOrThrow.mockResolvedValue(liveArgoApps())
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({ rows: liveTrivyRows(), partial: false })
    getOssClusterInventory.mockResolvedValue(liveClusterInventoryFixture())

    const res = await getOssUpdateStatus(NOW)
    expect(res).toMatchObject({ upstreamLookup: "disabled", argocd: "ok", trivy: "ok", k8s: "ok" })

    const byName = new Map(res.rows.map((r) => [r.component, r]))

    expect(byName.get("Kubernetes")).toMatchObject({
      running: { version: "1.35.7", rawTag: "v1.35.7", source: "/version", reason: null },
      drift: "in-sync",
      cves: null,
      cveReason: "no-scan-for-running",
      upstreamReason: "upstream-off",
    })
    expect(byName.get("Gitea")).toMatchObject({
      running: { version: "1.26.2", rawTag: "1.26.2-rootless", source: "deploy/devtools/gitea (gitea)", reason: null },
      drift: "in-sync",
      cves: { critical: 6, high: 99 },
    })
    expect(byName.get("metrics-server")).toMatchObject({
      running: { version: "0.8.1", rawTag: "v0.8.1", source: "deploy/kube-system/metrics-server (metrics-server)", reason: null },
      drift: "in-sync",
      cves: null,
      cveReason: "no-scan-for-running",
    })
    expect(byName.get("kube-vip")).toMatchObject({
      running: { version: "1.1.2", rawTag: "v1.1.2", source: "pod/kube-system/kube-vip-narwhal-master-1 (kube-vip)", reason: null },
      drift: "in-sync",
      cveReason: "no-scan-for-running",
    })

    expect(byName.get("Helm")).toMatchObject({
      running: { version: null, reason: "not-applicable" },
      drift: "not-applicable",
      driftReason: "not-applicable",
      cves: null,
      cveReason: "not-applicable",
    })

    expect(byName.get("Gateway API CRDs")).toMatchObject({
      running: { version: null, reason: "no-permission", reasonDetail: "customresourcedefinitions.apiextensions.k8s.io" },
      drift: "unknown",
      driftReason: "no-permission",
      cves: null,
      cveReason: "not-applicable",
    })

    expect(byName.get("Cilium")).toMatchObject({
      running: { version: "1.19.4", rawTag: "v1.19.4", source: "ds/kube-system/cilium (cilium-agent)" },
      drift: "in-sync",
      cves: null,
      cveReason: "no-scan-for-running",
    })
    expect(byName.get("Cilium")!.running.siblings).toEqual([
      {
        label: "cilium-envoy",
        source: "ds/kube-system/cilium-envoy (cilium-envoy)",
        rawTag: "v1.36.6-1778235340-b87d1e32f522b33bd51701c6476d199326f01496",
        version: null,
        imageTags: ["v1.36.6-1778235340-b87d1e32f522b33bd51701c6476d199326f01496"],
        reason: "tag-unparseable",
        reasonDetail: "v1.36.6-1778235340-b87d1e32f522b33bd51701c6476d199326f01496",
        cves: { critical: 0, high: 6 },
        cveReason: null,
      },
      {
        label: "cilium-operator",
        source: "deploy/kube-system/cilium-operator (cilium-operator)",
        rawTag: "v1.19.4",
        version: "1.19.4",
        imageTags: ["1.19.4"],
        reason: null,
        reasonDetail: null,
        cves: null,
        cveReason: "no-scan-for-running",
      },
    ])

    expect(byName.get("APISIX")).toMatchObject({
      running: { version: "3.15.0", rawTag: "3.15.0-ubuntu", chartVersion: "2.13.0", source: "deploy/platform-system/apisix (apisix)" },
      drift: "in-sync",
      cves: { critical: 0, high: 6 },
    })
    expect(byName.get("APISIX")!.running.siblings).toEqual([
      {
        label: "apisix-ingress-controller",
        source: "deploy/platform-system/apisix-ingress-controller (apisix-ingress-controller)",
        rawTag: "1.8.0",
        version: "1.8.0",
        imageTags: ["1.8.0"],
        reason: null,
        reasonDetail: null,
        cves: { critical: 3, high: 56 },
        cveReason: null,
      },
    ])

    expect(byName.get("Falco")).toMatchObject({
      running: { version: "0.45.1", rawTag: "0.45.1", chartVersion: "9.2.1", source: "ds/security-system/falco (falco)" },
      drift: "in-sync",
      cves: { critical: 1, high: 1 },
    })
    expect(byName.get("Falco")!.running.siblings[0]).toMatchObject({
      label: "falcosidekick",
      rawTag: "2.35.0",
      version: "2.35.0",
      cves: { critical: 3, high: 25 },
      cveReason: null,
    })

    expect(byName.get("Velero")).toMatchObject({
      running: { version: "1.18.1", rawTag: "v1.18.1", chartVersion: "12.0.3", source: "deploy/storage/velero (velero)" },
      drift: "in-sync",
      cves: { critical: 0, high: 109 },
    })
    expect(byName.get("Velero")!.running.siblings.map((s) => [s.label, s.rawTag, s.cves])).toEqual([
      ["velero-plugin-for-aws", "v1.14.1", { critical: 0, high: 40 }],
      ["velero-ui", "0.10.1", { critical: 11, high: 126 }],
    ])

    expect(byName.get("Istio")).toMatchObject({
      running: { version: "1.30.1", rawTag: "1.30.1-distroless", chartVersion: "1.30.1", source: "deploy/istio-system/istiod (discovery)" },
      drift: "in-sync",
      cves: { critical: 0, high: 42 },
    })

    expect(byName.get("csi-driver-nfs")).toMatchObject({
      running: { version: "4.13.2", rawTag: "v4.13.2", source: "deploy/kube-system/csi-nfs-controller (nfs)" },
      drift: "in-sync",
    })

    expect(res.rows.filter((r) => r.drift === "drift")).toEqual([])
  })

  it("K1 reproduction 1: Falco workload absent with matching chart 9.2.1 is unknown, never in-sync", async () => {
    getArgoAppsOrThrow.mockResolvedValue([{ metadata: { name: "falco" }, spec: { source: { targetRevision: "9.2.1" } } }])
    const inv = liveClusterInventoryFixture()
    if (inv.daemonsets.status === "ok") {
      inv.daemonsets.data = inv.daemonsets.data.filter((w) => w.name !== "falco")
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    const falco = res.rows.find((r) => r.component === "Falco")!
    expect(falco).toMatchObject({
      running: { version: null, chartVersion: "9.2.1", reason: "workload-not-found" },
      drift: "unknown",
      driftReason: "workload-not-found",
      cves: null,
      cveReason: "trivy-unavailable",
    })
  })

  it("K1 reproduction 2: container renamed/absent with matching chart is unknown (container-not-found)", async () => {
    getArgoAppsOrThrow.mockResolvedValue([{ metadata: { name: "falco" }, spec: { source: { targetRevision: "9.2.1" } } }])
    const inv = liveClusterInventoryFixture()
    if (inv.pods.status === "ok") {
      inv.pods.data = inv.pods.data.map((p) =>
        p.namespace === "security-system" && p.labels.app === "falco"
          ? { ...p, containers: [{ name: "renamed-falco", image: "docker.io/falcosecurity/falco:0.45.1", running: true }] }
          : p,
      )
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: { version: null, reason: "container-not-found", reasonDetail: "falco" },
      drift: "unknown",
      driftReason: "container-not-found",
    })
  })

  it("K1 reproduction 3: K8s list failure with Trivy tag present stays unknown (never substitutes Trivy for in-sync)", async () => {
    getArgoAppsOrThrow.mockResolvedValue([{ metadata: { name: "falco" }, spec: { source: { targetRevision: "9.2.1" } } }])
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({
      rows: [vuln("docker.io/falcosecurity/falco:0.45.1", 0, 3, "security-system")],
      partial: false,
    })
    // emptyUnavailableInventory() -> all K8s reads fail
    const res = await getOssUpdateStatus(NOW)
    const falco = res.rows.find((r) => r.component === "Falco")!
    expect(falco).toMatchObject({
      running: { version: null, rawTag: null, reason: "k8s-unavailable", trivyHint: "0.45.1" },
      drift: "unknown",
      driftReason: "k8s-unavailable",
      cves: null,
      cveReason: "no-running-image",
    })
  })

  it("K2 reproduction: mixed-image rollout across Running pods -> drift (rollout-mixed); zero running pods -> unknown (no-running-pod)", async () => {
    const inv = liveClusterInventoryFixture()
    if (inv.pods.status === "ok") {
      inv.pods.data.push(
        pod(
          "security-system",
          "falco-pod-2",
          { app: "falco" },
          [["falco", "docker.io/falcosecurity/falco:0.45.2"]],
          [],
          { ownerReferences: [{ kind: "DaemonSet", name: "falco", uid: "security-system-falco-uid", controller: true }] },
        ),
      )
      inv.pods.data = inv.pods.data.filter((p) => !(p.namespace === "monitoring" && p.labels.app === "tempo"))
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: {
        version: null,
        imageTags: ["0.45.1", "0.45.2"],
        reason: "rollout-mixed",
        reasonDetail: "0.45.1, 0.45.2",
      },
      drift: "drift",
      driftReason: "rollout-mixed",
    })
    expect(res.rows.find((r) => r.component === "Tempo")).toMatchObject({
      running: { version: null, reason: "no-running-pod" },
      drift: "unknown",
      driftReason: "no-running-pod",
    })
  })

  it("K2 reproduction: kube-vip checks ALL static pods, flagging mixed pods as drift", async () => {
    const inv = liveClusterInventoryFixture()
    if (inv.pods.status === "ok") {
      inv.pods.data.push(
        pod(
          "kube-system",
          "kube-vip-narwhal-master-2",
          { "k8s-app": "kube-vip" },
          [["kube-vip", "ghcr.io/kube-vip/kube-vip:v1.1.1"]],
          [],
          {
            nodeName: "narwhal-master-2",
            mirrorAnnotation: "2ca7f49e21c8f451169e05eb2bfd10b2",
            ownerReferences: [{ kind: "Node", name: "narwhal-master-2", controller: true }],
          },
        ),
      )
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "kube-vip")).toMatchObject({
      running: {
        version: null,
        imageTags: ["1.1.2", "1.1.1"],
        reason: "rollout-mixed",
        reasonDetail: "1.1.2, 1.1.1",
      },
      drift: "drift",
      driftReason: "rollout-mixed",
    })
  })

  it("K3 reproduction: unrecognized suffixes (-rc1, -preview1, -custom) are shown verbatim and never match", async () => {
    getArgoAppsOrThrow.mockResolvedValue([{ metadata: { name: "apisix" }, spec: { source: { targetRevision: "2.13.0" } } }])
    for (const badTag of ["3.15.0-rc1", "3.15.0-preview1", "3.15.0-custom"]) {
      const inv = liveClusterInventoryFixture()
      if (inv.pods.status === "ok") {
        inv.pods.data = inv.pods.data.map((p) =>
          p.namespace === "platform-system" && p.labels.app === "apisix"
            ? { ...p, containers: [{ name: "apisix", image: `apache/apisix:${badTag}`, running: true }] }
            : p,
        )
      }
      getOssClusterInventory.mockResolvedValue(inv)
      const res = await getOssUpdateStatus(NOW)
      const apisix = res.rows.find((r) => r.component === "APISIX")!
      expect(apisix.running).toMatchObject({
        version: null,
        rawTag: badTag,
        imageTags: [badTag],
        reason: "tag-unparseable",
        reasonDetail: badTag,
      })
      expect(apisix.drift).toBe("unknown")
    }
  })

  it("K4 reproduction: reports for older tag only must NOT attribute CVEs to the running image", async () => {
    const inv = liveClusterInventoryFixture()
    getOssClusterInventory.mockResolvedValue(inv)
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({
      rows: [
        // Only an OLDER tag of falco & falcosidekick exists in Trivy
        vuln("index.docker.io/falcosecurity/falco:0.45.0", 9, 99, "security-system"),
        vuln("index.docker.io/falcosecurity/falcosidekick:2.34.0", 4, 40, "security-system"),
      ],
      partial: false,
    })

    const res = await getOssUpdateStatus(NOW)
    const falco = res.rows.find((r) => r.component === "Falco")!
    expect(falco.cves).toBeNull()
    expect(falco.cveReason).toBe("no-scan-for-running")
    expect(falco.running.siblings[0]).toMatchObject({
      label: "falcosidekick",
      cves: null,
      cveReason: "no-scan-for-running",
    })
  })

  it("K5 reproduction: digest-only sibling shows shortened digest + reason; unreadable sibling stays visible with reason", async () => {
    const dig = "sha256:0123456789abcdef" + "0".repeat(48)
    const inv = liveClusterInventoryFixture()
    if (inv.daemonsets.status === "ok") {
      inv.daemonsets.data = inv.daemonsets.data.filter((w) => w.name !== "istio-cni-node")
    }
    if (inv.pods.status === "ok") {
      inv.pods.data = inv.pods.data.map((p) =>
        p.namespace === "kube-system" && p.labels.app === "cilium-envoy"
          ? { ...p, containers: [{ name: "cilium-envoy", image: `quay.io/cilium/cilium-envoy@${dig}`, running: true }] }
          : p,
      )
    }
    getOssClusterInventory.mockResolvedValue(inv)
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({
      rows: [vuln("quay.io/cilium/cilium-envoy:latest", 1, 3, "kube-system", dig)],
      partial: false,
    })

    const res = await getOssUpdateStatus(NOW)
    const cilium = res.rows.find((r) => r.component === "Cilium")!
    expect(cilium.running.siblings[0]).toMatchObject({
      label: "cilium-envoy",
      rawTag: null,
      version: null,
      imageTags: ["sha256:0123456789ab"],
      reason: "digest-only",
      reasonDetail: "sha256:0123456789ab",
      cves: { critical: 1, high: 3 },
      cveReason: null,
    })

    const istio = res.rows.find((r) => r.component === "Istio")!
    expect(istio.running.siblings[0]).toMatchObject({
      label: "istio-cni-node",
      rawTag: null,
      version: null,
      reason: "workload-not-found",
      cveReason: "no-running-image",
    })
  })

  it("never produces drift from sibling/init images even when sibling tag differs wildly", async () => {
    const inv = liveClusterInventoryFixture()
    if (inv.pods.status === "ok") {
      inv.pods.data = inv.pods.data.map((p) => {
        if (p.namespace === "kube-system" && p.labels.app === "cilium-envoy") {
          return { ...p, containers: [{ name: "cilium-envoy", image: "quay.io/cilium/cilium-envoy:v99.0.0-custom", running: true }] }
        }
        if (p.namespace === "platform-system" && p.labels.app === "apisix-ingress-controller") {
          return { ...p, containers: [{ name: "apisix-ingress-controller", image: "apache/apisix-ingress-controller:9.9.9", running: true }] }
        }
        if (p.namespace === "security-system" && p.labels.app === "falco-falcosidekick") {
          return { ...p, containers: [{ name: "falcosidekick", image: "docker.io/falcosecurity/falcosidekick:8.8.8", running: true }] }
        }
        if (p.namespace === "storage" && p.labels.app === "velero") {
          return { ...p, initContainers: [{ name: "velero-plugin-for-aws", image: "velero/velero-plugin-for-aws:v0.0.1", running: false, completed: true }] }
        }
        return p
      })
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    for (const name of ["Cilium", "APISIX", "Falco", "Velero"]) {
      const row = res.rows.find((r) => r.component === name)!
      expect(row.drift).toBe("in-sync")
      expect(row.cves).toBeNull()
    }
  })

  it("marks unparseable container tag as unknown (tag-unparseable), never in-sync", async () => {
    const inv = emptyUnavailableInventory()
    inv.deployments = { status: "ok", data: [wl("devtools", "gitea", [["gitea", "docker.gitea.com/gitea:latest"]])] }
    inv.replicasets = { status: "ok", data: [rs("devtools", "gitea-rs", "gitea", "devtools-gitea-uid")] }
    inv.pods = {
      status: "ok",
      data: [
        pod(
          "devtools",
          "gitea-0",
          { app: "gitea" },
          [["gitea", "docker.gitea.com/gitea:latest"]],
          [],
          { ownerReferences: [{ kind: "ReplicaSet", name: "gitea-rs", uid: "devtools-gitea-rs-uid", controller: true }] },
        ),
      ],
    }
    getOssClusterInventory.mockResolvedValue(inv)
    const res = await getOssUpdateStatus(NOW)
    const gitea = res.rows.find((r) => r.component === "Gitea")!
    expect(gitea).toMatchObject({
      running: { version: null, rawTag: "latest", reason: "tag-unparseable", reasonDetail: "latest" },
      drift: "unknown",
      driftReason: "tag-unparseable",
    })
  })

  it("L1 reproduction: ignores Job pod with overlapping labels when no workload-owned pod exists (and for static pods without mirror annotation)", async () => {
    const inv = emptyUnavailableInventory()
    inv.deployments = { status: "ok", data: [wl("devtools", "gitea", [["gitea", "docker.gitea.com/gitea:1.26.2-rootless"]])] }
    inv.replicasets = { status: "ok", data: [rs("devtools", "gitea-rs", "gitea", "devtools-gitea-uid")] }
    inv.daemonsets = { status: "ok", data: [wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]])] }
    inv.pods = {
      status: "ok",
      data: [
        // Job pod carrying identical selector labels { app: "gitea" } but owned by a Job, not gitea-rs
        pod(
          "devtools",
          "gitea-smoke-job-xyz",
          { app: "gitea" },
          [["gitea", "docker.gitea.com/gitea:1.26.2-rootless"]],
          [],
          { ownerReferences: [{ kind: "Job", name: "gitea-smoke", controller: true }] },
        ),
        // Unowned pod carrying { app: "falco" }
        pod("security-system", "falco-stray-pod", { app: "falco" }, [["falco", "docker.io/falcosecurity/falco:0.45.1"]]),
        // Non-mirror pod pretending to be kube-vip
        pod("kube-system", "kube-vip-spoof", { "k8s-app": "kube-vip" }, [["kube-vip", "ghcr.io/kube-vip/kube-vip:v1.1.2"]]),
      ],
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "Gitea")).toMatchObject({
      running: { version: null, reason: "no-running-pod" },
      drift: "unknown",
      driftReason: "no-running-pod",
    })
    expect(res.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: { version: null, reason: "no-running-pod" },
      drift: "unknown",
      driftReason: "no-running-pod",
    })
    expect(res.rows.find((r) => r.component === "kube-vip")).toMatchObject({
      running: { version: null, reason: "workload-not-found" },
      drift: "unknown",
      driftReason: "workload-not-found",
    })
  })

  it("L1 RBAC: replicasets no-permission keeps Deployment rows unknown while DaemonSet rows stay in-sync", async () => {
    const inv = liveClusterInventoryFixture()
    inv.replicasets = { status: "no-permission", resource: "replicasets.apps" }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "Gitea")).toMatchObject({
      running: { version: null, reason: "no-permission", reasonDetail: "replicasets.apps" },
      drift: "unknown",
      driftReason: "no-permission",
    })
    expect(res.rows.find((r) => r.component === "Falco")?.drift).toBe("in-sync")
  })

  it("L2 reproduction: truncated bounded lists mark affected rows unknown (list-truncated), never in-sync", async () => {
    const inv = liveClusterInventoryFixture()
    inv.pods = { status: "truncated", resource: "pods" }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.k8s).toBe("partial")
    expect(res.rows.find((r) => r.component === "Kubernetes")?.drift).toBe("in-sync")
    expect(res.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: { version: null, reason: "list-truncated", reasonDetail: "pods" },
      drift: "unknown",
      driftReason: "list-truncated",
    })
    expect(res.rows.find((r) => r.component === "kube-vip")).toMatchObject({
      running: { version: null, reason: "list-truncated", reasonDetail: "pods" },
      drift: "unknown",
      driftReason: "list-truncated",
    })

    // Also when only replicasets is truncated: Deployment rows are unknown, DaemonSet stays in-sync
    const invRs = liveClusterInventoryFixture()
    invRs.replicasets = { status: "truncated", resource: "replicasets.apps" }
    getOssClusterInventory.mockResolvedValue(invRs)
    const resRs = await getOssUpdateStatus(NOW)
    expect(resRs.rows.find((r) => r.component === "Gitea")).toMatchObject({
      running: { version: null, reason: "list-truncated", reasonDetail: "replicasets.apps" },
      drift: "unknown",
      driftReason: "list-truncated",
    })
    expect(resRs.rows.find((r) => r.component === "Falco")?.drift).toBe("in-sync")
  })

  it("L3 reproduction: two running pods with the same tag but different imageID digests surface as rollout-mixed drift with both short digests", async () => {
    const digA = "sha256:aaaaaaaaaaaa" + "0".repeat(52)
    const digB = "sha256:bbbbbbbbbbbb" + "1".repeat(52)
    const inv = emptyUnavailableInventory()
    inv.daemonsets = { status: "ok", data: [wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]])] }
    inv.pods = {
      status: "ok",
      data: [
        pod(
          "security-system",
          "falco-1",
          { app: "falco" },
          [["falco", "docker.io/falcosecurity/falco:0.45.1", digA, true]],
          [],
          { ownerReferences: [{ kind: "DaemonSet", name: "falco", uid: "security-system-falco-uid", controller: true }] },
        ),
        pod(
          "security-system",
          "falco-2",
          { app: "falco" },
          [["falco", "docker.io/falcosecurity/falco:0.45.1", digB, true]],
          [],
          { ownerReferences: [{ kind: "DaemonSet", name: "falco", uid: "security-system-falco-uid", controller: true }] },
        ),
      ],
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: {
        version: null,
        imageTags: ["0.45.1 (sha256:aaaaaaaaaaaa)", "0.45.1 (sha256:bbbbbbbbbbbb)"],
        reason: "rollout-mixed",
        reasonDetail: "0.45.1 (sha256:aaaaaaaaaaaa), 0.45.1 (sha256:bbbbbbbbbbbb)",
      },
      drift: "drift",
      driftReason: "rollout-mixed",
    })
  })

  it("L3 reproduction: CrashLoopBackOff (running: false) is not a running pod; running status-old image contributes its status image", async () => {
    const invCrash = emptyUnavailableInventory()
    invCrash.daemonsets = { status: "ok", data: [wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]])] }
    invCrash.pods = {
      status: "ok",
      data: [
        pod(
          "security-system",
          "falco-crash",
          { app: "falco" },
          [["falco", "docker.io/falcosecurity/falco:0.45.0", undefined, false]],
          [],
          { ownerReferences: [{ kind: "DaemonSet", name: "falco", uid: "security-system-falco-uid", controller: true }] },
        ),
      ],
    }
    getOssClusterInventory.mockResolvedValue(invCrash)
    const resCrash = await getOssUpdateStatus(NOW)
    expect(resCrash.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: { version: null, reason: "no-running-pod" },
      drift: "unknown",
      driftReason: "no-running-pod",
    })

    // Pod running with old status image while workload template spec is new -> reports status image (drift)
    if (invCrash.pods.status === "ok") {
      invCrash.pods.data[0].containers[0].running = true
    }
    const resStatusOld = await getOssUpdateStatus(NOW)
    expect(resStatusOld.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: { version: "0.45.0", rawTag: "0.45.0", reason: null },
      drift: "drift",
    })
  })

  it("L3 reproduction: only Terminating, Pending, Succeeded, or Failed pods yield unknown (no-running-pod)", async () => {
    for (const opts of [
      { phase: "Running", terminating: true },
      { phase: "Pending" },
      { phase: "Succeeded" },
      { phase: "Failed" },
    ]) {
      const inv = emptyUnavailableInventory()
      inv.daemonsets = { status: "ok", data: [wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]])] }
      inv.pods = {
        status: "ok",
        data: [
          pod(
            "security-system",
            "falco-0",
            { app: "falco" },
            [["falco", "docker.io/falcosecurity/falco:0.45.1"]],
            [],
            { ...opts, ownerReferences: [{ kind: "DaemonSet", name: "falco", uid: "security-system-falco-uid", controller: true }] },
          ),
        ],
      }
      getOssClusterInventory.mockResolvedValue(inv)
      const res = await getOssUpdateStatus(NOW)
      expect(res.rows.find((r) => r.component === "Falco")).toMatchObject({
        running: { version: null, reason: "no-running-pod" },
        drift: "unknown",
        driftReason: "no-running-pod",
      })
    }
  })

  it("L3 healthy single-pod and multi-replica (same tag + same imageID digest) matches return in-sync", async () => {
    const dig = "sha256:cccccccccccc" + "2".repeat(52)
    const inv = emptyUnavailableInventory()
    inv.daemonsets = { status: "ok", data: [wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]])] }
    inv.deployments = { status: "ok", data: [wl("devtools", "gitea", [["gitea", "docker.gitea.com/gitea:1.26.2-rootless"]])] }
    inv.replicasets = { status: "ok", data: [rs("devtools", "gitea-rs", "gitea", "devtools-gitea-uid")] }
    inv.pods = {
      status: "ok",
      data: [
        // Healthy single-pod match
        pod(
          "devtools",
          "gitea-0",
          { app: "gitea" },
          [["gitea", "docker.gitea.com/gitea:1.26.2-rootless", dig, true]],
          [],
          { ownerReferences: [{ kind: "ReplicaSet", name: "gitea-rs", uid: "devtools-gitea-rs-uid", controller: true }] },
        ),
        // Healthy multi-replica match (same tag + same digest)
        pod(
          "security-system",
          "falco-1",
          { app: "falco" },
          [["falco", "docker.io/falcosecurity/falco:0.45.1", dig, true]],
          [],
          { ownerReferences: [{ kind: "DaemonSet", name: "falco", uid: "security-system-falco-uid", controller: true }] },
        ),
        pod(
          "security-system",
          "falco-2",
          { app: "falco" },
          [["falco", "docker.io/falcosecurity/falco:0.45.1", dig, true]],
          [],
          { ownerReferences: [{ kind: "DaemonSet", name: "falco", uid: "security-system-falco-uid", controller: true }] },
        ),
      ],
    }
    getOssClusterInventory.mockResolvedValue(inv)

    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "Gitea")).toMatchObject({
      running: { version: "1.26.2", rawTag: "1.26.2-rootless", reason: null },
      drift: "in-sync",
    })
    expect(res.rows.find((r) => r.component === "Falco")).toMatchObject({
      running: { version: "0.45.1", rawTag: "0.45.1", reason: null },
      drift: "in-sync",
    })
  })

  it("resolves Gateway API CRDs if CRD read permission is granted", async () => {
    const inv = emptyUnavailableInventory()
    inv.gatewayApiCrdAnnotations = {
      status: "ok",
      data: { "gateway.networking.k8s.io/bundle-version": "v1.5.1" },
    }
    getOssClusterInventory.mockResolvedValue(inv)
    const res = await getOssUpdateStatus(NOW)
    expect(res.rows.find((r) => r.component === "Gateway API CRDs")).toMatchObject({
      running: { version: "1.5.1", rawTag: "v1.5.1", reason: null },
      drift: "in-sync",
      cveReason: "not-applicable",
    })
  })

  it("survives Argo CD, Trivy, and K8s all being unavailable", async () => {
    getArgoAppsOrThrow.mockRejectedValue(new Error("boom"))
    const res = await getOssUpdateStatus(NOW)
    expect(res).toMatchObject({ argocd: "unavailable", trivy: "unavailable", k8s: "unavailable" })
    expect(res.rows.length).toBeGreaterThan(10)
    expect(res.rows.filter((r) => r.component !== "Helm").every((r) => r.drift === "unknown")).toBe(true)
  })

  it("marks upstreamState unavailable when the enabled lookup returns null", async () => {
    process.env.OSS_UPDATES_UPSTREAM = "true"
    const res = await getOssUpdateStatus(NOW)
    expect(res.upstreamLookup).toBe("enabled")
    expect(res.rows.every((r) => r.upstreamState === "unavailable" && r.upstreamReason === "upstream-failed" && r.upstream === null)).toBe(true)
  })
})
