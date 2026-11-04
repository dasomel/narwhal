import { beforeEach, describe, expect, it, vi } from "vitest"
import { InMemoryIdempotencyStore, setIdempotencyStoreForTesting } from "@/lib/idempotency"

vi.mock("server-only", () => ({}))
vi.mock("@/lib/auth", () => ({
  requireRole: vi.fn(),
  getActorId: vi.fn((s) => s.user.email ?? "unknown"),
}))
vi.mock("@/lib/operation-context", () => ({
  beginOperation: vi.fn().mockResolvedValue({}),
  completeOperation: vi.fn().mockResolvedValue(undefined),
  failOperation: vi.fn().mockResolvedValue(undefined),
}))
vi.mock("@/lib/oss-updates/upstream", () => ({ getUpstreamReleases: vi.fn() }))
vi.mock("@/lib/oss-updates/status", () => ({
  getIssueRepo: () => "dasomel/narwhal",
  declaredRow: (c: string) => (c === "Falco" ? { component: "Falco", section: "Security", version: "0.45.1", chartVersion: "9.2.1" } : undefined),
}))

const { requireRole } = await import("@/lib/auth")
const { beginOperation, completeOperation } = await import("@/lib/operation-context")
const { getUpstreamReleases } = await import("@/lib/oss-updates/upstream")
const { POST } = await import("./route")

const admin = { user: { role: "cluster-admin", email: "admin@example.com" } }
const req = (body: unknown) => new Request("http://localhost/api/settings/oss-updates/request", { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body) })

beforeEach(() => {
  vi.clearAllMocks()
  setIdempotencyStoreForTesting(new InMemoryIdempotencyStore())
  vi.mocked(requireRole).mockResolvedValue({ session: admin } as never)
  vi.mocked(getUpstreamReleases).mockResolvedValue(null)
})

describe("POST /api/settings/oss-updates/request — auth", () => {
  it("401/403 without side effects", async () => {
    vi.mocked(requireRole).mockResolvedValueOnce({ error: "unauthorized" } as never)
    expect((await POST(req({ component: "Falco", target: "0.46.0" }))).status).toBe(401)
    vi.mocked(requireRole).mockResolvedValueOnce({ error: "forbidden" } as never)
    expect((await POST(req({ component: "Falco", target: "0.46.0" }))).status).toBe(403)
    expect(beginOperation).not.toHaveBeenCalled()
  })
})

describe("POST /api/settings/oss-updates/request — validation", () => {
  it.each([
    ["not json", "{", "component"],
    ["unknown component", { component: "Nope", target: "1.0.0" }, "component"],
    ["non-string component", { component: 1, target: "1.0.0" }, "component"],
    ["junk target", { component: "Falco", target: "latest; rm -rf /" }, "target"],
    ["markdown injection", { component: "Falco", target: "1.0.0\n## x" }, "target"],
    ["equal to declared", { component: "Falco", target: "v0.45.1" }, "target"],
    ["downgrade", { component: "Falco", target: "0.44.0" }, "target"],
  ])("400s %s", async (_n, body, field) => {
    const res = await POST(req(body))
    expect(res.status).toBe(400)
    expect((await res.json()).field).toBe(field)
    expect(beginOperation).not.toHaveBeenCalled()
  })
})

describe("POST /api/settings/oss-updates/request — cooling", () => {
  it("409s a target younger than 7 days when upstream knows it", async () => {
    vi.mocked(getUpstreamReleases).mockResolvedValue([{ tag: "0.46.0", publishedAt: new Date(Date.now() - 86_400_000).toISOString(), url: "https://x" }])
    const res = await POST(req({ component: "Falco", target: "0.46.0" }))
    expect(res.status).toBe(409)
    expect((await res.json()).error).toBe("Cooling")
    expect(beginOperation).not.toHaveBeenCalled()
  })
  it("allows a cooled target and uses its release url", async () => {
    vi.mocked(getUpstreamReleases).mockResolvedValue([{ tag: "0.46.0", publishedAt: new Date(Date.now() - 20 * 86_400_000).toISOString(), url: "https://github.com/falcosecurity/falco/releases/tag/0.46.0" }])
    const res = await POST(req({ component: "Falco", target: "0.46.0" }))
    expect(res.status).toBe(201)
    expect((await res.json()).issue.body).toContain("releases/tag/0.46.0")
  })
})

describe("POST /api/settings/oss-updates/request — issue + audit + idempotency", () => {
  it("creates the issue text, audits once, and replays duplicates without a second audit", async () => {
    const first = await POST(req({ component: "Falco", target: "v0.46.0" }))
    expect(first.status).toBe(201)
    const body = await first.json()
    expect(body).toMatchObject({ component: "Falco", from: "0.45.1", target: "0.46.0", duplicate: false, requestedBy: "admin@example.com" })
    expect(body.issue.title).toBe("deps: upgrade Falco 0.45.1 -> 0.46.0")
    expect(body.issue.body).toContain("Checklist")
    expect(body.issue.newIssueUrl).toContain("https://github.com/dasomel/narwhal/issues/new?")
    expect(vi.mocked(beginOperation).mock.calls[0][0]).toMatchObject({ operationType: "oss.update.request", source: "manual", resource: { kind: "OssComponent", name: "Falco" } })
    expect(completeOperation).toHaveBeenCalledTimes(1)

    const second = await POST(req({ component: "Falco", target: "0.46.0" }))
    expect(second.status).toBe(200)
    const dup = await second.json()
    expect(dup).toMatchObject({ duplicate: true, requestedBy: "admin@example.com", requestedAt: body.requestedAt })
    expect(dup.issue.body).toBe(body.issue.body)
    expect(beginOperation).toHaveBeenCalledTimes(1)

    const other = await POST(req({ component: "Falco", target: "0.47.0" }))
    expect(other.status).toBe(201)
    expect(beginOperation).toHaveBeenCalledTimes(2)
  })
})
