import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { cacheGet, cacheSet } from "@/lib/valkey"
import { cacheKeys, cacheTtl } from "@/lib/cache-keys"
import { getDependencyUrl } from "@/lib/config"
import { namespaceVisible, requireEffectiveScope, withScopeHeader, type EffectiveScope } from "@/lib/scope"
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

interface ExtractedScopedSpan {
  namespace: string
  serviceName: string
  operationName: string
  startTimeNs: string | null
  durationNs: string | null
  durationMs: number | null
}

interface CachedScopedTrace {
  traceID: string
  spans: ExtractedScopedSpan[]
}

type TempoAttrValue = { stringValue?: string }
type TempoAttr = { key?: string; value?: TempoAttrValue | string }
type TempoAttrs = TempoAttr[] | Record<string, string | undefined>

interface TempoSpan {
  spanID?: string
  name?: string
  startTimeUnixNano?: string
  durationNanos?: string | number
  durationMs?: number
  attributes?: TempoAttrs
  resource?: { attributes?: TempoAttrs }
}

interface TempoSpanSet {
  spans?: TempoSpan[]
  matched?: number
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

function normalizeNanoString(raw: string | number | undefined): string | undefined {
  if (typeof raw === "number") {
    return Number.isFinite(raw) && raw >= 0 ? String(Math.trunc(raw)) : undefined
  }
  if (typeof raw === "string") {
    const trimmed = raw.trim()
    if (/^\d+$/.test(trimmed)) return trimmed
  }
  return undefined
}

const NS_PER_MS = BigInt(1_000_000)
const HALF_MS_NS = BigInt(500_000)

function nanoToMsFloor(nsStr: string | null): number {
  if (!nsStr) return 0
  return Number(BigInt(nsStr) / NS_PER_MS)
}

function nanoToMsRounded(nsStr: string | null): number {
  if (!nsStr) return 0
  return Number((BigInt(nsStr) + HALF_MS_NS) / NS_PER_MS)
}

// D-PORTAL-TENANT-SCOPE-678 (M1): For non-admins, extract per-span metadata from
// Tempo's matched spanSets only and never read trace-level rootServiceName,
// rootTraceName, durationMs, startTimeUnixNano, or spanSet.matched, because those
// belong to the root span / whole trace and leak foreign tenant operations when
// an in-scope service appears as a child span under another tenant's root.
// Trace ID policy: `traceID` is kept when at least one in-scope span matched
// because the caller's own service participated in that trace, while all other
// columns (serviceName, operationName, startTime, duration, spanCount) are
// projected exclusively from the caller's in-scope spans.
function extractScopedSpans(trace: TempoSearchTrace): ExtractedScopedSpan[] {
  const sets = trace.spanSets?.length ? trace.spanSets : trace.spanSet ? [trace.spanSet] : []
  const seenSpanIds = new Set<string>()
  const spans: ExtractedScopedSpan[] = []

  for (const spanSet of sets) {
    for (const span of spanSet.spans ?? []) {
      if (typeof span.spanID === "string" && span.spanID.length > 0) {
        if (seenSpanIds.has(span.spanID)) continue
        seenSpanIds.add(span.spanID)
      }
      const namespace =
        readNamespaceFromAttrs(span.resource?.attributes) ??
        readNamespaceFromAttrs(span.attributes) ??
        readNamespaceFromAttrs(spanSet.attributes) ??
        readNamespaceFromAttrs(trace.resource?.attributes) ??
        readNamespaceFromAttrs(trace.attributes)
      if (!namespace) continue

      const serviceName =
        readAttrString(span.resource?.attributes, "service.name") ??
        readAttrString(span.attributes, "service.name") ??
        readAttrString(spanSet.attributes, "service.name") ??
        ""

      const operationName =
        typeof span.name === "string" && span.name.trim()
          ? span.name.trim()
          : readAttrString(span.attributes, "span:name") ??
            readAttrString(span.attributes, "name") ??
            readAttrString(span.attributes, "span.name") ??
            ""

      spans.push({
        namespace,
        serviceName,
        operationName,
        startTimeNs: normalizeNanoString(span.startTimeUnixNano) ?? null,
        durationNs: normalizeNanoString(span.durationNanos) ?? null,
        durationMs: typeof span.durationMs === "number" && Number.isFinite(span.durationMs) ? span.durationMs : null,
      })
    }
  }

  return spans
}

function pickEarliestSpan(spans: ExtractedScopedSpan[]): ExtractedScopedSpan {
  let earliest = spans[0]
  let earliestNs = earliest.startTimeNs !== null ? BigInt(earliest.startTimeNs) : null

  for (let i = 1; i < spans.length; i++) {
    const candidate = spans[i]
    if (candidate.startTimeNs === null) continue
    const candidateNs = BigInt(candidate.startTimeNs)
    if (earliestNs === null || candidateNs < earliestNs) {
      earliest = candidate
      earliestNs = candidateNs
    }
  }

  return earliest
}

function projectScopedTrace(record: CachedScopedTrace, scope: EffectiveScope): TraceEntry | null {
  const visibleSpans = record.spans.filter((s) => Boolean(s.namespace) && namespaceVisible(s.namespace, scope))
  if (visibleSpans.length === 0) return null

  const earliest = pickEarliestSpan(visibleSpans)
  return {
    traceID: record.traceID,
    serviceName: earliest.serviceName,
    operationName: earliest.operationName,
    duration: earliest.durationNs !== null ? nanoToMsRounded(earliest.durationNs) : earliest.durationMs ?? 0,
    startTime: nanoToMsFloor(earliest.startTimeNs),
    spanCount: visibleSpans.length,
  }
}

function projectAdminTrace(t: TempoSearchTrace): TraceEntry {
  return {
    traceID: t.traceID,
    serviceName: t.rootServiceName ?? "",
    operationName: t.rootTraceName ?? "",
    duration: t.durationMs ?? 0,
    startTime: Math.floor(Number(t.startTimeUnixNano) / 1000000),
    spanCount: t.spanSets?.[0]?.spans?.length ?? t.spanSet?.spans?.length ?? 0,
  }
}

function isValidAdminTraces(value: unknown): value is TraceEntry[] {
  if (!Array.isArray(value)) return false
  return value.every(
    (item) =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as TraceEntry).traceID === "string" &&
      typeof (item as TraceEntry).serviceName === "string" &&
      typeof (item as TraceEntry).operationName === "string" &&
      typeof (item as TraceEntry).duration === "number" &&
      typeof (item as TraceEntry).startTime === "number" &&
      typeof (item as TraceEntry).spanCount === "number",
  )
}

function isValidCachedScopedTraces(value: unknown): value is CachedScopedTrace[] {
  if (!Array.isArray(value)) return false
  return value.every(
    (item) =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as CachedScopedTrace).traceID === "string" &&
      Array.isArray((item as CachedScopedTrace).spans) &&
      (item as CachedScopedTrace).spans.every(
        (s) =>
          typeof s === "object" &&
          s !== null &&
          typeof s.namespace === "string" &&
          typeof s.serviceName === "string" &&
          typeof s.operationName === "string",
      ),
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
          return withScopeHeader(NextResponse.json({ error: "Forbidden" }, { status: 403 }), scope)
        }
      }
      if (scope.namespaces.size === 0) {
        return withScopeHeader(NextResponse.json([]), scope)
      }
    }

    const cacheKey = scope.all ? cacheKeys.traces(service) : cacheKeys.tracesScoped(scope.fingerprint, service)
    const cached = await cacheGet<unknown>(cacheKey)
    if (scope.all) {
      if (isValidAdminTraces(cached)) return withScopeHeader(NextResponse.json(cached), scope)
    } else if (isValidCachedScopedTraces(cached)) {
      const projected = cached
        .map((r) => projectScopedTrace(r, scope))
        .filter((e): e is TraceEntry => e !== null)
      return withScopeHeader(NextResponse.json(projected), scope)
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
      query = `${filter} | select(resource.k8s.namespace.name, resource.service.namespace, resource.service.name)`
    }

    const res = await fetch(
      `${tempoUrl()}/api/search?q=${encodeURIComponent(query)}&limit=20`,
      { signal: AbortSignal.timeout(5000) },
    )
    if (!res.ok) return withScopeHeader(NextResponse.json([]), scope)

    const data = await res.json() as { traces?: TempoSearchTrace[] }
    if (scope.all) {
      const entries = (data.traces ?? []).map(projectAdminTrace)
      await cacheSet(cacheKey, entries, cacheTtl("traces"))
      return withScopeHeader(NextResponse.json(entries), scope)
    }

    const records: CachedScopedTrace[] = (data.traces ?? [])
      .map((t) => ({
        traceID: t.traceID,
        spans: extractScopedSpans(t).filter((s) => namespaceVisible(s.namespace, scope)),
      }))
      .filter((r) => r.spans.length > 0)

    await cacheSet(cacheKey, records, cacheTtl("tracesScoped"))
    const projected = records
      .map((r) => projectScopedTrace(r, scope))
      .filter((e): e is TraceEntry => e !== null)
    return withScopeHeader(NextResponse.json(projected), scope)
  } catch (err) {
    console.error("[api/traces]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
