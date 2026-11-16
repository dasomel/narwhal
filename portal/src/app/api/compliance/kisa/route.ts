import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { getKisaControls } from "@/lib/kisa"

export const dynamic = "force-dynamic"

export async function GET() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    return NextResponse.json(await getKisaControls())
  } catch (err) {
    console.error("GET /api/compliance/kisa error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
