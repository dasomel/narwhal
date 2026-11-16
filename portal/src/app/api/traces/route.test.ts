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
          spanSets: [{
            spans: [{
              spanID: "span-a-1",
              name: "GET /a",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "12000000",
              attributes: [
                { key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } },
                { key: "service.name", value: { stringValue: "team-a-svc" } },
              ],
            }],
          }],
        },
        {
          traceID: "trace-b-1",
          rootServiceName: "team-b-svc",
          rootTraceName: "GET /b-secret",
          durationMs: 30,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{
            spans: [{
              spanID: "span-b-1",
              name: "GET /b-secret",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "30000000",
              attributes: [
                { key: "k8s.namespace.name", value: { stringValue: "team-b-ns" } },
                { key: "service.name", value: { stringValue: "team-b-svc" } },
              ],
            }],
          }],
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
          spanSets: [{
            spans: [{
              spanID: "span-a-checkout",
              name: "GET /a-checkout",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "10000000",
              attributes: [
                { key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } },
                { key: "service.name", value: { stringValue: "checkout" } },
              ],
            }],
          }],
        },
        {
          traceID: "trace-b-checkout-leak",
          rootServiceName: "checkout",
          rootTraceName: "POST /team-b-secret-op",
          durationMs: 25,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{
            spans: [{
              spanID: "span-b-checkout",
              name: "POST /team-b-secret-op",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "25000000",
              attributes: [
                { key: "k8s.namespace.name", value: { stringValue: "team-b-ns" } },
                { key: "service.name", value: { stringValue: "checkout" } },
              ],
            }],
          }],
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

  it("re-filters and re-projects cached traces against the caller's current scope on every read", async () => {
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
          spanSets: [{
            spans: [{
              spanID: "span-a-1",
              name: "GET /orders",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "10000000",
              attributes: [
                { key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } },
                { key: "service.name", value: { stringValue: "orders" } },
              ],
            }],
          }],
        },
        {
          traceID: "trace-c-1",
          rootServiceName: "checkout",
          rootTraceName: "GET /team-b-moved",
          durationMs: 20,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{
            spans: [{
              spanID: "span-c-1",
              name: "GET /team-b-moved",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "20000000",
              attributes: [
                { key: "service.namespace", value: { stringValue: "team-c-ns" } },
                { key: "service.name", value: { stringValue: "checkout" } },
              ],
            }],
          }],
        },
        {
          traceID: "trace-ca-mixed",
          rootServiceName: "team-b-moved-root",
          rootTraceName: "POST /team-b-moved-root-op",
          durationMs: 80,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{
            spans: [
              {
                spanID: "span-c-earliest",
                name: "POST /team-b-moved-root-op",
                startTimeUnixNano: "1700000000000000000",
                durationNanos: "80000000",
                attributes: [
                  { key: "service.namespace", value: { stringValue: "team-c-ns" } },
                  { key: "service.name", value: { stringValue: "team-b-moved-root" } },
                ],
              },
              {
                spanID: "span-a-child",
                name: "GET /orders-child",
                startTimeUnixNano: "1700000000050000000",
                durationNanos: "14000000",
                attributes: [
                  { key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } },
                  { key: "service.name", value: { stringValue: "orders" } },
                ],
              },
            ],
          }],
        },
      ],
    })))

    mockAuth.mockResolvedValue({ groups: ["developer"], teams: ["team-a"], user: { role: "developer" } })
    const warmRes = await GET(req())
    expect(await warmRes.json()).toHaveLength(3)

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
    expect(afterBody.map((t: { traceID: string }) => t.traceID)).toEqual(["trace-a-1", "trace-ca-mixed"])
    const mixedRow = afterBody.find((t: { traceID: string }) => t.traceID === "trace-ca-mixed")
    expect(mixedRow).toEqual({
      traceID: "trace-ca-mixed",
      serviceName: "orders",
      operationName: "GET /orders-child",
      duration: 14,
      startTime: 1700000000050,
      spanCount: 1,
    })
    expect(JSON.stringify(afterBody)).not.toContain("team-b")
  })

  it.each(["developer", "viewer"] as const)(
    "projects only in-scope span data when a trace has a foreign team-b root and an in-scope team-a child for %s (and leaves cluster-admin unchanged)",
    async (role) => {
      const fetchMock = vi.fn<(url?: string | URL | Request) => Promise<Response>>(async () => Response.json({
        traces: [
          {
            traceID: "4bf92f3577b34da6a3ce929d0e0e4736",
            rootServiceName: "team-b-secret",
            rootTraceName: "POST /team-b-secret-op",
            durationMs: 950,
            startTimeUnixNano: "1700000000000000000",
            spanSets: [{
              matched: 5,
              spans: [
                {
                  spanID: "span-a-child-later",
                  name: "GET /a-child-later",
                  startTimeUnixNano: "1700000000200000000",
                  durationNanos: "8000000",
                  attributes: [
                    { key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } },
                    { key: "service.name", value: { stringValue: "team-a-svc" } },
                  ],
                },
                {
                  spanID: "span-a-child-earliest",
                  name: "GET /a-child-earliest",
                  startTimeUnixNano: "1700000000100000000",
                  durationNanos: "15000000",
                  attributes: [
                    { key: "k8s.namespace.name", value: { stringValue: "team-a-ns" } },
                    { key: "service.name", value: { stringValue: "team-a-svc" } },
                  ],
                },
              ],
            }],
          },
        ],
      }))
      vi.stubGlobal("fetch", fetchMock)

      mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
      const res = await GET(req())
      expect(res.status).toBe(200)
      const body = await res.json()
      expect(body).toEqual([
        {
          traceID: "4bf92f3577b34da6a3ce929d0e0e4736",
          serviceName: "team-a-svc",
          operationName: "GET /a-child-earliest",
          duration: 15,
          startTime: 1700000000100,
          spanCount: 2,
        },
      ])
      const serialized = JSON.stringify(body)
      expect(serialized).not.toContain("team-b")
      expect(serialized).not.toContain("950")
      expect(serialized).not.toContain("1700000000000")

      const requestedUrl = String(fetchMock.mock.calls[0]?.[0] ?? "")
      expect(decodeURIComponent(requestedUrl)).toContain("resource.service.name")

      mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
      const adminRes = await GET(req())
      expect(adminRes.status).toBe(200)
      expect(await adminRes.json()).toEqual([
        {
          traceID: "4bf92f3577b34da6a3ce929d0e0e4736",
          serviceName: "team-b-secret",
          operationName: "POST /team-b-secret-op",
          duration: 950,
          startTime: 1700000000000,
          spanCount: 2,
        },
      ])
    },
  )

  it.each(["developer", "viewer"] as const)("does not leak team-b traces or foreign root data on the >50 namespaces fallback path for %s", async (role) => {
    const manyNamespaces: NamespaceInfo[] = [
      ...Array.from({ length: 51 }, (_, i) => ({
        name: `team-a-ns-${i}`,
        status: "Active",
        labels: { "narwhal.io/team": "team-a" },
        createdAt: "2026-01-01T00:00:00Z",
      })),
      { name: "team-b-ns", status: "Active", labels: { "narwhal.io/team": "team-b" }, createdAt: "2026-01-01T00:00:00Z" },
    ]
    mockGetNamespaces.mockResolvedValue(manyNamespaces)
    const fetchMock = vi.fn<(url?: string | URL | Request) => Promise<Response>>(async () => Response.json({
      traces: [
        {
          traceID: "trace-a-51",
          rootServiceName: "checkout",
          rootTraceName: "GET /a-ok",
          durationMs: 10,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{
            spans: [{
              spanID: "span-a-51",
              name: "GET /a-ok",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "10000000",
              attributes: [
                { key: "k8s.namespace.name", value: { stringValue: "team-a-ns-0" } },
                { key: "service.name", value: { stringValue: "checkout" } },
              ],
            }],
          }],
        },
        {
          traceID: "trace-b-51-leak",
          rootServiceName: "team-b-secret",
          rootTraceName: "GET /team-b-secret",
          durationMs: 99,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{
            spans: [{
              spanID: "span-b-51",
              name: "GET /team-b-secret",
              startTimeUnixNano: "1700000000000000000",
              durationNanos: "99000000",
              attributes: [
                { key: "k8s.namespace.name", value: { stringValue: "team-b-ns" } },
                { key: "service.name", value: { stringValue: "team-b-secret" } },
              ],
            }],
          }],
        },
        {
          traceID: "trace-mixed-51",
          rootServiceName: "team-b-secret",
          rootTraceName: "POST /team-b-secret-op",
          durationMs: 400,
          startTimeUnixNano: "1700000000000000000",
          spanSets: [{
            matched: 4,
            spans: [
              {
                spanID: "span-b-root-51",
                name: "POST /team-b-secret-op",
                startTimeUnixNano: "1700000000000000000",
                durationNanos: "400000000",
                attributes: [
                  { key: "k8s.namespace.name", value: { stringValue: "team-b-ns" } },
                  { key: "service.name", value: { stringValue: "team-b-secret" } },
                ],
              },
              {
                spanID: "span-a-child-51",
                name: "GET /a-child-51",
                startTimeUnixNano: "1700000000050000000",
                durationNanos: "22000000",
                attributes: [
                  { key: "k8s.namespace.name", value: { stringValue: "team-a-ns-1" } },
                  { key: "service.name", value: { stringValue: "team-a-svc" } },
                ],
              },
            ],
          }],
        },
      ],
    }))
    vi.stubGlobal("fetch", fetchMock)

    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    const requestedUrl = decodeURIComponent(String(fetchMock.mock.calls[0]?.[0] ?? ""))
    expect(requestedUrl).not.toContain("k8s.namespace.name=~")
    expect(requestedUrl).toContain("{} | select(")
    expect(body).toEqual([
      {
        traceID: "trace-a-51",
        serviceName: "checkout",
        operationName: "GET /a-ok",
        duration: 10,
        startTime: 1700000000000,
        spanCount: 1,
      },
      {
        traceID: "trace-mixed-51",
        serviceName: "team-a-svc",
        operationName: "GET /a-child-51",
        duration: 22,
        startTime: 1700000000050,
        spanCount: 1,
      },
    ])
    expect(JSON.stringify(body)).not.toContain("team-b")
  })
})
