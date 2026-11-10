"use client"

import { Activity, Box, CircleAlert, Layers, Network } from "lucide-react"
import type { ServiceGraphResponse } from "@/app/api/service-graph/route"
import { useT } from "@/lib/i18n-client"
import { IconChip, ServiceStatus, SummaryCard } from "./architecture-primitives"
import { summarizeServices } from "./service-map-model"

export function ServiceSummary({ data }: { data: ServiceGraphResponse }) {
  const t = useT()
  const counts = summarizeServices(data)
  return <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
    <SummaryCard icon={Box} label={t("architecture.view.services")} value={counts.services} />
    <SummaryCard icon={Activity} label={t("arch.healthy")} value={counts.healthy} />
    <SummaryCard icon={CircleAlert} label={t("arch.degraded")} value={counts.degraded} />
    <SummaryCard icon={Layers} label={t("arch.namespaces")} value={counts.namespaces} />
  </div>
}

export function ServiceGroups({ data, selectedId, focusService, onSelect }: {
  data: ServiceGraphResponse
  selectedId: string | null
  focusService?: string
  onSelect: (id: string | null) => void
}) {
  const t = useT()
  const groups = new Map<string, ServiceGraphResponse["nodes"]>()
  for (const node of data.nodes) {
    const key = node.namespace || "unknown"
    groups.set(key, [...(groups.get(key) ?? []), node])
  }
  return <div className={`grid min-w-0 items-start gap-4 md:grid-cols-2 ${selectedId ? "" : "xl:grid-cols-3"}`}>
    {[...groups].sort(([a], [b]) => a.localeCompare(b)).map(([namespace, nodes]) => <section key={namespace} className="min-w-0 rounded-xl border border-border bg-card p-5">
      <div className="mb-4 flex min-w-0 items-center gap-2"><Network aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /><h2 className="min-w-0 break-all text-sm font-medium">{namespace === "unknown" ? t("arch.unknown") : namespace}</h2><span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{nodes.length}</span></div>
      <div className="space-y-2">{nodes.map((node) => {
        const active = selectedId === node.id
        return <button type="button" key={node.id} aria-pressed={active} onClick={() => onSelect(active ? null : node.id)} className={`flex w-full min-w-0 items-start gap-2.5 rounded-lg border p-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-ring ${active || focusService === node.id ? "border-narwhal-accent bg-narwhal-accent/5" : "border-border"}`}>
          <IconChip icon={Box} />
          <div className="min-w-0 flex-1"><p className="break-all text-sm font-medium">{node.id.startsWith("unmapped-pods:") ? `${node.id.replace("unmapped-pods:", "")} *` : node.id}</p><div className="mt-1 flex flex-wrap items-center justify-between gap-x-2 gap-y-1"><p className="min-w-0 truncate text-xs text-muted-foreground">{namespace === "unknown" ? t("arch.unknown") : namespace}</p><ServiceStatus status={node.status} /></div></div>
        </button>
      })}</div>
    </section>)}
  </div>
}
