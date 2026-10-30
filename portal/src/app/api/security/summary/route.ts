import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { getSecuritySummary } from "@/lib/trivy"

export const dynamic = "force-dynamic"

export async function GET(_req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) {
    return NextResponse.json(
      { error: gate.error === "unauthorized" ? "Unauthorized" : "Forbidden" },
      { status: gate.error === "unauthorized" ? 401 : 403 }
    )
  }

  try {
    const summary = await getSecuritySummary()
    // #505: body already carries partial/partialReason; the header lets clients check without parsing.
    return NextResponse.json(summary, summary.partial ? { headers: { "X-Partial-Data": "true" } } : undefined)
  } catch (err) {
    console.error("GET /api/security/summary error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
