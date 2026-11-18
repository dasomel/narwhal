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
  return { ...actual, getNamespaces: mockGetNamespaces }
})
vi.mock("@/lib/alertmanager", () => ({ getAlerts: vi.fn() }))
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))

const { getAlerts } = await import("@/lib/alertmanager")
const { GET } = await import("./route")

const namespaces: NamespaceInfo[] = [
  { name: "team-a-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
  { name: "team-b-ns", status: "Active", labels: { "narwhal.io/team": "team-b" }, createdAt: "2026-01-01T00:00:00Z" },
]

const teamAAlert = {
  labels: { alertname: "TeamAHighLatency", namespace: "team-a-ns", severity: "warning" },
  annotations: { summary: "team-a latency" },
  status: { state: "active" },
  startsAt: "2026-11-17T00:00:00Z",
}
const teamBAlert = {
  labels: { alertname: "TeamBCrashLoop", namespace: "team-b-ns", severity: "critical" },
  annotations: { summary: "team-b-workload crashing" },
  status: { state: "active" },
  startsAt: "2026-11-17T00:00:00Z",
}
const nodeAlert = {
  labels: { alertname: "NodeMemoryPressure", severity: "critical", instance: "worker-02" },
  annotations: { summary: "node pressure" },
  status: { state: "active" },
  startsAt: "2026-11-17T00:00:00Z",
}

function req(query = "") {
  return new Request(`http://localhost/api/alerts${query}`)
}

describe("GET /api/alerts — tenant scope", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetNamespaces.mockResolvedValue(namespaces)
    vi.mocked(getAlerts).mockResolvedValue([teamAAlert, teamBAlert, nodeAlert] as never)
  })

  it("shows cluster-admin all alerts including namespace-less cluster alerts", async () => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toHaveLength(3)
  })

  it.each(["developer", "viewer"] as const)("scopes %s to team-a alerts only and hides team-b and namespace-less alerts", async (role) => {
    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET(req())
    expect(res.status).toBe(200)
    const body = await res.json()
    expect(body).toHaveLength(1)
    expect(body[0].labels.alertname).toBe("TeamAHighLatency")
    const serialized = JSON.stringify(body)
    expect(serialized).not.toContain("team-b")
    expect(serialized).not.toContain("TeamBCrashLoop")
    expect(serialized).not.toContain("NodeMemoryPressure")
    expect(serialized).not.toContain("worker-02")
  })

  it.each(["developer", "viewer"] as const)("fails closed when scope resolution throws for %s", async (role) => {
    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    mockGetNamespaces.mockRejectedValue(new Error("k8s unreachable"))
    const res = await GET(req())
    expect([403, 500]).toContain(res.status)
    const serialized = JSON.stringify(await res.json())
    expect(serialized).not.toContain("TeamBCrashLoop")
  })

  it.each(["developer", "viewer"] as const)("denies an unmapped %s with 403 ACCESS_NOT_GRANTED", async (role) => {
    mockAuth.mockResolvedValue({ groups: [role], teams: ["unmapped-team"], user: { role } })
    const res = await GET(req())
    expect(res.status).toBe(403)
    expect(await res.json()).toEqual({ error: "Forbidden", code: "ACCESS_NOT_GRANTED" })
  })
})
