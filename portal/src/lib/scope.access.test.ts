import { beforeEach, describe, expect, it, vi } from "vitest"
import type { NamespaceInfo } from "./k8s-client"

vi.mock("./k8s-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./k8s-client")>()
  return { ...actual, getNamespaces: vi.fn() }
})

const { getNamespaces } = await import("./k8s-client")
const {
  ACCESS_NOT_GRANTED_CODE,
  hasEffectiveAccess,
  hasSessionAccess,
  resolveEffectiveAccess,
  getEffectiveScope,
} = await import("./scope")

const namespaces: NamespaceInfo[] = [
  { name: "platform-system", status: "Active", labels: {}, createdAt: "2026-01-01T00:00:00Z" },
  { name: "tenant-a", status: "Active", labels: { "narwhal.io/team": "tenant-a-team" }, createdAt: "2026-01-01T00:00:00Z" },
]

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getNamespaces).mockResolvedValue(namespaces)
})

describe("portal access gate (D-PORTAL-UNMAPPED-DENY)", () => {
  it("allows cluster-admin with or without team claims without listing namespaces", async () => {
    for (const teams of [[], ["unmapped-team"], ["platform-team"]]) {
      expect(await hasSessionAccess({ groups: ["cluster-admin"], teams })).toBe(true)
      expect(await resolveEffectiveAccess({ groups: ["cluster-admin"], teams })).toMatchObject({ allowed: true })
    }
    expect(getNamespaces).not.toHaveBeenCalled()
  })

  it("allows a non-admin user with a statically mapped team", async () => {
    expect(await hasSessionAccess({ groups: ["developer"], teams: ["platform-team"] })).toBe(true)
    expect(await hasSessionAccess({ groups: ["viewer"], teams: ["frontend-team"] })).toBe(true)
    expect(getNamespaces).not.toHaveBeenCalled()
  })

  it("allows a label-only team that owns a narwhal.io/team namespace", async () => {
    const verdict = await resolveEffectiveAccess({ groups: ["developer"], teams: ["tenant-a-team"] })
    expect(verdict.allowed).toBe(true)
    expect(getNamespaces).toHaveBeenCalledTimes(1)
    if (verdict.allowed && verdict.scope) {
      expect([...verdict.scope.namespaces]).toEqual(["tenant-a"])
    }
  })

  it.each(["developer", "viewer"])("denies an unmapped %s team with no matching namespace label", async (role) => {
    expect(await hasSessionAccess({ groups: [role], teams: ["unmapped-team"] })).toBe(false)
    expect(getNamespaces).toHaveBeenCalledTimes(1)
  })

  it.each(["developer", "viewer"])("denies a %s with no team claims without listing namespaces", async (role) => {
    expect(await hasSessionAccess({ groups: [role], teams: [] })).toBe(false)
    expect(getNamespaces).not.toHaveBeenCalled()
  })

  it("denies guest, unknown/tampered role groups, empty groups, and null session", async () => {
    for (const session of [
      null,
      undefined,
      { groups: ["guest"], teams: [] },
      { groups: ["cluster-admins"], teams: [] },
      { groups: ["super-admin"], teams: [] },
      { groups: [], teams: [] },
    ]) {
      expect(await hasSessionAccess(session)).toBe(false)
    }
    expect(await hasSessionAccess({ groups: ["guest"], teams: ["unmapped-team"] })).toBe(false)
  })

  it("keeps hasEffectiveAccess consistent with getEffectiveScope", async () => {
    const mapped = await getEffectiveScope({ groups: ["developer"], teams: ["platform-team"] })
    const labelOnly = await getEffectiveScope({ groups: ["viewer"], teams: ["tenant-a-team"] })
    const unmapped = await getEffectiveScope({ groups: ["developer"], teams: ["unmapped-team"] })
    const teamless = await getEffectiveScope({ groups: ["developer"], teams: [] })

    expect(hasEffectiveAccess(mapped)).toBe(true)
    expect(hasEffectiveAccess(labelOnly)).toBe(true)
    expect(hasEffectiveAccess(unmapped)).toBe(false)
    expect(hasEffectiveAccess(teamless)).toBe(false)
    expect(ACCESS_NOT_GRANTED_CODE).toBe("ACCESS_NOT_GRANTED")
  })
})
