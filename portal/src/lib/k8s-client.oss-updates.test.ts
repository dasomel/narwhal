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

  it("maps /version, workloads with uid/selectors, replicasets, status pod images with ownerReferences/imageID, and CRD 403 -> no-permission", async () => {
    const dig = "sha256:" + "a".repeat(64)
    mockFetch.mockImplementation(async (url: string) => {
      if (url.endsWith("/version")) return Response.json({ gitVersion: "v1.35.7" })
      if (url.includes("/deployments")) {
        return Response.json({
          metadata: {},
          items: [{
            metadata: { namespace: "devtools", name: "gitea", uid: "dep-gitea" },
            spec: {
              selector: { matchLabels: { app: "gitea" } },
              template: { spec: { containers: [{ name: "gitea", image: "docker.gitea.com/gitea:1.26.2-rootless" }] } },
            },
          }],
        })
      }
      if (url.includes("/replicasets")) {
        return Response.json({
          metadata: {},
          items: [{
            metadata: {
              namespace: "devtools",
              name: "gitea-rs-1",
              uid: "rs-gitea",
              ownerReferences: [{ kind: "Deployment", name: "gitea", uid: "dep-gitea", controller: true }],
            },
          }],
        })
      }
      if (url.includes("/daemonsets") || url.includes("/statefulsets")) {
        return Response.json({ metadata: {}, items: [] })
      }
      if (url.includes("/api/v1/pods")) {
        return Response.json({
          metadata: {},
          items: [{
            metadata: {
              namespace: "devtools",
              name: "gitea-abc",
              labels: { app: "gitea" },
              ownerReferences: [{ kind: "ReplicaSet", name: "gitea-rs-1", uid: "rs-gitea", controller: true }],
            },
            spec: { containers: [{ name: "gitea", image: "docker.gitea.com/gitea:1.26.2-rootless" }] },
            status: {
              phase: "Running",
              containerStatuses: [{
                name: "gitea",
                image: "docker.gitea.com/gitea:1.26.2-rootless",
                imageID: `docker.gitea.com/gitea@${dig}`,
                state: { running: { startedAt: "2026-11-23T00:00:00Z" } },
              }],
            },
          }],
        })
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
        uid: "dep-gitea",
        selector: { matchLabels: { app: "gitea" } },
        containers: [{ name: "gitea", image: "docker.gitea.com/gitea:1.26.2-rootless" }],
        initContainers: [],
      }],
    })
    expect(inv.replicasets).toEqual({
      status: "ok",
      data: [{
        namespace: "devtools",
        name: "gitea-rs-1",
        uid: "rs-gitea",
        ownerReferences: [{ kind: "Deployment", name: "gitea", uid: "dep-gitea", controller: true }],
      }],
    })
    expect(inv.pods).toEqual({
      status: "ok",
      data: [{
        namespace: "devtools",
        name: "gitea-abc",
        labels: { app: "gitea" },
        phase: "Running",
        ownerReferences: [{ kind: "ReplicaSet", name: "gitea-rs-1", uid: "rs-gitea", controller: true }],
        containers: [{
          name: "gitea",
          image: "docker.gitea.com/gitea:1.26.2-rootless",
          digest: dig,
          running: true,
        }],
        initContainers: [],
      }],
    })
    expect(inv.gatewayApiCrdAnnotations).toEqual({
      status: "no-permission",
      resource: "customresourcedefinitions.apiextensions.k8s.io",
    })
  })

  it("propagates truncated when a bounded list hits maxPages, reads status image (not spec), and maps replicasets 403", async () => {
    const dig = "sha256:" + "b".repeat(64)
    mockFetch.mockImplementation(async (url: string) => {
      if (url.endsWith("/version")) return Response.json({ gitVersion: "v1.35.7" })
      if (url.includes("/replicasets")) return new Response("Forbidden", { status: 403 })
      if (url.includes("/deployments") || url.includes("/daemonsets") || url.includes("/statefulsets")) {
        return Response.json({ metadata: {}, items: [] })
      }
      if (url.includes("/api/v1/pods")) {
        // Always return a continue token so listBounded hits DEFAULT_LIST_MAX_PAGES (20) -> truncated: true
        return Response.json({
          metadata: { continue: "next-page" },
          items: [{
            metadata: { namespace: "security-system", name: "falco-1", deletionTimestamp: "2026-11-23T00:00:00Z" },
            spec: { containers: [{ name: "falco", image: "docker.io/falcosecurity/falco:0.45.2" }] },
            status: {
              phase: "Running",
              containerStatuses: [{
                name: "falco",
                image: "docker.io/falcosecurity/falco:0.45.1",
                imageID: dig,
                state: { waiting: { reason: "CrashLoopBackOff" } },
              }],
            },
          }],
        })
      }
      return new Response("Not Found", { status: 404 })
    })

    const inv = await getOssClusterInventory()
    expect(inv.pods).toEqual({ status: "truncated", resource: "pods" })
    expect(inv.replicasets).toEqual({ status: "no-permission", resource: "replicasets.apps" })

    // Verify status image/running/terminating/mirror mapping when pods is not truncated
    mockFetch.mockImplementation(async (url: string) => {
      if (url.endsWith("/version")) return Response.json({ gitVersion: "v1.35.7" })
      if (url.includes("/deployments") || url.includes("/daemonsets") || url.includes("/statefulsets") || url.includes("/replicasets")) {
        return Response.json({ metadata: {}, items: [] })
      }
      if (url.includes("/api/v1/pods")) {
        return Response.json({
          metadata: {},
          items: [
            {
              metadata: { namespace: "security-system", name: "falco-crash", deletionTimestamp: "2026-11-23T00:00:00Z" },
              spec: { containers: [{ name: "falco", image: "docker.io/falcosecurity/falco:0.45.2" }] },
              status: {
                phase: "Running",
                containerStatuses: [{
                  name: "falco",
                  image: "docker.io/falcosecurity/falco:0.45.1",
                  imageID: dig,
                  state: { waiting: { reason: "CrashLoopBackOff" } },
                }],
              },
            },
            {
              metadata: {
                namespace: "kube-system",
                name: "kube-vip-master-1",
                annotations: { "kubernetes.io/config.mirror": "abc123" },
                ownerReferences: [{ kind: "Node", name: "master-1", controller: true }],
              },
              spec: {
                nodeName: "master-1",
                containers: [{ name: "kube-vip", image: "ghcr.io/kube-vip/kube-vip:v1.1.2" }],
              },
              status: {
                phase: "Running",
                containerStatuses: [{
                  name: "kube-vip",
                  image: "ghcr.io/kube-vip/kube-vip:v1.1.2",
                  imageID: `ghcr.io/kube-vip/kube-vip@${dig}`,
                  state: { running: {} },
                }],
              },
            },
          ],
        })
      }
      return new Response("Not Found", { status: 404 })
    })

    const inv2 = await getOssClusterInventory()
    expect(inv2.pods.status).toBe("ok")
    if (inv2.pods.status === "ok") {
      expect(inv2.pods.data[0]).toMatchObject({
        name: "falco-crash",
        terminating: true,
        containers: [{
          name: "falco",
          image: "docker.io/falcosecurity/falco:0.45.1",
          digest: dig,
          running: false,
        }],
      })
      expect(inv2.pods.data[1]).toMatchObject({
        name: "kube-vip-master-1",
        nodeName: "master-1",
        mirrorAnnotation: "abc123",
        containers: [{ name: "kube-vip", image: "ghcr.io/kube-vip/kube-vip:v1.1.2", digest: dig, running: true }],
      })
    }
  })
})
