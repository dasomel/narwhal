import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// narwhal#459: next-auth beta.32 turns a non-OK /session response (what @auth/core returns for
// an invalid provider configuration) into `null`; beta.30 returned the error object, which is
// truthy, so `if (!session)` gates in requireRole() and the API routes failed open. Nothing
// here mocks next-auth or @auth/core: the real NextAuth()/auth() from ./auth runs end to end.
// Only next/headers (request scope) is supplied; vitest.config.ts inlines next-auth so its
// extensionless "next/server" import resolves.
const cookieJar = { value: "" }
vi.mock("next/headers", () => ({
  headers: async () =>
    new Headers({ host: "portal.test", "x-forwarded-proto": "https", cookie: cookieJar.value }),
}))
vi.mock("@/lib/alertmanager", () => ({ getAlerts: vi.fn(async () => []) }))

const ENV_KEYS = [
  "AUTH_SECRET",
  "AUTH_URL",
  "AUTH_TRUST_HOST",
  "AUTH_MOCK",
  "KEYCLOAK_ISSUER",
  "KEYCLOAK_CLIENT_ID",
  "KEYCLOAK_CLIENT_SECRET",
] as const
const saved: Record<string, string | undefined> = {}

beforeEach(() => {
  for (const k of ENV_KEYS) saved[k] = process.env[k]
  vi.resetModules()
  vi.spyOn(console, "error").mockImplementation(() => {})
  cookieJar.value = ""
  process.env.AUTH_TRUST_HOST = "true"
  process.env.AUTH_URL = "https://portal.test"
  process.env.KEYCLOAK_ISSUER = "https://keycloak.test/realms/narwhal"
  process.env.KEYCLOAK_CLIENT_ID = "portal"
  process.env.KEYCLOAK_CLIENT_SECRET = "s3cret"
  delete process.env.AUTH_MOCK
})

afterEach(() => {
  for (const k of ENV_KEYS) {
    if (saved[k] === undefined) delete process.env[k]
    else process.env[k] = saved[k]
  }
  vi.restoreAllMocks()
})

describe("Auth.js fails closed (real next-auth, #459)", () => {
  it("valid config and no session cookie yields no session (positive control)", async () => {
    process.env.AUTH_SECRET = "x".repeat(32)
    const { auth, requireRole } = await import("./auth")
    expect(await auth()).toBeNull()
    expect(await requireRole("viewer")).toEqual({ error: "unauthorized" })
  })

  it("invalid provider configuration (no AUTH_SECRET) yields null, not an error object", async () => {
    delete process.env.AUTH_SECRET
    const { auth, requireRole } = await import("./auth")
    const session = await auth()
    expect(session).toBeNull()
    expect(await requireRole("viewer", "developer", "cluster-admin")).toEqual({
      error: "unauthorized",
    })
  })

  it("GET /api/alerts returns 401 under the invalid configuration", async () => {
    delete process.env.AUTH_SECRET
    const { GET } = await import("@/app/api/alerts/route")
    const res = await GET(new Request("https://portal.test/api/alerts"))
    expect(res.status).toBe(401)
  })

  it("GET /api/alerts returns 401 with a valid config and no cookie (positive control)", async () => {
    process.env.AUTH_SECRET = "x".repeat(32)
    const { GET } = await import("@/app/api/alerts/route")
    const res = await GET(new Request("https://portal.test/api/alerts"))
    expect(res.status).toBe(401)
  })
})
