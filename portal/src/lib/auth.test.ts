import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Session } from "next-auth"
import type { JWT } from "next-auth/jwt"

// auth.ts calls NextAuth(config) at module load time, which pulls in next-auth's
// "next/server" import — that subpath fails to resolve under vitest's plain-Node
// environment (pre-existing, unrelated to this change: reproduces with a bare
// `node -e "import('next/server')"` too). These modules aren't exercised by the
// pure-function tests below, so stub them out rather than loading the real thing.
const mockAuth = vi.fn()
vi.mock("next-auth", () => ({
  default: () => ({ handlers: {}, auth: mockAuth, signIn: vi.fn(), signOut: vi.fn() }),
}))
vi.mock("next-auth/providers/credentials", () => ({
  default: (opts: unknown) => opts,
}))
vi.mock("./k8s-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./k8s-client")>()
  return { ...actual, getNamespaces: vi.fn(async () => []) }
})

const { classifyGroupClaim, config, getRoleFromGroups, requireRole, sanitizeGroups, unknownGroups } =
  await import("./auth")

describe("sanitizeGroups", () => {
  it("keeps known groups", () => expect(sanitizeGroups(["developer"])).toEqual(["developer"]))
  it("drops unrecognized groups", () =>
    expect(sanitizeGroups(["some-random-idp-group"])).toEqual([]))
  it("keeps known and drops unknown from a mixed list", () =>
    expect(sanitizeGroups(["developer", "some-random-group"])).toEqual(["developer"]))
  it("empty/absent input sanitizes to an empty list", () => {
    expect(sanitizeGroups([])).toEqual([])
    expect(sanitizeGroups(undefined)).toEqual([])
  })
})

describe("unknownGroups", () => {
  it("is empty for fully known input", () => expect(unknownGroups(["developer"])).toEqual([]))
  it("surfaces unrecognized values", () =>
    expect(unknownGroups(["some-random-idp-group"])).toEqual(["some-random-idp-group"]))
  it("surfaces the unknown one even when a known group is also present", () =>
    expect(unknownGroups(["developer", "some-random-group"])).toEqual(["some-random-group"]))
})

describe("classifyGroupClaim", () => {
  it("known groups -> ok", () => expect(classifyGroupClaim(["developer"])).toBe("ok"))
  it("entirely unrecognized groups -> unknown_groups", () =>
    expect(classifyGroupClaim(["some-random-idp-group"])).toBe("unknown_groups"))
  it("empty array -> no_groups", () => expect(classifyGroupClaim([])).toBe("no_groups"))
  it("absent/undefined -> no_groups", () => expect(classifyGroupClaim(undefined)).toBe("no_groups"))
  it("mixed known + unknown -> unknown_groups (unknown must not hide behind a valid match)", () =>
    expect(classifyGroupClaim(["developer", "some-random-group"])).toBe("unknown_groups"))
})

describe("getRoleFromGroups", () => {
  it("known group resolves to its role", () => expect(getRoleFromGroups(["developer"])).toBe("developer"))
  it("unrecognized groups sanitize to empty and fail closed to guest", () => {
    const sanitized = sanitizeGroups(["some-random-idp-group"])
    expect(getRoleFromGroups(sanitized)).toBe("guest")
  })
  it("empty/absent groups resolve to guest", () => expect(getRoleFromGroups([])).toBe("guest"))
  it("mixed known + unknown: the known role still wins", () => {
    const sanitized = sanitizeGroups(["developer", "some-random-group"])
    expect(getRoleFromGroups(sanitized)).toBe("developer")
  })
  it("cluster-admin outranks a lower role present in the same list", () =>
    expect(getRoleFromGroups(["developer", "cluster-admin"])).toBe("cluster-admin"))
})

describe("requireRole effective scope gate (D-PORTAL-UNMAPPED-DENY)", () => {
  it("denies a teamless/unmapped developer with ACCESS_NOT_GRANTED", async () => {
    mockAuth.mockResolvedValue({
      groups: ["developer"],
      teams: [],
      user: { role: "developer" },
    })
    expect(await requireRole("cluster-admin", "developer")).toEqual({
      error: "forbidden",
      code: "ACCESS_NOT_GRANTED",
    })
  })

  it("uses ACCESS_NOT_GRANTED when an unmapped guest is excluded by the route role list", async () => {
    mockAuth.mockResolvedValue({
      groups: ["guest"],
      teams: [],
      user: { role: "guest" },
    })
    expect(await requireRole("cluster-admin", "developer", "viewer")).toEqual({
      error: "forbidden",
      code: "ACCESS_NOT_GRANTED",
    })
  })

  it("allows a mapped developer and cluster-admin", async () => {
    const mapped = { groups: ["developer"], teams: ["platform-team"], user: { role: "developer" } }
    mockAuth.mockResolvedValue(mapped)
    expect(await requireRole("cluster-admin", "developer")).toEqual({ session: mapped })

    const admin = { groups: ["cluster-admin"], teams: [], user: { role: "cluster-admin" } }
    mockAuth.mockResolvedValue(admin)
    expect(await requireRole("cluster-admin")).toEqual({ session: admin })
  })

  it("keeps guest-inclusive health summary role checks reachable", async () => {
    const guest = { groups: ["guest"], teams: [], user: { role: "guest" } }
    mockAuth.mockResolvedValue(guest)
    expect(await requireRole("cluster-admin", "developer", "viewer", "guest")).toEqual({
      session: guest,
    })
  })
})

function makeJwt(
  payload: Record<string, unknown>,
  header: Record<string, unknown> = { alg: "RS256", typ: "JWT" },
): string {
  const encode = (v: Record<string, unknown>) =>
    Buffer.from(JSON.stringify(v), "utf8").toString("base64url")
  return `${encode(header)}.${encode(payload)}.test-signature`
}

function makeSession(token: JWT): Session {
  type SessionCallbackParams = Parameters<
    NonNullable<NonNullable<typeof config.callbacks>["session"]>
  >[0]
  const session: Session = {
    expires: new Date(Date.UTC(2026, 10, 19, 2, 0, 0)).toISOString(),
    groups: [],
    user: { role: "guest" },
  }
  return config.callbacks!.session!({
    session: session as unknown as SessionCallbackParams["session"],
    token,
    user: undefined as never,
    newSession: undefined,
    trigger: "update",
  }) as Session
}

async function runJwt(token: JWT): Promise<JWT> {
  return (await config.callbacks!.jwt!({
    token,
    user: undefined as never,
    account: null,
  })) as JWT
}

describe("jwt refresh group re-derivation (narwhal#734)", () => {
  const FIXED_NOW_MS = Date.UTC(2026, 10, 19, 1, 0, 0)
  const FIXED_NOW_SEC = Math.floor(FIXED_NOW_MS / 1000)
  const ISSUER = "https://keycloak.test/realms/narwhal"
  const CLIENT_ID = "narwhal-portal"
  const ENV_KEYS = ["AUTH_MOCK", "KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_CLIENT_SECRET"] as const
  const savedEnv: Record<string, string | undefined> = {}

  beforeEach(() => {
    for (const k of ENV_KEYS) savedEnv[k] = process.env[k]
    delete process.env.AUTH_MOCK
    process.env.KEYCLOAK_ISSUER = ISSUER
    process.env.KEYCLOAK_CLIENT_ID = CLIENT_ID
    process.env.KEYCLOAK_CLIENT_SECRET = "s3cret"
    vi.useFakeTimers()
    vi.setSystemTime(FIXED_NOW_MS)
    vi.spyOn(console, "error").mockImplementation(() => {})
    vi.spyOn(console, "warn").mockImplementation(() => {})
  })

  afterEach(() => {
    for (const k of ENV_KEYS) {
      if (savedEnv[k] === undefined) delete process.env[k]
      else process.env[k] = savedEnv[k]
    }
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("shrinks token.groups, teams, and derived role when refreshed id_token has fewer groups", async () => {
    const refreshedIdToken = makeJwt({
      iss: ISSUER,
      aud: CLIENT_ID,
      sub: "user-1",
      exp: FIXED_NOW_SEC + 300,
      groups: ["viewer", "frontend-team"],
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            access_token: "new-access",
            expires_in: 300,
            refresh_token: "rt-2",
            id_token: refreshedIdToken,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    const token = await runJwt({
      sub: "user-1",
      groups: ["cluster-admin", "developer"],
      teams: ["platform-team", "frontend-team"],
      groupClaimStatus: "ok",
      refreshToken: "rt-1",
      accessToken: "old-access",
      idToken: "old-id",
      expiresAt: FIXED_NOW_SEC + 30,
    })

    expect(token.groups).toEqual(["viewer"])
    expect(token.teams).toEqual(["frontend-team"])
    expect(token.groupClaimStatus).toBe("unknown_groups")
    expect(token.error).toBeUndefined()
    expect(token.idToken).toBe(refreshedIdToken)
    expect(token.refreshToken).toBe("rt-2")

    const session = makeSession(token)
    expect(session.groups).toEqual(["viewer"])
    expect(session.teams).toEqual(["frontend-team"])
    expect(session.user.role).toBe("viewer")
    expect(session.error).toBeUndefined()
  })

  it("shrinks to guest and empty teams when refreshed token carries an empty groups array", async () => {
    const refreshedIdToken = makeJwt({
      iss: ISSUER,
      aud: CLIENT_ID,
      sub: "user-1",
      exp: FIXED_NOW_SEC + 300,
      groups: [],
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            access_token: "new-access",
            expires_in: 300,
            id_token: refreshedIdToken,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    const token = await runJwt({
      sub: "user-1",
      groups: ["developer"],
      teams: ["platform-team"],
      groupClaimStatus: "ok",
      refreshToken: "rt-empty-groups",
      expiresAt: FIXED_NOW_SEC + 10,
    })

    expect(token.groups).toEqual(["guest"])
    expect(token.teams).toEqual([])
    expect(token.groupClaimStatus).toBe("no_groups")
    expect(token.error).toBeUndefined()

    const session = makeSession(token)
    expect(session.user.role).toBe("guest")
    expect(session.teams).toEqual([])
    expect(session.error).toBeUndefined()
  })

  it("grows token.groups, teams, and derived role when refreshed token adds groups (including access_token fallback when id_token is absent)", async () => {
    const refreshedAccessToken = makeJwt({
      iss: ISSUER,
      aud: [CLIENT_ID, "account"],
      sub: "user-2",
      exp: FIXED_NOW_SEC + 300,
      groups: ["developer", "platform-team"],
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            access_token: refreshedAccessToken,
            expires_in: 300,
            refresh_token: "rt-grow-2",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    const token = await runJwt({
      sub: "user-2",
      groups: ["viewer"],
      teams: [],
      groupClaimStatus: "ok",
      refreshToken: "rt-grow-1",
      expiresAt: FIXED_NOW_SEC + 30,
    })

    expect(token.groups).toEqual(["developer"])
    expect(token.teams).toEqual(["platform-team"])
    expect(token.groupClaimStatus).toBe("unknown_groups")
    expect(token.error).toBeUndefined()

    const session = makeSession(token)
    expect(session.groups).toEqual(["developer"])
    expect(session.teams).toEqual(["platform-team"])
    expect(session.user.role).toBe("developer")
    expect(session.expiresAt).toBe(FIXED_NOW_SEC + 300)
    expect(session.error).toBeUndefined()
  })

  it("fails closed to guest and flags session when refreshed token has no groups claim", async () => {
    const tokenWithoutGroups = makeJwt({
      iss: ISSUER,
      aud: CLIENT_ID,
      sub: "user-3",
      exp: FIXED_NOW_SEC + 300,
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            access_token: "new-access",
            expires_in: 300,
            id_token: tokenWithoutGroups,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    const token = await runJwt({
      sub: "user-3",
      groups: ["cluster-admin"],
      teams: ["platform-team"],
      groupClaimStatus: "ok",
      refreshToken: "rt-no-groups",
      expiresAt: FIXED_NOW_SEC + 30,
    })

    expect(token.groups).toEqual(["guest"])
    expect(token.teams).toEqual([])
    expect(token.error).toBe("RefreshTokenError")

    const session = makeSession(token)
    expect(session.groups).toEqual(["guest"])
    expect(session.teams).toEqual([])
    expect(session.user.role).toBe("guest")
    expect(session.error).toBe("RefreshTokenError")
  })

  it("fails closed to guest and flags session on non-array groups claim", async () => {
    const malformedGroupsToken = makeJwt({
      iss: ISSUER,
      aud: CLIENT_ID,
      sub: "user-3b",
      exp: FIXED_NOW_SEC + 300,
      groups: "cluster-admin",
    })
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            access_token: "new-access",
            expires_in: 300,
            id_token: malformedGroupsToken,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    const token = await runJwt({
      sub: "user-3b",
      groups: ["developer"],
      teams: ["platform-team"],
      groupClaimStatus: "ok",
      refreshToken: "rt-bad-groups-type",
      expiresAt: FIXED_NOW_SEC + 30,
    })

    expect(token.groups).toEqual(["guest"])
    expect(token.teams).toEqual([])
    expect(token.error).toBe("RefreshTokenError")
  })

  it("fails closed to guest and flags session on iss/aud mismatch or expired exp during refresh", async () => {
    const cases = [
      {
        name: "iss mismatch",
        jwt: makeJwt({
          iss: "https://evil.example/realms/narwhal",
          aud: CLIENT_ID,
          sub: "user-4",
          exp: FIXED_NOW_SEC + 300,
          groups: ["cluster-admin", "platform-team"],
        }),
      },
      {
        name: "aud mismatch",
        jwt: makeJwt({
          iss: ISSUER,
          aud: "other-client",
          sub: "user-4",
          exp: FIXED_NOW_SEC + 300,
          groups: ["cluster-admin", "platform-team"],
        }),
      },
      {
        name: "expired exp",
        jwt: makeJwt({
          iss: ISSUER,
          aud: CLIENT_ID,
          sub: "user-4",
          exp: FIXED_NOW_SEC - 1,
          groups: ["cluster-admin", "platform-team"],
        }),
      },
      {
        name: "alg none",
        jwt: makeJwt(
          {
            iss: ISSUER,
            aud: CLIENT_ID,
            sub: "user-4",
            exp: FIXED_NOW_SEC + 300,
            groups: ["cluster-admin", "platform-team"],
          },
          { alg: "none", typ: "JWT" },
        ),
      },
    ]

    for (const [idx, c] of cases.entries()) {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () =>
          new Response(
            JSON.stringify({
              access_token: "untrusted-access",
              expires_in: 300,
              id_token: c.jwt,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        ),
      )

      const token = await runJwt({
        sub: "user-4",
        groups: ["cluster-admin"],
        teams: ["platform-team"],
        groupClaimStatus: "ok",
        idToken: "orig-id-token",
        refreshToken: `rt-invalid-${idx}`,
        expiresAt: FIXED_NOW_SEC + 30,
      })

      expect(token.groups, c.name).toEqual(["guest"])
      expect(token.teams, c.name).toEqual([])
      expect(token.error, c.name).toBe("RefreshTokenError")
      expect(token.idToken, c.name).toBe("orig-id-token")

      const session = makeSession(token)
      expect(session.user.role, c.name).toBe("guest")
      expect(session.error, c.name).toBe("RefreshTokenError")
    }
  })

  it("fails closed to guest, clears teams, sets RefreshTokenError, and denies access via requireRole when the Keycloak token endpoint fails", async () => {
    for (const stub of [
      () => Promise.resolve(new Response("invalid_grant", { status: 400 })),
      () => Promise.reject(new Error("ECONNREFUSED")),
    ]) {
      vi.stubGlobal("fetch", vi.fn(stub))

      const token = await runJwt({
        sub: "user-5",
        groups: ["cluster-admin"],
        teams: ["platform-team"],
        groupClaimStatus: "ok",
        refreshToken: "rt-fail",
        expiresAt: FIXED_NOW_SEC + 30,
      })

      expect(token.error).toBe("RefreshTokenError")
      expect(token.groups).toEqual(["guest"])
      expect(token.teams).toEqual([])
      expect(token.groupClaimStatus).toBe("no_groups")

      const session = makeSession(token)
      expect(session.groups).toEqual(["guest"])
      expect(session.teams).toEqual([])
      expect(session.user.role).toBe("guest")
      expect(session.error).toBe("RefreshTokenError")

      mockAuth.mockResolvedValueOnce(session)
      expect(await requireRole("cluster-admin", "developer")).toEqual({
        error: "forbidden",
        code: "ACCESS_NOT_GRANTED",
      })
    }
  })
})
