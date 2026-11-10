import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@tanstack/react-query", () => ({ useQuery: vi.fn() }))
vi.mock("@/lib/i18n-client", () => ({ useT: () => (key: string) => key }))

import { useQuery } from "@tanstack/react-query"
import { ClusterInfraView } from "./cluster-infra-view"

const truncatedInfra = {
  nodes: [],
  controlPlane: [],
  namespaces: [],
  summary: { totalNodes: 1, readyNodes: 1, totalPods: 10, totalNamespaces: 1, truncated: true },
}

function queryResult(data: unknown, isError = false) {
  return { data, isLoading: false, isError, error: isError ? new Error("failed") : null, refetch: vi.fn() } as never
}

describe("ClusterInfraView data states", () => {
  it("shows an error state instead of empty after the API fails", () => {
    vi.mocked(useQuery).mockReturnValue(queryResult(undefined, true))
    const html = renderToStaticMarkup(<ClusterInfraView />)
    expect(html).toContain("dataState.error")
    expect(html).not.toContain("dataState.empty")
  })

  it("renders node cards, readiness text, and absent telemetry without inventing usage", () => {
    vi.mocked(useQuery).mockReturnValue(queryResult({ ...truncatedInfra, nodes: [{
      name: "worker-a", status: "NotReady", roles: ["worker"],
      cpu: { total: 4, used: null, percent: null },
      memory: { totalGi: 8, usedGi: 2, percent: 25 },
      pods: { running: 1, total: 2 }, kubeletVersion: "v1.35.2",
    }] }))
    const html = renderToStaticMarkup(<ClusterInfraView />)
    expect(html).toContain('href="/nodes/worker-a"')
    expect(html).toContain("NotReady")
    expect(html).toContain("worker")
    expect(html).toContain("arch640.noTelemetry")
    expect(html).toContain('aria-valuenow="25"')
    expect(html).not.toContain("<table")
    expect(html).not.toContain('aria-valuenow="0"')
  })

  it("keeps summary data visible with a partial response banner", () => {
    vi.mocked(useQuery).mockReturnValue(queryResult(truncatedInfra))
    const html = renderToStaticMarkup(<ClusterInfraView />)
    expect(html).toContain("dataState.partial")
    expect(html).toContain("1/1")
    expect(html).toContain("10")
  })
})
