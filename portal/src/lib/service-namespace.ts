import { getArgoAppsOrThrow } from "./argocd"
import { namespaceVisible, type EffectiveScope } from "./scope"
import { getWorkloadSnapshot } from "./workload-health"

export interface ServiceOwnershipMap {
  complete: boolean
  services: Map<string, Set<string>>
}

/**
 * Resolves known service/workload names to Kubernetes namespace(s) using the
 * same ArgoCD Application + Kubernetes workload snapshot sources used by
 * service-graph. Marks `complete: false` if any provider fails or truncates
 * so callers fail closed for non-admins.
 */
export async function buildServiceNamespaceMap(): Promise<ServiceOwnershipMap> {
  const [appsResult, snapshotResult] = await Promise.allSettled([
    getArgoAppsOrThrow(),
    getWorkloadSnapshot(),
  ])

  const services = new Map<string, Set<string>>()
  const add = (name: string | undefined, ns: string | undefined) => {
    if (!name || !ns) return
    let set = services.get(name)
    if (!set) {
      set = new Set<string>()
      services.set(name, set)
    }
    set.add(ns)
  }

  if (appsResult.status === "fulfilled") {
    for (const app of appsResult.value) {
      add(app.metadata.name, app.spec.destination?.namespace ?? app.metadata.namespace)
    }
  }

  const snapshotComplete =
    snapshotResult.status === "fulfilled" && snapshotResult.value.complete === true

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

  return {
    complete: appsResult.status === "fulfilled" && snapshotComplete,
    services,
  }
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
  ownership: ServiceOwnershipMap,
  scope: EffectiveScope,
): boolean {
  if (scope.all) return true
  if (!ownership.complete) return false
  const namespaces = resolveServiceNamespaces(service, ownership.services)
  if (!namespaces || namespaces.size === 0) return false
  for (const ns of namespaces) {
    if (!namespaceVisible(ns, scope)) return false
  }
  return true
}

/**
 * True when ownership is complete and at least one namespace owning `service`
 * is visible to the caller. Used where downstream queries and post-filters
 * further isolate results by namespace (e.g. Tempo traces).
 */
export function hasServiceInScope(
  service: string,
  ownership: ServiceOwnershipMap,
  scope: EffectiveScope,
): boolean {
  if (scope.all) return true
  if (!ownership.complete) return false
  const namespaces = resolveServiceNamespaces(service, ownership.services)
  if (!namespaces || namespaces.size === 0) return false
  for (const ns of namespaces) {
    if (namespaceVisible(ns, scope)) return true
  }
  return false
}

export function getInScopeServiceNames(
  ownership: ServiceOwnershipMap,
  scope: EffectiveScope,
): Set<string> {
  if (!ownership.complete) return new Set<string>()
  const allowed = new Set<string>()
  for (const [service, namespaces] of ownership.services.entries()) {
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
