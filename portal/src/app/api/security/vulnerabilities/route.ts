import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { getWorkloadVulnerabilitiesWithMeta, getImageVulnReport } from "@/lib/trivy"
import type { Severity } from "@/types/security"

export const dynamic = "force-dynamic"

const VALID_SEVERITIES: Severity[] = ["Critical", "High", "Medium", "Low", "Unknown"]

export async function GET(req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) {
    return NextResponse.json(
      { error: gate.error === "unauthorized" ? "Unauthorized" : "Forbidden" },
      { status: gate.error === "unauthorized" ? 401 : 403 }
    )
  }

  try {
    const { searchParams } = new URL(req.url)
    const image = searchParams.get("image")
    const severityParam = searchParams.get("severity")
    const namespaceParam = searchParams.get("namespace")

    // Single image report
    if (image) {
      const report = await getImageVulnReport(image)
      if (!report) return NextResponse.json({ error: "Image not found" }, { status: 404 })
      return NextResponse.json(report)
    }

    // Workload list with optional filters
    // #505: the body stays a bare array (existing contract); partial state goes in headers.
    const list = await getWorkloadVulnerabilitiesWithMeta()
    let rows = list.rows

    if (namespaceParam) {
      rows = rows.filter((r) => r.namespace === namespaceParam)
    }

    if (severityParam) {
      if (!VALID_SEVERITIES.includes(severityParam as Severity)) {
        return NextResponse.json({ error: "Invalid severity value" }, { status: 400 })
      }
      const sev = severityParam as Severity
      rows = rows.filter((r) => r.summary[sev] > 0)
    }

    return NextResponse.json(
      rows,
      list.partial
        ? { headers: { "X-Partial-Data": "true", "X-Partial-Reason": list.partialReason ?? "unknown" } }
        : undefined,
    )
  } catch (err) {
    console.error("GET /api/security/vulnerabilities error:", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
