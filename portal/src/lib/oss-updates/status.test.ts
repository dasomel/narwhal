import { beforeEach, describe, expect, it, vi } from "vitest"
import type { WorkloadVulnRow } from "@/types/security"
import type { OssClusterInventory, OssWorkloadSpecItem } from "../k8s-client"
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

const { computeDrift, getOssUpdateStatus, summarizeImages, summarizeUpstream } = await import("./status")

const NOW = Date.parse("2026-11-23T00:00:00Z")
const rel = (tag: string, ageDaysAgo: number): UpstreamRelease => ({
  tag, publishedAt: new Date(NOW - ageDaysAgo * 86_400_000).toISOString(), url: `https://example.test/${tag}`,
})
const sev = (critical = 0, high = 0) => ({ Critical: critical, High: high, Medium: 0, Low: 0, Unknown: 0 })
const vuln = (image: string, critical = 0, high = 0, namespace = "n"): WorkloadVulnRow => ({
  namespace, kind: "Deployment", name: "w", image, summary: sev(critical, high),
})
const wl = (
  namespace: string,
  name: string,
  containers: Array<[string, string]>,
  initContainers: Array<[string, string]> = [],
): OssWorkloadSpecItem => ({
  namespace,
  name,
  containers: containers.map(([cname, image]) => ({ name: cname, image })),
  initContainers: initContainers.map(([cname, image]) => ({ name: cname, image })),
})

function emptyUnavailableInventory(): OssClusterInventory {
  return {
    serverVersion: { status: "error" },
    deployments: { status: "error" },
    daemonsets: { status: "error" },
    statefulsets: { status: "error" },
    kubeSystemPods: { status: "error" },
    gatewayApiCrdAnnotations: { status: "error" },
  }
}

function liveClusterInventoryFixture(): OssClusterInventory {
  return {
    serverVersion: { status: "ok", data: "v1.35.7" },
    deployments: {
      status: "ok",
      data: [
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
      ],
    },
    daemonsets: {
      status: "ok",
      data: [
        wl("kube-system", "cilium", [["cilium-agent", "quay.io/cilium/cilium:v1.19.4"]]),
        wl("kube-system", "cilium-envoy", [["cilium-envoy", "quay.io/cilium/cilium-envoy:v1.36.6-1778235340-b87d1e32f522b33bd51701c6476d199326f01496"]]),
        wl("platform-system", "metallb-speaker", [["speaker", "quay.io/metallb/speaker:v0.16.1"]]),
        wl("istio-system", "istio-cni-node", [["install-cni", "registry.istio.io/release/install-cni:1.30.1-distroless"]]),
        wl("istio-system", "ztunnel", [["istio-proxy", "registry.istio.io/release/ztunnel:1.30.1"]]),
        wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]]),
      ],
    },
    statefulsets: {
      status: "ok",
      data: [
        wl("storage", "seaweedfs-master", [["seaweedfs", "chrislusf/seaweedfs:4.34"]]),
        wl("iam", "keycloak", [["keycloak", "quay.io/keycloak/keycloak:26.5.7"]]),
        wl("storage", "openbao", [["openbao", "quay.io/openbao/openbao:2.5.4"]]),
        wl("monitoring", "loki", [["loki", "docker.io/grafana/loki:3.7.3"], ["loki-sc-rules", "docker.io/kiwigrid/k8s-sidecar:2.8.1"]]),
        wl("monitoring", "tempo", [["tempo", "docker.io/grafana/tempo:2.9.0"]]),
      ],
    },
    kubeSystemPods: {
      status: "ok",
      data: [
        wl("kube-system", "kube-vip-narwhal-master-1", [["kube-vip", "ghcr.io/kube-vip/kube-vip:v1.1.2"]]),
      ],
    },
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

describe("summarizeImages / computeDrift", () => {
  it("extracts tags (digest-stripped), de-duplicates images and excludes sibling repos", () => {
    const out = summarizeImages(
      [
        vuln("docker.io/falcosecurity/falco:0.45.1@sha256:" + "a".repeat(64), 1, 2),
        vuln("docker.io/falcosecurity/falco:0.45.1@sha256:" + "a".repeat(64), 1, 2),
        vuln("index.docker.io/falcosecurity/falcosidekick:2.35.0", 9, 9),
        vuln("x/other:1.0.0", 9, 9),
      ],
      "falcosecurity/falco",
    )
    expect(out).toEqual({ tags: ["0.45.1"], rawTags: ["0.45.1"], cves: { critical: 1, high: 2 } })
    expect(summarizeImages([vuln("x/y:1.0.0")], "nomatch")).toEqual({ tags: [], rawTags: [], cves: null })
    expect(summarizeImages([vuln("x/y:1.0.0")], undefined)).toEqual({ tags: [], rawTags: [], cves: null })
  })

  it("drift: in-sync / drift / unknown; unparseable tag is unknown, never in-sync", () => {
    const d = { version: "0.45.1", chartVersion: "9.2.1" }
    expect(computeDrift(d, "9.2.1", ["0.45.1"])).toBe("in-sync")
    expect(computeDrift(d, "9.2.0", ["0.45.1"])).toBe("drift")
    expect(computeDrift(d, null, ["0.45.0"])).toBe("drift")
    expect(computeDrift(d, "HEAD", [])).toBe("unknown")
    expect(computeDrift(d, "9.2.1", ["latest"])).toBe("unknown")
    expect(computeDrift({ version: "latest", chartVersion: null }, null, ["latest"])).toBe("unknown")
    expect(computeDrift({ version: "1.20.2", chartVersion: null }, "v1.20.2", [])).toBe("in-sync")
  })
})

describe("getOssUpdateStatus — live cluster symptoms & sibling isolation", () => {
  it("reproduces all live facts truthfully: no false drift, real running versions, explicit unknown reasons", async () => {
    getArgoAppsOrThrow.mockResolvedValue(liveArgoApps())
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({ rows: liveTrivyRows(), partial: false })
    getOssClusterInventory.mockResolvedValue(liveClusterInventoryFixture())

    const res = await getOssUpdateStatus(NOW)
    expect(res).toMatchObject({ upstreamLookup: "disabled", argocd: "ok", trivy: "ok", k8s: "ok" })

    const byName = new Map(res.rows.map((r) => [r.component, r]))

    // Previously unknown -> now truth
    expect(byName.get("Kubernetes")).toMatchObject({
      running: { version: "1.35.7", rawTag: "v1.35.7", source: "/version", reason: null },
      drift: "in-sync",
      cves: null,
      cveReason: "no-scan",
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
      cveReason: "no-scan",
    })
    expect(byName.get("kube-vip")).toMatchObject({
      running: { version: "1.1.2", rawTag: "v1.1.2", source: "pod/kube-system/kube-vip-narwhal-master-1 (kube-vip)", reason: null },
      drift: "in-sync",
      cveReason: "no-scan",
    })

    // Helm -> explicit CLI not-applicable
    expect(byName.get("Helm")).toMatchObject({
      running: { version: null, reason: "not-applicable" },
      drift: "not-applicable",
      driftReason: "not-applicable",
      cves: null,
      cveReason: "not-applicable",
    })

    // Gateway API CRDs -> honest RBAC no-permission (not widened)
    expect(byName.get("Gateway API CRDs")).toMatchObject({
      running: { version: null, reason: "no-permission", reasonDetail: "customresourcedefinitions.apiextensions.k8s.io" },
      drift: "unknown",
      driftReason: "no-permission",
      cves: null,
      cveReason: "not-applicable",
    })

    // Previously false drift -> now in-sync with separate sibling sub-rows & isolated CVEs
    expect(byName.get("Cilium")).toMatchObject({
      running: { version: "1.19.4", rawTag: "v1.19.4", source: "ds/kube-system/cilium (cilium-agent)" },
      drift: "in-sync",
      cves: null,
      cveReason: "no-scan",
    })
    expect(byName.get("Cilium")!.running.siblings).toEqual([
      {
        label: "cilium-envoy",
        source: "ds/kube-system/cilium-envoy (cilium-envoy)",
        rawTag: "v1.36.6-1778235340-b87d1e32f522b33bd51701c6476d199326f01496",
        version: "1.36.6",
      },
      {
        label: "cilium-operator",
        source: "deploy/kube-system/cilium-operator (cilium-operator)",
        rawTag: "v1.19.4",
        version: "1.19.4",
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
      },
    ])

    expect(byName.get("Falco")).toMatchObject({
      running: { version: "0.45.1", rawTag: "0.45.1", chartVersion: "9.2.1", source: "ds/security-system/falco (falco)" },
      drift: "in-sync",
      cves: { critical: 1, high: 1 },
    })
    expect(byName.get("Falco")!.running.siblings).toEqual([
      {
        label: "falcosidekick",
        source: "deploy/security-system/falco-falcosidekick (falcosidekick)",
        rawTag: "2.35.0",
        version: "2.35.0",
      },
    ])

    expect(byName.get("Velero")).toMatchObject({
      running: { version: "1.18.1", rawTag: "v1.18.1", chartVersion: "12.0.3", source: "deploy/storage/velero (velero)" },
      drift: "in-sync",
      cves: { critical: 0, high: 109 },
    })
    expect(byName.get("Velero")!.running.siblings.map((s) => [s.label, s.rawTag])).toEqual([
      ["velero-plugin-for-aws", "v1.14.1"],
      ["velero-ui", "0.10.1"],
    ])

    // Istio CVE join finds registry.istio.io/release/pilot
    expect(byName.get("Istio")).toMatchObject({
      running: { version: "1.30.1", rawTag: "1.30.1-distroless", chartVersion: "1.30.1", source: "deploy/istio-system/istiod (discovery)" },
      drift: "in-sync",
      cves: { critical: 0, high: 42 },
    })

    // CSI sidecars do not cause drift
    expect(byName.get("csi-driver-nfs")).toMatchObject({
      running: { version: "4.13.2", rawTag: "v4.13.2", source: "deploy/kube-system/csi-nfs-controller (nfs)" },
      drift: "in-sync",
    })

    // Zero false drifts across the live fixture
    expect(res.rows.filter((r) => r.drift === "drift")).toEqual([])
  })

  it("never produces drift from sibling/init images even when sibling tag differs wildly", async () => {
    const inv = liveClusterInventoryFixture()
    inv.daemonsets = {
      status: "ok",
      data: [
        wl("kube-system", "cilium", [["cilium-agent", "quay.io/cilium/cilium:v1.19.4"]]),
        wl("kube-system", "cilium-envoy", [["cilium-envoy", "quay.io/cilium/cilium-envoy:v99.0.0-custom"]]),
        wl("security-system", "falco", [["falco", "docker.io/falcosecurity/falco:0.45.1"]]),
      ],
    }
    inv.deployments = {
      status: "ok",
      data: [
        wl("platform-system", "apisix", [["apisix", "apache/apisix:3.15.0-ubuntu"]]),
        wl("platform-system", "apisix-ingress-controller", [["apisix-ingress-controller", "apache/apisix-ingress-controller:9.9.9"]]),
        wl("security-system", "falco-falcosidekick", [["falcosidekick", "docker.io/falcosecurity/falcosidekick:8.8.8"]]),
        wl("storage", "velero", [["velero", "docker.io/velero/velero:v1.18.1"]], [["velero-plugin-for-aws", "velero/velero-plugin-for-aws:v0.0.1"]]),
      ],
    }
    getOssClusterInventory.mockResolvedValue(inv)
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({
      rows: [
        vuln("quay.io/cilium/cilium-envoy:v99.0.0-custom", 5, 5, "kube-system"),
        vuln("index.docker.io/apache/apisix-ingress-controller:9.9.9", 5, 5, "platform-system"),
        vuln("index.docker.io/falcosecurity/falcosidekick:8.8.8", 5, 5, "security-system"),
        vuln("index.docker.io/velero/velero-plugin-for-aws:v0.0.1", 5, 5, "storage"),
      ],
      partial: false,
    })

    const res = await getOssUpdateStatus(NOW)
    for (const name of ["Cilium", "APISIX", "Falco", "Velero"]) {
      const row = res.rows.find((r) => r.component === name)!
      expect(row.drift).toBe("in-sync")
      expect(row.cves).toBeNull()
    }
  })

  it("marks unparseable container tag as unknown (tag-unparseable), never in-sync", async () => {
    const inv = emptyUnavailableInventory()
    inv.deployments = {
      status: "ok",
      data: [wl("devtools", "gitea", [["gitea", "docker.gitea.com/gitea:latest"]])],
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

  it("air-gap default: no upstream calls, Trivy fallback still works when K8s unreachable", async () => {
    getArgoAppsOrThrow.mockResolvedValue([{ metadata: { name: "falco" }, spec: { source: { targetRevision: "9.2.1" } } }])
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({ rows: [vuln("docker.io/falcosecurity/falco:0.45.1", 0, 3)], partial: false })
    const res = await getOssUpdateStatus(NOW)
    expect(getUpstreamReleases).not.toHaveBeenCalled()
    expect(res).toMatchObject({ upstreamLookup: "disabled", argocd: "ok", trivy: "ok", k8s: "unavailable", requestMode: "copy", coolingDays: 7 })
    const falco = res.rows.find((r) => r.component === "Falco")!
    expect(falco).toMatchObject({ upstreamState: "disabled", upstreamReason: "upstream-off", upstream: null, drift: "in-sync", cves: { critical: 0, high: 3 } })
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
