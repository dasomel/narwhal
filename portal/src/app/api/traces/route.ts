import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { cacheGet, cacheSet } from "@/lib/valkey"
import { cacheKeys, cacheTtl } from "@/lib/cache-keys"
import { getDependencyUrl } from "@/lib/config"
import { namespaceVisible, requireEffectiveScope, type EffectiveScope } from "@/lib/scope"
import { buildServiceNamespaceMap, hasServiceInScope } from "@/lib/service-namespace"

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

interface CachedTraceRecord {
  entry: TraceEntry
  namespaces: string[]
}

type TempoAttrValue = { stringValue?: string }
type TempoAttr = { key?: string; value?: TempoAttrValue | string }
type TempoAttrs = TempoAttr[] | Record<string, string | undefined>

interface TempoSpan {
  attributes?: TempoAttrs
  resource?: { attributes?: TempoAttrs }
}

interface TempoSpanSet {
  spans?: TempoSpan[]
  attributes?: TempoAttrs
}

interface TempoSearchTrace {
  traceID: string
  rootServiceName?: string
  rootTraceName?: string
  durationMs?: number
  startTimeUnixNano?: string
  spanSets?: TempoSpanSet[]
  spanSet?: TempoSpanSet
  resource?: { attributes?: TempoAttrs }
  attributes?: TempoAttrs
}

function readAttrString(attrs: TempoAttrs | undefined, targetKey: string): string | undefined {
  if (!attrs) return undefined
  if (Array.isArray(attrs)) {
    for (const item of attrs) {
      if (item?.key !== targetKey) continue
      const raw = typeof item.value === "string" ? item.value : item.value?.stringValue
      if (typeof raw === "string" && raw.trim()) return raw.trim()
    }
    return undefined
  }
  if (typeof attrs === "object") {
    const raw = attrs[targetKey]
    if (typeof raw === "string" && raw.trim()) return raw.trim()
  }
  return undefined
}

function readNamespaceFromAttrs(attrs: TempoAttrs | undefined): string | undefined {
  return readAttrString(attrs, "k8s.namespace.name") ?? readAttrString(attrs, "service.namespace")
}

function extractTraceNamespaces(trace: TempoSearchTrace): string[] {
  const found = new Set<string>()
  const add = (ns: string | undefined) => {
    if (ns) found.add(ns)
  }

  add(readNamespaceFromAttrs(trace.resource?.attributes))
  add(readNamespaceFromAttrs(trace.attributes))

  const sets: TempoSpanSet[] = [
    ...(trace.spanSets ?? []),
    ...(trace.spanSet ? [trace.spanSet] : []),
  ]
  for (const spanSet of sets) {
    add(readNamespaceFromAttrs(spanSet.attributes))
    for (const span of spanSet.spans ?? []) {
      add(readNamespaceFromAttrs(span.resource?.attributes) ?? readNamespaceFromAttrs(span.attributes))
    }
  }

  return Array.from(found)
}

function traceVisible(namespaces: string[], scope: EffectiveScope): boolean {
  if (scope.all) return true
  return namespaces.length > 0 && namespaces.every((ns) => namespaceVisible(ns, scope))
}

function isValidCachedTraces(value: unknown): value is CachedTraceRecord[] {
  if (!Array.isArray(value)) return false
  return value.every(
    (item) =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as CachedTraceRecord).entry?.traceID === "string" &&
      Array.isArray((item as CachedTraceRecord).namespaces),
  )
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

    if (!scope.all) {
      if (service) {
        const ownership = await buildServiceNamespaceMap()
        if (!hasServiceInScope(service, ownership, scope)) {
          return NextResponse.json({ error: "Forbidden" }, { status: 403 })
        }
      }
      if (scope.namespaces.size === 0) {
        return NextResponse.json([])
      }
    }

    const cacheKey = scope.all ? cacheKeys.traces(service) : cacheKeys.tracesScoped(scope.fingerprint, service)
    const cached = await cacheGet<unknown>(cacheKey)
    if (isValidCachedTraces(cached)) {
      const visible = scope.all ? cached : cached.filter((r) => traceVisible(r.namespaces, scope))
      return NextResponse.json(visible.map((r) => r.entry))
    }

    // D-PORTAL-TENANT-SCOPE-678: Traces are scoped by their own OTel namespace
    // resource attributes (k8s.namespace.name / service.namespace), never by
    // service.name alone. Traces lacking a namespace attribute are denied to
    // non-admins (fail closed).
    let query = "{}"
    if (scope.all) {
      if (service) query = `{resource.service.name="${service}"}`
    } else {
      const clauses: string[] = []
      if (service) clauses.push(`resource.service.name="${service}"`)
      if (scope.namespaces.size <= 50) {
        const nsPattern = Array.from(scope.namespaces).sort().map(escapeRegexLiteral).join("|")
        clauses.push(`(resource.k8s.namespace.name=~"^(${nsPattern})$" || resource.service.namespace=~"^(${nsPattern})$")`)
      }
      const filter = clauses.length > 0 ? `{${clauses.join(" && ")}}` : "{}"
      query = `${filter} | select(resource.k8s.namespace.name, resource.service.namespace)`
    }

    const res = await fetch(
      `${tempoUrl()}/api/search?q=${encodeURIComponent(query)}&limit=20`,
      { signal: AbortSignal.timeout(5000) },
    )
    if (!res.ok) return NextResponse.json([])

    const data = await res.json() as { traces?: TempoSearchTrace[] }
    let records: CachedTraceRecord[] = (data.traces ?? []).map((t) => ({
      entry: {
        traceID: t.traceID,
        serviceName: t.rootServiceName ?? "",
        operationName: t.rootTraceName ?? "",
        duration: t.durationMs ?? 0,
        startTime: Math.floor(Number(t.startTimeUnixNano) / 1000000),
        spanCount: t.spanSets?.[0]?.spans?.length ?? t.spanSet?.spans?.length ?? 0,
      },
      namespaces: extractTraceNamespaces(t),
    }))

    if (!scope.all) {
      records = records.filter((r) => traceVisible(r.namespaces, scope))
    }

    await cacheSet(cacheKey, records, cacheTtl(scope.all ? "traces" : "tracesScoped"))
    return NextResponse.json(records.map((r) => r.entry))
  } catch (err) {
    console.error("[api/traces]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
