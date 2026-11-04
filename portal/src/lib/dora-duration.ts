import type { TranslationKey } from "./i18n"

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string

export function formatLeadTime(
  value: { leadTimeMinutes?: number | null; leadTimeHours: number | null },
  t: Translate,
): string {
  // D3: Only absent minutes use legacy cached hours; explicit null is unknown.
  // Cost: old rounding cannot be recovered; fallback can go after the cache TTL/migration.
  const minutes = value.leadTimeMinutes === undefined
    ? value.leadTimeHours === null ? null : value.leadTimeHours * 60
    : value.leadTimeMinutes
  if (minutes === null || !Number.isFinite(minutes) || minutes < 0) return "—"
  if (minutes > 0 && minutes < 1) return t("dora.durationUnderMinute")
  // Whole elapsed minutes avoid displaying a sub-hour duration as an hour.
  const wholeMinutes = Math.floor(minutes)
  if (wholeMinutes < 60) return t("dora.durationMinutes", { minutes: wholeMinutes })
  return t("dora.durationHoursMinutes", { hours: Math.floor(wholeMinutes / 60), minutes: wholeMinutes % 60 })
}
