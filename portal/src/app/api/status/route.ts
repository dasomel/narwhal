import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getPlatformStatus } from "@/lib/platform-status"
import { requireEffectiveScope } from "@/lib/scope"

export const dynamic = "force-dynamic"

export async function GET() {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  try {
    const scoped = await requireEffectiveScope(session)
    if ("denied" in scoped) return scoped.denied
    const status = await getPlatformStatus(scoped.scope)
    return NextResponse.json(status)
  } catch (err) {
    console.error("[api/status]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
