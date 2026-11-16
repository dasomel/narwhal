import { beforeEach, describe, expect, it, vi } from "vitest"

const mockRedirect = vi.hoisted(() =>
  vi.fn((url: string): never => {
    throw new Error(`NEXT_REDIRECT:${url}`)
  }),
)
const mockAuth = vi.hoisted(() => vi.fn())
const mockHasSessionAccess = vi.hoisted(() => vi.fn())

vi.mock("server-only", () => ({}))
vi.mock("next/navigation", () => ({ redirect: mockRedirect }))
vi.mock("@/lib/auth", () => ({ auth: mockAuth }))
vi.mock("@/lib/scope", () => ({ hasSessionAccess: mockHasSessionAccess }))

const { requireDashboardAccess } = await import("./dashboard-access")

describe("requireDashboardAccess", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("redirects unauthenticated callers to /login before checking scope", async () => {
    mockAuth.mockResolvedValue(null)
    await expect(requireDashboardAccess()).rejects.toThrow("NEXT_REDIRECT:/login")
    expect(mockRedirect).toHaveBeenCalledWith("/login")
    expect(mockHasSessionAccess).not.toHaveBeenCalled()
  })

  it("redirects non-admin callers with empty scope to /access-denied", async () => {
    const session = { groups: ["developer"], teams: [], user: { role: "developer" } }
    mockAuth.mockResolvedValue(session)
    mockHasSessionAccess.mockResolvedValue(false)

    await expect(requireDashboardAccess()).rejects.toThrow("NEXT_REDIRECT:/access-denied")
    expect(mockHasSessionAccess).toHaveBeenCalledWith(session)
    expect(mockRedirect).toHaveBeenCalledWith("/access-denied")
  })

  it("returns the session when scope access is granted", async () => {
    const session = { groups: ["developer"], teams: ["platform-team"], user: { role: "developer" } }
    mockAuth.mockResolvedValue(session)
    mockHasSessionAccess.mockResolvedValue(true)

    await expect(requireDashboardAccess()).resolves.toBe(session)
    expect(mockRedirect).not.toHaveBeenCalled()
  })
})
