// One place that answers "what may this caller see", so the routes do not each
// assemble it from parts.
//
// role-filter.ts stays pure — it knows about configuration and labels, not about the
// Kubernetes API. This module is the seam: it fetches the namespace list (cached, so
// this is not an extra round trip) and hands back the resolved answer.

import { NextResponse } from "next/server"
import { getNamespaces } from "./k8s-client"
import {
  getVisibilityScope,
  resolveNamespaceScope,
  scopeFingerprint,
  projectMatchesScope,
  type ResolvedNamespaceScope,
} from "./role-filter"
import { DEFAULT_CLUSTER_ID } from "@/types/cluster"

export interface EffectiveScope {
  /** Every namespace — cluster-admin or an explicit team wildcard. */
  all: boolean
  namespaces: Set<string>
  argocdProjects: string[]
  /**
   * Whether the caller has ANY scope — not whether they have a config entry.
   *
   * getVisibilityScope's own hasMapping means "matched a teamMapping or cluster-admin",
   * which under the label model is the wrong question: a team can own namespaces
   * through narwhal.io/team with nothing in role-filter.json, and routes that gate on
   * the config answer would show those users an empty page while their namespaces sat
   * right there. /api/events/stream drops every event when this is false, so getting
   * it wrong is silent.
   */
  hasMapping: boolean
  /** Stable per-scope cache-key component. */
  fingerprint: string
  resolved: ResolvedNamespaceScope
  /**
   * portal#21: which cluster this scope was resolved against. Defaults to
   * DEFAULT_CLUSTER_ID (the one cluster every pre-#21 route implicitly talks
   * to) so existing callers of getEffectiveScope — none of which pass a
   * clusterId — see identical `fingerprint`/`hasMapping`/etc. behavior. A
   * route that resolves a real per-request cluster_id can pass it through;
   * cluster scope stays ABOVE namespace scope per the issue's requirement —
   * namespaceVisible/appVisible/alertVisible below don't (yet) consult it
   * themselves, since there is no second live cluster to authorize against.
   */
  clusterId: string
}

export interface ScopeSession {
  groups?: string[]
  teams?: string[]
  user?: { role?: string }
}

export const ACCESS_NOT_GRANTED_CODE = "ACCESS_NOT_GRANTED" as const

export interface AccessDeniedBody {
  error: string
  code: typeof ACCESS_NOT_GRANTED_CODE
}

// D-PORTAL-UNMAPPED-DENY: Non-admin users with an empty effective scope must not
// see portal screens or read data APIs (owner decision 2026-11-16, follow-up to
// narwhal#659). Cost: callers with team claims but no static argocdProjects
// resolve through getEffectiveScope() -> getNamespaces() (cached 30s in Valkey
// under k8s:namespaces), so newly added narwhal.io/team labels can take up to 30s
// to grant access (and role-filter.json updates require a process reload); admin
// and teamless callers decide without hitting Kubernetes. Escape hatch: map the
// team in config/role-filter.json or label a namespace with narwhal.io/team. If the
// Kubernetes namespace list cannot be read, label-only teams are denied (fail closed)
// until it recovers.
export function hasEffectiveAccess(
  scope: Pick<EffectiveScope, "all" | "hasMapping" | "namespaces" | "argocdProjects">,
): boolean {
  return Boolean(scope.all || scope.hasMapping || scope.namespaces.size > 0 || scope.argocdProjects.length > 0)
}

function normalizeScopeGroups(session: ScopeSession): string[] {
  if (session.groups !== undefined) return session.groups
  return session.user?.role ? [session.user.role] : []
}

export async function resolveEffectiveAccess(
  session: ScopeSession,
  clusterId: string = DEFAULT_CLUSTER_ID,
): Promise<{ allowed: true; scope?: EffectiveScope } | { allowed: false }> {
  const groups = normalizeScopeGroups(session)
  const teams = session.teams ?? []
  const vis = getVisibilityScope(groups, teams)

  if (vis.namespaces.includes("*")) return { allowed: true }
  if (!vis.hasMapping && teams.length === 0) return { allowed: false }
  if (vis.hasMapping && vis.argocdProjects.length > 0) return { allowed: true }

  const scope = await getEffectiveScope({ groups, teams }, clusterId)
  return hasEffectiveAccess(scope) ? { allowed: true, scope } : { allowed: false }
}

export async function hasSessionAccess(
  session: ScopeSession | null | undefined,
  clusterId: string = DEFAULT_CLUSTER_ID,
): Promise<boolean> {
  if (!session) return false
  return (await resolveEffectiveAccess(session, clusterId)).allowed
}

export function accessDeniedResponse(): NextResponse<AccessDeniedBody> {
  return NextResponse.json<AccessDeniedBody>(
    { error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE },
    { status: 403 },
  )
}

export async function requireSessionAccess(
  session: ScopeSession | null | undefined,
  clusterId: string = DEFAULT_CLUSTER_ID,
): Promise<NextResponse<AccessDeniedBody> | null> {
  if (await hasSessionAccess(session, clusterId)) return null
  return accessDeniedResponse()
}

export async function requireEffectiveScope(
  session: ScopeSession,
  clusterId: string = DEFAULT_CLUSTER_ID,
): Promise<{ scope: EffectiveScope } | { denied: NextResponse<AccessDeniedBody> }> {
  const scope = await getEffectiveScope(
    { groups: normalizeScopeGroups(session), teams: session.teams ?? [] },
    clusterId,
  )
  if (!hasEffectiveAccess(scope)) return { denied: accessDeniedResponse() }
  return { scope }
}

export async function getEffectiveScope(session: ScopeSession, clusterId: string = DEFAULT_CLUSTER_ID): Promise<EffectiveScope> {
  const groups = session.groups ?? []
  const teams = session.teams ?? []
  const scope = getVisibilityScope(groups, teams)

  // A caller with no mapping and no team sees nothing, and asking the API for the
  // namespace list to prove that is wasted work. Teams without static mappings
  // still need label resolution (D-PORTAL-UNMAPPED-SCOPE).
  if (!scope.hasMapping && scope.namespaces.length === 0 && teams.length === 0) {
    const empty = { all: false, names: new Set<string>(), byLabel: new Set<string>(), byPattern: new Set<string>() }
    return {
      all: false,
      namespaces: empty.names,
      argocdProjects: scope.argocdProjects,
      hasMapping: false,
      fingerprint: scopeFingerprint(groups, teams, clusterId),
      resolved: empty,
      clusterId,
    }
  }

  const namespaces = await getNamespaces()
  const resolved = resolveNamespaceScope(namespaces, scope, teams)
  return {
    all: resolved.all,
    namespaces: resolved.names,
    argocdProjects: scope.argocdProjects,
    hasMapping: resolved.all || resolved.names.size > 0 || scope.argocdProjects.length > 0,
    // D2: resolved namespace labels can change while claims do not. Include the
    // resolved set to retire scoped cache entries at the next namespace refresh; a
    // provider scope revision could replace the current 30-second cache window.
    fingerprint: scopeFingerprint(
      groups,
      teams,
      clusterId,
      resolved.all ? ["*"] : resolved.names,
    ),
    resolved,
    clusterId,
  }
}

export function namespaceVisible(namespace: string, scope: EffectiveScope): boolean {
  return scope.all || scope.namespaces.has(namespace)
}

/**
 * Whether an ArgoCD application is visible: its project OR its destination namespace.
 *
 * Either alone is enough because the two describe the same tenancy from different
 * directions — a team owns namespaces, and projects are scoped to namespaces.
 * Requiring both would hide an app from the team that owns it whenever only one of
 * the two was configured.
 */
export function appVisible(project: string, namespace: string, scope: EffectiveScope): boolean {
  return projectMatchesScope(project, scope.argocdProjects) || namespaceVisible(namespace, scope)
}

/**
 * Alerts carry their namespace in a label, or none at all when cluster-wide.
 *
 * A namespace-less alert stays visible to everyone: node and control-plane alerts have
 * no namespace, and hiding them from every scoped user would blind the timeline to the
 * failures that matter most. This is the weakest point in the scoping story and it is
 * deliberate; the default-deny decision is tracked on #12.
 */
export function alertVisible(labels: Record<string, string | undefined>, scope: EffectiveScope): boolean {
  const ns = labels.namespace ?? labels.exported_namespace ?? ""
  return !ns || namespaceVisible(ns, scope)
}
