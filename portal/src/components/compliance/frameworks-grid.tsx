"use client"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useT, useLocale } from "@/lib/i18n-client"
import { translateNotEvaluatedReason } from "@/lib/i18n"
import { COMPLIANCE_EXCEPTIONS_DOC_URL } from "@/lib/compliance-accepted"
import { DataState } from "@/components/ui/data-state"
import { translateTitle } from "@/lib/check-translations"
import { Badge } from "@/components/ui/badge"
import { fetchComplianceJson } from "./fetch-json"
import {
  formatPassRatePct,
  type AcceptedScope,
  type ComplianceControl,
  type ComplianceFramework,
  type ComplianceFrameworkDetail,
  type Severity,
} from "@/types/compliance"

const severityBadgeClass: Record<Severity, string> = {
  Critical: "bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-400",
  High: "bg-orange-100 text-orange-700 dark:bg-orange-950/50 dark:text-orange-400",
  Medium: "bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400",
  Low: "bg-blue-100 text-blue-700 dark:bg-blue-950/50 dark:text-blue-400",
  Unknown: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
}

const SEVERITY_ORDER: Severity[] = ["Critical", "High", "Medium", "Low", "Unknown"]

function acceptedScopeBadgeLabel(scope: AcceptedScope | undefined, t: ReturnType<typeof useT>): string {
  if (scope === "system") return t("compliance.table.accepted")
  if (scope === "builtin") return t("compliance.table.acceptedRbac")
  if (scope === "upstream") return t("compliance.table.acceptedUpstream")
  if (scope === "mixed") return t("compliance.framework.mixedScope")
  return t("compliance.table.acceptedException")
}

function PassRateBar({ rate }: { rate: number }) {
  const pct = Math.min(100, Math.max(0, rate))
  const color = pct >= 80 ? "bg-green-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500"
  return (
    <div className="w-full bg-muted rounded-full h-2 mt-2">
      <div className={`h-2 rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
    </div>
  )
}

function verdictText(verdict: "passed" | "failed" | "not_evaluated", t: ReturnType<typeof useT>): string {
  if (verdict === "passed") return t("compliance.framework.verdict.passed")
  if (verdict === "failed") return t("compliance.framework.verdict.failed")
  return t("compliance.framework.verdict.not_evaluated")
}

function ControlPreviewCard({
  ctrl,
  frameworkId,
  locale,
  t,
}: {
  ctrl: ComplianceControl
  frameworkId: string
  locale: ReturnType<typeof useLocale>
  t: ReturnType<typeof useT>
}) {
  const actionableFail = ctrl.actionableFailCount ?? ctrl.failCount
  const acceptedFail = ctrl.acceptedFailCount ?? 0
  const droppedFail = ctrl.droppedFailCount ?? 0
  const isAcceptedOnly = Boolean(ctrl.accepted)
  const isNotEvaluated = (ctrl.evaluationState === "not_evaluated" || (!ctrl.evaluationState && ctrl.failCount === 0))
  const isTestedPass =
    ctrl.evaluationState === "passed" && (ctrl.testedByBenchmark === true || ctrl.testedByReport === true)
  const ev = ctrl.benchmarkEvidence
  const cardBorder = isNotEvaluated
    ? "border-amber-200/80 bg-amber-50/40 dark:bg-amber-950/10 dark:border-amber-900/60"
    : isAcceptedOnly
      ? "border-slate-200 bg-slate-50/60 dark:bg-slate-900/20 dark:border-slate-800"
      : actionableFail > 0
        ? "border-red-200 bg-red-50/60 dark:bg-red-950/10 dark:border-red-900"
        : "bg-card"

  return (
    <div className={`rounded-md border p-2 text-xs ${cardBorder}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-muted-foreground">{ctrl.id}</span>
        <Badge className={`text-xs ${severityBadgeClass[ctrl.severity] ?? severityBadgeClass.Unknown}`}>
          {ctrl.severity}
        </Badge>
        {isNotEvaluated && (
          <Badge
            variant="outline"
            className="text-[0.65rem] px-1.5 py-0 text-amber-700 border-amber-300 dark:text-amber-400 dark:border-amber-800"
          >
            {t("compliance.framework.notEvaluatedShort")}
          </Badge>
        )}
        {isTestedPass && (
          <Badge
            variant="outline"
            className="text-[0.65rem] px-1.5 py-0 text-green-700 border-green-300 dark:text-green-400 dark:border-green-800"
          >
            {t("compliance.framework.testedBenchmarkShort")}
          </Badge>
        )}
        {isAcceptedOnly && (
          <Badge
            variant="outline"
            className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
          >
            {acceptedScopeBadgeLabel(ctrl.acceptedScope, t)}
          </Badge>
        )}
      </div>
      <p className="mt-1 font-medium text-foreground">
        {frameworkId.startsWith("k8s-cis") ? translateTitle("cis", ctrl.id, ctrl.name, locale) : ctrl.name}
      </p>
      {isNotEvaluated ? (
        <p className="mt-1 text-[0.7rem] text-muted-foreground">
          {translateNotEvaluatedReason(locale, ctrl.notEvaluatedReason)}
          {ctrl.notEvaluatedDetail ? ` (${ctrl.notEvaluatedDetail})` : ""}
        </p>
      ) : (
        <div className="space-y-1 mt-1">
          <div className="flex flex-wrap items-center gap-3 text-muted-foreground">
            <span className="text-green-600 font-medium">✓ {ctrl.passCount}</span>
            <span className={`font-medium ${actionableFail > 0 ? "text-red-600" : "text-muted-foreground"}`}>
              ✗ {actionableFail}
            </span>
            {(acceptedFail > 0 || ctrl.failCount !== actionableFail || droppedFail > 0) && (
              <Badge
                variant="outline"
                className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
              >
                {[
                  acceptedFail > 0 ? `+${acceptedFail} ${t("compliance.framework.acceptedShort")}` : null,
                  droppedFail > 0 ? t("compliance.framework.droppedShort", { count: String(droppedFail) }) : null,
                ]
                  .filter(Boolean)
                  .join(" · ") || t("compliance.framework.rawChecks", { count: String(ctrl.failCount) })}
                {acceptedFail > 0 || droppedFail > 0
                  ? ` (${t("compliance.framework.rawChecks", { count: String(ctrl.failCount) })})`
                  : ""}
              </Badge>
            )}
          </div>
          {ev && ev.finishedAt && (
            <p className="text-[0.7rem] text-muted-foreground font-mono">
              {t("compliance.framework.benchmarkSourceLine", {
                tool: ev.tool,
                version: ev.toolVersion,
                benchmarkId: ev.benchmarkId,
                nodes: t("compliance.framework.benchmarkNodes", { count: String(ev.nodeCount) }),
                finishedAt: ev.finishedAt,
              })}
            </p>
          )}
          {ev?.sourceDisagreement && (
            <p className="text-[0.7rem] text-amber-700 dark:text-amber-400">
              {t("compliance.framework.sourceDisagreement", {
                trivy: verdictText(ev.sourceDisagreement.trivyVerdict, t),
                benchmark: verdictText(ev.sourceDisagreement.benchmarkVerdict, t),
              })}
            </p>
          )}
          {ev?.failFindings?.map((f) => (
            <div key={f.node} className="text-[0.7rem] text-muted-foreground space-y-0.5">
              {f.actualValue && (
                <p className="truncate">
                  <span className="font-medium">{f.node} · {t("compliance.framework.benchmarkActual")}:</span> {f.actualValue}
                </p>
              )}
              {f.expectedResult && (
                <p className="truncate">
                  <span className="font-medium">{t("compliance.framework.benchmarkExpected")}:</span> {f.expectedResult}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function ExpandedFrameworkPanel({ id }: { id: string }) {
  const t = useT()
  const locale = useLocale()

  const { data: detail, isLoading, isError, refetch } = useQuery<ComplianceFrameworkDetail>({
    queryKey: ["compliance-framework-detail", id],
    queryFn: () => fetchComplianceJson<ComplianceFrameworkDetail>(`/api/compliance/frameworks?id=${encodeURIComponent(id)}`),
    enabled: true,
    staleTime: 60_000,
  })

  const actionableAndOther = detail?.controls
    ? [...detail.controls]
        .filter((c) => !c.accepted && (c.evaluationState === "passed" || c.evaluationState === "failed" || (!c.evaluationState && c.failCount > 0)))
        .sort((a, b) => {
          const aFail = (a.actionableFailCount ?? a.failCount) > 0 ? 0 : 1
          const bFail = (b.actionableFailCount ?? b.failCount) > 0 ? 0 : 1
          if (aFail !== bFail) return aFail - bFail
          return SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)
        })
    : []
  const top5 = actionableAndOther.slice(0, 5)
  const acceptedControls = detail?.controls
    ? [...detail.controls]
        .filter((c) => Boolean(c.accepted))
        .sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity))
    : []
  const notEvaluatedControls = detail?.controls
    ? [...detail.controls]
        .filter((c) => (c.evaluationState === "not_evaluated" || (!c.evaluationState && c.failCount === 0)))
        .sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity))
    : []

  return (
    <div className="mt-3 pt-3 border-t border-dashed space-y-2">
      {isLoading ? (
        <span className="text-xs text-muted-foreground animate-pulse">{t("common.loading")}</span>
      ) : isError ? (
        <DataState state="unavailable" variant="inline" onRetry={() => { void refetch() }} />
      ) : detail ? (
        <>
          {top5.length === 0 && acceptedControls.length === 0 && notEvaluatedControls.length === 0 ? (
            <span className="text-xs text-muted-foreground">{t("common.notFound")}</span>
          ) : (
            <>
              {top5.map((ctrl) => (
                <ControlPreviewCard key={ctrl.id} ctrl={ctrl} frameworkId={id} locale={locale} t={t} />
              ))}
              {acceptedControls.length > 0 && (
                <details className="rounded-md border border-slate-200 bg-slate-50/60 dark:bg-slate-900/20 dark:border-slate-800 p-2 text-xs">
                  <summary className="cursor-pointer font-medium text-muted-foreground select-none">
                    {t("compliance.framework.acceptedControlsGroup", { count: String(acceptedControls.length) })}
                  </summary>
                  <div className="mt-2 space-y-1.5">
                    <div className="flex justify-end">
                      <a
                        href={COMPLIANCE_EXCEPTIONS_DOC_URL}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="underline text-muted-foreground hover:text-foreground"
                      >
                        {t("compliance.framework.exceptionsDocLink")}
                      </a>
                    </div>
                    {acceptedControls.map((ctrl) => (
                      <ControlPreviewCard key={ctrl.id} ctrl={ctrl} frameworkId={id} locale={locale} t={t} />
                    ))}
                  </div>
                </details>
              )}
              {notEvaluatedControls.length > 0 && (
                <details className="rounded-md border border-amber-200/80 bg-amber-50/40 dark:bg-amber-950/10 dark:border-amber-900/60 p-2 text-xs">
                  <summary className="cursor-pointer font-medium text-amber-700 dark:text-amber-400 select-none">
                    {t("compliance.framework.notEvaluatedControlsGroup", { count: String(notEvaluatedControls.length) })}
                  </summary>
                  <div className="mt-2 space-y-1.5">
                    {notEvaluatedControls.map((ctrl) => (
                      <ControlPreviewCard key={ctrl.id} ctrl={ctrl} frameworkId={id} locale={locale} t={t} />
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
          <a className="inline-flex items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-[0.8rem] font-medium hover:bg-muted transition-colors" href={`/compliance/frameworks/${encodeURIComponent(id)}`}>
              {t("common.viewFullDetails")}
            </a>
        </>
      ) : (
        <span className="text-xs text-muted-foreground">{t("common.notFound")}</span>
      )}
    </div>
  )
}

export function FrameworksGrid() {
  const t = useT()
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data: frameworks = [], isLoading, isError, refetch } = useQuery<ComplianceFramework[]>({
    queryKey: ["compliance-frameworks"],
    queryFn: () => fetchComplianceJson<ComplianceFramework[]>("/api/compliance/frameworks"),
    staleTime: 60_000,
  })

  if (isLoading) return <DataState state="loading" onRetry={() => { void refetch() }} />
  if (isError) return <DataState state="unavailable" onRetry={() => { void refetch() }} />

  if (frameworks.length === 0) {
    return <DataState state="empty" />
  }

  const totalActionable = frameworks.reduce((acc, f) => acc + (f.actionableFailCount ?? f.failCount), 0)
  const totalAccepted = frameworks.reduce((acc, f) => acc + (f.acceptedFailCount ?? 0), 0)
  const totalNotEvaluated = frameworks.reduce((acc, f) => acc + (f.notEvaluatedCount ?? Math.max(0, f.totalControls - f.failCount)), 0)
  const totalRaw = frameworks.reduce((acc, f) => acc + f.failCount, 0)
  const anyCoverageUnknown = frameworks.some((f) => (Boolean(f.coverageUnknown) || f.evaluatedPassCount === undefined || f.notEvaluatedCount === undefined))

  return (
    <div className="space-y-3">
      {anyCoverageUnknown && (
        <div
          role="status"
          className="rounded-md border border-amber-300/80 bg-amber-50/70 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"
        >
          {t("compliance.framework.coverageUnknownNotice")}
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t("compliance.framework.failureOnlyNote")}</p>
      {(totalActionable > 0 || totalAccepted > 0 || totalNotEvaluated > 0 || totalRaw > 0) && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>
            {t("compliance.framework.actionableSummary", {
              actionable: String(totalActionable),
              accepted: String(totalAccepted),
              notEvaluated: String(totalNotEvaluated),
              raw: String(totalRaw),
            })}
          </span>
          <a
            href={COMPLIANCE_EXCEPTIONS_DOC_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="underline hover:text-foreground transition-colors"
          >
            {t("compliance.framework.exceptionsDocLink")}
          </a>
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {frameworks.map((fw) => {
          const isExpanded = expandedId === fw.id
          const actionableFail = fw.actionableFailCount ?? fw.failCount
          const acceptedFail = fw.acceptedFailCount ?? 0
          const notEvaluated = fw.notEvaluatedCount ?? Math.max(0, fw.totalControls - fw.failCount)
          const evaluatedPass = fw.evaluatedPassCount ?? 0
          const testedPass = fw.testedPassCount ?? (fw.benchmarkTestedPassCount ?? 0) + (fw.reportTestedPassCount ?? 0)
          const noFailurePass = fw.noFailureRecordedPassCount ?? Math.max(0, evaluatedPass - testedPass)
          const droppedChecks = fw.droppedCheckCount ?? 0
          const droppedControls = fw.droppedFailCount ?? 0
          const evaluatedRate = fw.evaluatedPassRate ?? 0
          const effectiveRate = (fw.evaluatedPassCount === undefined || fw.notEvaluatedCount === undefined ? 0 : fw.effectivePassRate ?? 0)
          const effectivePassNum = Math.round(effectiveRate * fw.totalControls)
          const hasActionable = actionableFail > 0
          const hasNotEval = notEvaluated > 0 || (Boolean(fw.coverageUnknown) || fw.evaluatedPassCount === undefined || fw.notEvaluatedCount === undefined)
          const evaluatedPctText = formatPassRatePct(evaluatedRate, {
            hasActionableFail: hasActionable,
            hasNotEvaluated: hasNotEval,
          })
          const effectivePctText = formatPassRatePct(effectiveRate, {
            hasActionableFail: hasActionable,
            hasNotEvaluated: hasNotEval,
          })

          return (
            <div key={fw.id} className="rounded-lg border bg-card p-4">
              <button
                className="w-full text-left"
                onClick={() => setExpandedId((prev) => (prev === fw.id ? null : fw.id))}
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold text-sm">{fw.title}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {t("compliance.framework.controls", { count: fw.totalControls })}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span
                      title={`${evaluatedPass}/${fw.totalControls}`}
                      className={`text-sm font-bold ${
                        evaluatedRate >= 0.8
                          ? "text-green-600"
                          : evaluatedRate >= 0.5
                          ? "text-amber-600"
                          : "text-red-600"
                      }`}
                    >
                      {evaluatedPctText}
                    </span>
                    <span
                      className="text-muted-foreground text-sm inline-block transition-transform duration-200"
                      style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}
                      title={isExpanded ? t("common.collapse") : t("common.expand")}
                    >
                      ›
                    </span>
                  </div>
                </div>
                <PassRateBar rate={hasActionable || hasNotEval ? Math.min(99.9, evaluatedRate * 100) : evaluatedRate * 100} />
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1.5 text-[0.7rem] text-muted-foreground">
                  <span>
                    {evaluatedPass}/{fw.totalControls} (
                    {testedPass > 0
                      ? t("compliance.counter.frameworksPassSplit", {
                          tested: String(testedPass),
                          noFailure: String(noFailurePass),
                        })
                      : `${noFailurePass} ${t("compliance.framework.noFailureRecordedShort")}`}
                    )
                  </span>
                  <span>{t("compliance.framework.effectivePassRate")}: {effectivePctText} ({effectivePassNum}/{fw.totalControls})</span>
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-2 text-xs text-muted-foreground">
                  {testedPass > 0 ? (
                    <>
                      <span className="text-green-600 font-medium" title={t("compliance.framework.testedBenchmarkLabel")}>
                        ✓ {testedPass} {t("compliance.framework.testedBenchmarkShort")}
                      </span>
                      <span className="text-emerald-600 font-medium" title={t("compliance.framework.noFailureRecordedLabel")}>
                        ✓ {noFailurePass} {t("compliance.framework.noFailureRecordedShort")}
                      </span>
                    </>
                  ) : (
                    <span className="text-green-600 font-medium" title={t("compliance.framework.noFailureRecordedLabel")}>
                      ✓ {noFailurePass} {t("compliance.framework.noFailureRecordedShort")}
                    </span>
                  )}
                  <span className={`font-medium ${actionableFail > 0 ? "text-red-600" : "text-muted-foreground"}`}>
                    ✗ {actionableFail}
                  </span>
                  {notEvaluated > 0 && (
                    <Badge
                      variant="outline"
                      className="text-[0.65rem] px-1.5 py-0 text-amber-700 border-amber-300 dark:text-amber-400 dark:border-amber-800"
                    >
                      ? {notEvaluated} {t("compliance.framework.notEvaluatedShort")}
                    </Badge>
                  )}
                  {(acceptedFail > 0 || fw.failCount !== actionableFail || droppedChecks > 0 || droppedControls > 0) && (
                    <Badge
                      variant="outline"
                      className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
                    >
                      {[
                        acceptedFail > 0 ? `+${acceptedFail} ${t("compliance.framework.acceptedShort")}` : null,
                        droppedChecks > 0 || droppedControls > 0
                          ? t("compliance.framework.droppedShort", { count: String(droppedChecks || droppedControls) })
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ") || t("compliance.framework.rawChecks", { count: String(fw.failCount) })}
                      {(acceptedFail > 0 || droppedChecks > 0 || droppedControls > 0) &&
                        ` (${t("compliance.framework.rawChecks", { count: String(fw.failCount) })})`}
                    </Badge>
                  )}
                </div>
              </button>

              {isExpanded && <ExpandedFrameworkPanel id={fw.id} />}
            </div>
          )
        })}
      </div>
    </div>
  )
}
