import { beforeEach, describe, expect, it, vi } from "vitest"
import type { WorkloadVulnRow } from "@/types/security"
import type { UpstreamRelease } from "./upstream"

vi.mock("server-only", () => ({}))
const getArgoAppsOrThrow = vi.fn()
vi.mock("../argocd", () => ({ getArgoAppsOrThrow: () => getArgoAppsOrThrow() }))
const getWorkloadVulnerabilitiesWithMeta = vi.fn()
vi.mock("../trivy", () => ({ getWorkloadVulnerabilitiesWithMeta: () => getWorkloadVulnerabilitiesWithMeta() }))
const getUpstreamReleases = vi.fn()
vi.mock("./upstream", () => ({
  isUpstreamLookupEnabled: () => process.env.OSS_UPDATES_UPSTREAM === "true",
  getUpstreamReleases: (r: string) => getUpstreamReleases(r),
}))

const { computeDrift, getOssUpdateStatus, summarizeImages, summarizeUpstream } = await import("./status")

const NOW = Date.parse("2026-11-04T00:00:00Z")
const rel = (tag: string, ageDaysAgo: number): UpstreamRelease => ({
  tag, publishedAt: new Date(NOW - ageDaysAgo * 86_400_000).toISOString(), url: `https://example.test/${tag}`,
})
const sev = (critical = 0, high = 0) => ({ Critical: critical, High: high, Medium: 0, Low: 0, Unknown: 0 })
const vuln = (image: string, critical = 0, high = 0): WorkloadVulnRow => ({ namespace: "n", kind: "Deployment", name: "w", image, summary: sev(critical, high) })

beforeEach(() => {
  delete process.env.OSS_UPDATES_UPSTREAM
  getArgoAppsOrThrow.mockReset().mockResolvedValue([])
  getWorkloadVulnerabilitiesWithMeta.mockReset().mockResolvedValue({ rows: [], partial: false })
  getUpstreamReleases.mockReset().mockResolvedValue(null)
})

describe("summarizeUpstream", () => {
  const releases = [rel("v1.36.0", 2), rel("v1.35.9", 20), rel("v1.35.7", 60)]
  it("flags a cooling latest and proposes the newest cooled release as target", () => {
    const u = summarizeUpstream(releases, "1.35.7", false, NOW)!
    expect(u).toMatchObject({ latest: "v1.36.0", cooling: true, target: "v1.35.9", gap: "minor", daysBehind: 58 })
  })
  it("holds the minor line when asked (branch-latest)", () => {
    const u = summarizeUpstream(releases, "1.35.7", true, NOW)!
    expect(u).toMatchObject({ latest: "v1.35.9", cooling: false, target: "v1.35.9", gap: "patch" })
  })
  it("has no target when declared is already newest; daysBehind null when declared tag unseen", () => {
    expect(summarizeUpstream(releases, "1.36.0", false, NOW)).toMatchObject({ target: null, gap: "none" })
    expect(summarizeUpstream(releases, "1.20.0", false, NOW)?.daysBehind).toBeNull()
  })
  it("returns null for an empty pool", () => {
    expect(summarizeUpstream([], "1.0.0", false, NOW)).toBeNull()
    expect(summarizeUpstream([rel("v2.0.0", 30)], "1.0.0", true, NOW)).toBeNull()
  })
})

describe("summarizeImages / computeDrift", () => {
  it("extracts tags (digest-stripped), de-duplicates images and sums Critical/High per image", () => {
    const out = summarizeImages(
      [vuln("docker.io/falcosecurity/falco:0.45.1@sha256:" + "a".repeat(64), 1, 2), vuln("docker.io/falcosecurity/falco:0.45.1@sha256:" + "a".repeat(64), 1, 2), vuln("x/other:1.0.0", 9, 9)],
      "falcosecurity/falco",
    )
    expect(out).toEqual({ tags: ["0.45.1"], cves: { critical: 1, high: 2 } })
    expect(summarizeImages([vuln("x/y:1.0.0")], "nomatch")).toEqual({ tags: [], cves: null })
    expect(summarizeImages([vuln("x/y:1.0.0")], undefined)).toEqual({ tags: [], cves: null })
  })
  it("drift: in-sync / drift / unknown", () => {
    const d = { version: "0.45.1", chartVersion: "9.2.1" }
    expect(computeDrift(d, "9.2.1", ["0.45.1"])).toBe("in-sync")
    expect(computeDrift(d, "9.2.0", ["0.45.1"])).toBe("drift")
    expect(computeDrift(d, null, ["0.45.0"])).toBe("drift")
    expect(computeDrift(d, "HEAD", [])).toBe("unknown")
    expect(computeDrift({ version: "1.20.2", chartVersion: null }, "v1.20.2", [])).toBe("in-sync")
  })
})

describe("getOssUpdateStatus", () => {
  it("air-gap default: no upstream calls, rows still carry declared/running/CVE data", async () => {
    getArgoAppsOrThrow.mockResolvedValue([{ metadata: { name: "falco" }, spec: { source: { targetRevision: "9.2.1" } } }])
    getWorkloadVulnerabilitiesWithMeta.mockResolvedValue({ rows: [vuln("docker.io/falcosecurity/falco:0.45.1", 0, 3)], partial: false })
    const res = await getOssUpdateStatus(NOW)
    expect(getUpstreamReleases).not.toHaveBeenCalled()
    expect(res).toMatchObject({ upstreamLookup: "disabled", argocd: "ok", trivy: "ok", requestMode: "copy", coolingDays: 7 })
    const falco = res.rows.find((r) => r.component === "Falco")!
    expect(falco).toMatchObject({ upstreamState: "disabled", upstream: null, drift: "in-sync", cves: { critical: 0, high: 3 } })
  })
  it("survives Argo CD and Trivy being unavailable", async () => {
    getArgoAppsOrThrow.mockRejectedValue(new Error("boom"))
    const res = await getOssUpdateStatus(NOW)
    expect(res).toMatchObject({ argocd: "unavailable", trivy: "unavailable" })
    expect(res.rows.length).toBeGreaterThan(10)
    expect(res.rows.every((r) => r.drift === "unknown")).toBe(true)
  })
  it("marks upstreamState unavailable when the enabled lookup returns unknown", async () => {
    process.env.OSS_UPDATES_UPSTREAM = "true"
    const res = await getOssUpdateStatus(NOW)
    expect(res.upstreamLookup).toBe("enabled")
    expect(res.rows.every((r) => r.upstreamState === "unavailable" && r.upstream === null)).toBe(true)
  })
})
