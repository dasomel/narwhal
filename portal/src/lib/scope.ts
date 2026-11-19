// One place that answers "what may this caller see", so the routes do not each
// assemble it from parts.
//
// role-filter.ts stays pure — it knows about configuration and labels, not about the
// Kubernetes API. This module is the seam: it fetches the namespace list (cached, so
// this is not an extra round trip) and hands back the resolved answer.

import { createHmac, randomBytes } from "node:crypto"
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
import { ACCESS_NOT_GRANTED_CODE, SCOPE_FINGERPRINT_HEADER } from "./scope-constants"

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
  error?: string
}

export { ACCESS_NOT_GRANTED_CODE, SCOPE_FINGERPRINT_HEADER }

const EPHEMERAL_SCOPE_KEY_SYMBOL = Symbol.for("narwhal.portal.scopeHeaderEphemeralKey")

function getScopeHeaderSecret(): string | Buffer {
  const configured = process.env.AUTH_SECRET || process.env.NEXTAUTH_SECRET
  if (configured) return configured
  if (
    process.env.NODE_ENV === "production" &&
    process.env.AUTH_MOCK !== "true" &&
    process.env.NEXT_PHASE !== "phase-production-build"
  ) {
    throw new Error("Missing required production configuration: AUTH_SECRET")
  }
  const g = globalThis as Record<symbol, Buffer | undefined>
  if (!g[EPHEMERAL_SCOPE_KEY_SYMBOL]) {
    g[EPHEMERAL_SCOPE_KEY_SYMBOL] = randomBytes(32)
  }
  return g[EPHEMERAL_SCOPE_KEY_SYMBOL]
}

export function signScopeFingerprint(fingerprint: string): string {
  return createHmac("sha256", getScopeHeaderSecret())
    .update("scope-header:v1:")
    .update(fingerprint)
    .digest("hex")
    .slice(0, 32)
}

export function withScopeHeader<T extends NextResponse>(
  response: T,
  scope: Pick<EffectiveScope, "fingerprint"> | null | undefined,
): T {
  if (scope?.fingerprint) {
    response.headers.set(SCOPE_FINGERPRINT_HEADER, signScopeFingerprint(scope.fingerprint))
  }
  return response
}

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
  if (session.error) return { allowed: false }
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
  if (session.error) return { denied: accessDeniedResponse() }
  const scope = await getEffectiveScope(
    { groups: normalizeScopeGroups(session), teams: session.teams ?? [] },
    clusterId,
  )
  if (!hasEffectiveAccess(scope)) return { denied: accessDeniedResponse() }
  return { scope }
}

export async function getEffectiveScope(session: ScopeSession, clusterId: string = DEFAULT_CLUSTER_ID): Promise<EffectiveScope> {
  const groups = session.error ? [] : (session.groups ?? [])
  const teams = session.error ? [] : (session.teams ?? [])
  const scope = getVisibilityScope(groups, teams)
  const roleClass: "admin" | "non-admin" = groups.includes("cluster-admin") ? "admin" : "non-admin"

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
      fingerprint: scopeFingerprint(groups, teams, clusterId, [], {
        all: false,
        argocdProjects: scope.argocdProjects,
        roleClass,
      }),
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
    // D2: resolved namespace labels and project mappings can change while claims
    // do not. Fold every visibility dimension (all, sorted namespaces, sorted
    // argocdProjects, roleClass) into the fingerprint to retire stale cache entries.
    fingerprint: scopeFingerprint(
      groups,
      teams,
      clusterId,
      resolved.all ? ["*"] : resolved.names,
      {
        all: resolved.all,
        argocdProjects: scope.argocdProjects,
        roleClass,
      },
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

// D-PORTAL-TENANT-SCOPE-678: Alerts without a namespace label (node/cluster-level)
// are visible to cluster-admin (scope.all) only; non-admins see only alerts whose
// namespace or exported_namespace belongs to their effective namespaces.
// Reason: node/cluster-level alerts expose node names, host IPs, and cluster-wide
// internals across tenants; fail closed so non-admins cannot read another tenant's
// or cluster-level alert metadata via /api/alerts, /api/hero, /api/status, /api/events,
// or /api/my-apps.
// Cost: scoped developers/viewers do not see cluster-wide/node alerts without a
// namespace label.
// Escape hatch: attach a tenant `namespace` label on alert rules intended for teams,
// or grant `cluster-admin` where cluster-wide alert visibility is required.
export function alertVisible(labels: Record<string, string | undefined>, scope: EffectiveScope): boolean {
  if (scope.all) return true
  const ns = labels.namespace ?? labels.exported_namespace ?? ""
  return Boolean(ns) && namespaceVisible(ns, scope)
}
