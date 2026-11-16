import { getArgoApps } from "./argocd"
import { namespaceVisible, type EffectiveScope } from "./scope"
import { getWorkloadSnapshot } from "./workload-health"

/**
 * Resolves known service/workload names to Kubernetes namespace(s) using the
 * same ArgoCD Application + Kubernetes workload snapshot sources used by
 * service-graph.
 */
export async function buildServiceNamespaceMap(): Promise<Map<string, Set<string>>> {
  const [appsResult, snapshotResult] = await Promise.allSettled([
    getArgoApps(),
    getWorkloadSnapshot(),
  ])

  const map = new Map<string, Set<string>>()
  const add = (name: string | undefined, ns: string | undefined) => {
    if (!name || !ns) return
    let set = map.get(name)
    if (!set) {
      set = new Set<string>()
      map.set(name, set)
    }
    set.add(ns)
  }

  if (appsResult.status === "fulfilled") {
    for (const app of appsResult.value) {
      add(app.metadata.name, app.spec.destination?.namespace ?? app.metadata.namespace)
    }
  }

  if (snapshotResult.status === "fulfilled") {
    const snapshot = snapshotResult.value
    for (const workload of snapshot.workloads) {
      add(workload.name, workload.namespace)
    }
    for (const pod of snapshot.livePods ?? []) {
      add(pod.name, pod.namespace)
      for (const owner of pod.ownerNames) {
        add(owner, pod.namespace)
      }
    }
  }

  return map
}

export function resolveServiceNamespaces(
  service: string,
  map: Map<string, Set<string>>,
): Set<string> | undefined {
  const exact = map.get(service)
  if (exact && exact.size > 0) return exact
  const withoutHash = service.replace(/-[0-9a-f]{5,10}$/, "")
  if (withoutHash !== service) {
    const stripped = map.get(withoutHash)
    if (stripped && stripped.size > 0) return stripped
  }
  return undefined
}

export function isServiceInScope(
  service: string,
  map: Map<string, Set<string>>,
  scope: EffectiveScope,
): boolean {
  if (scope.all) return true
  const namespaces = resolveServiceNamespaces(service, map)
  if (!namespaces || namespaces.size === 0) return false
  for (const ns of namespaces) {
    if (!namespaceVisible(ns, scope)) return false
  }
  return true
}

export function getInScopeServiceNames(
  map: Map<string, Set<string>>,
  scope: EffectiveScope,
): Set<string> {
  const allowed = new Set<string>()
  for (const [service, namespaces] of map.entries()) {
    if (namespaces.size === 0) continue
    let allVisible = true
    for (const ns of namespaces) {
      if (!namespaceVisible(ns, scope)) {
        allVisible = false
        break
      }
    }
    if (allVisible) allowed.add(service)
  }
  return allowed
}
