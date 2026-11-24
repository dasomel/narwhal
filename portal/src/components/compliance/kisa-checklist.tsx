"use client"
import { useState, useCallback } from "react"
import { useQuery } from "@tanstack/react-query"
import { useT, useLocale } from "@/lib/i18n-client"
import { pick } from "@/lib/i18n-utils"
import { DataState } from "@/components/ui/data-state"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { fetchComplianceJson } from "./fetch-json"
import type { KisaResponse, KisaControl, KisaReasonKey, KisaStatus } from "@/types/kisa"
import type { Severity } from "@/types/security"

const severityBadgeClass: Record<Severity, string> = {
  Critical: "bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-400",
  High: "bg-orange-100 text-orange-700 dark:bg-orange-950/50 dark:text-orange-400",
  Medium: "bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400",
  Low: "bg-blue-100 text-blue-700 dark:bg-blue-950/50 dark:text-blue-400",
  Unknown: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
}

const statusBadgeClass: Record<KisaStatus, string> = {
  fail: "bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-400",
  warn: "bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400",
  manual: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  pass: "bg-green-100 text-green-700 dark:bg-green-950/50 dark:text-green-400",
}

function SummaryBadge({ label, count, className }: { label: string; count: number; className: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>
      {label} <span className="font-bold">{count}</span>
    </span>
  )
}

function kisaReasonLabel(key: KisaReasonKey, t: ReturnType<typeof useT>): string {
  switch (key) {
    case "compliance.kisa.reason.apiForbidden": return t("compliance.kisa.reason.apiForbidden")
    case "compliance.kisa.reason.apiUnavailable": return t("compliance.kisa.reason.apiUnavailable")
    case "compliance.kisa.reason.listTruncated": return t("compliance.kisa.reason.listTruncated")
    case "compliance.kisa.reason.resourceMissing": return t("compliance.kisa.reason.resourceMissing")
    case "compliance.kisa.reason.etcdIsolatedTls": return t("compliance.kisa.reason.etcdIsolatedTls")
    case "compliance.kisa.reason.etcdIsolatedPlainHttp": return t("compliance.kisa.reason.etcdIsolatedPlainHttp")
    case "compliance.kisa.reason.etcdPolicyMissing": return t("compliance.kisa.reason.etcdPolicyMissing")
    case "compliance.kisa.reason.etcdPolicyBroad": return t("compliance.kisa.reason.etcdPolicyBroad")
    case "compliance.kisa.reason.etcdContainerMissing": return t("compliance.kisa.reason.etcdContainerMissing")
    case "compliance.kisa.reason.secCronJobNotEvaluated": return t("compliance.kisa.reason.secCronJobNotEvaluated")
    case "compliance.kisa.reason.secNoExternalKms": return t("compliance.kisa.reason.secNoExternalKms")
    case "compliance.kisa.reason.secSecretMetaNotEvaluated": return t("compliance.kisa.reason.secSecretMetaNotEvaluated")
    case "compliance.kisa.reason.secCronJobWeak": return t("compliance.kisa.reason.secCronJobWeak")
    case "compliance.kisa.reason.secSecretExposed": return t("compliance.kisa.reason.secSecretExposed")
    case "compliance.kisa.reason.imgNodeManual": return t("compliance.kisa.reason.imgNodeManual")
    case "compliance.kisa.reason.tlsStrictNoExceptions": return t("compliance.kisa.reason.tlsStrictNoExceptions")
    case "compliance.kisa.reason.tlsStrictWithExceptions": return t("compliance.kisa.reason.tlsStrictWithExceptions")
    case "compliance.kisa.reason.tlsNotStrict": return t("compliance.kisa.reason.tlsNotStrict")
    case "compliance.kisa.reason.obsConfigured": return t("compliance.kisa.reason.obsConfigured")
    case "compliance.kisa.reason.obsNoChannel": return t("compliance.kisa.reason.obsNoChannel")
    case "compliance.kisa.reason.obsFalcoDown": return t("compliance.kisa.reason.obsFalcoDown")
    case "compliance.kisa.reason.obsConfigMissing": return t("compliance.kisa.reason.obsConfigMissing")
    case "compliance.kisa.reason.imgPolicyEnforced": return t("compliance.kisa.reason.imgPolicyEnforced")
    case "compliance.kisa.reason.imgVerifyAudit": return t("compliance.kisa.reason.imgVerifyAudit")
    case "compliance.kisa.reason.imgPolicyBaselineDiff": return t("compliance.kisa.reason.imgPolicyBaselineDiff")
    case "compliance.kisa.reason.imgPolicyNotReady": return t("compliance.kisa.reason.imgPolicyNotReady")
    case "compliance.kisa.reason.imgPolicyInvalidRule": return t("compliance.kisa.reason.imgPolicyInvalidRule")
    case "compliance.kisa.reason.imgViolations": return t("compliance.kisa.reason.imgViolations")
    case "compliance.kisa.reason.imgPolicyReportsNotEvaluated": return t("compliance.kisa.reason.imgPolicyReportsNotEvaluated")
    case "compliance.kisa.reason.imgInventoryNotEvaluated": return t("compliance.kisa.reason.imgInventoryNotEvaluated")
    case "compliance.kisa.reason.rbacClean": return t("compliance.kisa.reason.rbacClean")
    case "compliance.kisa.reason.rbacHighBindings": return t("compliance.kisa.reason.rbacHighBindings")
    case "compliance.kisa.reason.rbacCriticalBindings": return t("compliance.kisa.reason.rbacCriticalBindings")
    case "compliance.kisa.reason.cpAuditEnabled": return t("compliance.kisa.reason.cpAuditEnabled")
    case "compliance.kisa.reason.cpAuditPartialOrUnready": return t("compliance.kisa.reason.cpAuditPartialOrUnready")
    case "compliance.kisa.reason.cpAuditMissing": return t("compliance.kisa.reason.cpAuditMissing")
    case "compliance.kisa.reason.cpPodNotReady": return t("compliance.kisa.reason.cpPodNotReady")
    case "compliance.kisa.reason.apiserverContainerMissing": return t("compliance.kisa.reason.apiserverContainerMissing")
    case "compliance.kisa.reason.etcdEncEnabled": return t("compliance.kisa.reason.etcdEncEnabled")
    case "compliance.kisa.reason.etcdEncPartialOrUnready": return t("compliance.kisa.reason.etcdEncPartialOrUnready")
    case "compliance.kisa.reason.etcdEncMissing": return t("compliance.kisa.reason.etcdEncMissing")
    case "compliance.kisa.reason.podPsaEnforced": return t("compliance.kisa.reason.podPsaEnforced")
    case "compliance.kisa.reason.podPsaPartial": return t("compliance.kisa.reason.podPsaPartial")
    case "compliance.kisa.reason.podPsaMissing": return t("compliance.kisa.reason.podPsaMissing")
    case "compliance.kisa.reason.netDenyAll": return t("compliance.kisa.reason.netDenyAll")
    case "compliance.kisa.reason.netDenyPartial": return t("compliance.kisa.reason.netDenyPartial")
    case "compliance.kisa.reason.netDenyMissing": return t("compliance.kisa.reason.netDenyMissing")
    case "compliance.kisa.reason.imgCveClean": return t("compliance.kisa.reason.imgCveClean")
    case "compliance.kisa.reason.imgCveCritical": return t("compliance.kisa.reason.imgCveCritical")
    case "compliance.kisa.reason.imgCveHigh": return t("compliance.kisa.reason.imgCveHigh")
    case "compliance.kisa.reason.imgCveNoReports": return t("compliance.kisa.reason.imgCveNoReports")
    case "compliance.kisa.reason.imgCveTruncated": return t("compliance.kisa.reason.imgCveTruncated")
    case "compliance.kisa.reason.cfgClean": return t("compliance.kisa.reason.cfgClean")
    case "compliance.kisa.reason.cfgActionableFail": return t("compliance.kisa.reason.cfgActionableFail")
    case "compliance.kisa.reason.cfgUnevaluated": return t("compliance.kisa.reason.cfgUnevaluated")
    case "compliance.kisa.reason.cfgEmpty": return t("compliance.kisa.reason.cfgEmpty")
    case "compliance.kisa.reason.tlsCertOk": return t("compliance.kisa.reason.tlsCertOk")
    case "compliance.kisa.reason.tlsCertInvalid": return t("compliance.kisa.reason.tlsCertInvalid")
    case "compliance.kisa.reason.tlsCertMissingExpiry": return t("compliance.kisa.reason.tlsCertMissingExpiry")
    case "compliance.kisa.reason.admEnforced": return t("compliance.kisa.reason.admEnforced")
    case "compliance.kisa.reason.admPartialOrDiffers": return t("compliance.kisa.reason.admPartialOrDiffers")
    case "compliance.kisa.reason.etcdWorkloadNotReady": return t("compliance.kisa.reason.etcdWorkloadNotReady")
  }
}

function ExpandedPanel({ control }: { control: KisaControl }) {
  const t = useT()
  const locale = useLocale()
  const detailText = pick(control.detailLocalized ?? control.detail, locale)
  return (
    <div className="px-6 py-4 bg-muted/10 border-t border-dashed space-y-3 text-xs">
      <div>
        <p className="font-medium text-muted-foreground mb-1">{t("compliance.kisa.evidence")}</p>
        <p className="text-foreground">{control.evidence}</p>
      </div>
      {(control.reasonKey || detailText) && (
        <div>
          <p className="font-medium text-muted-foreground mb-1">{t("compliance.kisa.statusDetail")}</p>
          {control.reasonKey && (
            <p className="font-medium text-foreground mb-0.5">
              {kisaReasonLabel(control.reasonKey, t)}
            </p>
          )}
          {detailText && <p className="text-foreground">{detailText}</p>}
        </div>
      )}
      <div>
        <p className="font-medium text-muted-foreground mb-1">{t("compliance.kisa.remediation")}</p>
        <p className="text-foreground">{control.remediation}</p>
      </div>
      {!control.live && (
        <div className="rounded-md border border-amber-200 bg-amber-50/60 dark:bg-amber-950/10 dark:border-amber-900 px-3 py-2">
          <p className="text-amber-700 dark:text-amber-400">{t("compliance.kisa.manualNote")}</p>
        </div>
      )}
    </div>
  )
}

export function KisaChecklist() {
  const t = useT()
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, isError, refetch } = useQuery<KisaResponse>({
    queryKey: ["compliance", "kisa"],
    queryFn: () => fetchComplianceJson<KisaResponse>("/api/compliance/kisa"),
    staleTime: 60_000,
  })

  const toggleRow = useCallback((id: string) => {
    setExpandedId((prev) => (prev === id ? null : id))
  }, [])

  if (isLoading) return <DataState state="loading" onRetry={() => { void refetch() }} />
  if (isError) return <DataState state="unavailable" onRetry={() => { void refetch() }} />

  if (!data || data.controls.length === 0) {
    return <DataState state="empty" />
  }

  const { controls, summary } = data

  return (
    <>
      {/* Summary row */}
      <div className="flex flex-wrap gap-2 mb-3">
        <SummaryBadge
          label={t("compliance.kisa.status.all")}
          count={summary.total}
          className="bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
        />
        <SummaryBadge
          label={t("compliance.kisa.status.fail")}
          count={summary.fail}
          className={statusBadgeClass.fail}
        />
        <SummaryBadge
          label={t("compliance.kisa.status.warn")}
          count={summary.warn}
          className={statusBadgeClass.warn}
        />
        <SummaryBadge
          label={t("compliance.kisa.status.manual")}
          count={summary.manual}
          className={statusBadgeClass.manual}
        />
        <SummaryBadge
          label={t("compliance.kisa.status.pass")}
          count={summary.pass}
          className={statusBadgeClass.pass}
        />
      </div>

      <Card className="p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-muted-foreground bg-muted/30">
                <th className="px-3 py-3 w-8"><span className="sr-only">{t("common.details")}</span></th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">{t("compliance.kisa.col.id")}</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">{t("compliance.kisa.col.domain")}</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">{t("compliance.kisa.col.control")}</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">{t("compliance.kisa.col.severity")}</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">{t("compliance.kisa.col.status")}</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">{t("compliance.kisa.col.standards")}</th>
              </tr>
            </thead>
            <tbody>
              {controls.map((control) => {
                const isExpanded = expandedId === control.id
                return (
                  <>
                    <tr key={control.id} className="border-b hover:bg-muted/20 transition-colors">
                      <td className="px-3 py-2.5 text-muted-foreground">
                        <button type="button" aria-label={t("common.details")} className="inline-block transition-transform duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={(event) => { event.stopPropagation(); toggleRow(control.id) }}
                          style={{ transform: isExpanded ? "rotate(90deg)" : "rotate(0deg)" }}
                          title={isExpanded ? t("common.collapse") : t("common.expand")}
                        >
                          ›
                        </button>
                      </td>
                      <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground whitespace-nowrap">
                        {control.id}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                        {control.domain}
                      </td>
                      <td className="px-4 py-2.5 font-medium">{control.title}</td>
                      <td className="px-4 py-2.5">
                        <Badge className={`text-xs ${severityBadgeClass[control.severity] ?? severityBadgeClass.Unknown}`}>
                          {control.severity}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge className={`text-xs ${statusBadgeClass[control.status]}`}>
                          {t(`compliance.kisa.status.${control.status}` as Parameters<typeof t>[0])}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground">
                        {control.standardRefs.join(", ")}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${control.id}__expand`}>
                        <td colSpan={7} className="p-0">
                          <ExpandedPanel control={control} />
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  )
}
