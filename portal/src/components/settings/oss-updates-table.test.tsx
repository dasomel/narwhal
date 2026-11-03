import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
  useMutation: () => ({ mutate: vi.fn(), isPending: false, data: undefined, error: null }),
}))
vi.mock("@/lib/i18n-client", () => ({
  useT: () => (key: string, p?: Record<string, string | number>) => (p ? `${key} ${JSON.stringify(p)}` : key),
  useLocale: () => "ko",
}))

import { useQuery } from "@tanstack/react-query"
import { OssUpdatesTable } from "./oss-updates-table"
import type { OssUpdateRow, OssUpdatesResponse } from "@/types/oss-updates"

const row = (over: Partial<OssUpdateRow>): OssUpdateRow => ({
  component: "Falco",
  section: "Security",
  declared: { version: "0.45.1", chartVersion: "9.2.1" },
  running: { chartVersion: null, imageTags: [] },
  drift: "unknown",
  upstreamState: "disabled",
  upstream: null,
  cves: null,
  heldMinor: false,
  highRisk: false,
  ...over,
})

function render(data: Partial<OssUpdatesResponse> & { rows: OssUpdateRow[] }) {
  vi.mocked(useQuery).mockReturnValue({
    data: { generatedAt: "2026-11-04T00:00:00Z", coolingDays: 7, upstreamLookup: "disabled", argocd: "ok", trivy: "ok", requestMode: "copy", issueRepo: "dasomel/narwhal", ...data },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as never)
  return renderToStaticMarkup(<OssUpdatesTable />)
}

describe("OssUpdatesTable", () => {
  it("air-gap: explains the off switch, shows unknown (not blank) and no cooling/gap badge", () => {
    const html = render({ rows: [row({})] })
    expect(html).toContain("ossUpdates.upstreamOff")
    expect(html).toContain("ossUpdates.requestModeCopy")
    expect(html).toContain("ossUpdates.unknown")
    expect(html).not.toContain("ossUpdates.cooling")
  })

  it("is an accessible table: labelled, column + row headers, named request button", () => {
    const html = render({ rows: [row({})] })
    expect(html).toContain('aria-label="ossUpdates.tableLabel"')
    expect(html).toMatch(/<th scope="col"[^>]*>ossUpdates\.colComponent/)
    expect(html).toMatch(/<th scope="row"[^>]*>Falco/)
    expect(html).toContain('aria-label="ossUpdates.requestFor {&quot;component&quot;:&quot;Falco&quot;}"')
  })

  it("shows a cooling badge with age/total, gap, days behind and drift", () => {
    const html = render({
      upstreamLookup: "enabled",
      rows: [row({
        drift: "drift",
        upstreamState: "ok",
        upstream: { latest: "0.46.0", latestPublishedAt: "2026-11-01T00:00:00Z", latestUrl: "https://github.com/falcosecurity/falco/releases/tag/0.46.0", latestAgeDays: 3, cooling: true, target: null, targetPublishedAt: null, gap: "minor", daysBehind: 40 },
      })],
    })
    expect(html).not.toContain("ossUpdates.upstreamOff")
    expect(html).toContain('ossUpdates.cooling {&quot;age&quot;:3,&quot;total&quot;:7}')
    expect(html).toContain("ossUpdates.gapMinor")
    expect(html).toContain("ossUpdates.driftDrift")
    expect(html).toContain('rel="noopener noreferrer"')
  })

  it("renders the error state with retry instead of an empty table", () => {
    vi.mocked(useQuery).mockReturnValue({ data: undefined, isLoading: false, isError: true, refetch: vi.fn() } as never)
    const html = renderToStaticMarkup(<OssUpdatesTable />)
    expect(html).not.toContain("<table")
    expect(html).toContain("common.retry")
  })
})
