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
})
