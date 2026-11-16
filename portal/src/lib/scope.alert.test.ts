import { describe, expect, it } from "vitest"
import { alertVisible, type EffectiveScope } from "./scope"

function makeScope(all: boolean, namespaces: string[]): EffectiveScope {
  return {
    all,
    namespaces: new Set(namespaces),
    argocdProjects: all ? ["*"] : [],
    hasMapping: all || namespaces.length > 0,
    fingerprint: all ? "admin" : `scoped:${namespaces.join(",")}`,
    resolved: {
      all,
      names: new Set(namespaces),
      byLabel: new Set(namespaces),
      byPattern: new Set(),
    },
    clusterId: "default",
  }
}

describe("alertVisible (D-PORTAL-TENANT-SCOPE-678)", () => {
  const admin = makeScope(true, [])
  const teamA = makeScope(false, ["team-a"])
  const empty = makeScope(false, [])

  it("allows cluster-admin to see namespaced and namespace-less alerts", () => {
    expect(alertVisible({ namespace: "team-a" }, admin)).toBe(true)
    expect(alertVisible({ namespace: "team-b" }, admin)).toBe(true)
    expect(alertVisible({}, admin)).toBe(true)
    expect(alertVisible({ namespace: "" }, admin)).toBe(true)
  })

  it("allows scoped users to see alerts in their effective namespaces (namespace or exported_namespace)", () => {
    expect(alertVisible({ namespace: "team-a" }, teamA)).toBe(true)
    expect(alertVisible({ exported_namespace: "team-a" }, teamA)).toBe(true)
    expect(alertVisible({ namespace: "team-b" }, teamA)).toBe(false)
    expect(alertVisible({ exported_namespace: "team-b" }, teamA)).toBe(false)
  })

  it("hides namespace-less (node/cluster-level) alerts from non-admins", () => {
    expect(alertVisible({}, teamA)).toBe(false)
    expect(alertVisible({ namespace: "" }, teamA)).toBe(false)
    expect(alertVisible({ exported_namespace: "" }, teamA)).toBe(false)
    expect(alertVisible({}, empty)).toBe(false)
  })
})
