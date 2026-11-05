import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import type { TrivyDbStatus } from "@/types/security"

vi.mock("@tanstack/react-query", () => ({ useQuery: vi.fn() }))
vi.mock("@/lib/i18n-client", () => ({
  useT: () => (key: string) => key,
  useLocale: () => "en",
}))

import { useQuery } from "@tanstack/react-query"
import { TrivyDbStatusCard } from "./trivy-db-status-card"

function status(overall: TrivyDbStatus["overall"], extra: Partial<TrivyDbStatus> = {}): TrivyDbStatus {
  return {
    observedAt: "2026-11-04T00:00:00.000Z",
    sloDays: 7,
    harborReachable: true,
    overall,
    dbRegistry: "harbor.devtools.svc.cluster.local/trivy-db/aquasecurity/trivy-db",
    artifacts: [
      { name: "trivy-db", consumer: "vulnScan", sloDays: 7, repository: "aquasecurity/trivy-db", tag: "2", status: overall, ageDays: 1, createdAt: "2026-11-03T13:22:26Z" },
      { name: "trivy-java-db", consumer: "javaScan", sloDays: 7, repository: "aquasecurity/trivy-java-db", tag: "1", status: "fresh", ageDays: 2 },
      { name: "trivy-checks", consumer: "configAudit", sloDays: null, repository: "aquasecurity/trivy-checks", tag: "1", status: "pinned", ageDays: 392 },
    ],
    scanner: { state: "ok", reportCount: 7, newestReportAt: "2026-11-03T23:00:00Z", recentScanFailures: 0, lastFailureAt: null },
    ...extra,
  }
}

function render(data: TrivyDbStatus | undefined, flags: { isLoading?: boolean; isError?: boolean } = {}) {
  vi.mocked(useQuery).mockReturnValue({ data, isLoading: false, isError: false, refetch: vi.fn(), ...flags } as never)
  return renderToStaticMarkup(<TrivyDbStatusCard />)
}

describe("TrivyDbStatusCard", () => {
  it("shows artifacts and no procedure when fresh", () => {
    const html = render(status("fresh"))
    expect(html).toContain("trivy-db:2")
    expect(html).toContain("trivy-java-db:1")
    expect(html).toContain("trivy-checks:1")
    expect(html).toContain("security.trivyDb.status.fresh")
    expect(html).not.toContain("security.trivyDb.fix.title")
  })

  it("lists every source with its consumer, SLO and a pinned badge for the versioned one", () => {
    const html = render(status("fresh"))
    for (const c of ["vulnScan", "javaScan", "configAudit"]) expect(html).toContain(`security.trivyDb.consumer.${c}`)
    expect(html).toContain("security.trivyDb.slo.days")
    expect(html).toContain("security.trivyDb.slo.versioned")
    expect(html).toContain("security.trivyDb.status.pinned")
  })

  it.each(["stale", "missing"] as const)("shows the copyable manual procedure when %s", (overall) => {
    const html = render(status(overall))
    expect(html).toContain(`security.trivyDb.status.${overall}`)
    expect(html).toContain("security.trivyDb.fix.why")
    expect(html).toContain("fetch-security-db.sh --bundle")
    expect(html).toContain("promote-security-db.sh --bundle &lt;dir&gt; --approved-by")
    expect(html).toContain("push-security-db.sh --bundle")
    expect(html).toContain("aria-label=\"security.trivyDb.fix.copy\"")
  })

  it("says unknown, not missing, when Harbor is unreachable, and offers no procedure", () => {
    const html = render(status("unknown", { harborReachable: false }))
    expect(html).toContain("security.trivyDb.harborDown")
    expect(html).toContain("security.trivyDb.status.unknown")
    expect(html).not.toContain("security.trivyDb.fix.title")
  })

  it("raises an alert for failing scans", () => {
    const html = render(status("missing", { scanner: { state: "failing", reportCount: 0, newestReportAt: null, recentScanFailures: 3, lastFailureAt: "2026-11-04T00:00:00Z" } }))
    expect(html).toContain("role=\"alert\"")
    expect(html).toContain("security.trivyDb.scanner.failing")
  })

  it("shows unavailable on a failed query", () => {
    expect(render(undefined, { isError: true })).toContain("dataState.unavailable")
  })
})
