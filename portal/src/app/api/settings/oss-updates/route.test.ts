import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("server-only", () => ({}))
vi.mock("@/lib/auth", () => ({ requireRole: vi.fn() }))
vi.mock("@/lib/oss-updates/status", () => ({ getOssUpdateStatus: vi.fn() }))

const { requireRole } = await import("@/lib/auth")
const { getOssUpdateStatus } = await import("@/lib/oss-updates/status")
const { GET } = await import("./route")

beforeEach(() => vi.clearAllMocks())

describe("GET /api/settings/oss-updates", () => {
  it("401s unauthenticated and 403s non cluster-admin without reading anything", async () => {
    vi.mocked(requireRole).mockResolvedValueOnce({ error: "unauthorized" } as never)
    expect((await GET()).status).toBe(401)
    vi.mocked(requireRole).mockResolvedValueOnce({ error: "forbidden" } as never)
    expect((await GET()).status).toBe(403)
    expect(getOssUpdateStatus).not.toHaveBeenCalled()
    expect(requireRole).toHaveBeenCalledWith("cluster-admin")
  })
  it("returns the status payload for a cluster-admin", async () => {
    vi.mocked(requireRole).mockResolvedValue({ session: { user: { role: "cluster-admin" } } } as never)
    vi.mocked(getOssUpdateStatus).mockResolvedValue({ rows: [], requestMode: "copy" } as never)
    const res = await GET()
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ rows: [], requestMode: "copy" })
  })
})
