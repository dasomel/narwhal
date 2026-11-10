import { describe, expect, it } from "vitest"
import { buildKpiModels } from "./dashboard-overview"
import type { TranslationKey } from "@/lib/i18n"

const translate = (key: TranslationKey, values?: Record<string, string | number>) => key === "dashboard.nodeAverage" ? `${values?.count} node average` : key

describe("dashboard KPI data states", () => {
  it("uses no-data labels and empty meters when metrics are unavailable", () => {
    const cards = buildKpiModels(undefined, translate)

    expect(cards.map(({ value, detail, percent, unavailable }) => ({ value, detail, percent, unavailable }))).toEqual([
      { value: "—", detail: "dashboard.noData", percent: undefined, unavailable: true },
      { value: "—", detail: "dashboard.noData", percent: undefined, unavailable: true },
      { value: "—", detail: "dashboard.noData", percent: undefined, unavailable: true },
      { value: "—", detail: "dashboard.noData", percent: undefined, unavailable: true },
    ])
  })

  it("shows all ready only when known nodes are all ready", () => {
    const cards = buildKpiModels({ cpu: 0, memory: 0, nodes: { total: 3, ready: 3 }, pods: { total: 0, running: 0 } }, translate)

    expect(cards[0]).toMatchObject({ value: "0%", detail: "3 node average", percent: 0, unavailable: false })
    expect(cards[1]).toMatchObject({ value: "0%", detail: "3 node average", unavailable: false })
    expect(cards[2]).toMatchObject({ value: "3 / 3", detail: "dashboard.allReady", unavailable: false })
    expect(cards[3]).toMatchObject({ value: "0", detail: "dashboard.running", unavailable: false })
  })

  it("reports the known not-ready count", () => {
    const cards = buildKpiModels({ cpu: null, memory: null, nodes: { total: 5, ready: 3 }, pods: { total: null, running: null } }, translate)

    expect(cards[2]).toMatchObject({ value: "3 / 5", detail: "2 NotReady", unavailable: false })
  })

  it("never invents a memory capacity (the API only reports a percentage)", () => {
    const cards = buildKpiModels({ cpu: 40, memory: 61, nodes: { total: 6, ready: 6 }, pods: { total: 10, running: 10 } }, translate)

    expect(cards[1].detail).toBe("6 node average")
    expect(cards[1].detail).not.toMatch(/GiB/)
  })
})
