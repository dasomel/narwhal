import type { ServiceGraphResponse } from "@/app/api/service-graph/route"

export function scoreTierBorderColor(tier?: string): string {
  switch (tier) {
    case "gold":
      return "var(--narwhal-warning)"
    case "silver":
      return "var(--muted-foreground)"
    case "bronze":
      return "var(--narwhal-warning)"
    default:
      return "var(--border)"
  }
}

export function edgeColor(errorRate: number): string {
  if (errorRate > 0.05) return "var(--narwhal-danger)" // >5% 빨강
  if (errorRate > 0.01) return "var(--narwhal-warning)" // 1–5% 노랑
  return "var(--narwhal-success)" // 정상 초록
}

export function statusColor(status: string): string {
  switch (status) {
    case "healthy":
      return "var(--narwhal-success)"
    case "down":
    case "degraded":
      return "var(--narwhal-danger)"
    default:
      return "var(--muted-foreground)"
  }
}

function formatRate(v: number): string {
  if (v >= 100) return v.toFixed(0)
  if (v >= 1) return v.toFixed(1)
  return v.toFixed(2)
}

// requestRate 단위: L7=req/s, L4(istio_tcp_sent_bytes_total)=bytes/s
export function formatTraffic(v: number, kind?: string): string {
  if (kind === "l4") {
    if (v >= 1048576) return `${(v / 1048576).toFixed(1)} MB/s`
    if (v >= 1024) return `${(v / 1024).toFixed(1)} KB/s`
    return `${v.toFixed(0)} B/s`
  }
  return `${formatRate(v)} req/s`
}

type GraphEdge = ServiceGraphResponse["edges"][number]

// 혼합(L7+L4) 그래프에서 엣지별 단위 결정
export function edgeKind(e: GraphEdge, metricKind?: string): "l7" | "l4" {
  return (e.kind ?? (metricKind === "l4" ? "l4" : "l7")) as "l7" | "l4"
}

// kind가 섞인 엣지 합계를 "X req/s · Y KB/s" 형태로
export function sumTraffic(edges: GraphEdge[], metricKind?: string): string {
  let l7 = 0
  let l4 = 0
  for (const e of edges) {
    if (edgeKind(e, metricKind) === "l4") l4 += e.requestRate
    else l7 += e.requestRate
  }
  const parts: string[] = []
  if (l7 > 0) parts.push(formatTraffic(l7, "l7"))
  if (l4 > 0) parts.push(formatTraffic(l4, "l4"))
  return parts.length > 0 ? parts.join(" · ") : formatTraffic(0, metricKind)
}

// D1: Count only observed nodes/namespaces. No inferred layers or health defaults;
// cost: no platform category labels; escape hatch: consume a future API group field.
export function summarizeServices(data: ServiceGraphResponse) {
  return {
    services: data.nodes.length,
    healthy: data.nodes.filter((node) => node.status === "healthy").length,
    degraded: data.nodes.filter((node) => (node.status === "degraded" || node.status === "down")).length,
    namespaces: new Set(data.nodes.map((node) => node.namespace).filter((ns) => ns && ns !== "unknown")).size,
  }
}
