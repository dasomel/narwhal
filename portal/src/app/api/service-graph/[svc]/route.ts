import { NextRequest, NextResponse } from "next/server"
import { requireRole } from "@/lib/auth"
import { roleGateErrorResponse } from "@/lib/role-gate-response"
import { getEffectiveScope, withScopeHeader } from "@/lib/scope"
import { getServiceDependencies, isAllowedWindow } from "@/lib/service-graph"
import { buildServiceNamespaceMap, isServiceInScope } from "@/lib/service-namespace"
import { ValidationError, toValidationErrorBody, assertK8sName } from "@/lib/validation"

export const dynamic = "force-dynamic"

export interface ServiceGraphDetailResponse {
  serviceId: string
  inbound: Array<{
    source: string
    requestRate: number
    errorRate: number
    p95LatencyMs?: number | null
  }>
  outbound: Array<{
    destination: string
    requestRate: number
    errorRate: number
    p95LatencyMs?: number | null
  }>
  notice?: string
}

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ svc: string }> },
) {
  const gate = await requireRole("cluster-admin", "developer", "viewer")
  if ("error" in gate) return roleGateErrorResponse(gate)

  try {
    const { svc } = await params

    // svc 이름 검증 — unmapped-pods: 접두사를 가진 경우는 허용하지 않음
    assertK8sName(svc, "svc")

    const windowParam = req.nextUrl.searchParams.get("window") ?? "7d"
    if (!isAllowedWindow(windowParam)) {
      throw new ValidationError("window must be one of: 1h, 1d, 7d, 30d", "window")
    }

    const scope = await getEffectiveScope(gate.session)
    if (!scope.all) {
      const serviceMap = await buildServiceNamespaceMap()
      if (!isServiceInScope(svc, serviceMap, scope)) {
        return withScopeHeader(NextResponse.json({ error: "Forbidden" }, { status: 403 }), scope)
      }
    }

    const result = await getServiceDependencies(svc, windowParam, scope)

    const response: ServiceGraphDetailResponse = {
      serviceId: result.serviceId,
      inbound: result.inbound,
      outbound: result.outbound,
      ...(result.notice ? { notice: result.notice } : {}),
    }

    return withScopeHeader(NextResponse.json(response), scope)
  } catch (err) {
    if (err instanceof ValidationError) {
      return NextResponse.json(toValidationErrorBody(err), { status: 400 })
    }
    console.error("[api/service-graph/[svc]]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
