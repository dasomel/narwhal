import { describe, expect, it } from "vitest"
import { ageDays, COOLING_DAYS, gapKind, isCooling, normalizeVersion, parseSemver, sameVersion } from "./version"

const NOW = Date.parse("2026-11-04T00:00:00Z")
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString()

describe("parseSemver", () => {
  it("parses v-prefixed, 3-part and 2-part versions", () => {
    expect(parseSemver("v1.35.7")).toEqual({ major: 1, minor: 35, patch: 7 })
    expect(parseSemver("4.34")).toEqual({ major: 4, minor: 34, patch: 0 })
    expect(parseSemver("1.30.1-distroless")).toEqual({ major: 1, minor: 30, patch: 1 })
  })
  it("rejects non-versions", () => {
    for (const bad of ["", null, undefined, "latest", "kube-prometheus-stack-1.2.3", "HEAD"]) expect(parseSemver(bad)).toBeNull()
  })
})

describe("gapKind", () => {
  it("classifies major / minor / patch / none / ahead", () => {
    expect(gapKind("1.35.7", "2.0.0")).toBe("major")
    expect(gapKind("1.35.7", "1.36.2")).toBe("minor")
    expect(gapKind("1.35.7", "1.35.9")).toBe("patch")
    expect(gapKind("1.35.7", "v1.35.7")).toBe("none")
    expect(gapKind("1.36.0", "1.35.9")).toBe("ahead")
  })
  it("is unknown (never throws) for unparsable input", () => {
    expect(gapKind("HEAD", "1.0.0")).toBe("unknown")
    expect(gapKind("1.0.0", null)).toBe("unknown")
  })
})

describe("cooling (7-day rule)", () => {
  it("is exactly a 7 day window", () => {
    expect(COOLING_DAYS).toBe(7)
    expect(isCooling(daysAgo(0), NOW)).toBe(true)
    expect(isCooling(daysAgo(6), NOW)).toBe(true)
    expect(isCooling(daysAgo(7), NOW)).toBe(false)
    expect(isCooling(daysAgo(30), NOW)).toBe(false)
  })
  it("treats missing/invalid and future timestamps safely", () => {
    expect(isCooling(null, NOW)).toBe(false)
    expect(isCooling("not-a-date", NOW)).toBe(false)
    expect(ageDays(new Date(NOW + 86_400_000).toISOString(), NOW)).toBe(0)
  })
})

describe("sameVersion / normalizeVersion", () => {
  it("ignores v prefix and .0 padding", () => {
    expect(sameVersion("v1.20.2", "1.20.2")).toBe(true)
    expect(sameVersion("4.34", "4.34.0")).toBe(true)
    expect(sameVersion("1.20.2", "1.20.3")).toBe(false)
  })
  it("falls back to string equality for non-semver", () => {
    expect(sameVersion("HEAD", "HEAD")).toBe(true)
    expect(normalizeVersion("  v2.0 ")).toBe("2.0")
    expect(normalizeVersion("")).toBeNull()
  })
})
