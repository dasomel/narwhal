"use client"

import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Cpu, Database, Layers3, MemoryStick } from "lucide-react"
import { Card } from "@/components/ui/card"
import { DataState } from "@/components/ui/data-state"
import { useT } from "@/lib/i18n-client"
import type { TranslationKey } from "@/lib/i18n"
import type { ArgoCDResponse } from "@/types/api"

type Metrics = {
  cpu: number | null
  memory: number | null
  nodes: { total: number | null; ready: number | null }
  pods: { total: number | null; running: number | null }
}
type RangeData = { data: Array<{ timestamp: number; value: number }> }
type Alert = { labels: Record<string, string>; startsAt?: string }

const ranges = [{ label: "1h", minutes: 60 }, { label: "3h", minutes: 180 }, { label: "6h", minutes: 360 }, { label: "24h", minutes: 1440 }]
const meterWidths = ["w-0", "w-1/12", "w-2/12", "w-3/12", "w-4/12", "w-5/12", "w-6/12", "w-7/12", "w-8/12", "w-9/12", "w-10/12", "w-11/12", "w-full"]
const barWidths = ["w-0", "w-1/12", "w-2/12", "w-3/12", "w-4/12", "w-5/12", "w-6/12", "w-7/12", "w-8/12", "w-9/12", "w-10/12", "w-11/12", "w-full"]

type KpiData = { cpu: number | null; memory: number | null; nodes: { total: number | null; ready: number | null }; pods: { total: number | null; running: number | null } }
type KpiModel = { title: string; value: string; detail: string; percent?: number | null; unavailable: boolean; icon: typeof Cpu }

export function buildKpiModels(m: KpiData | undefined, t: (key: TranslationKey, values?: Record<string, string | number>) => string): KpiModel[] {
  const cpuUnavailable = m?.cpu == null
  const memoryUnavailable = m?.memory == null
  const nodesUnavailable = m?.nodes?.total == null || m?.nodes?.ready == null
  const podsUnavailable = m?.pods?.running == null
  const allReady = !nodesUnavailable && m.nodes.ready === m.nodes.total
  return [
    { title: t("metrics.cpu"), value: cpuUnavailable ? "—" : `${Math.round(m?.cpu ?? 0)}%`, detail: cpuUnavailable ? t("dashboard.noData") : t("dashboard.nodeAverage", { count: m?.nodes?.total ?? 0 }), percent: m?.cpu, unavailable: cpuUnavailable, icon: Cpu },
    { title: t("metrics.memory"), value: memoryUnavailable ? "—" : `${Math.round(m?.memory ?? 0)}%`, detail: memoryUnavailable ? t("dashboard.noData") : t("dashboard.nodeAverage", { count: m?.nodes?.total ?? 0 }), percent: m?.memory, unavailable: memoryUnavailable, icon: MemoryStick },
    { title: t("metrics.nodes"), value: nodesUnavailable ? "—" : `${m?.nodes?.ready ?? 0} / ${m?.nodes?.total ?? 0}`, detail: nodesUnavailable ? t("dashboard.noData") : allReady ? t("dashboard.allReady") : `${(m?.nodes?.total ?? 0) - (m?.nodes?.ready ?? 0)} NotReady`, percent: m?.nodes?.total == null ? undefined : (m.nodes.total ? (m.nodes.ready ?? 0) / m.nodes.total * 100 : 0), unavailable: nodesUnavailable, icon: Database },
    { title: t("metrics.pods"), value: podsUnavailable ? "—" : `${m?.pods?.running ?? 0}`, detail: podsUnavailable ? t("dashboard.noData") : t("dashboard.running"), percent: m?.pods?.total == null ? undefined : (m.pods.total ? (m.pods.running ?? 0) / m.pods.total * 100 : 0), unavailable: podsUnavailable, icon: Layers3 },
  ]
}

function useJson<T>(key: unknown[], url: string, interval = 30_000) {
  return useQuery<T>({ queryKey: key, queryFn: async () => {
    const response = await fetch(url)
    if (!response.ok) throw new Error(response.statusText)
    return response.json()
  }, refetchInterval: interval })
}

function Kpi({ title, value, detail, percent, unavailable, icon: Icon }: KpiModel) {
  return <Card className="p-5 rounded-xl">
    <div className="flex items-center gap-2.5"><span className="grid size-8 place-items-center rounded-lg bg-narwhal-accent/10 text-narwhal-accent"><Icon className="size-4" /></span><span className="text-[13px] text-muted-foreground">{title}</span></div>
    <div className="mt-3 flex items-baseline gap-2"><strong className="text-3xl leading-9 font-bold">{value}</strong><span className="text-xs text-muted-foreground">{detail}</span></div>
    {percent !== undefined && <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">{!unavailable && <div className={`h-full rounded-full bg-narwhal-accent ${meterWidths[Math.round(Math.min(100, Math.max(0, percent ?? 0)) / 100 * 12)]}`} role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.max(0, percent ?? 0))} />}</div>}
  </Card>
}

function Chart({ metric, minutes, title, compact = false }: { metric: string; minutes: number; title: string; compact?: boolean }) {
  const t = useT()
  const { data, isLoading, isError, refetch } = useJson<RangeData>(["metrics-range", metric, minutes], `/api/metrics/range?metric=${metric}&minutes=${minutes}`)
  const points = data?.data ?? []
  const max = metric === "pods" ? Math.max(1, ...points.map((p) => p.value)) : 100
  const coords = points.map((point, index) => `${points.length < 2 ? 50 : index / (points.length - 1) * 100},${100 - point.value / max * 90}`).join(" ")
  return <div className={compact ? "h-full" : "rounded-xl border border-border bg-card p-5 min-w-0"}>
    {!compact && <div className="flex items-center justify-between"><h2 className="text-sm font-medium">{title}</h2><span className="font-mono text-[11px] text-muted-foreground">Last {minutes / 60}h</span></div>}
    <div className={compact ? "h-full" : "mt-3 min-h-[120px]"}>
      {isLoading && !data ? <DataState state="loading" onRetry={() => { void refetch() }} /> : isError && !data ? <DataState state="unavailable" onRetry={() => { void refetch() }} /> : points.length === 0 ? <div className="flex h-full items-center justify-center"><DataState state={isError ? "stale" : "empty"} onRetry={() => { void refetch() }} /></div> : <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-[120px] w-full" role="img" aria-label={title}>
        <polygon points={`0,100 ${coords} 100,100`} fill="var(--narwhal-accent)" fillOpacity=".12" />
        <polyline points={coords} fill="none" stroke="var(--narwhal-accent)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>}
    </div>
    <span className="sr-only">{t("dashboard.description")}</span>
  </div>
}

export function DashboardOverview({ title, description }: { title: string; description: string }) {
  const t = useT()
  const [minutes, setMinutes] = useState(60)
  const metrics = useJson<Metrics>(["metrics"], "/api/metrics")
  const argo = useJson<ArgoCDResponse>(["argocd"], "/api/argocd", 10_000)
  const alertsQuery = useJson<Alert[]>(["alerts"], "/api/alerts", 15_000)
  const m = metrics.data
  const alerts = alertsQuery.data ?? []
  const cards = buildKpiModels(m, t)
  return <div className="space-y-6">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><h1 className="text-[26px] font-bold tracking-[-.01em]">{title}</h1><p className="mt-1 text-sm text-muted-foreground">{description}</p></div>
      <div className="flex rounded-lg bg-muted p-1" role="group" aria-label={t("dashboard.timeRange")}>
        {ranges.map((range) => <button key={range.label} type="button" onClick={() => setMinutes(range.minutes)} aria-pressed={minutes === range.minutes} className={`rounded-md px-3 py-1 text-xs ${minutes === range.minutes ? "bg-card text-foreground shadow-sm" : "text-muted-foreground"}`}>{range.label}</button>)}
      </div>
    </header>
    {metrics.isLoading && !m ? <DataState state="loading" onRetry={() => { void metrics.refetch() }} /> : metrics.isError && !m ? <DataState state="unavailable" onRetry={() => { void metrics.refetch() }} /> : <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">{cards.map((card) => <Kpi key={card.title} {...card} />)}</div>}
    <section className="grid grid-cols-1 gap-4 xl:grid-cols-3"><Chart metric="cpu" minutes={minutes} title={t("chart.cpu")} /><Chart metric="memory" minutes={minutes} title={t("chart.memory")} />
      <Card className="p-5 rounded-xl"><div className="flex items-center justify-between"><h2 className="text-sm font-medium">{t("dashboard.runningPodsTrend")}</h2><span className="font-mono text-[11px] text-muted-foreground">Last {minutes / 60}h</span></div><div className="mt-3 min-h-[120px]"><Chart metric="pods" minutes={minutes} title={t("chart.pods")} compact /></div></Card>
    </section>
    <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Card className="p-5 rounded-xl"><h2 className="text-sm font-medium">{t("argocd.title")}</h2>{argo.isLoading && !argo.data ? <div className="mt-3"><DataState state="loading" onRetry={() => { void argo.refetch() }} /></div> : argo.isError && !argo.data ? <div className="mt-3"><DataState state="unavailable" onRetry={() => { void argo.refetch() }} /></div> : <><div className="mt-3 flex flex-wrap gap-2">{([["Synced", argo.data?.summary.synced ?? 0, "bg-narwhal-success"], ["OutOfSync", argo.data?.summary.outOfSync ?? 0, "bg-narwhal-warning"], ["Degraded", argo.data?.summary.degraded ?? 0, "bg-narwhal-danger"]] as const).map(([label, count, color]) => <span key={label} className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs"><i className={`size-2 rounded-full ${color}`} />{label} {count}</span>)}</div><div className="mt-3 flex h-2 overflow-hidden rounded-full bg-muted">{([[argo.data?.summary.synced, "bg-narwhal-success"], [argo.data?.summary.outOfSync, "bg-narwhal-warning"], [argo.data?.summary.degraded, "bg-narwhal-danger"]] as const).map(([count, color], index) => { const share = (count ?? 0) / Math.max(1, argo.data?.summary.total ?? 0); return <span key={index} className={`${color} ${barWidths[Math.round(share * 12)]}`} /> })}</div></>}</Card>
      <Card className="p-5 rounded-xl"><h2 className="text-sm font-medium">{t("alerts.title")}</h2>{alertsQuery.isLoading && !alertsQuery.data ? <div className="mt-3"><DataState state="loading" onRetry={() => { void alertsQuery.refetch() }} /></div> : alertsQuery.isError && !alertsQuery.data ? <div className="mt-3"><DataState state="unavailable" onRetry={() => { void alertsQuery.refetch() }} /></div> : <ul className="mt-3 space-y-2">{alerts.slice(0, 4).map((alert, index) => <li key={`${alert.labels.alertname}-${index}`} className={`border-l-[3px] pl-2.5 ${alert.labels.severity === "critical" ? "border-narwhal-danger" : "border-narwhal-warning"}`}><div className="font-mono text-xs">{alert.labels.alertname}</div><div className="text-[11px] text-muted-foreground">{alert.labels.severity} · {t("dashboard.minutesAgo", { count: alert.startsAt ? Math.max(1, Math.floor((alertsQuery.dataUpdatedAt - new Date(alert.startsAt).getTime()) / 60000)) : 0 })}</div></li>)}{alerts.length === 0 && <li className="text-sm text-muted-foreground">{t("alerts.none")}</li>}</ul>}</Card>
    </section>
  </div>
}
