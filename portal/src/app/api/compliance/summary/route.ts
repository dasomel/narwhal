import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { ComplianceDataUnavailableError, getComplianceSummary } from "@/lib/compliance"

export const dynamic = "force-dynamic"

export async function GET() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    const summary = await getComplianceSummary()
    const sourceValues = summary.sources ? Object.values(summary.sources) : []
    const allUnavailable =
      sourceValues.length > 0 && sourceValues.every((s) => s.status === "unavailable")
    if (allUnavailable) {
      return NextResponse.json(
        { error: "Data unavailable", unavailable: true, sources: summary.sources },
        { status: 503 },
      )
    }
    return NextResponse.json(summary)
  } catch (err) {
    if (err instanceof ComplianceDataUnavailableError) {
      return NextResponse.json(
        { error: "Data unavailable", unavailable: true, source: err.source },
        { status: 503 },
      )
    }
    console.error("GET /api/compliance/summary error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
