import { NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { getOssUpdateStatus } from "@/lib/oss-updates/status"

export const dynamic = "force-dynamic"

// Cluster-admin only (same gate as every other Settings tab). Read-only: declared versions come from
// the committed VERSIONS.md snapshot, running versions from Argo CD + Trivy (existing portal access,
// no RBAC change), upstream from an opt-in public lookup. Nothing here mutates the cluster.
export async function GET() {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) {
    return NextResponse.json(
      { error: gate.error === "unauthorized" ? "Unauthorized" : "Forbidden" },
      { status: gate.error === "unauthorized" ? 401 : 403 },
    )
  }
  return NextResponse.json(await getOssUpdateStatus())
}
