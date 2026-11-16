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
    getAllNodesForDistribution: vi.fn(),
    getAllPodsForDistribution: vi.fn(),
  }
})
vi.mock("@/lib/prometheus", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/prometheus")>()
  return { ...actual, getNodeMetrics: vi.fn() }
})
vi.mock("@/lib/valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))

const { getAllNodesForDistribution, getAllPodsForDistribution } = await import("@/lib/k8s-client")
const { getNodeMetrics } = await import("@/lib/prometheus")
const { GET } = await import("./route")

const namespaces: NamespaceInfo[] = [
  { name: "team-a-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
]

describe("GET /api/governance/distribution — cluster-admin only", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetNamespaces.mockResolvedValue(namespaces)
    vi.mocked(getAllNodesForDistribution).mockResolvedValue({ items: [], truncated: false, pages: 1 })
    vi.mocked(getAllPodsForDistribution).mockResolvedValue({ items: [], truncated: false, pages: 1 })
    vi.mocked(getNodeMetrics).mockResolvedValue([])
  })

  it("returns 401 when unauthenticated", async () => {
    mockAuth.mockResolvedValue(null)
    const res = await GET()
    expect(res.status).toBe(401)
  })

  it.each(["developer", "viewer"] as const)("returns 403 without ACCESS_NOT_GRANTED for mapped %s", async (role) => {
    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET()
    expect(res.status).toBe(403)
    const body = await res.json()
    expect(body).toEqual({ error: "Forbidden" })
    expect(getAllNodesForDistribution).not.toHaveBeenCalled()
  })

  it("returns 200 for cluster-admin", async () => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const res = await GET()
    expect(res.status).toBe(200)
  })
})
