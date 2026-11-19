import { NextResponse } from "next/server"
import { getAlerts } from "@/lib/alertmanager"
import { auth } from "@/lib/auth"
import { alertVisible, requireEffectiveScope, withScopeHeader } from "@/lib/scope"

export const dynamic = "force-dynamic"

export async function GET(request: Request) {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  try {
    const scoped = await requireEffectiveScope(session)
    if ("denied" in scoped) return scoped.denied
    const { scope } = scoped

    const { searchParams } = new URL(request.url)
    const severityParam = searchParams.get("severity")

    // Validate severity param
    const validSeverities = ["warning", "critical"] as const
    type Severity = (typeof validSeverities)[number]
    const severityFilter: Severity | null =
      severityParam && (validSeverities as readonly string[]).includes(severityParam)
        ? (severityParam as Severity)
        : null

    if (severityParam && !severityFilter) {
      return NextResponse.json(
        { error: `Invalid severity value. Must be one of: ${validSeverities.join(", ")}` },
        { status: 400 }
      )
    }

    const alerts = (await getAlerts()).filter((a) => alertVisible(a.labels, scope))

    const filtered = severityFilter
      ? alerts.filter((a) => a.labels.severity === severityFilter)
      : alerts

    return withScopeHeader(NextResponse.json(filtered), scope)
  } catch (err) {
    console.error("[api/alerts]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
