import { requireDashboardAccess } from "@/lib/dashboard-access"
import { getLocale } from "@/lib/i18n-server"
import { t, translateNotEvaluatedReason, type Locale } from "@/lib/i18n"
import { COMPLIANCE_EXCEPTIONS_DOC_URL } from "@/lib/compliance-accepted"
import { translateTitle } from "@/lib/check-translations"
import { Badge } from "@/components/ui/badge"
import {
  formatPassRatePct,
  type AcceptedScope,
  type ComplianceControl,
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

const severityOrder: Severity[] = ["Critical", "High", "Medium", "Low", "Unknown"]

function acceptedScopeBadgeLabel(locale: Locale, scope?: AcceptedScope): string {
  if (scope === "system") return t(locale, "compliance.table.accepted")
  if (scope === "builtin") return t(locale, "compliance.table.acceptedRbac")
  if (scope === "upstream") return t(locale, "compliance.table.acceptedUpstream")
  if (scope === "mixed") return t(locale, "compliance.framework.mixedScope")
  return t(locale, "compliance.table.acceptedException")
}

interface Props {
  params: Promise<{ id: string }>
}

type DetailFetchResult =
  | { status: "ok"; detail: ComplianceFrameworkDetail }
  | { status: "not_found" }
  | { status: "unavailable" }

async function fetchDetail(id: string): Promise<DetailFetchResult> {
  try {
    const { getComplianceFrameworkDetail } = await import("@/lib/compliance")
    const detail = await getComplianceFrameworkDetail(id)
    if (!detail) return { status: "not_found" }
    return { status: "ok", detail }
  } catch (err) {
    if (err && typeof err === "object" && "name" in err && err.name === "ComplianceDataUnavailableError") {
      return { status: "unavailable" }
    }
    return { status: "not_found" }
  }
}

function ControlsTable({
  controls,
  frameworkId,
  locale,
  showAcceptedBadge,
  showNotEvaluated = false,
}: {
  controls: ComplianceControl[]
  frameworkId: string
  locale: Locale
  showAcceptedBadge: boolean
  showNotEvaluated?: boolean
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-muted-foreground bg-muted/20">
            <th className="px-4 py-2.5 font-medium">ID</th>
            <th className="px-4 py-2.5 font-medium">Name</th>
            <th className="px-4 py-2.5 font-medium">Severity</th>
            {showNotEvaluated ? (
              <th className="px-4 py-2.5 font-medium">{t(locale, "compliance.framework.notEvaluatedShort")}</th>
            ) : (
              <>
                <th className="px-4 py-2.5 font-medium">{t(locale, "compliance.check.passed")}</th>
                <th className="px-4 py-2.5 font-medium">{t(locale, "compliance.framework.actionableFailed")}</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {controls.map((ctrl) => {
            const actionableFail = ctrl.actionableFailCount ?? ctrl.failCount
            const acceptedFail = ctrl.acceptedFailCount ?? 0
            const droppedFail = ctrl.droppedFailCount ?? 0
            const isNotEvaluated = ctrl.evaluationState === "not_evaluated"
            return (
              <tr
                key={ctrl.id}
                className={`border-b last:border-0 hover:bg-muted/10 ${
                  isNotEvaluated
                    ? "bg-amber-50/30 dark:bg-amber-950/10"
                    : ctrl.accepted
                      ? "bg-slate-50/40 dark:bg-slate-900/20"
                      : ""
                }`}
              >
                <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
                  {ctrl.id}
                </td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="font-medium">
                      {frameworkId.startsWith("k8s-cis")
                        ? translateTitle("cis", ctrl.id, ctrl.name, locale)
                        : ctrl.name}
                    </p>
                    {showAcceptedBadge && ctrl.accepted && (
                      <Badge
                        variant="outline"
                        className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
                      >
                        {acceptedScopeBadgeLabel(locale, ctrl.acceptedScope)}
                      </Badge>
                    )}
                  </div>
                  {ctrl.description && (
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{ctrl.description}</p>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <Badge className={`text-xs ${severityBadgeClass[ctrl.severity] ?? severityBadgeClass.Unknown}`}>
                    {ctrl.severity}
                  </Badge>
                </td>
                {showNotEvaluated ? (
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    <Badge
                      variant="outline"
                      className="mr-2 text-[0.65rem] px-1.5 py-0 text-amber-700 border-amber-300 dark:text-amber-400 dark:border-amber-800"
                    >
                      {t(locale, "compliance.framework.notEvaluatedShort")}
                    </Badge>
                    {translateNotEvaluatedReason(locale, ctrl.notEvaluatedReason)}
                    {ctrl.notEvaluatedDetail ? ` (${ctrl.notEvaluatedDetail})` : ""}
                  </td>
                ) : (
                  <>
                    <td className="px-4 py-2.5 text-green-600 font-medium">{ctrl.passCount}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`font-medium ${actionableFail > 0 ? "text-red-600" : "text-muted-foreground"}`}>
                          {actionableFail}
                        </span>
                        {(acceptedFail > 0 || ctrl.failCount !== actionableFail || droppedFail > 0) && (
                          <Badge
                            variant="outline"
                            className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
                          >
                            {[
                              acceptedFail > 0 ? `+${acceptedFail} ${t(locale, "compliance.framework.acceptedShort")}` : null,
                              droppedFail > 0
                                ? t(locale, "compliance.framework.droppedShort", { count: String(droppedFail) })
                                : null,
                            ]
                              .filter(Boolean)
                              .join(" · ") || t(locale, "compliance.framework.rawChecks", { count: String(ctrl.failCount) })}
                            {(acceptedFail > 0 || droppedFail > 0) &&
                              ` (${t(locale, "compliance.framework.rawChecks", { count: String(ctrl.failCount) })})`}
                          </Badge>
                        )}
                      </div>
                    </td>
                  </>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default async function FrameworkDetailPage({ params }: Props) {
  const session = await requireDashboardAccess()

  const locale = await getLocale()

  if (session.user?.role !== "cluster-admin") {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-lg border bg-card p-8 max-w-md w-full text-center space-y-2">
          <div className="text-4xl">🔒</div>
          <h2 className="text-lg font-semibold">{t(locale, "compliance.forbidden")}</h2>
        </div>
      </div>
    )
  }

  const { id } = await params
  const idDecoded = decodeURIComponent(id)

  const fetched = await fetchDetail(idDecoded)

  if (fetched.status === "unavailable") {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <a className="inline-flex items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-[0.8rem] font-medium hover:bg-muted transition-colors" href="/compliance">← {t(locale, "common.back")}</a>
          <h1 className="text-xl font-bold">{t(locale, "compliance.detail.title")}</h1>
        </div>
        <div role="alert" className="rounded-lg border border-destructive/40 bg-destructive/5 p-8 text-center space-y-1">
          <p className="text-sm font-semibold text-destructive">{t(locale, "compliance.counter.dataUnavailable")}</p>
          <p className="text-xs text-muted-foreground">{t(locale, "dataState.unavailable")}</p>
        </div>
      </div>
    )
  }

  if (fetched.status === "not_found") {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          <a className="inline-flex items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-[0.8rem] font-medium hover:bg-muted transition-colors" href="/compliance">← {t(locale, "common.back")}</a>
          <h1 className="text-xl font-bold">{t(locale, "compliance.detail.title")}</h1>
        </div>
        <div className="rounded-lg border bg-card p-8 text-center text-muted-foreground">
          {t(locale, "common.notFound")}
        </div>
      </div>
    )
  }

  const { detail } = fetched

  const mainControls = [...detail.controls]
    .filter((c) => !c.accepted && c.evaluationState !== "not_evaluated")
    .sort((a, b) => {
      const aFail = (a.actionableFailCount ?? a.failCount) > 0 ? 0 : 1
      const bFail = (b.actionableFailCount ?? b.failCount) > 0 ? 0 : 1
      if (aFail !== bFail) return aFail - bFail
      return severityOrder.indexOf(a.severity) - severityOrder.indexOf(b.severity)
    })

  const acceptedControls = [...detail.controls]
    .filter((c) => Boolean(c.accepted))
    .sort((a, b) => severityOrder.indexOf(a.severity) - severityOrder.indexOf(b.severity))

  const notEvaluatedControls = [...detail.controls]
    .filter((c) => c.evaluationState === "not_evaluated")
    .sort((a, b) => severityOrder.indexOf(a.severity) - severityOrder.indexOf(b.severity))

  const evaluatedRate = detail.evaluatedPassRate ?? detail.passRate
  const effectiveRate = detail.effectivePassRate ?? detail.passRate
  const actionableControlsFail = detail.actionableFailCount ?? detail.failCount
  const acceptedControlsFail = detail.acceptedFailCount ?? acceptedControls.length
  const evaluatedPass = detail.evaluatedPassCount ?? detail.passCount
  const notEvaluated = detail.notEvaluatedCount ?? notEvaluatedControls.length
  const droppedControls = detail.droppedFailCount ?? 0
  const droppedChecks = detail.droppedCheckCount ?? 0

  const evaluatedPctText = formatPassRatePct(evaluatedRate, {
    hasActionableFail: actionableControlsFail > 0,
    hasNotEvaluated: notEvaluated > 0,
  })
  const effectivePctText = formatPassRatePct(effectiveRate, {
    hasActionableFail: actionableControlsFail > 0,
    hasNotEvaluated: notEvaluated > 0,
  })
  const rawPctText = formatPassRatePct(detail.passRate, {
    hasActionableFail: detail.failCount > 0,
  })
  const evaluatedWidthPct = Math.min(100, Math.max(0, evaluatedRate * (evaluatedRate <= 1 ? 100 : 1)))
  const barColor =
    evaluatedRate >= 0.8 ? "bg-green-500" : evaluatedRate >= 0.5 ? "bg-amber-500" : "bg-red-500"

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <a className="inline-flex items-center gap-1 rounded-lg border border-border bg-background px-2.5 py-1 text-[0.8rem] font-medium hover:bg-muted transition-colors" href="/compliance">← {t(locale, "common.back")}</a>
        <div>
          <h1 className="text-xl font-bold text-foreground">{detail.title}</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            {t(locale, "compliance.framework.evaluatedPassRate")}: {evaluatedPctText} &middot;{" "}
            {t(locale, "compliance.framework.rawPassRate")}: {rawPctText} ({detail.passCount}/{detail.totalControls}) &middot;{" "}
            {t(locale, "compliance.framework.effectivePassRate")}: {effectivePctText} &middot;{" "}
            {t(locale, "compliance.framework.controls", { count: detail.totalControls })}
          </p>
        </div>
      </div>

      {/* Pass rate bar + counters */}
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">{t(locale, "compliance.framework.evaluatedPassRate")}</span>
          <span
            className={`font-bold text-lg ${
              evaluatedRate >= 0.8
                ? "text-green-600"
                : evaluatedRate >= 0.5
                ? "text-amber-600"
                : "text-red-600"
            }`}
          >
            {evaluatedPctText}
          </span>
        </div>
        <div className="w-full bg-muted rounded-full h-2.5">
          <div className={`h-2.5 rounded-full ${barColor}`} style={{ width: `${evaluatedWidthPct}%` }} />
        </div>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <span className="text-green-600 font-medium">✓ {evaluatedPass} {t(locale, "compliance.check.passed")}</span>
          <span className={`font-medium ${actionableControlsFail > 0 ? "text-red-600" : "text-muted-foreground"}`}>
            ✗ {actionableControlsFail} {t(locale, "compliance.framework.actionableFailed")}
          </span>
          {notEvaluated > 0 && (
            <Badge
              variant="outline"
              className="text-xs text-amber-700 border-amber-300 dark:text-amber-400 dark:border-amber-800"
            >
              ? {notEvaluated} {t(locale, "compliance.framework.notEvaluatedShort")}
            </Badge>
          )}
          {(droppedChecks > 0 || droppedControls > 0) && (
            <Badge
              variant="outline"
              className="text-xs text-muted-foreground border-muted-foreground/30"
            >
              {t(locale, "compliance.framework.droppedShort", { count: String(droppedChecks || droppedControls) })}
            </Badge>
          )}
          {(acceptedControlsFail > 0 || detail.failCount !== actionableControlsFail) && (
            <>
              {acceptedControlsFail > 0 && (
                <Badge
                  variant="outline"
                  className="text-xs text-muted-foreground border-muted-foreground/30"
                >
                  +{acceptedControlsFail} {t(locale, "compliance.framework.acceptedShort")}
                </Badge>
              )}
              <span className="text-xs text-muted-foreground">
                {t(locale, "compliance.framework.rawSummary", {
                  fail: String(detail.failCount),
                  rate: rawPctText.replace(/%$/, ""),
                })}
              </span>
              <a
                href={COMPLIANCE_EXCEPTIONS_DOC_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs underline text-muted-foreground hover:text-foreground transition-colors"
              >
                {t(locale, "compliance.framework.exceptionsDocLink")}
              </a>
            </>
          )}
        </div>
      </div>

      {/* Main Controls table (actionable + evaluated passed) */}
      <div className="rounded-lg border bg-card overflow-hidden">
        <div className="px-4 py-3 border-b bg-muted/30">
          <h2 className="text-sm font-semibold">
            {t(locale, "compliance.framework.controls", { count: mainControls.length })}
          </h2>
        </div>
        <ControlsTable
          controls={mainControls}
          frameworkId={idDecoded}
          locale={locale}
          showAcceptedBadge={false}
        />
      </div>

      {/* Not-evaluated Controls (collapsed group) */}
      {notEvaluatedControls.length > 0 && (
        <details className="rounded-lg border border-amber-200/80 bg-card overflow-hidden dark:border-amber-900/60">
          <summary className="px-4 py-3 border-b bg-amber-50/40 dark:bg-amber-950/20 cursor-pointer text-sm font-semibold flex flex-wrap items-center justify-between gap-2 select-none">
            <span className="text-amber-800 dark:text-amber-300">
              {t(locale, "compliance.framework.notEvaluatedControlsGroup", { count: String(notEvaluatedControls.length) })}
            </span>
            <Badge
              variant="outline"
              className="text-[0.65rem] px-1.5 py-0 text-amber-700 border-amber-300 dark:text-amber-400 dark:border-amber-800"
            >
              {t(locale, "compliance.framework.notEvaluatedShort")}
            </Badge>
          </summary>
          <ControlsTable
            controls={notEvaluatedControls}
            frameworkId={idDecoded}
            locale={locale}
            showAcceptedBadge={false}
            showNotEvaluated={true}
          />
        </details>
      )}

      {/* Accepted-only Controls (collapsed group) */}
      {acceptedControls.length > 0 && (
        <details className="rounded-lg border bg-card overflow-hidden">
          <summary className="px-4 py-3 border-b bg-muted/30 cursor-pointer text-sm font-semibold flex flex-wrap items-center justify-between gap-2 select-none">
            <span>{t(locale, "compliance.framework.acceptedControlsGroup", { count: String(acceptedControls.length) })}</span>
            <Badge
              variant="outline"
              className="text-[0.65rem] px-1.5 py-0 text-muted-foreground border-muted-foreground/30"
            >
              {t(locale, "compliance.table.acceptedException")}
            </Badge>
          </summary>
          <div className="px-4 py-2 border-b bg-muted/10 flex justify-end">
            <a
              href={COMPLIANCE_EXCEPTIONS_DOC_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs underline text-muted-foreground hover:text-foreground transition-colors"
            >
              {t(locale, "compliance.framework.exceptionsDocLink")}
            </a>
          </div>
          <ControlsTable
            controls={acceptedControls}
            frameworkId={idDecoded}
            locale={locale}
            showAcceptedBadge={true}
          />
        </details>
      )}
    </div>
  )
}
