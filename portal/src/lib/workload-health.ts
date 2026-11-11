import { listBounded } from "./k8s-client"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"

export type WorkloadStatus = "healthy" | "degraded" | "down"
export type WorkloadKind = "Deployment" | "StatefulSet" | "DaemonSet"
interface Resource {
  metadata?: { name?: string; namespace?: string; ownerReferences?: Array<{ kind?: string; name?: string }> }
  spec?: { replicas?: number }
  status?: { readyReplicas?: number; desiredNumberScheduled?: number; numberReady?: number; phase?: string }
}
export interface WorkloadSnapshot {
  complete: boolean
  workloads: Array<{ namespace: string; kind: WorkloadKind; name: string; status?: WorkloadStatus }>
  hidden: Array<{ namespace: string; name: string }>
}

export function readiness(desired: unknown, ready: unknown): WorkloadStatus | undefined {
  if (typeof desired !== "number" || !Number.isSafeInteger(desired) || desired <= 0
    || typeof ready !== "number" || !Number.isSafeInteger(ready) || ready < 0) return undefined
  if (ready >= desired) return "healthy"
  return ready > 0 ? "degraded" : "down"
}

// D1 (#646): share bounded lists across both views for 30s; cost: stale readiness.
// Escape hatch: shorten this cache TTL, never request Jobs/ReplicaSets or widen RBAC.
export async function getWorkloadSnapshot(): Promise<WorkloadSnapshot> {
  const key = cacheKeys.architectureWorkloads()
  const cached = await cacheGet<WorkloadSnapshot>(key)
  if (cached) return cached
  const snapshot: WorkloadSnapshot = { complete: false, workloads: [], hidden: [] }
  const kinds = ["Deployment", "StatefulSet", "DaemonSet"] as const
  const results = await Promise.allSettled([
    ...["deployments", "statefulsets", "daemonsets"].map((resource) => listBounded<Resource>(`/apis/apps/v1/${resource}`)),
    listBounded<Resource>("/api/v1/pods", { maxPages: 40 }),
  ])
  results.forEach((result, index) => {
    if (result.status !== "fulfilled") return
    for (const item of result.value.items) {
      const name = item?.metadata?.name
      const namespace = item?.metadata?.namespace
      if (typeof name !== "string" || !name || typeof namespace !== "string" || !namespace) continue
      if (index === 3) {
        const owners = item.metadata?.ownerReferences
        const job = Array.isArray(owners) && owners.some((owner) => owner?.kind === "Job")
        const completed = ["Succeeded", "Failed"].includes(item.status?.phase ?? "")
        if (job || completed) {
          snapshot.hidden.push({ namespace, name })
          if (job) for (const owner of owners || []) {
            if (owner?.kind === "Job" && owner.name) snapshot.hidden.push({ namespace, name: owner.name })
          }
        }
        continue
      }
      const daemon = index === 2
      const status = readiness(daemon ? item.status?.desiredNumberScheduled : item.spec?.replicas,
        (daemon ? item.status?.numberReady : item.status?.readyReplicas) ?? 0)
      // D6 (#646): retain non-serving controller identities to avoid one-off filtering.
      // Cost: status is optional; escape hatch: existing id/ArgoCD/unknown fallback.
      snapshot.workloads.push({ namespace, name, kind: kinds[index], ...(status ? { status } : {}) })
    }
  })
  // D2 (#646): preserve the cache contract: partial/failed lists are not cached.
  // Cost: retry during outages; escape hatch: a separately designed failure backoff.
  snapshot.complete = results.every((result) => result.status === "fulfilled" && !result.value.truncated)
  if (snapshot.complete) {
    await cacheSet(key, snapshot, cacheTtl("architectureWorkloads"))
  }
  return snapshot
}

export function workloadStatus(snapshot: WorkloadSnapshot, name: string, namespace?: string, kind?: WorkloadKind) {
  const matches = snapshot.workloads.filter((w) => w.name === name && (!namespace || w.namespace === namespace) && (!kind || w.kind === kind))
  // Ambiguous namespace-free telemetry must not borrow another service's health.
  return matches.length === 1 ? matches[0].status : undefined
}
