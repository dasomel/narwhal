import { t } from "@/lib/i18n"
import type { Locale } from "@/lib/i18n"
import { formatPassRatePct, sumCheckSummary, type ComplianceSourceKey, type ComplianceSummary } from "@/types/compliance"

interface Props {
  summary: ComplianceSummary
  locale: Locale
}

function relativeTime(iso: string, locale: Locale): string {
  const ms = new Date(iso).getTime()
  if (!Number.isFinite(ms)) return iso
  const diff = Math.max(0, Date.now() - ms)
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return t(locale, "audit.justNow")
  if (mins < 60) return t(locale, "audit.minsAgo", { mins })
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return t(locale, "audit.hrsAgo", { hrs })
  const days = Math.floor(hrs / 24)
  return t(locale, "audit.daysAgo", { days })
}

function pooledRatio(numerator: number, denominator: number): number {
  if (denominator <= 0) return 0
  return Math.max(0, Math.min(1, numerator / denominator))
}

const counterConfig = [
  {
    key: "configAudit" as const,
    labelKey: "compliance.counter.configAudit" as const,
    bg: "bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-900",
    color: "text-red-600",
  },
  {
    key: "rbac" as const,
    labelKey: "compliance.counter.rbac" as const,
    bg: "bg-orange-50 border-orange-200 dark:bg-orange-950/30 dark:border-orange-900",
    color: "text-orange-500",
  },
  {
    key: "infra" as const,
    labelKey: "compliance.counter.infra" as const,
    bg: "bg-amber-50 border-amber-200 dark:bg-amber-950/30 dark:border-amber-900",
    color: "text-amber-500",
  },
  {
    key: "frameworks" as const,
    labelKey: "compliance.counter.frameworksPassRate" as const,
    bg: "bg-green-50 border-green-200 dark:bg-green-950/30 dark:border-green-900",
    color: "text-green-600",
  },
]

function sourceLabelText(key: ComplianceSourceKey, locale: Locale): string {
  switch (key) {
    case "configAudit": return t(locale, "compliance.tab.configAudit")
    case "rbac": return t(locale, "compliance.tab.rbac")
    case "infra": return t(locale, "compliance.tab.infra")
    case "frameworks": return t(locale, "compliance.tab.frameworks")
  }
}

const SOURCE_KEYS: readonly ComplianceSourceKey[] = ["configAudit", "rbac", "infra", "frameworks"]

export function FailureCounters({ summary, locale }: Props) {
  const sources = summary.sources
  const isUnavailable = (key: ComplianceSourceKey) => sources?.[key]?.status === "unavailable"

  const acceptedSystemTotal = sumCheckSummary(summary.acceptedSystemConfigAuditFailures)
  const unacceptedLowTotal = summary.unacceptedLowConfigAuditFailures?.Low ?? 0
  const acceptedLowTotal = summary.lowSeverityConfigAuditFailures?.Low ?? 0
  const droppedReports = summary.droppedConfigAuditReports ?? 0
  const droppedReportChecks = sumCheckSummary(summary.droppedConfigAuditFailures)
  const acceptedRbacTotal = sumCheckSummary(summary.acceptedRbacFailures)
  const acceptedInfraTotal = sumCheckSummary(summary.acceptedInfraFailures)

  const actionableControlsTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.actionableFailCount ?? f.failCount),
    0,
  )
  const acceptedControlsTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.acceptedFailCount ?? 0),
    0,
  )
  const evaluatedPassTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.evaluatedPassCount ?? 0),
    0,
  )
  const benchmarkTestedPassTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.benchmarkTestedPassCount ?? 0),
    0,
  )
  const hasReportTestedPass = summary.frameworks.some(
    (f) => f.testedPassCount !== undefined || (f.reportTestedPassCount ?? 0) > 0,
  )
  const testedPassTotal = hasReportTestedPass
    ? summary.frameworks.reduce(
        (acc, f) => acc + (f.testedPassCount ?? (f.benchmarkTestedPassCount ?? 0) + (f.reportTestedPassCount ?? 0)),
        0,
      )
    : benchmarkTestedPassTotal
  const noFailureRecordedTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.noFailureRecordedPassCount ?? Math.max(0, (f.evaluatedPassCount ?? 0) - (f.testedPassCount ?? f.benchmarkTestedPassCount ?? 0))),
    0,
  )
  const notEvaluatedTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.notEvaluatedCount ?? Math.max(0, f.totalControls - f.failCount)),
    0,
  )
  const rawControlsTotal = summary.frameworks.reduce((acc, f) => acc + f.failCount, 0)
  const droppedControlsTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.droppedFailCount ?? 0),
    0,
  )
  const droppedChecksTotal = summary.frameworks.reduce(
    (acc, f) => acc + (f.droppedCheckCount ?? 0),
    0,
  )

  const totalControlsAll = summary.frameworks.reduce((acc, f) => acc + f.totalControls, 0)
  const effectivePassTotal = summary.frameworks.reduce(
    (acc, f) => acc + Math.round(((f.evaluatedPassCount === undefined || f.notEvaluatedCount === undefined ? 0 : f.effectivePassRate ?? 0)) * f.totalControls),
    0,
  )
  const hasCoverageUnknown = summary.frameworks.some((f) => (Boolean(f.coverageUnknown) || f.evaluatedPassCount === undefined || f.notEvaluatedCount === undefined))

  const hasActionableControls = actionableControlsTotal > 0
  const hasNotEvaluatedControls = notEvaluatedTotal > 0 || hasCoverageUnknown

  const evaluatedPooled = pooledRatio(evaluatedPassTotal, totalControlsAll)
  const effectivePooled = pooledRatio(effectivePassTotal, totalControlsAll)

  const evaluatedPassRateText = formatPassRatePct(evaluatedPooled, {
    hasActionableFail: hasActionableControls,
    hasNotEvaluated: hasNotEvaluatedControls,
  })
  const effectivePassRateText = formatPassRatePct(effectivePooled, {
    hasActionableFail: hasActionableControls,
    hasNotEvaluated: hasNotEvaluatedControls,
  })

  const partialSourceLabels: string[] = sources
    ? SOURCE_KEYS.filter((k) => Boolean(sources[k]?.partial)).map((k) => {
        const label = sourceLabelText(k, locale)
        const details = sources[k]?.partialSources
        return details && details.length > 0 ? `${label} (${details.join(", ")})` : label
      })
    : []

  const values: Record<(typeof counterConfig)[number]["key"], string> = {
    configAudit: isUnavailable("configAudit")
      ? t(locale, "compliance.counter.dataUnavailable")
      : String(
          (summary.totalConfigAuditFailures.Critical ?? 0) +
            (summary.totalConfigAuditFailures.High ?? 0) +
            (summary.totalConfigAuditFailures.Medium ?? 0) +
            (summary.totalConfigAuditFailures.Low ?? 0),
        ),
    rbac: isUnavailable("rbac")
      ? t(locale, "compliance.counter.dataUnavailable")
      : String(
          (summary.totalRbacFailures.Critical ?? 0) +
            (summary.totalRbacFailures.High ?? 0) +
            (summary.totalRbacFailures.Medium ?? 0) +
            (summary.totalRbacFailures.Low ?? 0),
        ),
    infra: isUnavailable("infra")
      ? t(locale, "compliance.counter.dataUnavailable")
      : String(
          (summary.totalInfraFailures.Critical ?? 0) +
            (summary.totalInfraFailures.High ?? 0) +
            (summary.totalInfraFailures.Medium ?? 0) +
            (summary.totalInfraFailures.Low ?? 0),
        ),
    frameworks: isUnavailable("frameworks")
      ? t(locale, "compliance.counter.dataUnavailable")
      : evaluatedPassRateText,
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {counterConfig.map(({ key, labelKey, bg, color }) => {
          const unavailable = isUnavailable(key)
          return (
            <div key={key} className={`rounded-lg border p-4 ${bg}`}>
              <div
                className={`${unavailable ? "text-base text-destructive" : `text-2xl ${color}`} font-bold`}
                title={!unavailable && key === "frameworks" ? `${evaluatedPassTotal}/${totalControlsAll}` : undefined}
              >
                {values[key]}
                {!unavailable && key === "frameworks" && totalControlsAll > 0 && (
                  <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                    ({evaluatedPassTotal}/{totalControlsAll})
                  </span>
                )}
              </div>
              <div className="text-xs font-medium text-muted-foreground mt-1">
                {key === "frameworks" && testedPassTotal > 0
                  ? t(locale, "compliance.counter.frameworksPassRateWithBenchmark")
                  : t(locale, labelKey)}
              </div>
              {!unavailable && key === "configAudit" && (unacceptedLowTotal > 0 || acceptedLowTotal > 0 || acceptedSystemTotal > 0 || droppedReports > 0) && (
                <div className="text-[0.7rem] text-muted-foreground/80 mt-0.5 space-y-0.5">
                  <div>
                    {t(locale, "compliance.counter.configAuditBreakdown", {
                      unacceptedLow: String(unacceptedLowTotal),
                      low: String(acceptedLowTotal),
                      accepted: String(acceptedSystemTotal),
                    })}
                  </div>
                  {droppedReports > 0 && (
                    <div>
                      {t(locale, "compliance.counter.droppedReports", {
                        reports: String(droppedReports),
                        checks: String(droppedReportChecks),
                      })}
                    </div>
                  )}
                </div>
              )}
              {!unavailable && key === "rbac" && acceptedRbacTotal > 0 && (
                <div className="text-[0.7rem] text-muted-foreground/80 mt-0.5">
                  {t(locale, "compliance.counter.rbacBreakdown", {
                    accepted: String(acceptedRbacTotal),
                  })}
                </div>
              )}
              {!unavailable && key === "infra" && acceptedInfraTotal > 0 && (
                <div className="text-[0.7rem] text-muted-foreground/80 mt-0.5">
                  {t(locale, "compliance.counter.infraBreakdown", {
                    accepted: String(acceptedInfraTotal),
                  })}
                </div>
              )}
              {!unavailable && key === "frameworks" && (
                <div className="text-[0.7rem] text-muted-foreground/80 mt-0.5 space-y-0.5">
                  <div>
                    <span title={`${effectivePassTotal}/${totalControlsAll}`}>
                      {t(locale, "compliance.counter.frameworksEffectiveRate")}: {effectivePassRateText} ({effectivePassTotal}/{totalControlsAll})
                    </span>
                  </div>
                  {(acceptedControlsTotal > 0 || rawControlsTotal !== actionableControlsTotal || notEvaluatedTotal > 0) && (
                    <div>
                      {testedPassTotal > 0
                        ? t(locale, "compliance.counter.frameworksBreakdownWithBenchmark", {
                            tested: String(testedPassTotal),
                            noFailure: String(noFailureRecordedTotal),
                            notEvaluated: String(notEvaluatedTotal),
                            actionable: String(actionableControlsTotal),
                            accepted: String(acceptedControlsTotal),
                          })
                        : t(locale, "compliance.counter.frameworksBreakdown", {
                            passed: String(noFailureRecordedTotal),
                            notEvaluated: String(notEvaluatedTotal),
                            actionable: String(actionableControlsTotal),
                            accepted: String(acceptedControlsTotal),
                          })}
                      {rawControlsTotal !== actionableControlsTotal
                        ? ` (${t(locale, "compliance.framework.rawChecks", { count: String(rawControlsTotal) })})`
                        : ""}
                      {(droppedControlsTotal > 0 || droppedChecksTotal > 0) &&
                        ` · ${t(locale, "compliance.framework.droppedShort", {
                          count: String(droppedChecksTotal || droppedControlsTotal),
                        })}`}
                    </div>
                  )}
                  {testedPassTotal > 0 ? (
                    <div>
                      {evaluatedPassTotal}/{totalControlsAll} (
                      {t(locale, "compliance.counter.frameworksPassSplit", {
                        tested: String(testedPassTotal),
                        noFailure: String(noFailureRecordedTotal),
                      })}
                      )
                    </div>
                  ) : (
                    <div>
                      {evaluatedPassTotal}/{totalControlsAll} ({t(locale, "compliance.framework.noFailureRecordedLabel")})
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
      <p className="text-xs text-muted-foreground">{t(locale, "compliance.framework.failureOnlyNote")}</p>
      <div className="flex flex-wrap items-center gap-6 text-xs text-muted-foreground">
        <span>
          {t(locale, "compliance.summary.scannedWorkloads")}:{" "}
          <span className="font-medium text-foreground">
            {isUnavailable("configAudit") ? t(locale, "compliance.summary.sourceUnavailable") : summary.scannedWorkloads}
          </span>
        </span>
        <span>
          {t(locale, "compliance.summary.scannedRbacObjects")}:{" "}
          <span className="font-medium text-foreground">
            {isUnavailable("rbac") ? t(locale, "compliance.summary.sourceUnavailable") : summary.scannedRbacObjects}
          </span>
        </span>
        <span>
          {t(locale, "compliance.summary.scannedNodes")}:{" "}
          <span className="font-medium text-foreground">
            {isUnavailable("infra") ? t(locale, "compliance.summary.sourceUnavailable") : summary.scannedNodes}
          </span>
        </span>
        {summary.unusedAcceptanceRuleCount !== undefined && (
          <span>
            {t(locale, "compliance.summary.unusedRules")}:{" "}
            <span className="font-medium text-foreground">
              {summary.unusedAcceptanceRuleCount === 0
                ? t(locale, "compliance.summary.unusedRulesNone")
                : summary.unusedAcceptanceRuleCount}
            </span>
          </span>
        )}
      </div>
      {summary.unusedAcceptanceRules && summary.unusedAcceptanceRules.length > 0 && (
        <details className="rounded-md border border-slate-200 bg-slate-50/70 px-3 py-2 text-xs text-muted-foreground dark:border-slate-800 dark:bg-slate-900/30">
          <summary className="cursor-pointer font-medium text-foreground">
            {t(locale, "compliance.summary.unusedRulesGroup", {
              count: String(summary.unusedAcceptanceRules.length),
            })}
          </summary>
          <p className="mt-1 text-[0.7rem] text-muted-foreground">{t(locale, "compliance.summary.unusedRulesHint")}</p>
          <ul className="mt-2 space-y-1 font-mono text-[0.7rem]">
            {summary.unusedAcceptanceRules.map((rule) => (
              <li key={rule.id} className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="font-semibold text-foreground">{rule.id}</span>
                <span>
                  ({rule.scope === "cluster" ? "cluster" : rule.namespace}/{rule.kind}/{rule.name})
                </span>
                <span className="text-muted-foreground/80">[{rule.checks.join(", ")}]</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {sources ? (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground">
          <span className="font-medium text-foreground/80">{t(locale, "compliance.summary.lastUpdated")}:</span>
          {SOURCE_KEYS.map((key) => {
            const src = sources[key]
            const text =
              src.status === "unavailable"
                ? t(locale, "compliance.summary.sourceUnavailable")
                : !src.lastUpdated
                  ? t(locale, "compliance.summary.sourceEmpty")
                  : src.oldestUpdated && src.oldestUpdated !== src.lastUpdated
                    ? t(locale, "compliance.summary.ageRange", {
                        newest: relativeTime(src.lastUpdated, locale),
                        oldest: relativeTime(src.oldestUpdated, locale),
                      })
                    : relativeTime(src.lastUpdated, locale)
            return (
              <span key={key}>
                {sourceLabelText(key, locale)}:{" "}
                <span
                  className={`font-medium ${src.status === "unavailable" ? "text-destructive" : "text-foreground"}`}
                  title={src.lastUpdated ?? undefined}
                >
                  {text}
                </span>
                {src.partial && (
                  <span className="ml-1 text-amber-700 dark:text-amber-400">
                    ({t(locale, "compliance.summary.sourcePartial")})
                  </span>
                )}
              </span>
            )
          })}
          <span className="text-muted-foreground/80">{t(locale, "compliance.summary.rebuildNote")}</span>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-4 text-xs text-muted-foreground">
          <span>
            {t(locale, "compliance.summary.lastUpdated")}:{" "}
            <span className="font-medium text-foreground">
              {summary.lastUpdated ? relativeTime(summary.lastUpdated, locale) : t(locale, "compliance.summary.sourceUnavailable")}
            </span>
          </span>
          <span className="text-muted-foreground/80">{t(locale, "compliance.summary.rebuildNote")}</span>
        </div>
      )}
      {(partialSourceLabels.length > 0 || summary.partial) && (
        <div
          role="status"
          className="rounded-md border border-amber-300/80 bg-amber-50/70 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
        >
          {t(locale, "compliance.summary.partialNotice", {
            sources: partialSourceLabels.join(", ") || sourceLabelText("frameworks", locale),
          })}
        </div>
      )}
    </div>
  )
}
