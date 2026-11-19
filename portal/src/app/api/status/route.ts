import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getPlatformStatus } from "@/lib/platform-status"
import { requireEffectiveScope, withScopeHeader } from "@/lib/scope"

export const dynamic = "force-dynamic"

export async function GET() {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  try {
    const scoped = await requireEffectiveScope(session)
    if ("denied" in scoped) return scoped.denied
    const status = await getPlatformStatus(scoped.scope)
    return withScopeHeader(NextResponse.json(status), scoped.scope)
  } catch (err) {
    console.error("[api/status]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
