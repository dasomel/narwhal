import { describe, expect, it } from "vitest"
import type { ServiceGraphResponse } from "@/app/api/service-graph/route"
import { summarizeServices, sumTraffic } from "./service-map-model"

const empty: ServiceGraphResponse = { window: "7d", generatedAt: "2026-11-10T00:00:00Z", nodes: [], edges: [] }

describe("architecture service summary", () => {
  it("counts observed health and unique namespaces without counting unknown as healthy", () => {
    expect(summarizeServices({ ...empty, namespaces: ["unused-filter-option"], nodes: [
      { id: "api", namespace: "apps", status: "healthy" },
      { id: "web", namespace: "apps", status: "degraded" },
      { id: "database", namespace: "data", status: "unknown" },
      { id: "unmapped-pods:worker", namespace: "unknown", status: "unknown" },
      { id: "legacy", namespace: "", status: "unknown" },
    ] })).toEqual({ services: 5, healthy: 1, degraded: 1, namespaces: 2 })
  })

  it("does not invent sample services or namespaces for an empty API result", () => {
    expect(summarizeServices({ ...empty, namespaces: ["apps"] })).toEqual({ services: 0, healthy: 0, degraded: 0, namespaces: 0 })
  })

  it("retains separate L4 and L7 units when showing connected traffic", () => {
    expect(sumTraffic([
      { source: "a", destination: "b", requestRate: 2, errorRate: 0, kind: "l7" },
      { source: "a", destination: "c", requestRate: 2048, errorRate: 0, kind: "l4" },
    ], "mixed")).toBe("2.0 req/s · 2.0 KB/s")
  })
})
