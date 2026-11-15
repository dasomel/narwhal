import { describe, expect, it } from "vitest"
import { getVisibilityScope, getUserScope, resolveNamespaceScope, TEAM_LABEL, type LabelledNamespace } from "./role-filter"

// narwhal#659: fail-closed visibility scope when a non-admin user has no matching
// static teamMapping. Exercises the real portal/config/role-filter.json.
describe("getVisibilityScope", () => {
  it("grants cluster-admin wildcard namespaces and projects regardless of team groups", () => {
    for (const teams of [[], ["unmapped-team"], ["platform-team"]]) {
      expect(getVisibilityScope(["cluster-admin"], teams)).toEqual({
        groups: ["cluster-admin"],
        namespaces: ["*"],
        argocdProjects: ["*"],
        hasMapping: true,
      })
    }
  })

  it("scopes a developer with a mapped team to that team's namespaces and projects", () => {
    expect(getVisibilityScope(["developer"], ["platform-team"])).toEqual({
      groups: ["platform-team"],
      namespaces: ["platform-*", "monitoring", "storage"],
      argocdProjects: ["platform"],
      hasMapping: true,
    })
  })

  it("fails closed (empty scope) for a developer whose team groups do not match any teamMapping", () => {
    expect(getVisibilityScope(["developer"], ["unmapped-team"])).toEqual({
      groups: [],
      namespaces: [],
      argocdProjects: [],
      hasMapping: false,
    })
  })

  it("fails closed (empty scope) for a developer with no team groups at all", () => {
    expect(getVisibilityScope(["developer"], [])).toEqual({
      groups: [],
      namespaces: [],
      argocdProjects: [],
      hasMapping: false,
    })
  })

  it("scopes a viewer with a mapped team and fails closed when unmapped or teamless", () => {
    expect(getVisibilityScope(["viewer"], ["frontend-team"])).toEqual({
      groups: ["frontend-team"],
      namespaces: ["frontend-*"],
      argocdProjects: ["apps"],
      hasMapping: true,
    })
    expect(getVisibilityScope(["viewer"], ["unmapped-team"])).toEqual({
      groups: [],
      namespaces: [],
      argocdProjects: [],
      hasMapping: false,
    })
    expect(getVisibilityScope(["viewer"], [])).toEqual({
      groups: [],
      namespaces: [],
      argocdProjects: [],
      hasMapping: false,
    })
  })

  it("fails closed for tampered/unknown role groups or an empty roleGroups array", () => {
    expect(getVisibilityScope(["super-admin", "cluster-admins"], ["unmapped-team"])).toEqual({
      groups: [],
      namespaces: [],
      argocdProjects: [],
      hasMapping: false,
    })
    expect(getVisibilityScope([], [])).toEqual({
      groups: [],
      namespaces: [],
      argocdProjects: [],
      hasMapping: false,
    })
  })

  it.each(["developer", "viewer"])("resolves label-only %s team ownership without granting other tenants", (role) => {
    const raw = getVisibilityScope([role], ["tenant-a-team"])
    const namespaces: LabelledNamespace[] = [
      { name: "tenant-a", labels: { [TEAM_LABEL]: "tenant-a-team" } },
      { name: "tenant-b", labels: { [TEAM_LABEL]: "tenant-b-team" } },
      { name: "monitoring", labels: {} },
    ]
    const resolved = resolveNamespaceScope(namespaces, raw, ["tenant-a-team"])
    expect(resolved.all).toBe(false)
    expect([...resolved.names]).toEqual(["tenant-a"])
    expect([...resolved.byLabel]).toEqual(["tenant-a"])
  })
})

describe("strict write scope remains mapping-only", () => {
  it("does not grant writes from role defaults or unknown teams", () => {
    expect(getUserScope(["developer", "viewer", "unmapped-team"])).toEqual({
      groups: [], namespaces: [], argocdProjects: [], hasMapping: false,
    })
  })

  it("retains a mapped team's write scope", () => {
    expect(getUserScope(["developer", "platform-team"])).toEqual({
      groups: ["platform-team"], namespaces: ["platform-*", "monitoring", "storage"],
      argocdProjects: ["platform"], hasMapping: true,
    })
  })
})
