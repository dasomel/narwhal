import { describe, expect, it, vi } from "vitest"

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

const { classifyGroupClaim, getRoleFromGroups, requireRole, sanitizeGroups, unknownGroups } = await import(
  "./auth"
)

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
