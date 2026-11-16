import { NextResponse } from "next/server"
import { getKyvernoPolicies } from "@/lib/k8s-client"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"

export const dynamic = "force-dynamic"

export async function GET() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    return NextResponse.json(await getKyvernoPolicies())
  } catch (err) {
    console.error("GET /api/settings/policies error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
