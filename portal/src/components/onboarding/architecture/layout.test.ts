import { describe, expect, it } from "vitest"
import type { ArchitectureData } from "@/app/api/architecture/route"
import { EDGES, LAYERS, NODES, edgePath, nodeStatus } from "./layout"

describe("onboarding architecture", () => {
  it("preserves the five onboarding layers and ten unique components", () => {
    expect(LAYERS.map(layer => layer.nodes.map(node => node.id))).toEqual([
      ["portal"], ["keycloak", "apisix"], ["grafana", "argocd", "gitea", "harbor"], ["prometheus", "openbao"], ["k8s"],
    ])
    expect(new Set(NODES.map(node => node.id)).size).toBe(10)
    for (const [from, to] of EDGES) expect(edgePath(from, to)).not.toMatch(/undefined|NaN/)
  })
  it("never infers health from absent or unrelated API data", () => {
    for (const data of [undefined, { nodes: [], edges: [] }]) {
      for (const node of NODES) expect(nodeStatus(data, node.id)).toBe("unknown")
    }
    const data: ArchitectureData = { nodes: [{ id: "portal", label: "Portal", group: "platform", status: "degraded", responseMs: 0 }], edges: [] }
    expect(nodeStatus(data, "portal")).toBe("degraded")
    expect(nodeStatus(data, "k8s")).toBe("unknown")
    for (const status of ["healthy", "offline", "unknown"] as const) {
      data.nodes[0].status = status
      expect(nodeStatus(data, "portal")).toBe(status)
    }
  })
})
