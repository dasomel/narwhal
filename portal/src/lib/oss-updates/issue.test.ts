import { describe, expect, it } from "vitest"
import { buildIssueBody, issueTitle, newIssueUrl, type IssueInput } from "./issue"

const base: IssueInput = {
  component: "Falco",
  from: "0.45.1",
  target: "0.46.0",
  repo: "dasomel/narwhal",
  releaseUrl: "https://github.com/falcosecurity/falco/releases/tag/0.46.0",
  upstreamRepo: "falcosecurity/falco",
  highRisk: false,
  heldMinor: false,
}

describe("issue text", () => {
  it("carries component, versions, links and every skill checklist step", () => {
    const body = buildIssueBody(base)
    expect(issueTitle(base)).toBe("deps: upgrade Falco 0.45.1 -> 0.46.0")
    for (const needle of [
      "Falco", "0.45.1", "0.46.0", base.releaseUrl!, "VERSIONS.md", "narwhal-version-upgrade", "oss-update-process.md",
      "Cooling", "7 days", "Mirror by digest", "skopeo copy --all --preserve-digests", "check-mirror-pins.py",
      "check-version-consistency.sh", "Compatibility matrix", "Trace", "Independent review", "mark-review-pass.py", "Live verification",
    ]) expect(body, needle).toContain(needle)
  })
  it("falls back to the upstream releases page and adds risk/hold notes", () => {
    const body = buildIssueBody({ ...base, releaseUrl: null, highRisk: true, heldMinor: true })
    expect(body).toContain("https://github.com/falcosecurity/falco/releases")
    expect(body).toContain("High-risk component")
    expect(body).toContain("minor line is deliberately held")
  })
  it("never embeds a requester identity (public issue repo)", () => {
    expect(buildIssueBody(base)).not.toMatch(/@|requested by/i)
  })
  it("builds a bounded, encoded new-issue URL", () => {
    const url = newIssueUrl("dasomel/narwhal", "t & t", "x".repeat(20000))
    expect(url.startsWith("https://github.com/dasomel/narwhal/issues/new?title=t%20%26%20t&body=")).toBe(true)
    expect(url.length).toBeLessThan(9000)
  })
})
