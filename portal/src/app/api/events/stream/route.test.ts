import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { SCOPE_FINGERPRINT_HEADER } from "@/lib/scope-constants"

const metrics = { connectedClients: 0, disconnectCleanups: 0 }
const streamState = { failSetup: false, fingerprint: "" }

vi.mock("@/lib/auth", () => ({ auth: vi.fn(async () => ({ user: { role: "admin" }, groups: [], teams: [] })) }))
vi.mock("@/lib/scope", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/scope")>()
  return {
    ...actual,
    requireEffectiveScope: vi.fn(async (session) => {
      if (session?.error) return actual.requireEffectiveScope(session)
      return {
        scope: streamState.fingerprint ? { fingerprint: streamState.fingerprint } : {},
      }
    }),
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
const { auth } = await import("@/lib/auth")
const { getLiveStreamMetrics, subscribeLiveWithReplay } = await import("@/lib/live-stream")
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

  it("denies a session carrying RefreshTokenError via requireEffectiveScope", async () => {
    vi.mocked(auth).mockResolvedValueOnce({
      user: { role: "cluster-admin" },
      groups: ["cluster-admin"],
      teams: [],
      error: "RefreshTokenError",
    } as never)

    const response = await GET(new Request("http://localhost/api/events/stream"))
    expect(response.status).toBe(403)
    expect(await response.json()).toEqual({ error: "Forbidden", code: "ACCESS_NOT_GRANTED" })
    expect(getLiveStreamMetrics().connectedClients).toBe(0)
  })
})

async function drainUntilDone(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<boolean> {
  for (let i = 0; i < 20; i++) {
    const { done } = await reader.read()
    if (done) return true
  }
  return false
}

describe("/api/events/stream access-token lifetime bounding (narwhal#734)", () => {
  const FIXED_NOW_MS = Date.UTC(2026, 10, 19, 1, 0, 0)
  const FIXED_NOW_SEC = Math.floor(FIXED_NOW_MS / 1000)

  beforeEach(() => {
    metrics.connectedClients = 0
    metrics.disconnectCleanups = 0
    streamState.failSetup = false
    streamState.fingerprint = ""
    vi.useFakeTimers()
    vi.setSystemTime(FIXED_NOW_MS)
    vi.mocked(subscribeLiveWithReplay).mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("closes the SSE stream and aborts its live subscription when session.expiresAt is reached", async () => {
    vi.mocked(auth).mockResolvedValueOnce({
      user: { role: "developer" },
      groups: ["developer"],
      teams: ["platform-team"],
      expiresAt: FIXED_NOW_SEC + 120,
    } as never)

    const response = await GET(new Request("http://localhost/api/events/stream"))
    const reader = response.body!.getReader()
    await reader.read()

    expect(metrics.connectedClients).toBe(1)
    const subSignal = vi.mocked(subscribeLiveWithReplay).mock.calls[0]?.[1]
    expect(subSignal?.aborted).toBe(false)

    await vi.advanceTimersByTimeAsync(119_000)
    expect(metrics.connectedClients).toBe(1)
    expect(subSignal?.aborted).toBe(false)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(subSignal?.aborted).toBe(true)
    expect(getLiveStreamMetrics().connectedClients).toBe(0)
    await expect(drainUntilDone(reader)).resolves.toBe(true)
  })

  it("closes the SSE stream after the 5-minute default lifetime when session.expiresAt is unknown", async () => {
    vi.mocked(auth).mockResolvedValueOnce({
      user: { role: "developer" },
      groups: ["developer"],
      teams: ["platform-team"],
    } as never)

    const response = await GET(new Request("http://localhost/api/events/stream"))
    const reader = response.body!.getReader()
    await reader.read()

    expect(metrics.connectedClients).toBe(1)

    await vi.advanceTimersByTimeAsync(5 * 60_000 - 1_000)
    expect(metrics.connectedClients).toBe(1)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(getLiveStreamMetrics().connectedClients).toBe(0)
    await expect(drainUntilDone(reader)).resolves.toBe(true)
  })
})
