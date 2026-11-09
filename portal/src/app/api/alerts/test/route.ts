import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { sendNotificationTest } from "@/lib/firing-alerts"

export async function POST() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return NextResponse.json({ error: gate.error }, { status: gate.error === "unauthorized" ? 401 : 403 })
  try {
    await sendNotificationTest()
    return NextResponse.json({ accepted: true })
  } catch {
    return NextResponse.json({ error: "Test alert outcome unknown; verify delivery before retrying" }, { status: 502 })
  }
}
