import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { ComplianceDataUnavailableError, getConfigAuditList, getConfigAuditDetail } from "@/lib/compliance"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    const { searchParams } = new URL(req.url)
    const namespace = searchParams.get("namespace")
    const name = searchParams.get("name")

    if (namespace !== null && name !== null) {
      const detail = await getConfigAuditDetail(namespace, name)
      if (!detail) return NextResponse.json({ error: "Not found" }, { status: 404 })
      return NextResponse.json(detail)
    }

    const rows = await getConfigAuditList()
    return NextResponse.json(rows)
  } catch (err) {
    if (err instanceof ComplianceDataUnavailableError) {
      return NextResponse.json(
        { error: "Data unavailable", unavailable: true, source: err.source },
        { status: 503 },
      )
    }
    console.error("GET /api/compliance/config-audit error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
