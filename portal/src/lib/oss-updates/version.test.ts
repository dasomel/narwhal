import { describe, expect, it } from "vitest"
import {
  ageDays,
  COOLING_DAYS,
  extractImageRef,
  gapKind,
  isCooling,
  normalizeImageRepo,
  normalizeTag,
  normalizeVersion,
  parseSemver,
  repoMatches,
  sameVersion,
} from "./version"

const NOW = Date.parse("2026-11-04T00:00:00Z")
const daysAgo = (n: number) => new Date(NOW - n * 86_400_000).toISOString()

describe("parseSemver", () => {
  it("parses v-prefixed, 3-part and 2-part versions", () => {
    expect(parseSemver("v1.35.7")).toEqual({ major: 1, minor: 35, patch: 7 })
    expect(parseSemver("4.34")).toEqual({ major: 4, minor: 34, patch: 0 })
    expect(parseSemver("1.30.1-distroless")).toEqual({ major: 1, minor: 30, patch: 1 })
    expect(parseSemver("1.26.2-rootless")).toEqual({ major: 1, minor: 26, patch: 2 })
    expect(parseSemver("3.15.0-ubuntu")).toEqual({ major: 3, minor: 15, patch: 0 })
  })
  it("rejects non-versions and trailing non-delimiter junk", () => {
    for (const bad of ["", null, undefined, "latest", "kube-prometheus-stack-1.2.3", "HEAD", "8-alpine", "1.2.3.4", "1.2.3abc"]) {
      expect(parseSemver(bad)).toBeNull()
    }
  })
})

describe("extractImageRef / normalizeImageRepo / repoMatches / normalizeTag", () => {
  it("extracts repo and rawTag while stripping digests and keeping registry ports", () => {
    expect(extractImageRef("docker.gitea.com/gitea:1.26.2-rootless")).toEqual({
      repo: "docker.gitea.com/gitea",
      rawTag: "1.26.2-rootless",
    })
    expect(extractImageRef("quay.io/cilium/cilium:v1.19.4@sha256:" + "a".repeat(64))).toEqual({
      repo: "quay.io/cilium/cilium",
      rawTag: "v1.19.4",
    })
    expect(extractImageRef("192.168.56.1:5001/gitea/gitea:1.26.2-rootless")).toEqual({
      repo: "192.168.56.1:5001/gitea/gitea",
      rawTag: "1.26.2-rootless",
    })
    expect(extractImageRef("quay.io/cilium/cilium@sha256:" + "b".repeat(64))).toEqual({
      repo: "quay.io/cilium/cilium",
      rawTag: null,
    })
    expect(extractImageRef("apache/apisix")).toEqual({
      repo: "apache/apisix",
      rawTag: null,
    })
  })

  it("normalizes leading v, -rootless/-ubuntu/-distroless suffixes, and digests; unparseable -> null", () => {
    expect(normalizeTag("v1.35.7")).toBe("1.35.7")
    expect(normalizeTag("v0.8.1")).toBe("0.8.1")
    expect(normalizeTag("1.26.2-rootless")).toBe("1.26.2")
    expect(normalizeTag("3.15.0-ubuntu")).toBe("3.15.0")
    expect(normalizeTag("1.30.1-distroless")).toBe("1.30.1")
    expect(normalizeTag("v1.19.4@sha256:" + "c".repeat(64))).toBe("1.19.4")
    expect(normalizeTag("4.34")).toBe("4.34")
    for (const unparseable of ["latest", "HEAD", "8-alpine", "sha256:abcd", "1.2.3-rc.1", "1.2.3-alpha", "1.2.3.4", "", null, undefined]) {
      expect(normalizeTag(unparseable)).toBeNull()
    }
  })

  it("normalizes Docker Hub aliases and matches exact repo boundaries (never sibling substrings)", () => {
    expect(normalizeImageRepo("apache/apisix")).toBe("docker.io/apache/apisix")
    expect(normalizeImageRepo("index.docker.io/apache/apisix")).toBe("docker.io/apache/apisix")
    expect(normalizeImageRepo("kong")).toBe("docker.io/library/kong")

    expect(repoMatches("quay.io/cilium/cilium:v1.19.4", "cilium/cilium")).toBe(true)
    expect(repoMatches("quay.io/cilium/cilium-envoy:v1.36.6-1778235340", "cilium/cilium")).toBe(false)

    expect(repoMatches("index.docker.io/apache/apisix:3.15.0-ubuntu", "apache/apisix")).toBe(true)
    expect(repoMatches("index.docker.io/apache/apisix-ingress-controller:1.8.0", "apache/apisix")).toBe(false)

    expect(repoMatches("index.docker.io/falcosecurity/falco:0.45.1", "falcosecurity/falco")).toBe(true)
    expect(repoMatches("index.docker.io/falcosecurity/falcosidekick:2.35.0", "falcosecurity/falco")).toBe(false)

    expect(repoMatches("index.docker.io/velero/velero:v1.18.1", "velero/velero")).toBe(true)
    expect(repoMatches("index.docker.io/velero/velero-plugin-for-aws:v1.14.1", "velero/velero")).toBe(false)

    expect(repoMatches("quay.io/keycloak/keycloak:26.5.7", "keycloak/keycloak")).toBe(true)
    expect(repoMatches("quay.io/keycloak/keycloak-operator:26.5.7", "keycloak/keycloak")).toBe(false)

    expect(repoMatches("registry.istio.io/release/pilot:1.30.1-distroless", "registry.istio.io/release/pilot")).toBe(true)
    expect(repoMatches("registry.istio.io/release/pilot:1.30.1-distroless", "istio/pilot")).toBe(false)
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
