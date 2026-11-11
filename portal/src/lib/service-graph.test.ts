import { beforeEach, afterEach, describe, expect, it, vi } from "vitest"

vi.mock("./valkey", () => ({ cacheGet: vi.fn(async () => null), cacheSet: vi.fn() }))
vi.mock("./argocd", () => ({ getArgoApps: vi.fn(async () => []) }))

vi.mock("./workload-health", async (original) => ({ ...await original<typeof import("./workload-health")>(), getWorkloadSnapshot: vi.fn(async () => ({ workloads: [], hidden: [] })) }))

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
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({ workloads: [{ namespace: "ns", kind: "Deployment", name: "app", status: "down" }, { namespace: "ns", kind: "StatefulSet", name: "worker", status: "healthy" }], hidden: [{ namespace: "ns", name: "backup" }] })
    const graph = await getServiceGraph("1h")
    expect(graph.hiddenOneOffCount).toBe(1)
    expect(graph.nodes.map((n) => n.status)).toEqual(["down", "healthy"])
    expect(graph.edges).toHaveLength(1)
  })
  it("falls back to ArgoCD then unknown when Kubernetes fails", async () => {
    vi.mocked(getWorkloadSnapshot).mockRejectedValue(new Error("403"))
    const graph = await getServiceGraph("1h")
    expect(graph.nodes.map((n) => n.status)).toEqual(["healthy", "unknown", "unknown"])
    expect(graph.hiddenOneOffCount).toBe(0)
  })
})
