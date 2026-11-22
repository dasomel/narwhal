import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("./config", () => ({
  getK8sApiServer: () => "http://k8s.mock",
}))
vi.mock("./valkey", () => ({
  cacheGet: vi.fn(async () => null),
  cacheSet: vi.fn(async () => {}),
}))

import { getOssClusterInventory } from "./k8s-client"

describe("getOssClusterInventory", () => {
  const originalFetch = global.fetch
  const mockFetch = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    global.fetch = mockFetch
  })

  afterEach(() => {
    global.fetch = originalFetch
  })

  it("maps /version, workloads, and maps CRD 403 to no-permission without failing", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.endsWith("/version")) return Response.json({ gitVersion: "v1.35.7" })
      if (url.includes("/deployments")) {
        return Response.json({
          metadata: {},
          items: [{ metadata: { namespace: "devtools", name: "gitea" }, spec: { template: { spec: { containers: [{ name: "gitea", image: "docker.gitea.com/gitea:1.26.2-rootless" }] } } } }],
        })
      }
      if (url.includes("/daemonsets") || url.includes("/statefulsets") || url.includes("/pods")) {
        return Response.json({ metadata: {}, items: [] })
      }
      if (url.includes("customresourcedefinitions")) {
        return new Response("Forbidden", { status: 403 })
      }
      return new Response("Not Found", { status: 404 })
    })

    const inv = await getOssClusterInventory()
    expect(inv.serverVersion).toEqual({ status: "ok", data: "v1.35.7" })
    expect(inv.deployments).toEqual({
      status: "ok",
      data: [{
        namespace: "devtools",
        name: "gitea",
        containers: [{ name: "gitea", image: "docker.gitea.com/gitea:1.26.2-rootless" }],
        initContainers: [],
      }],
    })
    expect(inv.gatewayApiCrdAnnotations).toEqual({
      status: "no-permission",
      resource: "customresourcedefinitions.apiextensions.k8s.io",
    })
  })
})
