import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { getComplianceSummary } from "@/lib/compliance"

export const dynamic = "force-dynamic"

export async function GET() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    const summary = await getComplianceSummary()
    return NextResponse.json(summary)
  } catch (err) {
    console.error("GET /api/compliance/summary error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
