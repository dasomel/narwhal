import type { ArchitectureData, ArchNode } from "@/app/api/architecture/route"

// D1: Preserve the onboarding topology; the API owns health only. Cost: a small
// curated layout; escape hatch: use API edges when onboarding needs full topology.
export const LAYERS = [
  { key: "access", nodes: [{ id: "portal", label: "IDP Portal", x: 480 }] },
  { key: "gateway", nodes: [{ id: "keycloak", label: "Keycloak SSO", x: 270 }, { id: "apisix", label: "APISIX", x: 690 }] },
  { key: "delivery", nodes: [{ id: "grafana", label: "Grafana", x: 170 }, { id: "argocd", label: "ArgoCD", x: 380 }, { id: "gitea", label: "Gitea", x: 590 }, { id: "harbor", label: "Harbor", x: 800 }] },
  { key: "operations", nodes: [{ id: "prometheus", label: "Prometheus", x: 170 }, { id: "openbao", label: "OpenBao", x: 800 }] },
  { key: "foundation", nodes: [{ id: "k8s", label: "Kubernetes", x: 480 }] },
] as const

export const NODES = LAYERS.flatMap((layer, index) => layer.nodes.map(node => ({ ...node, y: 36 + index * 120 })))
export const EDGES = [
  ["portal", "keycloak"], ["portal", "apisix"],
  ["apisix", "argocd"], ["apisix", "gitea"], ["apisix", "harbor"], ["apisix", "grafana"],
  ["gitea", "argocd"], ["argocd", "k8s"], ["harbor", "k8s"],
  ["prometheus", "k8s"], ["grafana", "prometheus"], ["openbao", "k8s"],
] as const

export function nodeStatus(data: ArchitectureData | undefined, id: string): ArchNode["status"] {
  const status = data?.nodes?.find(node => node.id === id)?.status
  return status === "healthy" || status === "degraded" || status === "offline" ? status : "unknown"
}

export function edgePath(from: string, to: string) {
  const a = NODES.find(node => node.id === from)!
  const b = NODES.find(node => node.id === to)!
  if (a.y === b.y) return `M ${a.x} ${a.y + 42} C ${a.x - 16} ${a.y + 42}, ${b.x + 204} ${b.y + 42}, ${b.x + 188} ${b.y + 42}`
  const x1 = a.x + 94, y1 = a.y + 84, x2 = b.x + 94, y2 = b.y
  // Long dependencies use the central gutter rather than crossing node cards.
  if (y2 - y1 > 120) return `M ${x1} ${y1} C ${x1} ${y1 + 18}, 680 ${y1 + 18}, 680 ${y1 + 36} L 680 ${y2 - 18} Q 680 ${y2 - 8}, ${x2} ${y2}`
  return `M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`
}
