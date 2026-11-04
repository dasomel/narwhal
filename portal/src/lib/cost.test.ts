import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import type { NamespaceInfo } from "./k8s-client"

const cacheStore = new Map<string, unknown>()

vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async (key: string) => cacheStore.get(key) ?? null),
  cacheGetWithMeta: vi.fn(async (key: string) => {
    const value = cacheStore.get(key)
    return value === undefined ? null : { value, cachedAt: null, ageSeconds: null }
  }),
  cacheSet: vi.fn(async (key: string, value: unknown) => {
    cacheStore.set(key, value)
  }),
}))

vi.mock("./k8s-client", () => ({
  getNamespaces: vi.fn(),
}))

const { cacheSet } = await import("./valkey")
const { getNamespaces } = await import("./k8s-client")
const { getEffectiveScope } = await import("./scope")
const { scopeFingerprint } = await import("./role-filter")
const { getCost, getCostByService, getCostTrend } = await import("./cost")

const testNamespaces: NamespaceInfo[] = [
  { name: "platform-system", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
  { name: "frontend-app", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
]

function jsonResponse(result: unknown) {
  return Response.json({ data: { result } })
}

describe("cost cache multi-cluster and cross-scope isolation (Portal #64)", () => {
  const originalEnv = { ...process.env }

  beforeEach(() => {
    process.env = { ...originalEnv }
    cacheStore.clear()
    vi.clearAllMocks()
    vi.mocked(getNamespaces).mockResolvedValue(testNamespaces)

    // Standard fake Prometheus response
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("query_range")) {
        return jsonResponse([
          {
            metric: {},
            values: [
              [1700000000, "2.0"],
              [1700086400, "2.5"],
            ],
          },
        ])
      }
      if (url.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([
          {
            metric: {
              namespace: "platform-system",
              label_app_kubernetes_io_instance: "platform-svc",
              pod: "platform-pod-0",
            },
            value: [0, "1.5"],
          },
        ])
      }
      if (url.includes("container_memory_working_set_bytes")) {
        return jsonResponse([
          {
            metric: {
              namespace: "platform-system",
              label_app_kubernetes_io_instance: "platform-svc",
              pod: "platform-pod-0",
            },
            value: [0, "2000000000"],
          },
        ])
      }
      if (url.includes("kubelet_volume_stats_used_bytes")) {
        return jsonResponse([
          {
            metric: { namespace: "platform-system" },
            value: [0, "1000000000"],
          },
        ])
      }
      return jsonResponse([])
    }))
  })

  afterEach(() => {
    process.env = originalEnv
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("scopeFingerprint isolates digests when only clusterId differs", () => {
    const groups = ["developer"]
    const teams = ["platform-team"]
    const namespaces = ["platform-system"]

    const fpClusterA = scopeFingerprint(groups, teams, "cluster-alpha", namespaces)
    const fpClusterB = scopeFingerprint(groups, teams, "cluster-beta", namespaces)

    expect(fpClusterA).not.toBe(fpClusterB)
    expect(fpClusterA).toHaveLength(32)
    expect(fpClusterB).toHaveLength(32)

    // Also holds for unmapped scopes with no namespaces
    const emptyA = scopeFingerprint(groups, teams, "cluster-alpha")
    const emptyB = scopeFingerprint(groups, teams, "cluster-beta")
    expect(emptyA).not.toBe(emptyB)
  })

  it("produces distinct cost cache keys for namespace queries across different clusters", async () => {
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scopeClusterA = await getEffectiveScope(session, "cluster-alpha")
    const scopeClusterB = await getEffectiveScope(session, "cluster-beta")

    expect(scopeClusterA.clusterId).toBe("cluster-alpha")
    expect(scopeClusterB.clusterId).toBe("cluster-beta")
    expect(scopeClusterA.fingerprint).not.toBe(scopeClusterB.fingerprint)

    await getCost("namespace", scopeClusterA)
    await getCost("namespace", scopeClusterB)

    const setCalls = vi.mocked(cacheSet).mock.calls
    const namespaceKeys = setCalls
      .map(([key]) => key)
      .filter((k) => k.startsWith("cost:v2:namespace:"))

    expect(namespaceKeys).toHaveLength(2)
    expect(namespaceKeys[0]).not.toBe(namespaceKeys[1])
    expect(namespaceKeys[0]).toContain(scopeClusterA.fingerprint)
    expect(namespaceKeys[1]).toContain(scopeClusterB.fingerprint)
  })

  it("produces distinct cost cache keys for service queries across different clusters", async () => {
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scopeClusterA = await getEffectiveScope(session, "cluster-alpha")
    const scopeClusterB = await getEffectiveScope(session, "cluster-beta")

    await getCostByService("platform-svc", scopeClusterA, "platform-system")
    await getCostByService("platform-svc", scopeClusterB, "platform-system")

    const setCalls = vi.mocked(cacheSet).mock.calls
    const serviceKeys = setCalls
      .map(([key]) => key)
      .filter((k) => k.startsWith("cost:service:v2:"))

    expect(serviceKeys).toHaveLength(2)
    expect(serviceKeys[0]).not.toBe(serviceKeys[1])
    expect(serviceKeys[0]).toContain(scopeClusterA.fingerprint)
    expect(serviceKeys[1]).toContain(scopeClusterB.fingerprint)
  })

  it("produces distinct cost cache keys for trend queries across different clusters", async () => {
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scopeClusterA = await getEffectiveScope(session, "cluster-alpha")
    const scopeClusterB = await getEffectiveScope(session, "cluster-beta")

    await getCostTrend("namespace", "platform-system", 7, scopeClusterA)
    await getCostTrend("namespace", "platform-system", 7, scopeClusterB)

    const setCalls = vi.mocked(cacheSet).mock.calls
    const trendKeys = setCalls
      .map(([key]) => key)
      .filter((k) => k.startsWith("cost:trend:v2:namespace:"))

    expect(trendKeys).toHaveLength(2)
    expect(trendKeys[0]).not.toBe(trendKeys[1])
    expect(trendKeys[0]).toContain(scopeClusterA.fingerprint)
    expect(trendKeys[1]).toContain(scopeClusterB.fingerprint)
  })

  it("ensures a namespace cost value cached for cluster A is never returned for cluster B", async () => {
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scopeClusterA = await getEffectiveScope(session, "cluster-alpha")
    const scopeClusterB = await getEffectiveScope(session, "cluster-beta")

    // 1. Cluster A queries and populates Valkey cache with Cluster A specific values
    const clusterAResult = await getCost("namespace", scopeClusterA)
    expect(clusterAResult.items.length).toBeGreaterThan(0)
    expect(cacheStore.size).toBe(1)

    // 2. Mock fetch to return different metrics for Cluster B. All three required
    // queries (cpu/mem/storage) must resolve non-empty, or the new empty-vector
    // classification (portal#64 Codex 리뷰 #1) marks this "partial" and skips
    // caching — this test is about per-cluster cache isolation, not telemetry.
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([
          {
            metric: { namespace: "platform-system" },
            value: [0, "10.0"], // Distinct from cluster A's 1.5
          },
        ])
      }
      if (url.includes("container_memory_working_set_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "2000000000"] }])
      }
      if (url.includes("kubelet_volume_stats_used_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "500000000"] }])
      }
      return jsonResponse([])
    }))

    // 3. Cluster B queries cost for the same namespace under its own scope
    const clusterBResult = await getCost("namespace", scopeClusterB)

    // Cluster B must get fresh Prometheus results computed for Cluster B, NOT Cluster A's cached items
    expect(clusterBResult.items).not.toEqual(clusterAResult.items)
    const clusterBCpu = clusterBResult.items.find((i) => i.id === "platform-system")?.cpu.cores
    const clusterACpu = clusterAResult.items.find((i) => i.id === "platform-system")?.cpu.cores
    expect(clusterBCpu).toBe(10)
    expect(clusterACpu).toBe(1.5)

    // Cache now holds two separate entries
    expect(cacheStore.size).toBe(2)

    // 4. Repeated query for Cluster A still returns Cluster A's cached value without calling fetch
    const fetchSpy = vi.mocked(global.fetch)
    fetchSpy.mockClear()
    const cachedClusterA = await getCost("namespace", scopeClusterA)
    expect(cachedClusterA.items).toEqual(clusterAResult.items)
    expect(cachedClusterA.telemetry.queriedAt).toBe(clusterAResult.telemetry.queriedAt)
    expect(cachedClusterA.freshnessSource).toBe("cache")
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("ensures a service detail cost value cached for cluster A is never returned for cluster B", async () => {
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scopeClusterA = await getEffectiveScope(session, "cluster-alpha")
    const scopeClusterB = await getEffectiveScope(session, "cluster-beta")

    // 1. Cluster A populates cache
    const resultA = await getCostByService("platform-svc", scopeClusterA, "platform-system")
    expect("serviceId" in resultA).toBe(true)

    // 2. Change Prometheus metrics for Cluster B
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([
          {
            metric: {
              namespace: "platform-system",
              label_app_kubernetes_io_instance: "platform-svc",
              pod: "cluster-b-pod",
            },
            value: [0, "8.0"],
          },
        ])
      }
      return jsonResponse([])
    }))

    // 3. Cluster B queries cost for the same service
    const resultB = await getCostByService("platform-svc", scopeClusterB, "platform-system")
    expect("serviceId" in resultB).toBe(true)
    if ("serviceId" in resultA && "serviceId" in resultB) {
      expect(resultB.cpu.cores).toBe(8)
      expect(resultA.cpu.cores).toBe(1.5)
      expect(resultB.totalHourly).not.toBe(resultA.totalHourly)
    }

    // 4. Repeated query for Cluster A hits cache
    const fetchSpy = vi.mocked(global.fetch)
    fetchSpy.mockClear()
    const cachedA = await getCostByService("platform-svc", scopeClusterA, "platform-system")
    expect(cachedA).toEqual(resultA)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("ensures a cost trend value cached for cluster A is never returned for cluster B", async () => {
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scopeClusterA = await getEffectiveScope(session, "cluster-alpha")
    const scopeClusterB = await getEffectiveScope(session, "cluster-beta")

    // 1. Cluster A queries trend and caches result
    const trendA = await getCostTrend("namespace", "platform-system", 7, scopeClusterA)
    expect(trendA.points.length).toBeGreaterThan(0)

    // 2. Mock fetch to return different trend data for Cluster B
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([
      {
        metric: {},
        values: [
          [1700000000, "99.0"],
        ],
      },
    ])))

    // 3. Cluster B queries trend
    const trendB = await getCostTrend("namespace", "platform-system", 7, scopeClusterB)
    expect(trendB.points).not.toEqual(trendA.points)
    expect(trendB.points[0].total).toBeGreaterThan(trendA.points[0].total)

    // 4. Cluster A returns cached trend without calling fetch
    const fetchSpy = vi.mocked(global.fetch)
    fetchSpy.mockClear()
    const cachedTrendA = await getCostTrend("namespace", "platform-system", 7, scopeClusterA)
    expect(cachedTrendA).toEqual(trendA)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  // The three tests above vary clusterId only. These vary team only (same cluster,
  // same groups) — a fingerprint that dropped the team/scope dimension would still
  // pass the cluster-only tests above, since clusterId alone would keep separating
  // those entries.
  it("ensures a namespace cost value cached for platform-team is never returned for frontend-team on the same cluster", async () => {
    const scopePlatform = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] }, "cluster-alpha")
    const scopeFrontend = await getEffectiveScope({ groups: ["developer"], teams: ["frontend-team"] }, "cluster-alpha")
    expect(scopePlatform.clusterId).toBe(scopeFrontend.clusterId)
    expect(scopePlatform.fingerprint).not.toBe(scopeFrontend.fingerprint)

    // 1. platform-team queries and populates the cache with platform-system's cost
    const platformResult = await getCost("namespace", scopePlatform)
    expect(platformResult.items.map((i) => i.id)).toEqual(["platform-system"])

    // 2. frontend-team queries the SAME cluster, same mocked Prometheus data (which
    // only reports platform-system). frontend-team cannot see platform-system, so a
    // correctly-scoped result has no items — anything else (in particular
    // platform-team's cached platform-system item) would mean the team dimension was
    // dropped from the cache key.
    const frontendResult = await getCost("namespace", scopeFrontend)
    expect(frontendResult.items).toEqual([])

    const setKeys = vi.mocked(cacheSet).mock.calls.map(([key]) => key).filter((k) => k.startsWith("cost:v2:namespace:"))
    expect(new Set(setKeys).size).toBe(2)

    // 3. Repeated query for platform-team still returns platform-team's cached value.
    const cachedPlatform = await getCost("namespace", scopePlatform)
    expect(cachedPlatform.items).toEqual(platformResult.items)
    expect(cachedPlatform.freshnessSource).toBe("cache")
  })

  it("ensures a service detail cost value cached for platform-team is never returned for frontend-team on the same cluster", async () => {
    const scopePlatform = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] }, "cluster-alpha")
    const scopeFrontend = await getEffectiveScope({ groups: ["developer"], teams: ["frontend-team"] }, "cluster-alpha")
    expect(scopePlatform.fingerprint).not.toBe(scopeFrontend.fingerprint)

    // 1. platform-team populates the cache
    const resultA = await getCostByService("platform-svc", scopePlatform, "platform-system")
    expect("serviceId" in resultA).toBe(true)

    // 2. Swap the mocked metrics before the second call — a real cache-key collision
    // (team dimension dropped from the fingerprint) would still return resultA's
    // stale value here despite the new mock. Both cpu and memory must resolve
    // non-empty (cpuByServiceQuery/topPodCpuQuery and memByServiceQuery/topPodMemQuery
    // share these URL substrings), or telemetry.state !== "ok" and this call would
    // skip caching entirely, same as getCost's empty-vector classification.
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([
          {
            metric: {
              namespace: "platform-system",
              label_app_kubernetes_io_instance: "platform-svc",
              pod: "frontend-team-pod",
            },
            value: [0, "8.0"],
          },
        ])
      }
      if (url.includes("container_memory_working_set_bytes")) {
        return jsonResponse([
          {
            metric: {
              namespace: "platform-system",
              label_app_kubernetes_io_instance: "platform-svc",
              pod: "frontend-team-pod",
            },
            value: [0, "3000000000"],
          },
        ])
      }
      return jsonResponse([])
    }))

    const resultB = await getCostByService("platform-svc", scopeFrontend, "platform-system")
    expect("serviceId" in resultB).toBe(true)
    if ("serviceId" in resultA && "serviceId" in resultB) {
      expect(resultB.cpu.cores).toBe(8)
      expect(resultA.cpu.cores).toBe(1.5)
    }

    const setKeys = vi.mocked(cacheSet).mock.calls.map(([key]) => key).filter((k) => k.startsWith("cost:service:v2:"))
    expect(new Set(setKeys).size).toBe(2)

    // 3. Repeated query for platform-team hits its own cache entry, not frontend-team's.
    const fetchSpy = vi.mocked(global.fetch)
    fetchSpy.mockClear()
    const cachedA = await getCostByService("platform-svc", scopePlatform, "platform-system")
    expect(cachedA).toEqual(resultA)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("ensures a cost trend value cached for platform-team is never returned for frontend-team on the same cluster", async () => {
    const scopePlatform = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] }, "cluster-alpha")
    const scopeFrontend = await getEffectiveScope({ groups: ["developer"], teams: ["frontend-team"] }, "cluster-alpha")
    expect(scopePlatform.fingerprint).not.toBe(scopeFrontend.fingerprint)

    // 1. platform-team queries trend and caches the result
    const trendA = await getCostTrend("namespace", "platform-system", 7, scopePlatform)
    expect(trendA.points.length).toBeGreaterThan(0)

    // 2. Swap the mocked trend data before the second call
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([
      {
        metric: {},
        values: [
          [1700000000, "77.0"],
        ],
      },
    ])))

    // 3. frontend-team queries the same cluster/namespace/days
    const trendB = await getCostTrend("namespace", "platform-system", 7, scopeFrontend)
    expect(trendB.points).not.toEqual(trendA.points)

    const setKeys = vi.mocked(cacheSet).mock.calls.map(([key]) => key).filter((k) => k.startsWith("cost:trend:v2:namespace:"))
    expect(new Set(setKeys).size).toBe(2)

    // 4. platform-team returns its own cached trend without calling fetch
    const fetchSpy = vi.mocked(global.fetch)
    fetchSpy.mockClear()
    const cachedTrendA = await getCostTrend("namespace", "platform-system", 7, scopePlatform)
    expect(cachedTrendA).toEqual(trendA)
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})

describe("cost exclusions and telemetry (Portal #64 AC3/AC4)", () => {
  const originalEnv = { ...process.env }

  beforeEach(() => {
    process.env = { ...originalEnv }
    cacheStore.clear()
    vi.clearAllMocks()
    vi.mocked(getNamespaces).mockResolvedValue(testNamespaces)
  })

  afterEach(() => {
    process.env = originalEnv
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  // Service scope fetches per-pod usage plus kube_pod_labels / kube_pod_owner /
  // kube_replicaset_owner. Route on the metric name so each concurrent query gets
  // its own deterministic fixture.
  function routedFetch(overrides: Record<string, unknown[]>) {
    return vi.fn(async (url: string) => {
      const decoded = decodeURIComponent(url)
      if (decoded.includes("kube_replicaset_owner")) return jsonResponse(overrides.rsOwner ?? [])
      if (decoded.includes("kube_pod_owner")) return jsonResponse(overrides.owner ?? [])
      if (decoded.includes("kube_pod_labels")) return jsonResponse(overrides.labels ?? [])
      if (decoded.includes("container_cpu_usage_seconds_total")) return jsonResponse(overrides.cpu ?? [])
      if (decoded.includes("container_memory_working_set_bytes")) return jsonResponse(overrides.mem ?? [])
      return jsonResponse([])
    })
  }

  it("service scope attributes unlabeled pods by fallback and keeps totals consistent", async () => {
    vi.stubGlobal("fetch", routedFetch({
      cpu: [
        { metric: { namespace: "platform-system", pod: "web-1" }, value: [0, "1"] },
        { metric: { namespace: "platform-system", pod: "api-7d9f8c6b5-x2k4q" }, value: [0, "2"] },
        { metric: { namespace: "platform-system", pod: "" }, value: [0, "0.5"] },
      ],
      mem: [
        { metric: { namespace: "platform-system", pod: "web-1" }, value: [0, "1000000000"] },
        { metric: { namespace: "platform-system", pod: "api-7d9f8c6b5-x2k4q" }, value: [0, "2000000000"] },
        { metric: { namespace: "platform-system", pod: "" }, value: [0, "500000000"] },
      ],
      labels: [{ metric: { namespace: "platform-system", pod: "web-1", label_app_kubernetes_io_instance: "web" }, value: [0, "1"] }],
      owner: [{ metric: { namespace: "platform-system", pod: "api-7d9f8c6b5-x2k4q", owner_kind: "ReplicaSet", owner_name: "api-7d9f8c6b5" }, value: [0, "1"] }],
      rsOwner: [{ metric: { namespace: "platform-system", replicaset: "api-7d9f8c6b5", owner_kind: "Deployment", owner_name: "api" }, value: [0, "1"] }],
    }))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const result = await getCost("service", scope)

    expect(result.telemetry.state).toBe("ok")
    const bySource = Object.fromEntries(result.items.map((i) => [i.source, i]))
    expect(bySource.instance.id).toBe("web")
    expect(bySource.owner).toMatchObject({ name: "api", kind: "Deployment", namespace: "platform-system", pods: 1 })
    expect(bySource.unclassified).toMatchObject({ pods: 1, totalHourly: 0.0225 })
    const sum = result.items.reduce((s, i) => s + i.totalHourly, 0)
    // 3.5 cores * 0.04 + 3.5 GB * 0.005 = 0.1575
    expect(sum).toBeCloseTo(0.1575, 3)
    expect(result.exclusions?.attribution).toEqual({
      computable: true,
      fallbackWorkloads: 1,
      fallbackPods: 1,
      fallbackHourly: 0.09,
      unclassifiedPods: 1,
      unclassifiedHourly: 0.0225,
    })
  })

  it("service scope still attributes via owner when kube_pod_labels is empty (no KSM allowlist)", async () => {
    vi.stubGlobal("fetch", routedFetch({
      cpu: [{ metric: { namespace: "iam", pod: "keycloak-0" }, value: [0, "1"] }],
      mem: [{ metric: { namespace: "iam", pod: "keycloak-0" }, value: [0, "1000000000"] }],
      owner: [{ metric: { namespace: "iam", pod: "keycloak-0", owner_kind: "StatefulSet", owner_name: "keycloak" }, value: [0, "1"] }],
    }))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })
    scope.all = true

    const result = await getCost("service", scope)

    expect(result.items).toHaveLength(1)
    expect(result.items[0]).toMatchObject({ source: "owner", kind: "StatefulSet", name: "keycloak" })
  })

  it("service scope returns the empty notice and no rows when no usage exists", async () => {
    vi.stubGlobal("fetch", routedFetch({}))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const result = await getCost("service", scope)

    expect(result.items).toEqual([])
    expect(result.telemetry.state).toBe("empty")
    expect(result.exclusions?.attribution).toMatchObject({ computable: true, fallbackWorkloads: 0, unclassifiedPods: 0 })
  })

  it("service scope marks attribution computable=false and skips caching when the owner query fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const decoded = decodeURIComponent(url)
      if (decoded.includes("kube_pod_owner")) throw new Error("Prometheus query failed: 500")
      if (decoded.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([{ metric: { namespace: "platform-system", pod: "web-1" }, value: [0, "1"] }])
      }
      if (decoded.includes("container_memory_working_set_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system", pod: "web-1" }, value: [0, "1000000000"] }])
      }
      return jsonResponse([])
    }))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const result = await getCost("service", scope)

    expect(result.telemetry.state).toBe("ok")
    expect(result.exclusions?.attribution?.computable).toBe(false)
    expect(cacheSet).not.toHaveBeenCalled()
  })

  it("AC3: getCost('namespace') reports total unallocated storage once in exclusions (not duplicated per item)", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "2"] }])
      }
      if (url.includes("container_memory_working_set_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "4000000000"] }])
      }
      if (url.includes("kubelet_volume_stats_used_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "1000000000"] }])
      }
      return jsonResponse([])
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCost("namespace", scope)

    const item = result.items.find((i) => i.id === "platform-system")
    expect(item?.storage.gb).toBeGreaterThan(0)
    expect((item as unknown as Record<string, unknown>).unallocatedStorage).toBeUndefined()
    expect(result.exclusions?.unallocatedStorage).toEqual({
      computable: true,
      gb: item?.storage.gb,
      hourly: item?.storage.hourly,
    })
  })

  it("AC3/AC6: getCost('namespace') marks unallocatedStorage.computable=false when the storage query fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("kubelet_volume_stats_used_bytes")) {
        throw new Error("Prometheus query failed: 500")
      }
      if (url.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "2"] }])
      }
      if (url.includes("container_memory_working_set_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "4000000000"] }])
      }
      return jsonResponse([])
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCost("namespace", scope)

    expect(result.telemetry.state).toBe("partial")
    expect(result.exclusions?.unallocatedStorage).toEqual({ computable: false, gb: null, hourly: null })
  })

  it("AC4: getCost('cluster') reports telemetry.state='unavailable' and skips caching when Prometheus is entirely down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("ECONNREFUSED")
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCost("cluster", scope)

    expect(result.items).toEqual([])
    expect(result.telemetry.state).toBe("unavailable")
    expect(result.telemetry.source).toBe("none")
    expect(result.notice).toBeTruthy()
    expect(cacheSet).not.toHaveBeenCalled()
  })

  it.each([429, 503])("does not retry Prometheus %s responses for cost telemetry", async (status) => {
    const fetchSpy = vi.fn(async () => Response.json({}, { status }))
    vi.stubGlobal("fetch", fetchSpy)
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const result = await getCost("cluster", scope)

    expect(result.telemetry.state).toBe("unavailable")
    expect(fetchSpy).toHaveBeenCalledTimes(3)
  })

  it("maps a Prometheus timeout to unavailable telemetry and the existing timeout notice", async () => {
    vi.useFakeTimers()
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true })
    })))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const resultPromise = getCost("cluster", scope)
    await vi.advanceTimersByTimeAsync(5000)
    const result = await resultPromise

    expect(result.telemetry.state).toBe("unavailable")
    expect(result.notice).toContain("응답 시간 초과(5s)")
    expect(cacheSet).not.toHaveBeenCalled()
  })

  it("AC4: getCost('cluster') reports telemetry.state='partial' when only some Prometheus queries fail", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("container_memory_working_set_bytes")) {
        throw new Error("Prometheus query failed: 500")
      }
      if (url.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "2"] }])
      }
      if (url.includes("kubelet_volume_stats_used_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system" }, value: [0, "1000000000"] }])
      }
      return jsonResponse([])
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCost("cluster", scope)

    expect(result.telemetry.state).toBe("partial")
    expect(result.telemetry.reason).toBeTruthy()
    // Degraded but not discarded: the item is still computed from whatever succeeded.
    expect(result.items[0].cpu.cores).toBeGreaterThan(0)
    expect(result.items[0].memory.gb).toBe(0)
    // 크리틱 리뷰 #1: partial은 unavailable과 마찬가지로 캐시하지 않는다 — degraded
    // 값을 TTL 동안 재사용하면 Prometheus가 바로 복구돼도 계속 저평가된 값을 보여준다.
    expect(cacheSet).not.toHaveBeenCalled()
  })

  it("AC4 (크리틱 리뷰 #1): getCostByService does not cache a partial result either", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("sort_desc")) {
        // top pod queries fail; core cpu/mem-by-service queries succeed.
        throw new Error("Prometheus query failed: 500")
      }
      return jsonResponse([
        { metric: { namespace: "platform-system", label_app_kubernetes_io_instance: "platform-svc" }, value: [0, "1"] },
      ])
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCostByService("platform-svc", scope, "platform-system")

    expect(result.telemetry.state).toBe("partial")
    expect(cacheSet).not.toHaveBeenCalled()
  })

  it("AC4/Codex 리뷰 #2 (크리틱 리뷰 #1/#5): getCostTrend keeps mem-only points and does not cache when only the cpu query fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("container_cpu_usage_seconds_total")) {
        throw new Error("Prometheus range query failed: 500")
      }
      return jsonResponse([{ metric: {}, values: [[1700000000, "2.0"]] }])
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCostTrend("namespace", "platform-system", 7, scope)

    expect(result.telemetry.state).toBe("partial")
    // Codex 리뷰 #2: cpu 쿼리가 실패해도 mem이 성공한 timestamp(union)의 point는
    // 살아 있어야 한다 — 이전에는 cpuValues 하나로만 map해서 points 전체가 []가 됐다.
    // cpu 기여는 없음(0)으로 처리되므로 이 픽스처의 total은 mem만의 기여(반올림 후 0).
    expect(result.points).toEqual([{ date: "2023-11-14", total: 0 }])
    expect(cacheSet).not.toHaveBeenCalled()
  })

  it("service scope excludes out-of-scope namespaces from rows and attribution counts", async () => {
    vi.stubGlobal("fetch", routedFetch({
      cpu: [
        { metric: { namespace: "platform-system", pod: "a-0" }, value: [0, "1"] },
        { metric: { namespace: "frontend-app", pod: "b-0" }, value: [0, "5"] },
      ],
      mem: [
        { metric: { namespace: "platform-system", pod: "a-0" }, value: [0, "1000000000"] },
        { metric: { namespace: "frontend-app", pod: "b-0" }, value: [0, "5000000000"] },
      ],
    }))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const result = await getCost("service", scope)

    expect(result.items).toHaveLength(1)
    expect(result.items[0].namespace).toBe("platform-system")
    expect(result.exclusions?.attribution).toMatchObject({ fallbackWorkloads: 1, fallbackPods: 1 })
  })

  it("service scope accumulates cpu for one instance label spanning multiple namespaces", async () => {
    vi.mocked(getNamespaces).mockResolvedValueOnce([
      ...testNamespaces,
      { name: "monitoring", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
    ])
    vi.stubGlobal("fetch", routedFetch({
      cpu: [
        { metric: { namespace: "platform-system", pod: "a-0" }, value: [0, "1"] },
        { metric: { namespace: "monitoring", pod: "a-0" }, value: [0, "2"] },
      ],
      labels: [
        { metric: { namespace: "platform-system", pod: "a-0", label_app_kubernetes_io_instance: "svc-a" }, value: [0, "1"] },
        { metric: { namespace: "monitoring", pod: "a-0", label_app_kubernetes_io_instance: "svc-a" }, value: [0, "1"] },
      ],
    }))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const result = await getCost("service", scope)

    expect(result.items.find((i) => i.id === "svc-a")?.cpu.cores).toBe(3)
    expect(result.exclusions?.attribution).toMatchObject({ fallbackWorkloads: 0, unclassifiedPods: 0 })
  })

  it("AC4: getCostByService reports telemetry.state='unavailable' distinctly from a 200+notice-only response shape", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("ECONNREFUSED")
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCostByService("platform-svc", scope, "platform-system")

    expect("serviceId" in result).toBe(false)
    expect(result.telemetry.state).toBe("unavailable")
    if (!("serviceId" in result)) expect(result.notice).toBeTruthy()
  })

  it("AC4: getCostTrend distinguishes a genuinely empty-but-complete result from an unavailable one", async () => {
    // A caller with no recognized role/team mapping and no matching namespaces never
    // issues a Prometheus query at all for cluster scope — genuinely empty, not a
    // telemetry failure.
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])))
    const noAccessScope = await getEffectiveScope({ groups: [], teams: [] })
    expect(noAccessScope.all).toBe(false)
    expect(noAccessScope.namespaces.size).toBe(0)

    const emptyResult = await getCostTrend("cluster", "cluster", 7, noAccessScope)
    expect(emptyResult.points).toEqual([])
    expect(emptyResult.telemetry.state).toBe("empty")
    expect(fetch).not.toHaveBeenCalled()

    // Now the same shape of empty `points` but because Prometheus itself is down —
    // must be distinguishable via telemetry.state, not conflated with the above.
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("ECONNREFUSED")
    }))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)
    const downResult = await getCostTrend("namespace", "platform-system", 7, scope)

    expect(downResult.points).toEqual([])
    expect(downResult.telemetry.state).toBe("unavailable")
    expect(downResult.telemetry.state).not.toBe(emptyResult.telemetry.state)
  })

  it("Codex 리뷰 #1: getCost('cluster') treats all-fulfilled-but-empty vectors as telemetry.state='empty', not 'ok' with a $0 item", async () => {
    // Prometheus responds 200 to every query but every vector is empty — the
    // scrape-outage-lookalike scenario the review flagged. Must not compute a
    // $0 cluster item, must not cache it, and must be distinguishable from a
    // genuine "unavailable" (hard failure).
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([])))
    const session = { groups: ["developer"], teams: ["platform-team"] }
    const scope = await getEffectiveScope(session)

    const result = await getCost("cluster", scope)

    expect(result.items).toEqual([])
    expect(result.telemetry.state).toBe("empty")
    expect(result.telemetry.source).toBe("prometheus") // it DID respond, just empty
    expect(result.notice).toBeTruthy()
    expect(cacheSet).not.toHaveBeenCalled()
  })

  it("Codex 리뷰 #4: getCost('service') does not cache a computable=false attribution, so a later successful call re-fetches", async () => {
    let ownerQueryShouldFail = true
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const decoded = decodeURIComponent(url)
      if (decoded.includes("kube_pod_owner")) {
        if (ownerQueryShouldFail) throw new Error("Prometheus query failed: 500")
        return jsonResponse([])
      }
      if (decoded.includes("container_cpu_usage_seconds_total")) {
        return jsonResponse([{ metric: { namespace: "platform-system", pod: "a-0" }, value: [0, "1"] }])
      }
      if (decoded.includes("container_memory_working_set_bytes")) {
        return jsonResponse([{ metric: { namespace: "platform-system", pod: "a-0" }, value: [0, "2000000000"] }])
      }
      return jsonResponse([])
    }))
    const scope = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })

    const first = await getCost("service", scope)
    expect(first.telemetry.state).toBe("ok")
    expect(first.exclusions?.attribution?.computable).toBe(false)
    expect(cacheSet).not.toHaveBeenCalled()

    ownerQueryShouldFail = false
    const second = await getCost("service", scope)
    expect(second.exclusions?.attribution?.computable).toBe(true)
    expect(cacheSet).toHaveBeenCalled()
  })
})
