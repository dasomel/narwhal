import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import {
  ComplianceDataUnavailableError,
  ComplianceDetailAmbiguousError,
  getConfigAuditList,
  getConfigAuditDetail,
  type ConfigAuditScope,
} from "@/lib/compliance"

export const dynamic = "force-dynamic"

export async function GET(req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    const { searchParams } = new URL(req.url)
    const scopeParam = searchParams.get("scope")
    const namespace = searchParams.get("namespace")
    const kind = searchParams.get("kind")
    const name = searchParams.get("name")

    if (name !== null || kind !== null || scopeParam !== null) {
      if (name === null || name.trim() === "") {
        return NextResponse.json({ error: "Missing resource name", ambiguous: true }, { status: 400 })
      }
      let resolvedScope: ConfigAuditScope
      if (scopeParam !== null) {
        if (scopeParam !== "cluster" && scopeParam !== "namespace") {
          return NextResponse.json({ error: "Invalid scope parameter", ambiguous: true }, { status: 400 })
        }
        resolvedScope = scopeParam
      } else if (namespace !== null) {
        resolvedScope = namespace === "" ? "cluster" : "namespace"
      } else {
        return NextResponse.json({ error: "Missing scope or namespace parameter", ambiguous: true }, { status: 400 })
      }
      const resolvedNamespace = namespace ?? ""
      const detail = await getConfigAuditDetail(resolvedScope, resolvedNamespace, (kind ?? "").trim(), name)
      if (!detail) return NextResponse.json({ error: "Not found" }, { status: 404 })
      return NextResponse.json(detail)
    }

    const rows = await getConfigAuditList()
    return NextResponse.json(rows)
  } catch (err) {
    if (err instanceof ComplianceDetailAmbiguousError) {
      return NextResponse.json(
        { error: err.message, ambiguous: true },
        { status: 409 },
      )
    }
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
