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
  running: {
    version: null,
    rawTag: null,
    source: null,
    chartVersion: null,
    imageTags: [],
    siblings: [],
    reason: "workload-not-found",
    reasonDetail: null,
  },
  drift: "unknown",
  driftReason: "workload-not-found",
  upstreamState: "disabled",
  upstreamReason: "upstream-off",
  upstream: null,
  cves: null,
  cveReason: "no-scan",
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
  it("air-gap: explains the off switch and explicit unknown reasons (no bare unknown)", () => {
    const html = render({ rows: [row({})] })
    expect(html).toContain("ossUpdates.upstreamOff")
    expect(html).toContain("ossUpdates.requestModeCopy")
    expect(html).toContain("ossUpdates.unknown")
    expect(html).toContain("ossUpdates.reasonUpstreamOff")
    expect(html).toContain("ossUpdates.reasonWorkloadNotFoundShort")
    expect(html).toContain("ossUpdates.reasonNoScanReport")
    expect(html).not.toContain("ossUpdates.cooling")
  })

  it("renders CLI not-applicable, RBAC no-permission, and companion sibling sub-rows", () => {
    const html = render({
      rows: [
        row({
          component: "Helm",
          declared: { version: "4.2.1", chartVersion: null },
          running: {
            version: null,
            rawTag: null,
            source: null,
            chartVersion: null,
            imageTags: [],
            siblings: [],
            reason: "not-applicable",
          },
          drift: "not-applicable",
          driftReason: "not-applicable",
          cveReason: "not-applicable",
        }),
        row({
          component: "Gateway API CRDs",
          declared: { version: "1.5.1", chartVersion: null },
          running: {
            version: null,
            rawTag: null,
            source: "crd/gateways.gateway.networking.k8s.io",
            chartVersion: null,
            imageTags: [],
            siblings: [],
            reason: "no-permission",
            reasonDetail: "customresourcedefinitions.apiextensions.k8s.io",
          },
          drift: "unknown",
          driftReason: "no-permission",
          cveReason: "not-applicable",
        }),
        row({
          component: "Cilium",
          declared: { version: "1.19.4", chartVersion: null },
          running: {
            version: "1.19.4",
            rawTag: "v1.19.4",
            source: "ds/kube-system/cilium (cilium-agent)",
            chartVersion: null,
            imageTags: ["1.19.4"],
            siblings: [{ label: "cilium-envoy", source: "ds/kube-system/cilium-envoy (cilium-envoy)", rawTag: "v1.36.6", version: "1.36.6" }],
            reason: null,
          },
          drift: "in-sync",
          driftReason: null,
          cveReason: "no-scan",
        }),
      ],
    })
    expect(html).toContain("ossUpdates.notApplicableCli")
    expect(html).toContain("ossUpdates.driftNotApplicable")
    expect(html).toContain('ossUpdates.reasonNoPermission {&quot;detail&quot;:&quot;customresourcedefinitions.apiextensions.k8s.io&quot;}')
    expect(html).toContain('ossUpdates.companionLabel {&quot;label&quot;:&quot;cilium-envoy&quot;,&quot;tag&quot;:&quot;v1.36.6&quot;}')
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
        driftReason: null,
        upstreamState: "ok",
        upstreamReason: null,
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
