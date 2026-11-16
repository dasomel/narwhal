import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { canReadAlerts } from "@/lib/alert-access"
import { getFiringAlerts } from "@/lib/firing-alerts"
import { requireSessionAccess } from "@/lib/scope"

export const dynamic = "force-dynamic"
export async function GET() {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  const denied = await requireSessionAccess(session)
  if (denied) return denied
  if (!canReadAlerts(session)) return NextResponse.json({ error: "Forbidden" }, { status: 403 })
  return NextResponse.json(await getFiringAlerts(), { headers: { "Cache-Control": "no-store" } })
}
