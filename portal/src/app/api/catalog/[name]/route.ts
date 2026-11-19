import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getArgoApp } from "@/lib/argocd"
import { getAlerts } from "@/lib/alertmanager"
import { alertVisible, appVisible, requireEffectiveScope, withScopeHeader } from "@/lib/scope"

export const dynamic = "force-dynamic"

export async function GET(_req: Request, { params }: { params: Promise<{ name: string }> }) {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const scoped = await requireEffectiveScope(session)
  if ("denied" in scoped) return scoped.denied
  const { scope } = scoped

  const { name } = await params

  try {
    const [app, alerts] = await Promise.all([getArgoApp(name), getAlerts()])

    if (!app) return withScopeHeader(NextResponse.json({ error: "Not found" }, { status: 404 }), scope)

    // portal#31: /api/catalog (list) already scopes with appVisible — this by-name
    // route did not, so a caller who knew or guessed an app name could fetch full
    // detail (repoURL, namespace, runbook, resource count) for any app regardless of
    // team ownership. 404 rather than 403 so the response doesn't confirm an
    // out-of-scope app's existence to the caller.
    if (!appVisible(app.spec.project ?? "default", app.spec.destination?.namespace ?? app.metadata.namespace ?? "default", scope)) {
      return withScopeHeader(NextResponse.json({ error: "Not found" }, { status: 404 }), scope)
    }

    const appNs = app.spec.destination?.namespace
    const relatedAlerts = alerts.filter(
      (a) =>
        alertVisible(a.labels, scope) &&
        ((Boolean(appNs) && a.labels.namespace === appNs) || a.labels.app === name),
    )

    return withScopeHeader(NextResponse.json({
      app,
      alerts: relatedAlerts,
    }), scope)
  } catch (err) {
    console.error("[api/catalog/detail]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
