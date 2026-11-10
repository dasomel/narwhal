"use client"

import { useId, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Activity, Box, Boxes, ChartNoAxesCombined, GitBranch, KeyRound, Layers3, Monitor, Route, ShieldCheck } from "lucide-react"
import type { ArchitectureData } from "@/app/api/architecture/route"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { DataState } from "@/components/ui/data-state"
import type { TranslationKey } from "@/lib/i18n"
import { useT } from "@/lib/i18n-client"
import { EDGES, LAYERS, NODES, edgePath, nodeStatus } from "./layout"

const STATUS = {
  healthy: "bg-narwhal-success", degraded: "bg-narwhal-warning",
  offline: "bg-narwhal-danger", unknown: "bg-muted-foreground",
}
const ICONS = { portal: Monitor, keycloak: KeyRound, apisix: Route, grafana: ChartNoAxesCombined, argocd: GitBranch, gitea: GitBranch, harbor: Box, prometheus: Activity, openbao: ShieldCheck, k8s: Boxes }
type Node = typeof NODES[number]

export function PlatformArchitecture() {
  const t = useT()
  const marker = useId().replace(/:/g, "")
  const [hovered, setHovered] = useState<string | null>(null)
  const [focused, setFocused] = useState<string | null>(null)
  const active = hovered ?? focused
  const { data, isLoading, isError, refetch } = useQuery<ArchitectureData>({
    queryKey: ["architecture"],
    queryFn: async () => {
      const response = await fetch("/api/architecture")
      if (!response.ok) throw new Error(response.statusText)
      return response.json()
    },
    refetchInterval: 30_000,
  })
  const text = (key: string) => t(`onboarding.arch.${key}` as TranslationKey)
  const statusText = (status: keyof typeof STATUS) => text(`status.${status}`)

  function nodeCard(node: Node) {
    const status = nodeStatus(data, node.id)
    const Icon = ICONS[node.id]
    return <button type="button" data-testid={`arch-node-${node.id}`} aria-label={`${node.label}: ${text(`role.${node.id}`)}. ${statusText(status)}`}
      onMouseEnter={() => setHovered(node.id)} onMouseLeave={() => setHovered(null)}
      onFocus={() => setFocused(node.id)} onBlur={() => setFocused(null)}
      className={`group flex h-full w-full items-start gap-3 rounded-xl border bg-card p-3 text-left text-foreground outline-none focus-visible:ring-2 focus-visible:ring-narwhal-accent focus-visible:ring-inset ${active === node.id ? "border-narwhal-accent" : "border-border"}`}>
      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-narwhal-accent/10 text-narwhal-accent"><Icon className="size-4" aria-hidden="true" /></span>
      <span className="min-w-0 flex-1"><span className="block text-sm font-medium leading-5">{node.label}</span><span className="block text-xs leading-4 text-muted-foreground">{text(`role.${node.id}`)}</span><span className="mt-1 flex items-center gap-1.5 text-[11px] leading-4 text-muted-foreground"><span className={`size-1.5 shrink-0 rounded-full ${STATUS[status]}`} aria-hidden="true" />{statusText(status)}</span></span>
    </button>
  }

  return <Card data-testid="onboarding-architecture" className="min-w-0 gap-4 overflow-hidden rounded-xl">
    <CardHeader className="gap-3">
      <div className="flex items-start gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-lg bg-narwhal-accent/10 text-narwhal-accent"><Layers3 className="size-4" aria-hidden="true" /></span><div><CardTitle className="text-base">{t("arch.title")}</CardTitle><p className="mt-1 text-sm text-muted-foreground">{text("description")}</p></div></div>
      <div className="flex flex-wrap gap-2" aria-label={text("legend")}>
        {Object.keys(STATUS).map(value => { const status = value as keyof typeof STATUS; return <span key={status} className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground"><span className={`size-2 rounded-full ${STATUS[status]}`} aria-hidden="true" />{statusText(status)}</span> })}
      </div>
    </CardHeader>
    <CardContent className="min-w-0">
      {isLoading && !data && <DataState state="loading" />}
      {isError && <div className="mb-3"><DataState state={data ? "stale" : "unavailable"} onRetry={() => { void refetch() }} /></div>}
      <p className="mb-3 text-xs text-muted-foreground">{text("hint")}</p>
      {/* D2: A stacked mobile figure keeps text readable without page overflow.
          Cost: two responsive presentations; escape hatch: one scrollable figure. */}
      <div className="space-y-3 lg:hidden" role="group" aria-label={text("diagram")}>
        {LAYERS.map((layer, index) => <section key={layer.key} className="rounded-xl border border-border bg-muted/30 p-3" aria-label={text(`layer.${layer.key}`)}>
          <h3 className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground"><span className="font-mono text-narwhal-accent">{String(index + 1).padStart(2, "0")}</span>{text(`layer.${layer.key}`)}</h3>
          <div className="grid gap-2 sm:grid-cols-2">{layer.nodes.map(node => <div key={node.id}>{nodeCard({ ...node, y: 36 + index * 120 })}</div>)}</div>
        </section>)}
      </div>
      <div className="hidden overflow-x-auto rounded-xl border border-border lg:block" role="group" aria-label={text("diagram")}>
        <svg viewBox="0 0 1020 620" className="block w-full min-w-[1020px]" aria-label={text("diagram")}>
          <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke" /></marker></defs>
          {LAYERS.map((layer, index) => <g key={layer.key}>
            <rect x="8" y={12 + index * 120} width="1004" height="108" rx="12" className="fill-muted/40" />
            <text x="24" y={44 + index * 120} className="fill-narwhal-accent font-mono text-[11px]">{String(index + 1).padStart(2, "0")}</text>
            <foreignObject x="24" y={54 + index * 120} width="136" height="50"><h3 className="text-xs font-medium text-muted-foreground">{text(`layer.${layer.key}`)}</h3></foreignObject>
          </g>)}
          {EDGES.map(([from, to]) => {
            const highlighted = active === from || active === to
            return <path key={`${from}-${to}`} data-edge={`${from}-${to}`} d={edgePath(from, to)} fill="none" className={highlighted ? "stroke-narwhal-accent" : "stroke-muted-foreground"} strokeOpacity={highlighted ? 1 : active ? 0.15 : 0.45} strokeWidth={highlighted ? 2 : 1.25} markerEnd={`url(#${marker})`} />
          })}
          {NODES.map(node => <foreignObject key={node.id} x={node.x} y={node.y} width="188" height="84">{nodeCard(node)}</foreignObject>)}
        </svg>
      </div>
    </CardContent>
  </Card>
}
