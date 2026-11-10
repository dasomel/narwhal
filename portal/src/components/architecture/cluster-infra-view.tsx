"use client"

import { useQuery } from "@tanstack/react-query"
import { Box, Cpu, Layers, Server } from "lucide-react"
import Link from "next/link"
import type { ClusterInfra } from "@/app/api/cluster/route"
import { useT } from "@/lib/i18n-client"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { DataState } from "@/components/ui/data-state"
import { CapacityMeter, IconChip, SummaryCard } from "./architecture-primitives"

const SYSTEM_NAMESPACES = new Set([
  "kube-system",
  "kube-public",
  "kube-node-lease",
  "cert-manager",
  "istio-system",
  "metallb-system",
  "platform-system",
  "devtools",
  "iam",
  "monitoring",
  "logging",
  "tracing",
])

function isClusterInfra(value: unknown): value is ClusterInfra {
  if (typeof value !== "object" || value === null) return false
  const response = value as Partial<ClusterInfra>
  return Array.isArray(response.nodes)
    && Array.isArray(response.controlPlane)
    && Array.isArray(response.namespaces)
    && typeof response.summary === "object"
    && response.summary !== null
    && typeof response.summary.truncated === "boolean"
}

export function ClusterInfraView() {
  const t = useT()
  const { data, isLoading, isError, refetch } = useQuery<ClusterInfra>({
    queryKey: ["cluster-infra"],
    queryFn: () => fetch("/api/cluster").then((r) => {
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    }),
    refetchInterval: 30_000,
  })
  const validData = isClusterInfra(data) ? data : undefined
  if (isLoading && !data) return <DataState state="loading" onRetry={() => { void refetch() }} />
  if ((isError && !data) || (data && !validData)) return <DataState state="error" onRetry={() => { void refetch() }} />
  if (!validData) return <DataState state="empty" />
  const cpReady = validData.controlPlane.filter((c) => c.status === "Running").length
  const cpTotal = validData.controlPlane.length

  return <div className="min-w-0 space-y-6">
    {isError ? <DataState state="error" onRetry={() => { void refetch() }} /> : validData.summary.truncated ? <DataState state="partial" /> : null}
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <SummaryCard icon={Server} label={t("arch.nodes")} value={`${validData.summary.readyNodes}/${validData.summary.totalNodes}`} sub={t("arch.ready")} />
      <SummaryCard icon={Box} label={t("arch.pods")} value={validData.summary.totalPods} />
      <SummaryCard icon={Layers} label={t("arch.namespaces")} value={validData.summary.totalNamespaces} />
      <SummaryCard icon={Cpu} label={t("arch.controlPlane")} value={`${cpReady}/${cpTotal}`} sub={t("arch.running")} />
    </div>
    <section className="space-y-4">
      <h2 className="text-sm font-medium">{t("arch.nodes")}</h2>
      {validData.nodes.length === 0 ? <DataState state="empty" onRetry={() => { void refetch() }} /> : <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {validData.nodes.map((node) => <Card key={node.name} className="min-w-0 rounded-xl p-5">
          <div className="flex items-start gap-2.5">
            <IconChip icon={Server} />
            <div className="min-w-0 flex-1"><Link href={`/nodes/${node.name}`} className="break-all text-sm font-medium hover:text-narwhal-accent hover:underline">{node.name}</Link><div className="mt-2 flex flex-wrap gap-1">{node.roles.map((role) => <Badge key={role} variant="outline" className="text-[11px]">{role}</Badge>)}</div></div>
          </div>
          <div className="flex items-center justify-between gap-2 text-xs"><span className="text-muted-foreground">{t("arch640.status")}</span><span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2 py-1"><span aria-hidden="true" className={`size-2 rounded-full ${node.status === "Ready" ? "bg-narwhal-success" : "bg-narwhal-danger"}`} />{node.status}</span></div>
          <CapacityMeter label={t("arch.cpu")} percent={node.cpu.percent} capacity={t("arch640.capacity", { value: `${node.cpu.total} CPU` })} />
          <CapacityMeter label={t("arch.memory")} percent={node.memory.percent} capacity={t("arch640.capacity", { value: `${node.memory.totalGi} GiB` })} />
          <dl className="space-y-2 border-t border-border pt-4 text-xs">
            <div className="flex justify-between gap-2"><dt className="text-muted-foreground">{t("arch.pods")}</dt><dd className="font-mono">{node.pods.running}/{node.pods.total}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-muted-foreground">{t("arch.version")}</dt><dd className="font-mono">{node.kubeletVersion}</dd></div>
          </dl>
        </Card>)}
      </div>}
    </section>
      {/* 섹션 3: 네임스페이스 그리드 */}
      <div className="rounded-xl border border-border bg-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h2 className="text-sm font-semibold text-foreground">{t("arch.namespaces")}</h2>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-3 rounded border border-narwhal-accent/40 bg-narwhal-accent/10" />
              {t("arch.legendSystem")}
            </span>
            <span className="flex items-center gap-1.5">
              {/* 실제 카드는 bg-muted/30이지만 3px 견본에서는 안 보여서 진하게 표시 */}
              <span className="inline-block w-3 h-3 rounded border border-muted-foreground/40 bg-muted" />
              {t("arch.legendUser")}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-3 h-3 rounded border border-dashed border-muted-foreground/60 bg-muted opacity-60" />
              {t("arch.legendTerminating")}
            </span>
          </div>
        </div>
        {validData.namespaces.length === 0 ? (
          <DataState state="empty" />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
            {validData.namespaces.map((ns) => {
              const isSystem = SYSTEM_NAMESPACES.has(ns.name)
              return (
                <div
                  key={ns.name}
                  className={`rounded-lg border p-3 flex flex-col gap-1 ${
                    isSystem
                      ? "border-narwhal-accent/20 bg-narwhal-accent/10"
                      : "border-border/50 bg-muted/30"
                  } ${ns.status === "Terminating" ? "opacity-50" : ""}`}
                >
                  <p
                    className={`text-xs font-medium truncate ${isSystem ? "text-narwhal-accent" : "text-foreground"}`}
                    title={ns.name}
                  >
                    {ns.name}
                  </p>
                  <div className="flex items-center gap-1">
                    <Badge
                      variant="outline"
                      className="text-xs px-1 py-0 h-4 border-border text-muted-foreground"
                    >
                      {ns.podCount} {t("arch.pods")}
                    </Badge>
                    {ns.status === "Terminating" && (
                      <Badge variant="destructive" className="text-xs px-1 py-0 h-4">
                        {t("arch.legendTerminating")}
                      </Badge>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
  </div>
}
