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

  it("extracts sha256:<64hex> from all kubelet imageID forms, preserves raw non-digest imageID, and falls back to status.image only when imageID is empty", async () => {
    const digA = "sha256:" + "a".repeat(64)
    const digB = "sha256:" + "b".repeat(64)
    const bareHex = "c".repeat(64)
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
              metadata: { namespace: "ns", name: "runtime-forms" },
              spec: { containers: [] },
              status: {
                phase: "Running",
                containerStatuses: [
                  { name: "repo-at", image: "repo:1.0", imageID: `repo@${digA}`, state: { running: {} } },
                  { name: "docker-pullable", image: "repo:1.0", imageID: `docker-pullable://repo@${digA}`, state: { running: {} } },
                  { name: "docker-prefix", image: "repo:1.0", imageID: `docker://${digA}`, state: { running: {} } },
                  { name: "containerd-prefix", image: "repo:1.0", imageID: `containerd://${digA}`, state: { running: {} } },
                  { name: "crio-prefix", image: "repo:1.0", imageID: `cri-o://${digA}`, state: { running: {} } },
                  { name: "bare-sha", image: "repo:1.0", imageID: digA, state: { running: {} } },
                  // Non-empty imageID without sha256:<64hex> keeps raw string as identity and does NOT fall back to status.image @digest
                  { name: "crio-raw-1", image: `repo:1.0@${digB}`, imageID: `cri-o://${bareHex}`, state: { running: {} } },
                  { name: "crio-raw-2", image: `repo:1.0@${digB}`, imageID: "cri-o://opaque-id-2", state: { running: {} } },
                  // Empty imageID falls back to status.image @digest; stays absent when neither has digest
                  { name: "empty-id-fallback", image: `repo:1.0@${digB}`, imageID: "", state: { running: {} } },
                  { name: "empty-both", image: "repo:1.0", imageID: "", state: { running: {} } },
                ],
              },
            },
          ],
        })
      }
      return new Response("Not Found", { status: 404 })
    })

    const inv = await getOssClusterInventory()
    expect(inv.pods.status).toBe("ok")
    if (inv.pods.status === "ok") {
      const byName = new Map(inv.pods.data[0].containers.map((c) => [c.name, c]))
      for (const name of ["repo-at", "docker-pullable", "docker-prefix", "containerd-prefix", "crio-prefix", "bare-sha"]) {
        expect(byName.get(name)).toEqual({ name, image: "repo:1.0", digest: digA, running: true })
      }
      expect(byName.get("crio-raw-1")).toEqual({
        name: "crio-raw-1",
        image: `repo:1.0@${digB}`,
        imageId: `cri-o://${bareHex}`,
        running: true,
      })
      expect(byName.get("crio-raw-2")).toEqual({
        name: "crio-raw-2",
        image: `repo:1.0@${digB}`,
        imageId: "cri-o://opaque-id-2",
        running: true,
      })
      expect(byName.get("empty-id-fallback")).toEqual({
        name: "empty-id-fallback",
        image: `repo:1.0@${digB}`,
        digest: digB,
        running: true,
      })
      expect(byName.get("empty-both")).toEqual({
        name: "empty-both",
        image: "repo:1.0",
        running: true,
      })
    }
  })
})
