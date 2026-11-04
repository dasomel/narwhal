import { describe, expect, it } from "vitest"
import { formatLeadTime } from "./dora-duration"
import { t, type Locale } from "./i18n"

describe.each<Locale>(["ko", "en"])("DORA duration (%s)", (locale) => {
  const translate = (key: Parameters<typeof t>[1], params?: Record<string, string | number>) => t(locale, key, params)
  it.each([
    [null, "—", "—"],
    [0, "0분", "0 min"],
    [0.001, "<1분", "<1 min"],
    [0.999, "<1분", "<1 min"],
    [1, "1분", "1 min"],
    [2.5, "2분", "2 min"],
    [59.99, "59분", "59 min"],
    [60, "1시간 0분", "1 h 0 min"],
    [125.5, "2시간 5분", "2 h 5 min"],
  ])("formats %s minutes", (minutes, ko, en) => {
    expect(formatLeadTime({ leadTimeMinutes: minutes, leadTimeHours: null }, translate)).toBe(locale === "ko" ? ko : en)
  })
  it("accepts legacy cache payloads and preserves zero versus null", () => {
    expect(formatLeadTime({ leadTimeHours: 1.5 }, translate)).toBe(locale === "ko" ? "1시간 30분" : "1 h 30 min")
    expect(formatLeadTime({ leadTimeHours: 0 }, translate)).toBe(locale === "ko" ? "0분" : "0 min")
    expect(formatLeadTime({ leadTimeHours: null }, translate)).toBe("—")
    expect(formatLeadTime({ leadTimeMinutes: null, leadTimeHours: 1 }, translate)).toBe("—")
  })
})
