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
vi.mock("@/lib/alertmanager", () => ({ getAlerts: vi.fn() }))
vi.mock("@/lib/prometheus", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/prometheus")>()
  return { ...actual, getClusterMetrics: vi.fn() }
})
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))

const { getArgoApps } = await import("@/lib/argocd")
const { getAlerts } = await import("@/lib/alertmanager")
const { getClusterMetrics } = await import("@/lib/prometheus")
const { cacheGet, cacheSet } = await import("@/lib/valkey")
const { GET } = await import("./route")

const namespaces: NamespaceInfo[] = [
  { name: "team-a-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
  { name: "team-b-ns", status: "Active", labels: { "narwhal.io/team": "team-b" }, createdAt: "2026-01-01T00:00:00Z" },
]

function makeApp(name: string, namespace: string, health: "Healthy" | "Degraded"): ArgoApp {
  return {
    metadata: { name },
    spec: { project: "default", destination: { namespace } },
    status: { sync: { status: health === "Degraded" ? "OutOfSync" : "Synced" }, health: { status: health } },
  }
}

describe("GET /api/hero — tenant scope", () => {
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
      makeApp("team-a-api", "team-a-ns", "Healthy"),
      makeApp("team-b-secret-svc", "team-b-ns", "Degraded"),
    ])
    vi.mocked(getAlerts).mockResolvedValue([
      {
        labels: { alertname: "TeamAWarning", namespace: "team-a-ns", severity: "warning" },
        annotations: { summary: "team-a warning" },
        status: { state: "active" },
        startsAt: "2026-11-17T00:00:00Z",
      },
      {
        labels: { alertname: "TeamBCritical", namespace: "team-b-ns", severity: "critical" },
        annotations: { summary: "team-b-secret-svc failing" },
        status: { state: "active" },
        startsAt: "2026-11-17T00:00:00Z",
      },
      {
        labels: { alertname: "NodeDiskPressure", severity: "critical", instance: "master-1" },
        annotations: { summary: "cluster node pressure" },
        status: { state: "active" },
        startsAt: "2026-11-17T00:00:00Z",
      },
    ] as never)
    vi.mocked(getClusterMetrics).mockResolvedValue({
      nodes: { total: 3, ready: 3 },
      pods: { total: 20, running: 20 },
      cpu: 15,
      memory: 30,
    } as never)
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({
      items: [{ metadata: { name: "worker-node-9" }, status: { conditions: [{ type: "MemoryPressure", status: "True" }] } }],
    })))
  })

  it("shows cluster-admin all incidents including node-pressure and namespace-less alerts", async () => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.mascot).toBe("critical")
    expect(body.incidentTotalCount).toBe(6)
    const serialized = JSON.stringify(body)
    expect(serialized).toContain("TeamBCritical")
    expect(serialized).toContain("NodeDiskPressure")
    expect(serialized).toContain("worker-node-9")
  })

  it.each(["developer", "viewer"] as const)("scopes %s incidents, counters, and mascot to team-a and isolates cache from admin", async (role) => {
    // Warm admin cache first
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    await GET()

    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.mascot).toBe("warning")
    expect(body.incidentTotalCount).toBe(1)
    expect(body.incidents.map((i: { title: string }) => i.title)).toEqual(["TeamAWarning"])
    const serialized = JSON.stringify(body)
    expect(serialized).not.toContain("team-b")
    expect(serialized).not.toContain("TeamBCritical")
    expect(serialized).not.toContain("NodeDiskPressure")
    expect(serialized).not.toContain("worker-node-9")
  })
})
