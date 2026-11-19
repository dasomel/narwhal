import { beforeEach, describe, expect, it, vi } from "vitest"

import { SCOPE_FINGERPRINT_HEADER } from "@/lib/scope-constants"

const metrics = { connectedClients: 0, disconnectCleanups: 0 }
const streamState = { failSetup: false, fingerprint: "" }

vi.mock("@/lib/auth", () => ({ auth: vi.fn(async () => ({ user: { role: "admin" }, groups: [], teams: [] })) }))
vi.mock("@/lib/scope", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/scope")>()
  return {
    ...actual,
    requireEffectiveScope: vi.fn(async () => ({
      scope: streamState.fingerprint ? { fingerprint: streamState.fingerprint } : {},
    })),
  }
})
vi.mock("@/lib/event-visibility", () => ({ isEventFiltered: vi.fn(() => false) }))
vi.mock("@/lib/live-stream", () => ({
  compareLiveEventIds: vi.fn(() => 1),
  connectLiveClient: vi.fn(() => { metrics.connectedClients++ }),
  disconnectLiveClient: vi.fn(() => { metrics.connectedClients = Math.max(0, metrics.connectedClients - 1); metrics.disconnectCleanups++ }),
  getLiveStreamMetrics: vi.fn(() => ({ ...metrics })),
  subscribeLiveWithReplay: vi.fn(async () => {
    if (streamState.failSetup) throw new Error("replay unavailable")
    return { replay: { events: [], gap: false, unknown: false }, live: { async *[Symbol.asyncIterator]() {} }, status: { dependency: "valkey", state: "ok", observedAt: new Date().toISOString() } }
  }),
}))

const { GET } = await import("./route")
const { getLiveStreamMetrics } = await import("@/lib/live-stream")
const { signScopeFingerprint } = await import("@/lib/scope")

describe("/api/events/stream client metrics", () => {
  beforeEach(() => {
    metrics.connectedClients = 0
    metrics.disconnectCleanups = 0
    streamState.failSetup = false
    streamState.fingerprint = ""
    vi.mocked(getLiveStreamMetrics).mockClear()
  })

  it("returns connected clients to zero when the request aborts", async () => {
    const abort = new AbortController()
    const response = await GET(new Request("http://localhost/api/events/stream", { signal: abort.signal }))
    const reader = response.body!.getReader()
    await reader.read()
    expect(metrics.connectedClients).toBe(1)

    abort.abort()
    await reader.read()

    expect(getLiveStreamMetrics().connectedClients).toBe(0)
  })

  it("returns connected clients to zero when replay setup throws", async () => {
    streamState.failSetup = true
    const response = await GET(new Request("http://localhost/api/events/stream"))
    const reader = response.body!.getReader()
    await reader.read()
    await reader.read()
    await expect(reader.read()).resolves.toMatchObject({ done: true })

    expect(getLiveStreamMetrics().connectedClients).toBe(0)
  })

  it("signs X-Narwhal-Scope with HMAC instead of exposing raw scope fingerprint", async () => {
    const rawFingerprint = "developer:team-a:platform-system"
    streamState.fingerprint = rawFingerprint
    const abort = new AbortController()
    const response = await GET(new Request("http://localhost/api/events/stream", { signal: abort.signal }))
    const header = response.headers.get(SCOPE_FINGERPRINT_HEADER)
    expect(header).toMatch(/^[0-9a-f]{32}$/)
    expect(header).toBe(signScopeFingerprint(rawFingerprint))
    expect(header).not.toContain("team-a")
    expect(header).not.toContain("platform-system")
    abort.abort()
    await response.body!.cancel()
  })
})
