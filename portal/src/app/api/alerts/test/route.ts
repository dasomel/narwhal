import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { sendNotificationTest } from "@/lib/firing-alerts"

export async function POST() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)
  try {
    await sendNotificationTest()
    return NextResponse.json({ accepted: true })
  } catch {
    return NextResponse.json({ error: "Test alert outcome unknown; verify delivery before retrying" }, { status: 502 })
  }
}
