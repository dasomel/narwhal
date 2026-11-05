import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"
import { useQuery } from "@tanstack/react-query"
import { LocaleProvider } from "@/lib/i18n-client"
import { DoraMetricsWidget } from "./dora-metrics"

vi.mock("@tanstack/react-query", () => ({ useQuery: vi.fn() }))

const metric = { leadTimeHours: 0, leadTimeMinutes: 2.5 }
const data = {
  ...metric,
  totalDeploys: 1,
  deployFrequency: 1,
  changeFailureRate: null,
  mttrMinutes: null,
  dailyDeploys: [],
  recent: [],
  perApp: [
    { ...metric, app: "git-app", namespace: "platform", deploys: 1 },
    { leadTimeHours: 0, leadTimeMinutes: 0, app: "zero", namespace: "platform", deploys: 1 },
    { leadTimeHours: null, leadTimeMinutes: null, leadTimeApplicable: false, app: "helm", namespace: "platform", deploys: 1 },
  ],
}

describe("DORA summary and app table rendering", () => {
  it.each(["ko", "en"] as const)("renders minute precision, zero and Helm in %s", (locale) => {
    vi.mocked(useQuery).mockReturnValue({ data, isLoading: false, isError: false, refetch: vi.fn() } as never)
    const html = renderToStaticMarkup(<LocaleProvider locale={locale}><DoraMetricsWidget /></LocaleProvider>)
    expect(html.split(locale === "ko" ? "2분" : "2 min")).toHaveLength(3)
    expect(html).toContain(locale === "ko" ? "0분" : "0 min")
    expect(html).toContain(locale === "ko" ? "해당 없음 (Helm 차트)" : "N/A (Helm chart)")
  })

  it.each(["ko", "en"] as const)("renders truthful status in recent deployments table for %s", (locale) => {
    const recentData = {
      ...data,
      dailyDeploys: [{ date: "11-04", count: 2 }, { date: "11-05", count: 3 }],
      recent: [
        { app: "dashboard", namespace: "devtools", revision: "4f1fadb", deployedAt: new Date().toISOString(), status: "Succeeded" as const },
        { app: "harbor", namespace: "devtools", revision: "1122334", deployedAt: new Date().toISOString(), status: "Failed" as const },
        { app: "platform", namespace: "devtools", revision: "db6ae53", deployedAt: new Date().toISOString(), status: "Unknown" as const },
      ],
    }
    vi.mocked(useQuery).mockReturnValue({ data: recentData, isLoading: false, isError: false, refetch: vi.fn() } as never)
    const html = renderToStaticMarkup(<LocaleProvider locale={locale}><DoraMetricsWidget /></LocaleProvider>)

    // Succeeded and Failed keep badges
    expect(html).toContain("Succeeded")
    expect(html).toContain("Failed")

    // Unknown does NOT render as "Unknown" badge; it renders "—" with the explanatory tooltip citing status.operationState.phase
    expect(html).not.toContain(">Unknown<")
    expect(html).toContain("status.operationState.phase")
    expect(html).toContain("status.history")
    if (locale === "ko") {
      expect(html).toContain("Argo CD는 최근 작업의 결과만 status.operationState.phase에 기록하며, 이전 배포 이력(status.history)에는 상태 필드가 없습니다.")
    } else {
      expect(html).toContain("Argo CD records the operation phase only for the latest operation (status.operationState.phase); older history entries (status.history) have no status field.")
    }
  })
})
