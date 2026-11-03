import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("server-only", () => ({}))
const store = new Map<string, { value: unknown; ttl: number }>()
vi.mock("../valkey", () => ({
  cacheGet: vi.fn(async (k: string) => store.get(k)?.value ?? null),
  cacheSet: vi.fn(async (k: string, value: unknown, ttl: number) => { store.set(k, { value, ttl }) }),
}))
const fetchWithPolicy = vi.fn()
vi.mock("../http-client", () => ({
  fetchWithPolicy: (...a: unknown[]) => fetchWithPolicy(...a),
  readJsonWithPolicy: async (res: Response) => res.json(),
}))

const { getUpstreamReleases, normalizeReleases } = await import("./upstream")

const gh = (tag: string, extra: Record<string, unknown> = {}) => ({
  tag_name: tag, published_at: "2026-10-01T00:00:00Z", html_url: `https://github.com/o/r/releases/tag/${tag}`, ...extra,
})

beforeEach(() => {
  store.clear()
  fetchWithPolicy.mockReset()
  delete process.env.OSS_UPDATES_UPSTREAM
})

describe("normalizeReleases", () => {
  it("drops drafts, prereleases, rc tags and unparsable tags, newest first", () => {
    const out = normalizeReleases([
      gh("v1.2.0"), gh("v1.10.0"), gh("v2.0.0-rc.1"), gh("v1.9.0", { prerelease: true }),
      gh("v1.8.0", { draft: true }), gh("nightly"), { tag_name: "v1.1.0" }, gh("v1.3.0", { html_url: "http://insecure" }),
    ])
    expect(out.map((r) => r.tag)).toEqual(["v1.10.0", "v1.3.0", "v1.2.0"])
    expect(out.find((r) => r.tag === "v1.3.0")?.url).toBe("")
  })
  it("returns [] for non-array input", () => {
    expect(normalizeReleases({ message: "rate limited" })).toEqual([])
  })
})

describe("getUpstreamReleases", () => {
  it("makes NO network call when the flag is off (air-gap default)", async () => {
    expect(await getUpstreamReleases("o/r")).toBeNull()
    expect(fetchWithPolicy).not.toHaveBeenCalled()
  })

  it("rejects a malformed repo even with the flag on", async () => {
    process.env.OSS_UPDATES_UPSTREAM = "true"
    expect(await getUpstreamReleases("../../etc/passwd")).toBeNull()
    expect(fetchWithPolicy).not.toHaveBeenCalled()
  })

  it("fetches unauthenticated, sends no credentials, and caches 6h", async () => {
    process.env.OSS_UPDATES_UPSTREAM = "true"
    fetchWithPolicy.mockResolvedValue(new Response(JSON.stringify([gh("v1.2.0")]), { status: 200 }))
    const out = await getUpstreamReleases("o/r")
    expect(out?.map((r) => r.tag)).toEqual(["v1.2.0"])
    const [url, init] = fetchWithPolicy.mock.calls[0]
    expect(url).toBe("https://api.github.com/repos/o/r/releases?per_page=30")
    expect(Object.keys((init as RequestInit).headers as Record<string, string>).map((h) => h.toLowerCase())).not.toContain("authorization")
    expect([...store.values()][0].ttl).toBe(21600)
    await getUpstreamReleases("o/r")
    expect(fetchWithPolicy).toHaveBeenCalledTimes(1)
  })

  it("degrades to null on failure and caches the failure for 10 minutes only", async () => {
    process.env.OSS_UPDATES_UPSTREAM = "true"
    fetchWithPolicy.mockResolvedValue(new Response("rate limited", { status: 403 }))
    expect(await getUpstreamReleases("o/r")).toBeNull()
    expect([...store.values()][0].ttl).toBe(600)
    expect(await getUpstreamReleases("o/r")).toBeNull()
    expect(fetchWithPolicy).toHaveBeenCalledTimes(1)
  })

  it("degrades to null on a network error", async () => {
    process.env.OSS_UPDATES_UPSTREAM = "true"
    fetchWithPolicy.mockRejectedValue(new Error("ENETUNREACH"))
    expect(await getUpstreamReleases("o/r")).toBeNull()
  })
})
