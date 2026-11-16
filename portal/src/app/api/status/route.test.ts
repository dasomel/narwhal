import { beforeEach, describe, expect, it, vi } from "vitest"
import type { NamespaceInfo } from "@/lib/k8s-client"

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
  return {
    ...actual,
    getNamespaces: mockGetNamespaces,
    getNodeReadiness: vi.fn(),
    getControlPlaneHealth: vi.fn(),
  }
})
vi.mock("@/lib/argocd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/argocd")>()
  return { ...actual, getArgoAppsOrThrow: vi.fn() }
})
vi.mock("@/lib/alertmanager", () => ({ getAlertsOrThrow: vi.fn() }))
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))

const { getNodeReadiness, getControlPlaneHealth } = await import("@/lib/k8s-client")
const { getArgoAppsOrThrow } = await import("@/lib/argocd")
const { getAlertsOrThrow } = await import("@/lib/alertmanager")
const { cacheGet, cacheSet } = await import("@/lib/valkey")
const { GET } = await import("./route")

const namespaces: NamespaceInfo[] = [
  { name: "team-a-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
  { name: "team-b-ns", status: "Active", labels: { "narwhal.io/team": "team-b" }, createdAt: "2026-01-01T00:00:00Z" },
]

describe("GET /api/status — tenant scope", () => {
  const cache = new Map<string, unknown>()

  beforeEach(() => {
    vi.clearAllMocks()
    cache.clear()
    mockGetNamespaces.mockResolvedValue(namespaces)
    vi.mocked(cacheGet).mockImplementation(async (k: string) => cache.get(k) ?? null)
    vi.mocked(cacheSet).mockImplementation(async (k: string, v: unknown) => {
      cache.set(k, v)
    })
    vi.mocked(getNodeReadiness).mockResolvedValue({ total: 3, ready: 3 })
    vi.mocked(getControlPlaneHealth).mockResolvedValue([
      { name: "kube-apiserver", status: "Running", restarts: 0 },
      { name: "etcd", status: "Running", restarts: 0 },
    ])
    vi.mocked(getArgoAppsOrThrow).mockResolvedValue([
      { metadata: { name: "keycloak" }, status: { health: { status: "Healthy" }, sync: { status: "Synced" } } },
    ] as never)
    vi.mocked(getAlertsOrThrow).mockResolvedValue([
      {
        labels: { alertname: "TeamAAlert", namespace: "team-a-ns", severity: "warning" },
        annotations: { summary: "team-a slow" },
        status: { state: "active" },
        startsAt: "2026-11-17T00:00:00Z",
      },
      {
        labels: { alertname: "TeamBAlert", namespace: "team-b-ns", severity: "critical" },
        annotations: { summary: "team-b-workload OOM" },
        status: { state: "active" },
        startsAt: "2026-11-17T00:00:00Z",
      },
      {
        labels: { alertname: "NodeDown", severity: "critical", instance: "node-99" },
        annotations: { summary: "node-99 down" },
        status: { state: "active" },
        startsAt: "2026-11-17T00:00:00Z",
      },
    ] as never)
  })

  it("shows cluster-admin platform status and all incidents including namespace-less", async () => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.components.some((c: { id: string }) => c.id === "keycloak")).toBe(true)
    expect(body.incidents.map((i: { title: string }) => i.title)).toEqual(["TeamAAlert", "TeamBAlert", "NodeDown"])
  })

  it.each(["developer", "viewer"] as const)("preserves core components for %s but filters incidents and isolates cache", async (role) => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    await GET()

    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET()
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body.components.some((c: { id: string }) => c.id === "keycloak")).toBe(true)
    expect(body.incidents.map((i: { title: string }) => i.title)).toEqual(["TeamAAlert"])
    const serialized = JSON.stringify(body)
    expect(serialized).not.toContain("team-b")
    expect(serialized).not.toContain("TeamBAlert")
    expect(serialized).not.toContain("NodeDown")
    expect(serialized).not.toContain("node-99")
  })
})
