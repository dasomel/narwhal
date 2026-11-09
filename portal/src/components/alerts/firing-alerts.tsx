"use client"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { useLocale, useT } from "@/lib/i18n-client"
import type { FiringAlertsResponse } from "@/types/firing-alerts"
import { TestAlertButton } from "./test-alert-button"

export function FiringAlerts({ canTest }: { canTest: boolean }) {
  const t = useT()
  const locale = useLocale()
  const [severity, setSeverity] = useState("")
  const query = useQuery<FiringAlertsResponse>({
    queryKey: ["alerts", "firing"],
    queryFn: async () => {
      const res = await fetch("/api/alerts/firing")
      if (!res.ok) throw new Error("Alerts unavailable")
      return res.json()
    },
    refetchInterval: 30_000,
  })
  const alerts = query.data?.alerts ?? []
  const severities = [...new Set(["critical", "warning", "info", ...alerts.map((a) => a.labels.severity).filter(Boolean)])]
  const rows = severity ? alerts.filter((a) => a.labels.severity === severity) : alerts
  const degraded = query.isError || query.data?.degraded
  return <div className="space-y-4">
    <div className="flex flex-wrap gap-3 items-center">
      <label className="text-sm flex gap-2 items-center">{t("alerts.severity")}
        <select className="rounded border bg-background p-2" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="">{t("alerts.allSeverities")}</option>
          {severities.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
      <Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>{t("alerts.refresh")}</Button>
    </div>
    {degraded && <p role="alert" className="rounded border border-amber-500 p-4 text-sm">{t("alerts.unreachable")}</p>}
    {query.isPending && <p role="status">{t("alerts.loading")}</p>}
    {!query.isPending && !degraded && rows.length === 0 && <p role="status">{t("alerts.none")}</p>}
    {rows.length > 0 && <div className="overflow-x-auto rounded-lg border"><table className="w-full text-sm text-left">
      <thead className="bg-muted"><tr>{[t("alerts.severity"), t("alerts.name"), t("alerts.namespace"), t("alerts.message"), t("alerts.detail.startsAt"), t("alerts.state")].map((label) => <th className="p-3" key={label}>{label}</th>)}</tr></thead>
      <tbody>{rows.map((alert, index) => <tr key={alert.fingerprint ?? index} className="border-t align-top">
        <td className="p-3 font-medium">{alert.labels.severity ?? "—"}</td>
        <td className="p-3 break-words">{alert.labels.alertname ?? "—"}</td>
        <td className="p-3">{alert.labels.namespace ?? "—"}</td>
        <td className="p-3 max-w-lg break-words"><p className="font-medium">{alert.annotations.summary}</p><p className="whitespace-pre-wrap">{alert.annotations.description}</p></td>
        <td className="p-3 whitespace-nowrap">{Number.isNaN(Date.parse(alert.startsAt)) ? "—" : new Date(alert.startsAt).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")}</td>
        <td className="p-3"><div>{alert.status.state}</div>{alert.status.silencedBy.length > 0 && <div>{t("alerts.silenced")}</div>}{alert.status.inhibitedBy.length > 0 && <div>{t("alerts.inhibited")}</div>}</td>
      </tr>)}</tbody>
    </table></div>}
    {canTest && <TestAlertButton />}
  </div>
}
