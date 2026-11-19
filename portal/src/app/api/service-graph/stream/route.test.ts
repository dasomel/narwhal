import { beforeEach, describe, expect, it, vi } from "vitest"
import { NextRequest } from "next/server"
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
vi.mock("@/lib/argocd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/argocd")>()
  return { ...actual, getArgoApps: vi.fn(async () => []) }
})
vi.mock("@/lib/hubble-stream", () => ({
  startFlowStream: vi.fn(() => ({ cancel: vi.fn() })),
}))

const { startFlowStream } = await import("@/lib/hubble-stream")
const { GET } = await import("./route")

const namespaces: NamespaceInfo[] = [
  { name: "team-a-ns", status: "Active", labels: { "narwhal.io/team": "team-a" }, createdAt: "2026-01-01T00:00:00Z" },
]

describe("GET /api/service-graph/stream — cluster-admin only", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetNamespaces.mockResolvedValue(namespaces)
  })

  it("returns 401 when unauthenticated", async () => {
    mockAuth.mockResolvedValue(null)
    const res = await GET(new NextRequest("http://localhost/api/service-graph/stream"))
    expect(res.status).toBe(401)
  })

  it.each(["developer", "viewer"] as const)("returns 403 without ACCESS_NOT_GRANTED for mapped %s", async (role) => {
    mockAuth.mockResolvedValue({ groups: [role], teams: ["team-a"], user: { role } })
    const res = await GET(new NextRequest("http://localhost/api/service-graph/stream"))
    expect(res.status).toBe(403)
    const body = await res.json()
    expect(body).toEqual({ error: "Forbidden" })
    expect(startFlowStream).not.toHaveBeenCalled()
  })

  it("opens SSE stream for cluster-admin", async () => {
    mockAuth.mockResolvedValue({ groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } })
    const ac = new AbortController()
    const res = await GET(new NextRequest("http://localhost/api/service-graph/stream", { signal: ac.signal }))
    expect(res.status).toBe(200)
    expect(res.headers.get("Content-Type")).toBe("text/event-stream")
    ac.abort()
  })

  it("closes the admin stream when the access token expires (narwhal#734)", async () => {
    vi.useFakeTimers()
    try {
      const now = Date.now()
      mockAuth.mockResolvedValue({
        groups: ["cluster-admin"],
        teams: [],
        user: { role: "cluster-admin" },
        expiresAt: Math.floor(now / 1000) + 60,
      })
      const cancel = vi.fn()
      vi.mocked(startFlowStream).mockReturnValueOnce({ cancel } as unknown as ReturnType<typeof startFlowStream>)
      const res = await GET(new NextRequest("http://localhost/api/service-graph/stream"))
      const reader = res.body!.getReader()
      const done = reader.read().then(function next(r): Promise<boolean> | boolean {
        return r.done ? true : reader.read().then(next)
      })
      await vi.advanceTimersByTimeAsync(59_000)
      expect(cancel).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(2_000)
      expect(cancel).toHaveBeenCalledTimes(1)
      await expect(done).resolves.toBe(true)
      // The lifetime comes from the session the admin gate authorised: one auth() read only.
      expect(mockAuth).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
