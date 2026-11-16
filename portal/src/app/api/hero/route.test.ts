import { beforeEach, describe, expect, it, vi } from "vitest"
import type { NamespaceInfo } from "@/lib/k8s-client"

vi.mock("@/lib/auth", () => ({ auth: vi.fn() }))
vi.mock("@/lib/hero", () => ({
  getHeroResponse: vi.fn().mockResolvedValue({
    mode: "overview",
    mascot: "healthy",
    summary: { nodes: { ready: 1, total: 1 }, pods: { running: 1, total: 1 }, cpu: 10, memory: 20, syncedAgo: null },
    incidents: [],
    incidentTotalCount: 0,
    copy: { title: "OK", subtitle: "All good" },
    generatedAt: "2026-01-01T00:00:00Z",
  }),
}))
vi.mock("@/lib/k8s-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/k8s-client")>()
  return { ...actual, getNamespaces: vi.fn() }
})

const { auth } = await import("@/lib/auth")
const { getHeroResponse } = await import("@/lib/hero")
const { getNamespaces } = await import("@/lib/k8s-client")
const { GET } = await import("./route")

const namespaces: NamespaceInfo[] = [
  { name: "tenant-a", status: "Active", labels: { "narwhal.io/team": "tenant-a-team" }, createdAt: "2026-01-01T00:00:00Z" },
]

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getNamespaces).mockResolvedValue(namespaces)
})

describe("GET /api/hero — unmapped user access gate", () => {
  it("returns 401 when unauthenticated", async () => {
    vi.mocked(auth).mockResolvedValue(null as never)
    const res = await GET()
    expect(res.status).toBe(401)
    expect(getHeroResponse).not.toHaveBeenCalled()
  })

  it("returns 403 ACCESS_NOT_GRANTED for an unmapped developer without reading hero data", async () => {
    vi.mocked(auth).mockResolvedValue({
      groups: ["developer"],
      teams: ["unmapped-team"],
      user: { role: "developer" },
    } as never)
    const res = await GET()
    expect(res.status).toBe(403)
    expect(await res.json()).toEqual({ error: "Forbidden", code: "ACCESS_NOT_GRANTED" })
    expect(getHeroResponse).not.toHaveBeenCalled()
  })

  it("returns 200 for a mapped team developer", async () => {
    vi.mocked(auth).mockResolvedValue({
      groups: ["developer"],
      teams: ["platform-team"],
      user: { role: "developer" },
    } as never)
    const res = await GET()
    expect(res.status).toBe(200)
    expect(getHeroResponse).toHaveBeenCalledTimes(1)
  })
})
