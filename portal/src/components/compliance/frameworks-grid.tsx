"use client"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { useT, useLocale } from "@/lib/i18n-client"
import { COMPLIANCE_EXCEPTIONS_DOC_URL } from "@/lib/compliance-accepted"
import { DataState } from "@/components/ui/data-state"
import { translateTitle } from "@/lib/check-translations"
import { Badge } from "@/components/ui/badge"
import type {
  AcceptedScope,
  ComplianceControl,
  ComplianceFramework,
  ComplianceFrameworkDetail,
  Severity,
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
  const isAcceptedOnly = Boolean(ctrl.accepted)
  const cardBorder = isAcceptedOnly
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
      <div className="flex flex-wrap items-center gap-3 mt-1 text-muted-foreground">
        <span className="text-green-600 font-medium">✓ {ctrl.passCount}</span>
        <span className={`font-medium ${actionableFail > 0 ? "text-red-600" : "text-muted-foreground"}`}>
          ✗ {actionableFail}
        </span>
        {(acceptedFail > 0 || ctrl.failCount !== actionableFail) && (
          <Badge
            variant="outline"
            className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
          >
            {acceptedFail > 0
              ? `+${acceptedFail} ${t("compliance.framework.acceptedShort")} (${t("compliance.framework.rawChecks", { count: String(ctrl.failCount) })})`
              : t("compliance.framework.rawChecks", { count: String(ctrl.failCount) })}
          </Badge>
        )}
      </div>
    </div>
  )
}

export function ExpandedFrameworkPanel({ id }: { id: string }) {
  const t = useT()
  const locale = useLocale()

  const { data: detail, isLoading } = useQuery<ComplianceFrameworkDetail>({
    queryKey: ["compliance-framework-detail", id],
    queryFn: () =>
      fetch(`/api/compliance/frameworks?id=${encodeURIComponent(id)}`).then((r) => r.json()),
    enabled: true,
    staleTime: 60_000,
  })

  const actionableAndOther = detail?.controls
    ? [...detail.controls]
        .filter((c) => !c.accepted)
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

  return (
    <div className="mt-3 pt-3 border-t border-dashed space-y-2">
      {isLoading ? (
        <span className="text-xs text-muted-foreground animate-pulse">{t("common.loading")}</span>
      ) : detail ? (
        <>
          {top5.length === 0 && acceptedControls.length === 0 ? (
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
    queryFn: () => fetch("/api/compliance/frameworks").then((r) => r.json()),
    staleTime: 60_000,
  })

  if (isLoading) return <DataState state="loading" onRetry={() => { void refetch() }} />
  if (isError) return <DataState state="unavailable" onRetry={() => { void refetch() }} />

  if (frameworks.length === 0) {
    return <DataState state="empty" />
  }

  const totalActionable = frameworks.reduce((acc, f) => acc + (f.actionableFailCount ?? f.failCount), 0)
  const totalAccepted = frameworks.reduce((acc, f) => acc + (f.acceptedFailCount ?? 0), 0)
  const totalRaw = frameworks.reduce((acc, f) => acc + f.failCount, 0)

  return (
    <div className="space-y-3">
      {(totalActionable > 0 || totalAccepted > 0 || totalRaw > 0) && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
          <span>
            {t("compliance.framework.actionableSummary", {
              actionable: String(totalActionable),
              accepted: String(totalAccepted),
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
          const displayPassRate = fw.effectivePassRate ?? fw.passRate
          const rawPct = Math.round(fw.passRate * 100)
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
                      className={`text-sm font-bold ${
                        displayPassRate >= 0.8
                          ? "text-green-600"
                          : displayPassRate >= 0.5
                          ? "text-amber-600"
                          : "text-red-600"
                      }`}
                    >
                      {Math.round(displayPassRate * 100)}%
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
                <PassRateBar rate={displayPassRate * 100} />
                <div className="flex flex-wrap items-center gap-2 mt-2 text-xs text-muted-foreground">
                  <span className="text-green-600 font-medium">✓ {fw.passCount}</span>
                  <span className={`font-medium ${actionableFail > 0 ? "text-red-600" : "text-muted-foreground"}`}>
                    ✗ {actionableFail}
                  </span>
                  {(acceptedFail > 0 || fw.failCount !== actionableFail) && (
                    <Badge
                      variant="outline"
                      className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
                      title={t("compliance.framework.rawSummary", {
                        fail: String(fw.failCount),
                        rate: String(rawPct),
                      })}
                    >
                      {acceptedFail > 0
                        ? `+${acceptedFail} ${t("compliance.framework.acceptedShort")} (${t("compliance.framework.rawChecks", { count: String(fw.failCount) })})`
                        : t("compliance.framework.rawChecks", { count: String(fw.failCount) })}
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
