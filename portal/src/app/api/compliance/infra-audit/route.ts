import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { ComplianceDataUnavailableError, getInfraAuditList, getInfraAuditDetail } from "@/lib/compliance"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    const { searchParams } = new URL(req.url)
    const node = searchParams.get("node")

    if (node !== null) {
      const detail = await getInfraAuditDetail(node)
      if (!detail) return NextResponse.json({ error: "Not found" }, { status: 404 })
      return NextResponse.json(detail)
    }

    const rows = await getInfraAuditList()
    return NextResponse.json(rows)
  } catch (err) {
    if (err instanceof ComplianceDataUnavailableError) {
      return NextResponse.json(
        { error: "Data unavailable", unavailable: true, source: err.source },
        { status: 503 },
      )
    }
    console.error("GET /api/compliance/infra-audit error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
