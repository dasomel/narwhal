"use client"

import { useRef } from "react"
import { Box } from "lucide-react"
import { ReactFlow, Background, Controls, MiniMap } from "reactflow"
import "reactflow/dist/style.css"
import type { ServiceGraphResponse } from "@/app/api/service-graph/route"
import { IconChip, ServiceStatus } from "./architecture-primitives"
import { edgeColor, edgeKind, formatTraffic, scoreTierBorderColor, statusColor } from "./service-map-model"

const POS_STORAGE_KEY = "narwhal-svcmap-positions-v1"

function loadSavedPositions(): Map<string, { x: number; y: number }> {
  try {
    const raw = globalThis.localStorage?.getItem(POS_STORAGE_KEY)
    if (!raw) return new Map()
    return new Map(JSON.parse(raw) as Array<[string, { x: number; y: number }]>)
  } catch {
    return new Map()
  }
}

export function ReactFlowMap({
  data,
  focusService,
  selectedId,
  onSelect,
  liveFlows,
}: {
  data: ServiceGraphResponse
  focusService?: string
  selectedId: string | null
  onSelect: (id: string | null) => void
  /** "src|dst" → flows/s (hubble 실시간 오버레이, live 모드에서만 채워짐) */
  liveFlows?: Map<string, number>
}) {
  const COLS = Math.ceil(Math.sqrt(data.nodes.length)) || 1
  const NODE_W = 200
  const NODE_H = 76
  const GAP_X = 80
  const GAP_Y = 80

  // 위치 안정화:
  // 1) id 정렬 → 데이터 순서(엣지 등장 순서)가 바뀌어도 그리드 슬롯이 결정적
  // 2) 드래그한 위치는 localStorage에 저장 → 폴링/새로고침에도 유지
  const posRef = useRef<Map<string, { x: number; y: number }> | null>(null)
  if (posRef.current === null) posRef.current = loadSavedPositions()
  const savedPos = posRef.current
  const sortedNodes = [...data.nodes].sort((a, b) => a.id.localeCompare(b.id))

  // 선택 노드의 이웃 집합 — 선택 시 연결된 것만 선명하게, 나머지는 흐리게
  const neighborIds = new Set<string>()
  if (selectedId) {
    neighborIds.add(selectedId)
    for (const e of data.edges) {
      if (e.source === selectedId) neighborIds.add(e.destination)
      if (e.destination === selectedId) neighborIds.add(e.source)
    }
  }
  const isDimmedNode = (id: string) => selectedId !== null && !neighborIds.has(id)
  const isConnectedEdge = (s: string, d: string) =>
    selectedId === null || s === selectedId || d === selectedId

  const rfNodes = sortedNodes.map((n, i) => {
    const col = i % COLS
    const row = Math.floor(i / COLS)
    const isFocused = (focusService && n.id === focusService) || n.id === selectedId
    const isUnmapped = n.id.startsWith("unmapped-pods:")
    return {
      id: n.id,
      position: savedPos.get(n.id) ?? { x: col * (NODE_W + GAP_X), y: row * (NODE_H + GAP_Y) },
      data: { label: <div className="flex w-full items-start gap-2 text-left"><IconChip icon={Box} /><div className="min-w-0"><div className="truncate font-medium">{isUnmapped ? n.id.replace("unmapped-pods:", "") + " *" : n.id}</div><div className="truncate text-[10px] text-muted-foreground">{n.namespace}</div><ServiceStatus status={n.status} /></div></div> },
      style: {
        background: "var(--card)",
        color: "var(--card-foreground)",
        border: `1px solid ${isFocused ? "var(--narwhal-accent)" : "var(--border)"}`,
        borderRadius: 14,
        fontSize: 12,
        padding: 10,
        width: NODE_W,
        height: NODE_H,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        boxShadow: isFocused ? "0 0 0 2px var(--narwhal-accent)" : undefined,
        cursor: "pointer",
        opacity: isDimmedNode(n.id) ? 0.2 : 1,
        transition: "opacity 0.2s",
      },
    }
  })

  // 트래픽 양 → 엣지 굵기 (√ 스케일, 1.5px ~ 6px)
  // L7(req/s)과 L4(bytes/s)는 단위 스케일이 달라 종류별로 정규화
  const maxRateByKind = { l7: 0.000001, l4: 0.000001 }
  for (const e of data.edges) {
    const k = edgeKind(e, data.metricKind)
    if (e.requestRate > maxRateByKind[k]) maxRateByKind[k] = e.requestRate
  }
  const rfEdges = data.edges.map((e, i) => {
    const connected = isConnectedEdge(e.source, e.destination)
    const k = edgeKind(e, data.metricKind)
    const fps = liveFlows?.get(`${e.source}|${e.destination}`) ?? 0
    return {
      id: `e-${i}`,
      source: e.source,
      target: e.destination,
      style: {
        stroke: edgeColor(e.errorRate),
        strokeWidth: 1.5 + 4.5 * Math.sqrt(e.requestRate / maxRateByKind[k]),
        opacity: connected ? 0.9 : 0.07,
        transition: "opacity 0.2s",
      },
      // 흐려진 엣지는 라벨 숨김 (선택 노드 주변만 정보 표시)
      label: connected
        ? formatTraffic(e.requestRate, k) +
          (e.errorRate > 0.01 ? ` · ${(e.errorRate * 100).toFixed(1)}%` : "") +
          (fps > 0 ? ` ⚡${fps}/s` : "")
        : undefined,
      labelStyle: { fontSize: 10, fill: fps > 0 ? "var(--narwhal-warning)" : edgeColor(e.errorRate) },
      // 실시간 흐름이 지나가는 엣지는 즉시 애니메이션
      animated: connected && (fps > 0 || e.errorRate > 0.05),
    }
  })

  // hubble 스트림에만 보이는 연결(그래프 메트릭 미반영 신규 트래픽) → 점선 고스트 엣지
  const knownPairs = new Set(data.edges.map((e) => `${e.source}|${e.destination}`))
  const nodeIdSet = new Set(data.nodes.map((n) => n.id))
  const ghostEdges = Array.from(liveFlows?.entries() ?? [])
    .filter(([key, fps]) => fps > 0 && !knownPairs.has(key))
    .flatMap(([key, fps], gi) => {
      const [src, dst] = key.split("|")
      if (!nodeIdSet.has(src) || !nodeIdSet.has(dst)) return []
      if (!isConnectedEdge(src, dst)) return []
      return [
        {
          id: `ghost-${gi}`,
          source: src,
          target: dst,
          style: { stroke: "var(--narwhal-warning)", strokeWidth: 1.5, strokeDasharray: "6 4", opacity: 0.8 },
          label: `⚡${fps}/s`,
          labelStyle: { fontSize: 10, fill: "var(--narwhal-warning)" },
          animated: true,
        },
      ]
    })
  const allEdges = [...rfEdges, ...ghostEdges]

  return (
    <div className="h-[500px] min-w-0 overflow-hidden rounded-xl border border-border bg-background [&_.react-flow__controls-button]:bg-card [&_.react-flow__controls-button]:fill-foreground [&_.react-flow__controls-button]:border-border [&_.react-flow__edge-textbg]:fill-card [&_.react-flow__minimap]:bg-card">
      <ReactFlow
        nodes={rfNodes}
        edges={allEdges}
        fitView
        attributionPosition="bottom-right"
        onNodeClick={(_, node) => onSelect(node.id === selectedId ? null : node.id)}
        onPaneClick={() => onSelect(null)}
        onNodeDragStop={(_, node) => {
          savedPos.set(node.id, node.position)
          try {
            globalThis.localStorage?.setItem(POS_STORAGE_KEY, JSON.stringify(Array.from(savedPos.entries())))
          } catch {
            // 저장 실패는 무시 (세션 내 ref는 유지됨)
          }
        }}
      >
        <Background />
        <Controls />
        <MiniMap
          pannable
          zoomable
          nodeBorderRadius={8}
          nodeStrokeWidth={3}
          maskColor="var(--muted)"
          nodeColor={(n) => {
            if (n.id === selectedId) return "var(--narwhal-accent)"
            const node = data.nodes.find((nd) => nd.id === n.id)
            return statusColor(node?.status ?? "unknown")
          }}
          nodeStrokeColor={(n) => {
            const node = data.nodes.find((nd) => nd.id === n.id)
            return scoreTierBorderColor(node?.scoreTier)
          }}
        />
      </ReactFlow>
    </div>
  )
}

