import { beforeEach, afterEach, describe, expect, it, vi } from "vitest"

vi.mock("./valkey", () => ({ cacheGet: vi.fn(async () => null), cacheSet: vi.fn() }))
vi.mock("./argocd", () => ({ getArgoApps: vi.fn(async () => []) }))

vi.mock("./workload-health", async (original) => ({ ...await original<typeof import("./workload-health")>(), getWorkloadSnapshot: vi.fn(async () => ({ complete: false, workloads: [], hidden: [] })) }))

import { getArgoApps } from "./argocd"
import { getWorkloadSnapshot } from "./workload-health"
import { getServiceGraph } from "./service-graph"

afterEach(() => {
  vi.unstubAllGlobals()
  delete process.env.SERVICE_GRAPH_SOURCE
})

describe("service graph Prometheus transport", () => {
  it.each([429, 503])("does not retry Prometheus %s responses", async (status) => {
    process.env.SERVICE_GRAPH_SOURCE = "istio"
    const fetchSpy = vi.fn(async () => Response.json({}, { status }))
    vi.stubGlobal("fetch", fetchSpy)

    const graph = await getServiceGraph("1h")

    expect(graph.edges).toEqual([])
    expect(graph.notice).toContain("Prometheus 미응답")
    // Three L7 queries and one TCP query each get one attempt.
    expect(fetchSpy).toHaveBeenCalledTimes(4)
  })
})


describe("service graph workload evidence", () => {
  beforeEach(() => {
    process.env.SERVICE_GRAPH_SOURCE = "istio-tcp"
    vi.mocked(getArgoApps).mockResolvedValue([{ metadata: { name: "app" }, status: { health: { status: "Healthy" } } }] as never)
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: "success", data: { result: ["worker", "backup"].map((destination) => ({
      metric: { source_workload: "app", source_workload_namespace: "ns", destination_workload: destination, destination_workload_namespace: "ns" }, value: [1, "2"],
    })) } })))
  })
  it("prefers readiness and removes Job traffic with a count", async () => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete: false, workloads: [{ namespace: "ns", kind: "Deployment", name: "app", status: "down" }, { namespace: "ns", kind: "StatefulSet", name: "worker", status: "healthy" }], hidden: [{ namespace: "ns", name: "backup" }] })
    const graph = await getServiceGraph("1h")
    expect(graph.hiddenOneOffCount).toBe(1)
    expect(graph.nodes.map((n) => n.status)).toEqual(["down", "healthy"])
    expect(graph.edges).toHaveLength(1)
  })
  it.each([true, false])("hides deleted Job telemetry only with complete evidence (%s)", async (complete) => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete, hidden: [], workloads: [
      { namespace: "ns", kind: "Deployment", name: "app", status: "healthy" },
      { namespace: "ns", kind: "StatefulSet", name: "worker", status: "healthy" },
    ] })
    const graph = await getServiceGraph("1h")
    expect(graph.hiddenOneOffCount).toBe(complete ? 1 : 0)
    expect(graph.edges).toHaveLength(complete ? 1 : 2)
    expect(graph.nodes.some((n) => n.id === "unmapped-pods:backup")).toBe(!complete)
  })
  it("retains unknown telemetry with a complete but empty snapshot", async () => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete: true, hidden: [], workloads: [] })
    const graph = await getServiceGraph("1h")
    expect(graph.hiddenOneOffCount).toBe(0)
    expect(graph.edges).toHaveLength(2)
  })
  it.each(["ns", "other", undefined])("matches controller identity within known namespaces (%s)", async (namespace) => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete: true, hidden: [], workloads: [
      { namespace: namespace ?? "other", kind: "Deployment", name: "worker" },
    ] })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: "success", data: { result: [{
      metric: { source_workload: "app", destination_workload: "worker", destination_workload_namespace: namespace ? "ns" : "" }, value: [1, "2"],
    }] } })))
    const graph = await getServiceGraph("1h")
    expect(graph.hiddenOneOffCount).toBe(namespace === "other" ? 1 : 0)
    expect(graph.edges).toHaveLength(namespace === "other" ? 0 : 1)
    if (namespace !== "other") expect(graph.nodes.find((n) => n.id === "unmapped-pods:worker")?.status).toBe("unknown")
  })
  it.each(["healthy", "degraded", "down"] as const)("aggregates raw controller health into service-named nodes: %s", async (worst) => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete: true, hidden: [], workloads: [
      { namespace: "a", kind: "Deployment", name: "app-abcde", status: "healthy" },
      { namespace: "b", kind: "Deployment", name: "app-12345", status: worst },
      { namespace: "a", kind: "Deployment", name: "worker", status: "healthy" },
      { namespace: "b", kind: "Deployment", name: "worker", status: "healthy" },
    ] })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: "success", data: { result: [
      ["app-12345", "b"], ["app-abcde", "a"],
    ].map(([name, namespace]) => ({ metric: { source_workload: name, source_workload_namespace: namespace,
      destination_workload: "worker", destination_workload_namespace: namespace }, value: [1, "2"] })) } })))
    const graph = await getServiceGraph("1h")
    expect(graph.nodes.find((n) => n.id === "app")?.status).toBe(worst)
    expect(graph.hiddenOneOffCount).toBe(0)
  })
  it.each([
    ["narwhal-db-1", "database", "healthy"],
    ["narwhal-db-1", "database", "down"],
    ["narwhal-db", "database", "healthy"],
    ["narwhal-db", undefined, "down"],
    ["narwhal-db", "other", "healthy"],
  ] as const)("retains live CNPG endpoint %s in %s with status %s", async (name, namespace, status) => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ complete: true, hidden: [], workloads: [
      { namespace: "ns", kind: "Deployment", name: "app", status: "healthy" },
    ], livePods: [{ namespace: "database", name: "narwhal-db-1", ownerNames: ["narwhal-db"], status }] })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ status: "success", data: { result: [{
      metric: { source_workload: "app", destination_workload: name, destination_workload_namespace: namespace ?? "" }, value: [1, "2"],
    }] } })))
    const graph = await getServiceGraph("1h")
    expect(graph.hiddenOneOffCount).toBe(namespace === "other" ? 1 : 0)
    expect(graph.nodes.find((n) => n.id === `unmapped-pods:${name}`)?.status).toBe(namespace === "other" ? undefined : status)
  })
  it("uses worst live pod health before ArgoCD, but after controller health", async () => {
    const snapshot = { complete: true, hidden: [], workloads: [], livePods: [
      { namespace: "ns", name: "app-1", ownerNames: ["app"], status: "healthy" as const },
      { namespace: "ns", name: "app-2", ownerNames: ["app"], status: "down" as const },
      { namespace: "ns", name: "worker", ownerNames: [], status: "healthy" as const },
    ] }
    vi.mocked(getWorkloadSnapshot).mockResolvedValue(snapshot)
    expect((await getServiceGraph("1h")).nodes.find((n) => n.id === "app")?.status).toBe("down")
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ ...snapshot, workloads: [
      { namespace: "ns", kind: "Deployment", name: "app", status: "healthy" },
    ] })
    expect((await getServiceGraph("1h")).nodes.find((n) => n.id === "app")?.status).toBe("healthy")
  })
  it("falls back to ArgoCD then unknown when Kubernetes fails", async () => {
    vi.mocked(getWorkloadSnapshot).mockRejectedValue(new Error("403"))
    const graph = await getServiceGraph("1h")
    expect(graph.nodes.map((n) => n.status)).toEqual(["healthy", "unknown", "unknown"])
    expect(graph.hiddenOneOffCount).toBe(0)
  })
})
