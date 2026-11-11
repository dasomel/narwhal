import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { cacheGet, cacheSet } from "@/lib/valkey"
import { cacheKeys, cacheTtl } from "@/lib/cache-keys"
import { getWorkloadSnapshot, workloadStatus, type WorkloadKind } from "@/lib/workload-health"
import { getArgoApps } from "@/lib/argocd"

export const dynamic = "force-dynamic"

export interface ArchNode {
  id: string
  label: string
  group: "gateway" | "gitops" | "monitoring" | "storage" | "security" | "platform"
  status: "healthy" | "degraded" | "offline" | "unknown"
  responseMs: number
}

export interface ArchEdge {
  source: string
  target: string
  label?: string
}

export interface ArchitectureData {
  nodes: ArchNode[]
  edges: ArchEdge[]
}

function mapArgoHealth(h?: string): ArchNode["status"] {
  switch (h) {
    case "Healthy":
    case "Suspended":
      return "healthy"
    case "Degraded":
    case "Progressing":
      return "degraded"
    case "Missing":
      return "offline"
    default:
      return "unknown"
  }
}

// D3 (#646): explicit workload identities match cluster charts/provisioning. Cost:
// names track releases; escape hatch: ArgoCD fallback only for existing apps.
const NODE_WORKLOAD: Record<string, [string, WorkloadKind, string]> = {
  portal: ["devtools", "Deployment", "narwhal-portal"],
  keycloak: ["iam", "StatefulSet", "keycloak"],
  apisix: ["platform-system", "Deployment", "apisix"],
  argocd: ["devtools", "Deployment", "argocd-server"],
  gitea: ["devtools", "Deployment", "gitea"],
  harbor: ["devtools", "Deployment", "harbor-core"],
  grafana: ["monitoring", "Deployment", "prometheus-stack-grafana"],
  prometheus: ["monitoring", "StatefulSet", "prometheus-prometheus-stack-kube-prom-prometheus"],
  alertmanager: ["monitoring", "StatefulSet", "alertmanager-prometheus-stack-kube-prom-alertmanager"],
  headlamp: ["devtools", "Deployment", "headlamp"],
  hubble: ["kube-system", "Deployment", "hubble-ui"],
  openbao: ["storage", "StatefulSet", "openbao"],
  "velero-ui": ["storage", "Deployment", "velero-ui"],
  cnpg: ["platform-system", "Deployment", "cnpg-cloudnative-pg"],
  seaweedfs: ["storage", "StatefulSet", "seaweedfs-master"],
  istio: ["istio-system", "Deployment", "istiod"],
  cilium: ["kube-system", "DaemonSet", "cilium"],
}
const NODE_APP: Record<string, string[]> = {
  apisix: ["apisix"], harbor: ["harbor"], grafana: ["prometheus-stack"],
  prometheus: ["prometheus-stack"], alertmanager: ["prometheus-stack"],
  headlamp: ["headlamp"], openbao: ["openbao"], "velero-ui": ["velero-ui", "velero"],
  seaweedfs: ["seaweedfs"], istio: ["istiod", "istio-base"],
}

const ARCHITECTURE_NODES: Omit<ArchNode, "status" | "responseMs">[] = [
  { id: "developer", label: "Developer", group: "platform" },
  { id: "portal", label: "IDP Portal", group: "platform" },
  { id: "keycloak", label: "Keycloak SSO", group: "security" },
  { id: "apisix", label: "APISIX Gateway", group: "gateway" },
  { id: "argocd", label: "ArgoCD", group: "gitops" },
  { id: "gitea", label: "Gitea", group: "gitops" },
  { id: "harbor", label: "Harbor", group: "storage" },
  { id: "grafana", label: "Grafana", group: "monitoring" },
  { id: "prometheus", label: "Prometheus", group: "monitoring" },
  { id: "alertmanager", label: "Alertmanager", group: "monitoring" },
  { id: "headlamp", label: "Headlamp", group: "platform" },
  { id: "hubble", label: "Hubble UI", group: "platform" },
  { id: "openbao", label: "OpenBao", group: "security" },
  { id: "velero-ui", label: "Velero UI", group: "storage" },
  { id: "k8s", label: "Kubernetes", group: "platform" },
  { id: "cnpg", label: "PostgreSQL (CNPG)", group: "storage" },
  { id: "seaweedfs", label: "SeaweedFS", group: "storage" },
  { id: "istio", label: "Istio Mesh", group: "gateway" },
  { id: "cilium", label: "Cilium CNI", group: "gateway" },
]

const ARCHITECTURE_EDGES: ArchEdge[] = [
  { source: "developer", target: "portal", label: "HTTPS" },
  { source: "developer", target: "apisix", label: "HTTPS" },
  { source: "portal", target: "keycloak", label: "OIDC" },
  { source: "portal", target: "prometheus", label: "PromQL" },
  { source: "portal", target: "argocd", label: "API" },
  { source: "portal", target: "alertmanager", label: "API" },
  { source: "apisix", target: "argocd", label: "proxy" },
  { source: "apisix", target: "gitea", label: "proxy" },
  { source: "apisix", target: "harbor", label: "proxy" },
  { source: "apisix", target: "grafana", label: "proxy" },
  { source: "apisix", target: "headlamp", label: "proxy" },
  { source: "apisix", target: "hubble", label: "proxy" },
  { source: "apisix", target: "openbao", label: "proxy" },
  { source: "apisix", target: "velero-ui", label: "proxy" },
  { source: "keycloak", target: "cnpg", label: "SQL" },
  { source: "gitea", target: "argocd", label: "webhook" },
  { source: "argocd", target: "k8s", label: "deploy" },
  { source: "harbor", target: "k8s", label: "pull" },
  { source: "prometheus", target: "k8s", label: "scrape" },
  { source: "prometheus", target: "alertmanager", label: "alert" },
  { source: "grafana", target: "prometheus", label: "query" },
  { source: "k8s", target: "cnpg", label: "operator" },
  { source: "k8s", target: "seaweedfs", label: "CSI" },
  { source: "k8s", target: "istio", label: "mesh" },
  { source: "k8s", target: "cilium", label: "CNI" },
  { source: "openbao", target: "k8s", label: "inject" },
]

export async function GET() {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const cacheKey = cacheKeys.architectureTopology()
  const cached = await cacheGet<ArchitectureData>(cacheKey)
  if (cached) return NextResponse.json(cached)

  const [apps, snapshot] = await Promise.all([getArgoApps(), getWorkloadSnapshot().catch(() => ({ workloads: [], hidden: [] }))])
  const healthByName = new Map(apps.map((a) => [a.metadata.name, a.status?.health?.status]))
  const argoReachable = apps.length > 0

  function resolveStatus(id: string): ArchNode["status"] {
    // "developer" is the human actor (always present).
    if (id === "developer") return "healthy"
    if (id === "k8s") return argoReachable ? "healthy" : "unknown"
    const target = NODE_WORKLOAD[id]
    const status = target && workloadStatus(snapshot, target[2], target[0], target[1])
    if (status) return status === "down" ? "offline" : status
    for (const appName of NODE_APP[id] ?? []) {
      if (healthByName.has(appName)) return mapArgoHealth(healthByName.get(appName))
    }
    return "unknown"
  }

  const data: ArchitectureData = {
    nodes: ARCHITECTURE_NODES.map((node) => ({ ...node, status: resolveStatus(node.id), responseMs: 0 })),
    edges: ARCHITECTURE_EDGES,
  }

  await cacheSet(cacheKey, data, cacheTtl("architectureTopology"))
  return NextResponse.json(data)
}
