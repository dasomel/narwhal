import { beforeEach, describe, expect, it, vi } from "vitest"
import { NextRequest } from "next/server"
import type { NamespaceInfo } from "@/lib/k8s-client"
import type { ArgoApp } from "@/lib/argocd"

const mockAuth = vi.hoisted(() => vi.fn())
const mockGetNamespaces = vi.hoisted(() => vi.fn())

vi.mock("next-auth", () => ({
  default: () => ({ handlers: {}, auth: mockAuth, signIn: vi.fn(), signOut: vi.fn() }),
}))
vi.mock("next-auth/providers/credentials", () => ({
  default: (opts: unknown) => opts,
}))
vi.mock("@/lib/k8s-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/k8s-client")>()
  return { ...actual, getNamespaces: mockGetNamespaces }
})
vi.mock("@/lib/argocd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/argocd")>()
  return { ...actual, getArgoApps: vi.fn() }
})
vi.mock("@/lib/workload-health", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/workload-health")>()
  return { ...actual, getWorkloadSnapshot: vi.fn() }
})
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))

const { getArgoApps } = await import("@/lib/argocd")
const { getWorkloadSnapshot } = await import("@/lib/workload-health")
const { cacheGet, cacheSet } = await import("@/lib/valkey")
const { GET } = await import("./route")

const namespaces: NamespaceInfo[] = [
  { name: "team-a-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
  { name: "team-b-ns", status: "Active", labels: { "narwhal.io/team": "team-b" }, createdAt: "2026-01-01T00:00:00Z" },
]

function makeApp(name: string, namespace: string): ArgoApp {
  return {
    metadata: { name },
    spec: { project: "default", destination: { namespace } },
    status: { sync: { status: "Synced" }, health: { status: "Healthy" } },
  }
}

function req(svc: string) {
  return GET(new NextRequest(`http://localhost/api/service-graph/${svc}?window=1h`), {
    params: Promise.resolve({ svc }),
  })
}

describe("GET /api/service-graph/[svc] — tenant scope", () => {
  const cache = new Map<string, unknown>()

  beforeEach(() => {
    vi.clearAllMocks()
    cache.clear()
    process.env.SERVICE_GRAPH_SOURCE = "istio-tcp"
    mockGetNamespaces.mockResolvedValue(namespaces)
    vi.mocked(cacheGet).mockImplementation(async (k: string) => cache.get(k) ?? null)
    vi.mocked(cacheSet).mockImplementation(async (k: string, v: unknown) => {
      cache.set(k, v)
    })
    vi.mocked(getArgoApps).mockResolvedValue([
      makeApp("svc-a1", "team-a-ns"),
      makeApp("svc-a2", "team-a-ns"),
      makeApp("svc-b1", "team-b-ns"),
    ])
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: true,
      workloads: [
        { namespace: "team-a-ns", kind: "Deployment", name: "svc-a1", status: "healthy" },
        { namespace: "team-a-ns", kind: "Deployment", name: "svc-a2", status: "healthy" },
        { namespace: "team-b-ns", kind: "Deployment", name: "svc-b1", status: "healthy" },
      ],
      livePods: [],
      hidden: [],
    })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      status: "success",
      data: {
        result: [
          { metric: { source_workload: "svc-a2", source_workload_namespace: "team-a-ns", destination_workload: "svc-a1", destination_workload_namespace: "team-a-ns" }, value: [1, "2"] },
          { metric: { source_workload: "svc-a1", source_workload_namespace: "team-a-ns", destination_workload: "svc-b1", destination_workload_namespace: "team-b-ns" }, value: [1, "3"] },
        ],
      },
    })))
  })

  it("shows cluster-admin all peers for any service", async () => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const res = await req("svc-a1")
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.inbound.map((e: { source: string }) => e.source)).toEqual(["svc-a2"])
    expect(body.outbound.map((e: { destination: string }) => e.destination)).toEqual(["svc-b1"])
  })

  it.each(["developer", "viewer"] as const)("drops out-of-scope peers for %s and denies out-of-scope service with 403", async (role) => {
    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })

    const okRes = await req("svc-a1")
    expect(okRes.status).toBe(200)
    const okBody = await okRes.json()
    expect(okBody.inbound.map((e: { source: string }) => e.source)).toEqual(["svc-a2"])
    expect(okBody.outbound).toEqual([])
    expect(JSON.stringify(okBody)).not.toContain("svc-b1")

    const deniedRes = await req("svc-b1")
    expect(deniedRes.status).toBe(403)
    const deniedBody = await deniedRes.json()
    expect(deniedBody.code).toBeUndefined()
  })
})
