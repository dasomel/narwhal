"use client"

import { useEffect, useRef } from "react"
import { Box, X } from "lucide-react"
import type { ServiceGraphResponse } from "@/app/api/service-graph/route"
import { useT } from "@/lib/i18n-client"
import { IconChip, ServiceStatus } from "./architecture-primitives"
import { edgeColor, edgeKind, formatTraffic, scoreTierBorderColor, sumTraffic } from "./service-map-model"

export function NodeDetailPanel({
  data,
  nodeId,
  onClose,
  onSelect,
  t,
}: {
  data: ServiceGraphResponse
  nodeId: string
  onClose: () => void
  onSelect: (id: string) => void
  t: ReturnType<typeof useT>
}) {
  const panelRef = useRef<HTMLElement>(null)
  useEffect(() => {
    if (globalThis.matchMedia?.("(max-width: 1279px)").matches) {
      panelRef.current?.scrollIntoView({ block: "start" })
    }
  }, [nodeId])
  const node = data.nodes.find((n) => n.id === nodeId)
  const inbound = data.edges.filter((e) => e.destination === nodeId)
  const outbound = data.edges.filter((e) => e.source === nodeId)
  const isUnmapped = nodeId.startsWith("unmapped-pods:")
  const displayName = isUnmapped ? nodeId.replace("unmapped-pods:", "") : nodeId

  function EdgeRow({ peer, rate, err, p95, kind }: { peer: string; rate: number; err: number; p95?: number | null; kind: "l7" | "l4" }) {
    return (
      <button
        type="button"
        onClick={() => onSelect(peer)}
        className="w-full flex items-center justify-between gap-2 rounded px-2 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        <span className="font-mono text-xs truncate text-foreground" title={peer}>
          {peer.startsWith("unmapped-pods:") ? peer.replace("unmapped-pods:", "") + " *" : peer}
        </span>
        <span className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground font-mono">
          <span>{formatTraffic(rate, kind)}</span>
          <span style={{ color: edgeColor(err) }}>{(err * 100).toFixed(1)}%</span>
          {p95 != null && <span>{p95}ms</span>}
        </span>
      </button>
    )
  }

  return (
    <aside ref={panelRef} aria-label={displayName} className="order-first min-w-0 space-y-5 xl:order-last rounded-xl border border-border bg-card p-5 xl:sticky xl:top-4 xl:max-h-[700px] xl:overflow-y-auto">
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5"><IconChip icon={Box} /><div className="min-w-0"><h2 className="break-all text-sm font-medium">{displayName}</h2><ServiceStatus status={node?.status ?? "unknown"} badge /></div></div>
        <button type="button" onClick={onClose} aria-label={t("common.close")} className="text-muted-foreground hover:text-foreground text-sm leading-none px-1">
          <X className="size-4" />
        </button>
      </div>

      {isUnmapped && (
        <p className="text-xs text-muted-foreground">{t("svcMap.detail.unmapped")}</p>
      )}

      <div className="space-y-1.5 text-xs">
        <div className="flex flex-wrap justify-between gap-2">
          <span className="text-muted-foreground">{t("svcMap.detail.namespace")}</span>
          <span className="break-all font-mono text-foreground">{node?.namespace ?? "-"}</span>
        </div>
        <div className="flex flex-wrap justify-between gap-2">
          <span className="text-muted-foreground">{t("svcMap.detail.status")}</span>
          <ServiceStatus status={node?.status ?? "unknown"} />
        </div>
        {node?.scoreTier && node.scoreTier !== "none" && (
          <div className="flex flex-wrap justify-between gap-2">
            <span className="text-muted-foreground">{t("svcMap.detail.tier")}</span>
            <span className="capitalize" style={{ color: scoreTierBorderColor(node.scoreTier) }}>
              {node.scoreTier === "gold" ? "🥇" : node.scoreTier === "silver" ? "🥈" : "🥉"} {node.scoreTier}
            </span>
          </div>
        )}
        <div className="flex flex-wrap justify-between gap-2">
          <span className="text-muted-foreground">{t("svcMap.detail.totalIn")}</span>
          <span className="break-all font-mono text-foreground">{sumTraffic(inbound, data.metricKind)}</span>
        </div>
        <div className="flex flex-wrap justify-between gap-2">
          <span className="text-muted-foreground">{t("svcMap.detail.totalOut")}</span>
          <span className="break-all font-mono text-foreground">{sumTraffic(outbound, data.metricKind)}</span>
        </div>
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
          {t("svcMap.detail.inbound")} ({inbound.length})
        </p>
        {inbound.length === 0 ? (
          <p className="text-xs text-muted-foreground px-2">{t("svcMap.detail.noEdges")}</p>
        ) : (
          <div className="space-y-0.5">
            {inbound.map((e, i) => (
              <EdgeRow key={i} peer={e.source} rate={e.requestRate} err={e.errorRate} p95={e.p95LatencyMs} kind={edgeKind(e, data.metricKind)} />
            ))}
          </div>
        )}
      </div>

      <div>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
          {t("svcMap.detail.outbound")} ({outbound.length})
        </p>
        {outbound.length === 0 ? (
          <p className="text-xs text-muted-foreground px-2">{t("svcMap.detail.noEdges")}</p>
        ) : (
          <div className="space-y-0.5">
            {outbound.map((e, i) => (
              <EdgeRow key={i} peer={e.destination} rate={e.requestRate} err={e.errorRate} p95={e.p95LatencyMs} kind={edgeKind(e, data.metricKind)} />
            ))}
          </div>
        )}
      </div>

      {!isUnmapped && (
        <a href={`/catalog/${encodeURIComponent(nodeId)}`} className="inline-flex items-center justify-center rounded-md border border-border px-3 py-2 text-xs font-medium hover:bg-muted">
          {t("svcMap.detail.viewCatalog")}
        </a>
      )}
    </aside>
  )
}

