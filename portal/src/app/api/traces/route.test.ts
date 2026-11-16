import { beforeEach, describe, expect, it, vi } from "vitest"
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

function req(service?: string) {
  return new Request(`http://localhost/api/traces${service ? `?service=${service}` : ""}`)
}

describe("GET /api/traces — tenant scope", () => {
  const cache = new Map<string, unknown>()

  beforeEach(() => {
    vi.clearAllMocks()
    cache.clear()
    mockGetNamespaces.mockResolvedValue(namespaces)
    vi.mocked(cacheGet).mockImplementation(async (k: string) => cache.get(k) ?? null)
    vi.mocked(cacheSet).mockImplementation(async (k: string, v: unknown) => {
      cache.set(k, v)
    })
    vi.mocked(getArgoApps).mockResolvedValue([
      makeApp("team-a-svc", "team-a-ns"),
      makeApp("team-b-svc", "team-b-ns"),
    ])
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: true,
      workloads: [
        { namespace: "team-a-ns", kind: "Deployment", name: "team-a-svc", status: "healthy" },
        { namespace: "team-b-ns", kind: "Deployment", name: "team-b-svc", status: "healthy" },
      ],
      livePods: [],
      hidden: [],
    })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      traces: [
        { traceID: "trace-a-1", rootServiceName: "team-a-svc", rootTraceName: "GET /a", durationMs: 12, startTimeUnixNano: "1700000000000000000" },
        { traceID: "trace-b-1", rootServiceName: "team-b-svc", rootTraceName: "GET /b-secret", durationMs: 30, startTimeUnixNano: "1700000000000000000" },
        { traceID: "trace-u-1", rootServiceName: "unknown-cluster-svc", rootTraceName: "GET /u", durationMs: 5, startTimeUnixNano: "1700000000000000000" },
      ],
    })))
  })

  it("shows cluster-admin all traces including out-of-scope and unmapped services", async () => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toHaveLength(3)
  })

  it.each(["developer", "viewer"] as const)("scopes %s traces when service is omitted and isolates cache from admin", async (role) => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    await GET(req())

    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.map((t: { serviceName: string }) => t.serviceName)).toEqual(["team-a-svc"])
    const serialized = JSON.stringify(body)
    expect(serialized).not.toContain("team-b")
    expect(serialized).not.toContain("unknown-cluster-svc")
  })

  it.each(["developer", "viewer"] as const)("allows %s to query an in-scope service and denies out-of-scope or unknown services with 403 (no ACCESS_NOT_GRANTED)", async (role) => {
    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })

    const okRes = await GET(req("team-a-svc"))
    expect(okRes.status).toBe(200)

    const outRes = await GET(req("team-b-svc"))
    expect(outRes.status).toBe(403)
    const outBody = await outRes.json()
    expect(outBody.code).toBeUndefined()

    const unknownRes = await GET(req("mystery-service"))
    expect(unknownRes.status).toBe(403)
    const unknownBody = await unknownRes.json()
    expect(unknownBody.code).toBeUndefined()
  })
})
