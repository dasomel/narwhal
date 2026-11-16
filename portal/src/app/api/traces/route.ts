import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { cacheGet, cacheSet } from "@/lib/valkey"
import { cacheKeys, cacheTtl } from "@/lib/cache-keys"
import { getDependencyUrl } from "@/lib/config"
import { requireEffectiveScope } from "@/lib/scope"
import {
  buildServiceNamespaceMap,
  getInScopeServiceNames,
  isServiceInScope,
} from "@/lib/service-namespace"

export const dynamic = "force-dynamic"

function tempoUrl(): string {
  return getDependencyUrl("TEMPO_URL", "http://localhost:3200")
}

// M-3: TraceQL service-name parameter — DNS-style label characters only.
// Rejects quotes, braces, semicolons → no template breakout.
const SERVICE_NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/

function escapeRegexLiteral(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

export interface TraceEntry {
  traceID: string
  serviceName: string
  operationName: string
  duration: number
  startTime: number
  spanCount: number
}

export async function GET(req: Request) {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })

  const { searchParams } = new URL(req.url)
  const service = searchParams.get("service") ?? ""
  if (service && !SERVICE_NAME_RE.test(service)) {
    return NextResponse.json(
      { error: "ValidationError", message: "invalid service name", field: "service" },
      { status: 400 },
    )
  }

  try {
    const scoped = await requireEffectiveScope(session)
    if ("denied" in scoped) return scoped.denied
    const { scope } = scoped

    let serviceMap: Map<string, Set<string>> | null = null
    let inScopeServices: Set<string> | null = null

    if (!scope.all) {
      serviceMap = await buildServiceNamespaceMap()
      if (service) {
        if (!isServiceInScope(service, serviceMap, scope)) {
          return NextResponse.json({ error: "Forbidden" }, { status: 403 })
        }
      } else {
        inScopeServices = getInScopeServiceNames(serviceMap, scope)
        if (inScopeServices.size === 0) {
          return NextResponse.json([])
        }
      }
    }

    const cacheKey = scope.all ? cacheKeys.traces(service) : cacheKeys.tracesScoped(scope.fingerprint, service)
    const cached = await cacheGet<TraceEntry[]>(cacheKey)
    if (cached) return NextResponse.json(cached)

    let query = "{}"
    if (service) {
      query = `{resource.service.name="${service}"}`
    } else if (!scope.all && inScopeServices && inScopeServices.size > 0 && inScopeServices.size <= 50) {
      const pattern = Array.from(inScopeServices).sort().map(escapeRegexLiteral).join("|")
      query = `{resource.service.name=~"^(${pattern})$"}`
    }

    const res = await fetch(
      `${tempoUrl()}/api/search?q=${encodeURIComponent(query)}&limit=20`,
      { signal: AbortSignal.timeout(5000) }
    )
    if (!res.ok) return NextResponse.json([])

    const data = await res.json()
    let traces: TraceEntry[] = (data.traces ?? []).map((t: { traceID: string; rootServiceName: string; rootTraceName: string; durationMs: number; startTimeUnixNano: string; spanSets?: Array<{ spans: unknown[] }> }) => ({
      traceID: t.traceID,
      serviceName: t.rootServiceName ?? "",
      operationName: t.rootTraceName ?? "",
      duration: t.durationMs ?? 0,
      startTime: Math.floor(Number(t.startTimeUnixNano) / 1000000),
      spanCount: t.spanSets?.[0]?.spans?.length ?? 0,
    }))

    if (!scope.all && serviceMap) {
      traces = traces.filter((t) => {
        if (!t.serviceName) return false
        return inScopeServices ? inScopeServices.has(t.serviceName) : isServiceInScope(t.serviceName, serviceMap!, scope)
      })
    }

    await cacheSet(cacheKey, traces, cacheTtl(scope.all ? "traces" : "tracesScoped"))
    return NextResponse.json(traces)
  } catch (err) {
    console.error("[api/traces]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
