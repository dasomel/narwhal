import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { getDependencyHealthSummary } from "@/lib/dependency-health"

export const dynamic = "force-dynamic"

const AUTHENTICATED_ROLES = ["cluster-admin", "developer", "viewer", "guest"] as const

export async function GET() {
  const gate = await requireRole(...AUTHENTICATED_ROLES)
  if ("error" in gate) return roleGateErrorResponse(gate)

  return NextResponse.json(await getDependencyHealthSummary())
}
