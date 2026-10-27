import "server-only"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import { getDependencyUrl } from "./config"
import { fetchWithPolicy, readJsonWithPolicy } from "./http-client"
import { assertLogQLSafe } from "./validation"
import type { FalcoEvent, FalcoEventPriority } from "@/types/security"

// Read at call-time, not module top level (see 85ca55c / getDependencyUrl's contract
// in config.ts) so a missing LOKI_URL fails on first use in production instead of
// silently sending requests to the in-cluster default.
function getLokiUrl(): string {
  return getDependencyUrl("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100")
}

// Falcosidekick writes the `priority` stream label as the Capitalized name ("Critical") —
// types.PriorityType.String() in falcosidekick 2.31-2.35, confirmed on a live Falco 0.45.1 ->
// falcosidekick 2.35.0 capture (see falco.test.ts). It is NOT lowercase, and Loki label matching
// is case-sensitive, so a lowercase `priority="critical"` selector matches nothing. Match both
// spellings with an alternation (LOGQL_SAFE_RE rejects the `(?i)` flag) so a future lowercase
// label does not silently empty the feed either.
function lokiPriorityMatcher(p: FalcoEventPriority): string {
  const capitalized = capitalizeFirst(p)
  return `priority=~"${capitalized}|${capitalized.toLowerCase()}"`
}

function capitalizeFirst(s: string): string {
  if (!s) return s
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase()
}

// Falco emits JSON null (not a missing key) for fields it cannot resolve, e.g. k8s.pod.name for a
// process outside Kubernetes; treat null and empty as absent.
function fieldValue(v: string | null | undefined): string | undefined {
  return typeof v === "string" && v !== "" ? v : undefined
}

// Falcosidekick Loki line shape: the full Falco event as JSON. This requires the falcosidekick
// setting loki.format=json (gitops/charts/narwhal-apps/templates/falco.yaml); the default `text`
// format stores only the output string and every such line is dropped by parseFalcoEvent.
interface FalcoJsonLine {
  output?: string
  priority?: string
  rule?: string
  time?: string
  source?: string
  output_fields?: Record<string, string | null | undefined>
  hostname?: string
  tags?: string[]
}

// Loki query_range response shape
interface LokiStream {
  stream: Record<string, string>
  values: [string, string][] // [ts_nanoseconds, log_line]
}

interface LokiQueryResponse {
  status: string
  data: {
    resultType: string
    result: LokiStream[]
  }
}

function parseFalcoEvent(tsNano: string, line: string, streamMeta: Record<string, string>): FalcoEvent | null {
  let parsed: FalcoJsonLine
  try {
    parsed = JSON.parse(line) as FalcoJsonLine
  } catch {
    return null
  }

  const hostname = parsed.hostname ?? streamMeta["hostname"] ?? "?"
  const id = `${hostname}-${tsNano}`
  const time = new Date(Math.floor(parseInt(tsNano, 10) / 1_000_000)).toISOString()
  const rawPriority = parsed.priority ?? streamMeta["priority"] ?? "informational"
  const priority = capitalizeFirst(rawPriority) as FalcoEventPriority

  return {
    id,
    time,
    priority,
    rule: parsed.rule ?? "",
    output: parsed.output ?? line,
    source: parsed.source ?? "syscall",
    tags: parsed.tags,
    pod: fieldValue(parsed.output_fields?.["k8s.pod.name"]),
    namespace: fieldValue(parsed.output_fields?.["k8s.ns.name"]),
    container: fieldValue(parsed.output_fields?.["container.name"]),
    image: fieldValue(parsed.output_fields?.["container.image.repository"]),
  }
}

async function queryLoki(logql: string, sinceMinutes: number, limit: number): Promise<FalcoEvent[]> {
  // M-2: LogQL safety — defense in depth (callers use templated queries).
  assertLogQLSafe(logql)
  const nowMs = Date.now()
  const startMs = nowMs - sinceMinutes * 60 * 1000
  // Loki expects nanosecond timestamps; multiply ms by 1_000_000 using string to avoid BigInt
  const nowNs = String(nowMs) + "000000"
  const startNs = String(startMs) + "000000"

  const params = new URLSearchParams({
    query: logql,
    start: startNs,
    end: nowNs,
    limit: String(limit),
    direction: "BACKWARD",
  })

  const url = `${getLokiUrl()}/loki/api/v1/query_range?${params.toString()}`
  const res = await fetchWithPolicy(url, {}, { timeoutMs: 5000 })
  if (!res.ok) {
    throw new Error(`Loki API ${res.status} ${res.statusText}`)
  }

  const body = await readJsonWithPolicy<LokiQueryResponse>(res)
  const events: FalcoEvent[] = []

  for (const stream of body.data?.result ?? []) {
    for (const [tsNano, line] of stream.values) {
      const event = parseFalcoEvent(tsNano, line, stream.stream)
      if (event) events.push(event)
    }
  }

  return events
}

interface GetRuntimeEventsOpts {
  priority?: FalcoEventPriority
  sinceMinutes?: number
  limit?: number
}

export async function getRuntimeEvents(opts: GetRuntimeEventsOpts = {}): Promise<FalcoEvent[]> {
  const { priority, sinceMinutes = 60, limit = 100 } = opts
  const cacheKey = cacheKeys.falcoEvents(priority, sinceMinutes, limit)
  const cached = await cacheGet<FalcoEvent[]>(cacheKey)
  if (cached) return cached

  try {
    // Falcosidekick `source` label = Falco event source (e.g. "syscall"), not literal "falco" (the
    // literal source=falco exists only on Alertmanager alerts).
    // Use `rule` label presence as Falco-specific identifier (only Falcosidekick streams have it).
    const logql = priority
      ? `{rule=~".+", ${lokiPriorityMatcher(priority)}}`
      : `{rule=~".+"}`

    const events = await queryLoki(logql, sinceMinutes, limit)

    await cacheSet(cacheKey, events, cacheTtl("falcoEvents"))
    return events
  } catch (err) {
    console.warn("[falco] getRuntimeEvents failed:", err instanceof Error ? err.message : err)
    return []
  }
}

export async function getCriticalRuntimeAlerts(): Promise<FalcoEvent[]> {
  const cacheKey = cacheKeys.falcoCritical()
  const cached = await cacheGet<FalcoEvent[]>(cacheKey)
  if (cached) return cached

  try {
    // Use Loki with critical priority filter — Alertmanager only holds active alerts
    // and would require additional label mapping; Loki provides the full history with
    // the same priority label set by Falcosidekick.
    const events = await getRuntimeEvents({ priority: "Critical", limit: 50 })

    await cacheSet(cacheKey, events, cacheTtl("falcoCritical"))
    return events
  } catch (err) {
    console.warn("[falco] getCriticalRuntimeAlerts failed:", err instanceof Error ? err.message : err)
    return []
  }
}
