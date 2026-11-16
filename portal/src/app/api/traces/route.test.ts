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
  return { ...actual, getArgoAppsOrThrow: vi.fn() }
})
vi.mock("@/lib/workload-health", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/workload-health")>()
  return { ...actual, getWorkloadSnapshot: vi.fn() }
})
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))

const { getArgoAppsOrThrow } = await import("@/lib/argocd")
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
    vi.mocked(getArgoAppsOrThrow).mockResolvedValue([
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
        {
          traceID: "trace-a-1",
          rootServiceName: "team-a-svc",
          rootTraceName: "GET /a",
          durationMs: 12,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } }] }] }],
        },
        {
          traceID: "trace-b-1",
          rootServiceName: "team-b-svc",
          rootTraceName: "GET /b-secret",
          durationMs: 30,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "k8s.namespace.name", value: { stringValue: "team-b-ns" } }] }] }],
        },
        {
          traceID: "trace-u-1",
          rootServiceName: "unknown-cluster-svc",
          rootTraceName: "GET /u",
          durationMs: 5,
          startTimeUnixNano: "1700000000000000000",
        },
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

  it.each(["developer", "viewer"] as const)("isolates colliding service.name=checkout by trace namespace for %s (and lets admin see both)", async (role) => {
    vi.mocked(getArgoAppsOrThrow).mockResolvedValue([
      makeApp("checkout", "team-a-ns"),
      makeApp("backend", "team-b-ns"),
    ])
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: true,
      workloads: [
        { namespace: "team-a-ns", kind: "Deployment", name: "checkout", status: "healthy" },
        { namespace: "team-b-ns", kind: "Deployment", name: "backend", status: "healthy" },
      ],
      livePods: [],
      hidden: [],
    })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      traces: [
        {
          traceID: "trace-a-checkout",
          rootServiceName: "checkout",
          rootTraceName: "GET /a-checkout",
          durationMs: 10,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } }] }] }],
        },
        {
          traceID: "trace-b-checkout-leak",
          rootServiceName: "checkout",
          rootTraceName: "POST /team-b-secret-op",
          durationMs: 25,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "k8s.namespace.name", value: { stringValue: "team-b-ns" } }] }] }],
        },
        {
          traceID: "trace-no-ns",
          rootServiceName: "checkout",
          rootTraceName: "GET /team-b-no-ns",
          durationMs: 15,
          startTimeUnixNano: "1700000000000000000",
        },
      ],
    })))

    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET(req("checkout"))
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.map((t: { traceID: string }) => t.traceID)).toEqual(["trace-a-checkout"])
    expect(JSON.stringify(body)).not.toContain("team-b")

    const listRes = await GET(req())
    expect(listRes.status).toBe(200)
    const listBody = await listRes.json()
    expect(listBody.map((t: { traceID: string }) => t.traceID)).toEqual(["trace-a-checkout"])
    expect(JSON.stringify(listBody)).not.toContain("team-b")

    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const adminRes = await GET(req("checkout"))
    expect(adminRes.status).toBe(200)
    expect(await adminRes.json()).toHaveLength(3)
  })

  it("denies ?service= queries with 403 when ownership snapshot is incomplete", async () => {
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: false,
      workloads: [{ namespace: "team-a-ns", kind: "Deployment", name: "team-a-svc", status: "healthy" }],
      livePods: [],
      hidden: [],
    })
    mockAuth.mockResolvedValue({ groups: ["developer"], teams: ["team-a"], user: { role: "developer" } })
    const res = await GET(req("team-a-svc"))
    expect(res.status).toBe(403)
  })

  it("re-filters cached traces against the caller's current scope on every read", async () => {
    const teamANsWithC: NamespaceInfo[] = [
      ...namespaces,
      { name: "team-c-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
    ]
    mockGetNamespaces.mockResolvedValue(teamANsWithC)
    vi.mocked(getArgoAppsOrThrow).mockResolvedValue([
      makeApp("orders", "team-a-ns"),
      makeApp("checkout", "team-c-ns"),
    ])
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: true,
      workloads: [
        { namespace: "team-a-ns", kind: "Deployment", name: "orders", status: "healthy" },
        { namespace: "team-c-ns", kind: "Deployment", name: "checkout", status: "healthy" },
      ],
      livePods: [],
      hidden: [],
    })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      traces: [
        {
          traceID: "trace-a-1",
          rootServiceName: "orders",
          rootTraceName: "GET /orders",
          durationMs: 10,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } }] }] }],
        },
        {
          traceID: "trace-c-1",
          rootServiceName: "checkout",
          rootTraceName: "GET /team-b-moved",
          durationMs: 20,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "service.namespace", value: { stringValue: "team-c-ns" } }] }] }],
        },
      ],
    })))

    mockAuth.mockResolvedValue({ groups: ["developer"], teams: ["team-a"], user: { role: "developer" } })
    const warmRes = await GET(req())
    expect(await warmRes.json()).toHaveLength(2)

    // Simulate a warm cache entry whose key collides or was served even after team-c-ns was reassigned to team-b
    const cachedEntries = Array.from(cache.entries())
    const [, cachedPayload] = cachedEntries[0]
    mockGetNamespaces.mockResolvedValue([
      { name: "team-a-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
      { name: "team-c-ns", status: "Active", labels: { "narwhal.io/team": "team-b" }, createdAt: "2026-01-01T00:00:00Z" },
    ])
    vi.mocked(cacheGet).mockResolvedValueOnce(cachedPayload as never)

    const afterRes = await GET(req())
    expect(afterRes.status).toBe(200)
    const afterBody = await afterRes.json()
    expect(afterBody.map((t: { traceID: string }) => t.traceID)).toEqual(["trace-a-1"])
    expect(JSON.stringify(afterBody)).not.toContain("team-b")
  })

  it.each(["developer", "viewer"] as const)("does not leak team-b traces on the >50 in-scope services path for %s", async (role) => {
    const manyApps = Array.from({ length: 55 }, (_, i) => makeApp(`svc-${i}`, "team-a-ns"))
    manyApps.push(makeApp("checkout", "team-a-ns"))
    vi.mocked(getArgoAppsOrThrow).mockResolvedValue(manyApps)
    vi.mocked(getWorkloadSnapshot).mockResolvedValue({
      complete: true,
      workloads: manyApps.map((a) => ({ namespace: "team-a-ns", kind: "Deployment" as const, name: a.metadata.name, status: "healthy" as const })),
      livePods: [],
      hidden: [],
    })
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      traces: [
        {
          traceID: "trace-a-55",
          rootServiceName: "checkout",
          rootTraceName: "GET /a-ok",
          durationMs: 10,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } }] }] }],
        },
        {
          traceID: "trace-b-55-leak",
          rootServiceName: "checkout",
          rootTraceName: "GET /team-b-secret",
          durationMs: 99,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{ spans: [{ attributes: [{ key: "k8s.namespace.name", value: { stringValue: "team-b-ns" } }] }] }],
        },
      ],
    })))

    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.map((t: { traceID: string }) => t.traceID)).toEqual(["trace-a-55"])
    expect(JSON.stringify(body)).not.toContain("team-b")
  })
})
