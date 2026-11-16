import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getPlatformStatus } from "@/lib/platform-status"
import { requireSessionAccess } from "@/lib/scope"

export const dynamic = "force-dynamic"

export async function GET() {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  const denied = await requireSessionAccess(session)
  if (denied) return denied

  try {
    const status = await getPlatformStatus()
    return NextResponse.json(status)
  } catch (err) {
    console.error("[api/status]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
