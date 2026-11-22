import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { ComplianceDataUnavailableError, getComplianceFrameworks, getComplianceFrameworkDetail } from "@/lib/compliance"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    const { searchParams } = new URL(req.url)
    const id = searchParams.get("id")

    if (id !== null) {
      const detail = await getComplianceFrameworkDetail(id)
      if (!detail) return NextResponse.json({ error: "Not found" }, { status: 404 })
      return NextResponse.json(detail)
    }

    const frameworks = await getComplianceFrameworks()
    return NextResponse.json(frameworks)
  } catch (err) {
    if (err instanceof ComplianceDataUnavailableError) {
      return NextResponse.json(
        { error: "Data unavailable", unavailable: true, source: err.source },
        { status: 503 },
      )
    }
    console.error("GET /api/compliance/frameworks error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
