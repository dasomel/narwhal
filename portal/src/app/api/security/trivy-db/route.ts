import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { getTrivyDbStatus } from "@/lib/trivy-db-status"

export const dynamic = "force-dynamic"

// Read-only Trivy DB status. cluster-admin only: it exposes scanner operational detail
// (scan failures, registry path). It never triggers an update.
export async function GET(_req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) {
    return NextResponse.json(
      { error: gate.error === "unauthorized" ? "Unauthorized" : "Forbidden" },
      { status: gate.error === "unauthorized" ? 401 : 403 }
    )
  }

  try {
    return NextResponse.json(await getTrivyDbStatus())
  } catch (err) {
    console.error("GET /api/security/trivy-db error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
