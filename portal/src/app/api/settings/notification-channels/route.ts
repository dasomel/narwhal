import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { ChannelSaveError, getNotificationChannels, saveNotificationChannels } from "@/lib/notification-channels"
import { ChannelValidationError, parseChannelInput } from "@/lib/notification-channel-validation"

export const dynamic = "force-dynamic"
const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } })

export async function GET() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return json({ error: gate.error }, gate.error === "unauthorized" ? 401 : 403)
  try {
    return json(await getNotificationChannels())
  } catch {
    return json({ error: "Notification settings unavailable", degraded: true }, 502)
  }
}

export async function PUT(request: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return json({ error: gate.error }, gate.error === "unauthorized" ? 401 : 403)
  let input
  try { input = parseChannelInput(await request.json()) } catch { return json({ error: "Invalid notification channel settings" }, 400) }
  try {
    return json(await saveNotificationChannels(input))
  } catch (error) {
    if (error instanceof ChannelValidationError) return json({ error: error.message }, 400)
    return json({ error: error instanceof ChannelSaveError ? error.message : "Notification settings unavailable", partial: error instanceof ChannelSaveError }, 502)
  }
}
