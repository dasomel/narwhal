import { matchBenchmarkException, type AcceptedMatch } from "./compliance-accepted"
import type {
  AcceptedClass,
  AcceptedScope,
  BenchmarkControlEvidence,
  BenchmarkControlFinding,
  ComplianceControl,
  ControlEvaluationState,
  ControlNotEvaluatedReason,
} from "@/types/compliance"

export const BENCHMARK_CONFIGMAP_NAMESPACE = "security-system"
export const BENCHMARK_CONFIGMAP_LABEL_SELECTOR = "narwhal.io/compliance-tool=kube-bench"
export const BENCHMARK_CONFIGMAP_LIST_PATH =
  "/api/v1/namespaces/security-system/configmaps?labelSelector=narwhal.io%2Fcompliance-tool%3Dkube-bench&limit=100"
export const BENCHMARK_RECORD_KEY = "record.json"
export const BENCHMARK_EXPECTED_SCHEMA_VERSION = 1
export const BENCHMARK_EXPECTED_TOOL = "kube-bench"
export const BENCHMARK_EXPECTED_ID = "cis-1.23"
/** Daily kube-bench CronJob freshness bound (hours). Records older than 26h fail closed as stale. */
export const BENCHMARK_MAX_AGE_HOURS = 26
export const BENCHMARK_MAX_AGE_MS = BENCHMARK_MAX_AGE_HOURS * 60 * 60 * 1000
/** Maximum accepted record.json payload per ConfigMap (512 KiB). */
export const MAX_BENCHMARK_RECORD_BYTES = 512 * 1024
const BENCHMARK_EXCERPT_MAX_CHARS = 180

const ISO_TIMESTAMP_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

export interface RawConfigMapList {
  metadata?: {
    continue?: string
  }
  items?: Array<{
    metadata?: {
      name?: string
      labels?: Record<string, string>
    }
    data?: Record<string, string>
  }>
}

export type BenchmarkResultStatus = "PASS" | "FAIL" | "WARN" | "INFO"
export type BenchmarkResultType = "automated" | "manual"

export interface KubeBenchResultEntry {
  controlId: string
  section: string
  text: string
  status: BenchmarkResultStatus
  scored?: boolean
  type: BenchmarkResultType
  actualValue: string
  expectedResult: string
  reason: string
}

export interface ValidKubeBenchRecord {
  configMapName: string
  tool: string
  toolVersion: string
  benchmarkId: string
  node: string
  nodeRoles: readonly string[]
  finishedAt: string
  finishedMs: number
  resultsByControlId: ReadonlyMap<string, KubeBenchResultEntry>
}

interface NodeInvalidRecord {
  reason: BenchmarkFailureReason
  detail: string
}

type BenchmarkFailureReason = Extract<
  ControlNotEvaluatedReason,
  | "benchmark_missing"
  | "benchmark_stale"
  | "benchmark_error"
  | "benchmark_malformed"
  | "benchmark_list_forbidden"
  | "benchmark_info"
  | "coverage_unknown"
  | "missing_node_type"
  | "manual"
>

export interface BenchmarkStore {
  /** True when the ConfigMap list call itself failed or was truncated (caller must not cache). */
  listFailed: boolean
  listErrorReason?: "benchmark_list_forbidden" | "coverage_unknown" | "benchmark_malformed"
  listErrorDetail?: string
  /** True when at least one kube-bench ConfigMap was present in the list response. */
  hasAnyConfigMaps: boolean
  nodesAvailable: boolean
  clusterNodes: ReadonlyMap<string, "master" | "worker">
  validByNode: ReadonlyMap<string, ValidKubeBenchRecord>
  invalidByNode: ReadonlyMap<string, NodeInvalidRecord>
  unassignedErrors: readonly NodeInvalidRecord[]
  matchedRuleIds: Set<string>
}

function isPlainObject(val: unknown): val is Record<string, unknown> {
  if (!val || typeof val !== "object" || Array.isArray(val)) return false
  const proto = Object.getPrototypeOf(val)
  return proto === Object.prototype || proto === null
}

function truncateExcerpt(raw: string, maxChars = BENCHMARK_EXCERPT_MAX_CHARS): string {
  const collapsed = raw.replace(/\s+/g, " ").trim()
  if (collapsed.length <= maxChars) return collapsed
  return `${collapsed.slice(0, maxChars - 1)}…`
}

function hasControlPlaneRole(nodeRoles: readonly string[]): boolean {
  return nodeRoles.some((r) => {
    const lower = r.trim().toLowerCase()
    return lower === "control-plane" || lower === "master"
  })
}

/**
 * D-KUBE-BENCH-SECTION-SCOPE:
 * Maps a CIS control ID to its required node coverage scope:
 * - Sections 1.x, 2.x, 3.x (API server, controller-manager, scheduler, etcd, control-plane files):
 *   require a valid record from EVERY control-plane node (`role === "master"` in `/api/v1/nodes`),
 *   and each record's `nodeRoles` must declare `"control-plane"` or `"master"`.
 * - Section 4.x (worker / kubelet node checks):
 *   require a valid record from EVERY node in the cluster (control-plane + worker).
 * - Section 5.x (policies — RBAC, PSA, NetworkPolicy, Secrets):
 *   reported once at cluster scope; requires at least one valid record reporting the controlId
 *   with zero FAIL/WARN/INFO across reporting records.
 */
export function getCisControlNodeScope(controlId: string): "control-plane" | "all-nodes" | "cluster-once" {
  const top = controlId.split(".")[0]
  if (top === "1" || top === "2" || top === "3") return "control-plane"
  if (top === "4") return "all-nodes"
  return "cluster-once"
}

const VALID_STATUSES = new Set<string>(["PASS", "FAIL", "WARN", "INFO"])
const VALID_TYPES = new Set<string>(["automated", "manual"])

function validateSingleConfigMap(
  cm: NonNullable<RawConfigMapList["items"]>[number],
  clusterNodes: ReadonlyMap<string, "master" | "worker">,
  nodesAvailable: boolean,
  nowMs: number,
):
  | { ok: true; record: ValidKubeBenchRecord }
  | { ok: false; node?: string; reason: BenchmarkFailureReason; detail: string } {
  const cmName = cm.metadata?.name ?? "<unnamed>"
  const rawJson = cm.data?.[BENCHMARK_RECORD_KEY]
  if (typeof rawJson !== "string" || rawJson.length === 0) {
    return { ok: false, reason: "benchmark_malformed", detail: `${cmName}: missing ${BENCHMARK_RECORD_KEY}` }
  }
  if (Buffer.byteLength(rawJson, "utf8") > MAX_BENCHMARK_RECORD_BYTES) {
    return {
      ok: false,
      reason: "benchmark_malformed",
      detail: `${cmName}: ${BENCHMARK_RECORD_KEY} exceeds ${MAX_BENCHMARK_RECORD_BYTES} byte limit`,
    }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(rawJson)
  } catch {
    return { ok: false, reason: "benchmark_malformed", detail: `${cmName}: malformed JSON` }
  }
  if (!isPlainObject(parsed)) {
    return { ok: false, reason: "benchmark_malformed", detail: `${cmName}: root must be an object` }
  }

  const rawNode = typeof parsed.node === "string" && parsed.node.trim().length > 0 && parsed.node === parsed.node.trim()
    ? parsed.node
    : undefined

  if (parsed.schemaVersion !== BENCHMARK_EXPECTED_SCHEMA_VERSION) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: unsupported schemaVersion` }
  }
  if (parsed.tool !== BENCHMARK_EXPECTED_TOOL) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: unexpected tool` }
  }
  if (typeof parsed.toolVersion !== "string" || parsed.toolVersion.trim().length === 0) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: missing toolVersion` }
  }
  if (parsed.benchmarkId !== BENCHMARK_EXPECTED_ID) {
    return {
      ok: false,
      node: rawNode,
      reason: "benchmark_malformed",
      detail: `${cmName}: unexpected benchmarkId (${String(parsed.benchmarkId ?? "")})`,
    }
  }
  if (parsed.status === "error") {
    const reasonText = typeof parsed.reason === "string" && parsed.reason.trim().length > 0
      ? parsed.reason.trim()
      : "kube-bench run status=error"
    return { ok: false, node: rawNode, reason: "benchmark_error", detail: `${cmName}: ${reasonText}` }
  }
  if (parsed.status !== "complete") {
    return {
      ok: false,
      node: rawNode,
      reason: "benchmark_malformed",
      detail: `${cmName}: invalid status (${String(parsed.status ?? "")})`,
    }
  }
  if (!rawNode) {
    return { ok: false, reason: "benchmark_malformed", detail: `${cmName}: missing or invalid node` }
  }
  if (nodesAvailable && !clusterNodes.has(rawNode)) {
    return {
      ok: false,
      node: rawNode,
      reason: "benchmark_malformed",
      detail: `${cmName}: node "${rawNode}" does not exist in cluster`,
    }
  }
  if (
    !Array.isArray(parsed.nodeRoles) ||
    parsed.nodeRoles.length === 0 ||
    !parsed.nodeRoles.every((r) => typeof r === "string" && r.trim().length > 0)
  ) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: invalid nodeRoles` }
  }
  if (typeof parsed.finishedAt !== "string" || !ISO_TIMESTAMP_RE.test(parsed.finishedAt)) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: invalid finishedAt timestamp` }
  }
  const finishedMs = Date.parse(parsed.finishedAt)
  if (!Number.isFinite(finishedMs)) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: unparseable finishedAt` }
  }
  if (finishedMs > nowMs) {
    return { ok: false, node: rawNode, reason: "benchmark_stale", detail: `${cmName}: finishedAt is in the future` }
  }
  if (nowMs - finishedMs > BENCHMARK_MAX_AGE_MS) {
    return {
      ok: false,
      node: rawNode,
      reason: "benchmark_stale",
      detail: `${cmName}: record age exceeds ${BENCHMARK_MAX_AGE_HOURS}h`,
    }
  }

  if (!Array.isArray(parsed.results) || parsed.results.length === 0) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: results must be non-empty` }
  }

  const resultsByControlId = new Map<string, KubeBenchResultEntry>()
  const counts = { pass: 0, fail: 0, warn: 0, info: 0 }

  for (const item of parsed.results) {
    if (!isPlainObject(item)) {
      return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: non-object result entry` }
    }
    const controlId = item.controlId
    if (typeof controlId !== "string" || controlId.trim().length === 0 || controlId !== controlId.trim()) {
      return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: invalid controlId` }
    }
    if (resultsByControlId.has(controlId)) {
      return {
        ok: false,
        node: rawNode,
        reason: "benchmark_malformed",
        detail: `${cmName}: duplicate controlId "${controlId}"`,
      }
    }
    if (typeof item.status !== "string" || !VALID_STATUSES.has(item.status)) {
      return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: invalid result status` }
    }
    if (typeof item.type !== "string" || !VALID_TYPES.has(item.type)) {
      return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: invalid result type` }
    }
    if (typeof item.section !== "string" || typeof item.text !== "string") {
      return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: missing section/text` }
    }
    for (const optField of ["actualValue", "expectedResult", "reason"] as const) {
      if (item[optField] !== undefined && typeof item[optField] !== "string") {
        return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: invalid ${optField}` }
      }
    }
    const status = item.status as BenchmarkResultStatus
    if (status === "PASS") counts.pass++
    else if (status === "FAIL") counts.fail++
    else if (status === "WARN") counts.warn++
    else counts.info++

    resultsByControlId.set(controlId, {
      controlId,
      section: item.section,
      text: item.text,
      status,
      scored: typeof item.scored === "boolean" ? item.scored : undefined,
      type: item.type as BenchmarkResultType,
      actualValue: (item.actualValue as string | undefined) ?? "",
      expectedResult: (item.expectedResult as string | undefined) ?? "",
      reason: (item.reason as string | undefined) ?? "",
    })
  }

  if (!isPlainObject(parsed.totals)) {
    return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: missing totals` }
  }
  const totals = parsed.totals
  for (const k of ["pass", "fail", "warn", "info"] as const) {
    if (typeof totals[k] !== "number" || !Number.isSafeInteger(totals[k]) || totals[k] < 0) {
      return { ok: false, node: rawNode, reason: "benchmark_malformed", detail: `${cmName}: invalid totals.${k}` }
    }
  }
  if (
    totals.pass !== counts.pass ||
    totals.fail !== counts.fail ||
    totals.warn !== counts.warn ||
    totals.info !== counts.info
  ) {
    return {
      ok: false,
      node: rawNode,
      reason: "benchmark_malformed",
      detail: `${cmName}: totals inconsistent with results`,
    }
  }

  return {
    ok: true,
    record: {
      configMapName: cmName,
      tool: parsed.tool,
      toolVersion: parsed.toolVersion.trim(),
      benchmarkId: parsed.benchmarkId,
      node: rawNode,
      nodeRoles: parsed.nodeRoles as string[],
      finishedAt: parsed.finishedAt,
      finishedMs,
      resultsByControlId,
    },
  }
}

export function buildBenchmarkStore(opts: {
  configMapRes: { ok: true; data: RawConfigMapList } | { ok: false; error: string; status?: number }
  nodesAvailable: boolean
  clusterNodes: ReadonlyMap<string, "master" | "worker">
  nowMs?: number
}): BenchmarkStore {
  const { configMapRes, nodesAvailable, clusterNodes, nowMs = Date.now() } = opts
  const validByNode = new Map<string, ValidKubeBenchRecord>()
  const invalidByNode = new Map<string, NodeInvalidRecord>()
  const unassignedErrors: NodeInvalidRecord[] = []
  const matchedRuleIds = new Set<string>()

  if (!configMapRes.ok) {
    const isForbidden = configMapRes.status === 403 || /\b403\b/.test(configMapRes.error)
    return {
      listFailed: true,
      listErrorReason: isForbidden ? "benchmark_list_forbidden" : "coverage_unknown",
      listErrorDetail: configMapRes.error,
      hasAnyConfigMaps: false,
      nodesAvailable,
      clusterNodes,
      validByNode,
      invalidByNode,
      unassignedErrors,
      matchedRuleIds,
    }
  }

  if (typeof configMapRes.data.metadata?.continue === "string" && configMapRes.data.metadata.continue.length > 0) {
    return {
      listFailed: true,
      listErrorReason: "benchmark_malformed",
      listErrorDetail: "kube-bench ConfigMap list truncated at page limit",
      hasAnyConfigMaps: true,
      nodesAvailable,
      clusterNodes,
      validByNode,
      invalidByNode,
      unassignedErrors,
      matchedRuleIds,
    }
  }

  const items = configMapRes.data.items ?? []
  for (const cm of items) {
    const res = validateSingleConfigMap(cm, clusterNodes, nodesAvailable, nowMs)
    if (!res.ok) {
      const err: NodeInvalidRecord = { reason: res.reason, detail: res.detail }
      if (res.node && (!nodesAvailable || clusterNodes.has(res.node))) {
        validByNode.delete(res.node)
        invalidByNode.set(res.node, err)
      } else {
        unassignedErrors.push(err)
      }
      continue
    }

    const node = res.record.node
    if (invalidByNode.has(node)) continue
    if (validByNode.has(node)) {
      validByNode.delete(node)
      invalidByNode.set(node, {
        reason: "benchmark_malformed",
        detail: `duplicate kube-bench record for node ${node}`,
      })
      continue
    }
    validByNode.set(node, res.record)
  }

  return {
    listFailed: false,
    hasAnyConfigMaps: items.length > 0,
    nodesAvailable,
    clusterNodes,
    validByNode,
    invalidByNode,
    unassignedErrors,
    matchedRuleIds,
  }
}

const FAILURE_REASON_PRIORITY: ReadonlyArray<BenchmarkFailureReason> = [
  "benchmark_list_forbidden",
  "coverage_unknown",
  "benchmark_error",
  "benchmark_malformed",
  "benchmark_stale",
  "benchmark_missing",
  "missing_node_type",
  "benchmark_info",
  "manual",
]

function pickPriorityFailure(errors: readonly NodeInvalidRecord[]): NodeInvalidRecord {
  for (const reason of FAILURE_REASON_PRIORITY) {
    const found = errors.find((e) => e.reason === reason)
    if (found) return found
  }
  return errors[0] ?? { reason: "benchmark_missing", detail: "no kube-bench record" }
}

interface BenchmarkControlEval {
  verdict: ControlEvaluationState
  reason?: BenchmarkFailureReason
  detail?: string
  toolVersion: string
  nodeCount: number
  finishedAt: string
  passingNodeCount: number
  failFindings: BenchmarkControlFinding[]
  actionableFailCount: number
  acceptedFailCount: number
  acceptedScopes: Set<AcceptedClass>
}

function evaluateBenchmarkControl(controlId: string, store: BenchmarkStore): BenchmarkControlEval | null {
  if (store.listFailed) {
    return {
      verdict: "not_evaluated",
      reason: store.listErrorReason ?? "coverage_unknown",
      detail: store.listErrorDetail,
      toolVersion: "",
      nodeCount: 0,
      finishedAt: "",
      passingNodeCount: 0,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes: new Set(),
    }
  }

  // If no kube-bench ConfigMaps exist at all, return null unless nodes failed — caller keeps Trivy outcome.
  if (!store.hasAnyConfigMaps) {
    return null
  }

  // Check FAIL across ALL valid records FIRST (failure-first, regardless of missing/invalid sibling nodes).
  const failFindings: BenchmarkControlFinding[] = []
  let actionableFailCount = 0
  let acceptedFailCount = 0
  const acceptedScopes = new Set<AcceptedClass>()
  let newestFinishedMs = -1
  let newestFinishedAt = ""
  let toolVersion = ""

  for (const rec of store.validByNode.values()) {
    const entry = rec.resultsByControlId.get(controlId)
    if (!entry) continue
    if (rec.finishedMs > newestFinishedMs) {
      newestFinishedMs = rec.finishedMs
      newestFinishedAt = rec.finishedAt
      toolVersion = rec.toolVersion
    }
    if (entry.status === "FAIL") {
      const match: AcceptedMatch | null = matchBenchmarkException(BENCHMARK_EXPECTED_ID, controlId, rec.node)
      if (match) {
        acceptedFailCount++
        acceptedScopes.add(match.class)
        store.matchedRuleIds.add(match.ruleId)
      } else {
        actionableFailCount++
      }
      failFindings.push({
        node: rec.node,
        status: "FAIL",
        ...(entry.actualValue ? { actualValue: truncateExcerpt(entry.actualValue) } : {}),
        ...(entry.expectedResult ? { expectedResult: truncateExcerpt(entry.expectedResult) } : {}),
        ...(entry.reason ? { reason: truncateExcerpt(entry.reason) } : {}),
        ...(match
          ? {
              accepted: true,
              acceptedRuleId: match.ruleId,
              acceptedReasonKey: match.reasonKey,
              acceptedReason: match.reason,
              acceptedOwner: match.owner,
              acceptedReviewedAt: match.reviewedAt,
            }
          : { accepted: false }),
      })
    }
  }

  if (failFindings.length > 0) {
    return {
      verdict: "failed",
      toolVersion,
      nodeCount: failFindings.length,
      finishedAt: newestFinishedAt,
      passingNodeCount: 0,
      failFindings,
      actionableFailCount,
      acceptedFailCount,
      acceptedScopes,
    }
  }

  if (!store.nodesAvailable) {
    return {
      verdict: "not_evaluated",
      reason: "coverage_unknown",
      detail: "node list unavailable for kube-bench coverage verification",
      toolVersion,
      nodeCount: store.validByNode.size,
      finishedAt: newestFinishedAt,
      passingNodeCount: 0,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes,
    }
  }

  const scope = getCisControlNodeScope(controlId)
  if (scope === "cluster-once") {
    // Section 5 'policies': reported once cluster-wide; if no valid records exist, surface unassigned/node errors.
    if (store.validByNode.size === 0) {
      const allErrs = [...store.invalidByNode.values(), ...store.unassignedErrors]
      const chosen = pickPriorityFailure(allErrs)
      return {
        verdict: "not_evaluated",
        reason: chosen.reason,
        detail: chosen.detail,
        toolVersion: "",
        nodeCount: 0,
        finishedAt: "",
        passingNodeCount: 0,
        failFindings: [],
        actionableFailCount: 0,
        acceptedFailCount: 0,
        acceptedScopes,
      }
    }
    const entries: Array<{ rec: ValidKubeBenchRecord; entry: KubeBenchResultEntry }> = []
    for (const rec of store.validByNode.values()) {
      const entry = rec.resultsByControlId.get(controlId)
      if (entry) entries.push({ rec, entry })
    }
    if (entries.length === 0) {
      return {
        verdict: "not_evaluated",
        reason: "benchmark_missing",
        detail: `control ${controlId} absent from kube-bench results`,
        toolVersion: "",
        nodeCount: store.validByNode.size,
        finishedAt: newestFinishedAt,
        passingNodeCount: 0,
        failFindings: [],
        actionableFailCount: 0,
        acceptedFailCount: 0,
        acceptedScopes,
      }
    }
    const maxEntry = entries.reduce((a, b) => (b.rec.finishedMs > a.rec.finishedMs ? b : a))
    if (entries.some((e) => e.entry.type === "manual" || e.entry.status === "WARN")) {
      return {
        verdict: "not_evaluated",
        reason: "manual",
        detail: `kube-bench v${maxEntry.rec.toolVersion}: WARN / manual`,
        toolVersion: maxEntry.rec.toolVersion,
        nodeCount: entries.length,
        finishedAt: maxEntry.rec.finishedAt,
        passingNodeCount: 0,
        failFindings: [],
        actionableFailCount: 0,
        acceptedFailCount: 0,
        acceptedScopes,
      }
    }
    if (entries.some((e) => e.entry.status === "INFO")) {
      return {
        verdict: "not_evaluated",
        reason: "benchmark_info",
        detail: `kube-bench v${maxEntry.rec.toolVersion}: INFO`,
        toolVersion: maxEntry.rec.toolVersion,
        nodeCount: entries.length,
        finishedAt: maxEntry.rec.finishedAt,
        passingNodeCount: 0,
        failFindings: [],
        actionableFailCount: 0,
        acceptedFailCount: 0,
        acceptedScopes,
      }
    }
    if (entries.every((e) => e.entry.status === "PASS" && e.entry.type === "automated")) {
      return {
        verdict: "passed",
        toolVersion: maxEntry.rec.toolVersion,
        nodeCount: entries.length,
        finishedAt: maxEntry.rec.finishedAt,
        passingNodeCount: entries.length,
        failFindings: [],
        actionableFailCount: 0,
        acceptedFailCount: 0,
        acceptedScopes,
      }
    }
    return {
      verdict: "not_evaluated",
      reason: "benchmark_malformed",
      detail: "unexpected kube-bench result state",
      toolVersion: maxEntry.rec.toolVersion,
      nodeCount: entries.length,
      finishedAt: maxEntry.rec.finishedAt,
      passingNodeCount: 0,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes,
    }
  }

  // Node-scoped sections: 1.x/2.x/3.x -> all control-plane nodes; 4.x -> all cluster nodes.
  const expectedNodes: string[] = []
  for (const [nodeName, role] of store.clusterNodes) {
    if (scope === "control-plane" && role !== "master") continue
    expectedNodes.push(nodeName)
  }

  if (expectedNodes.length === 0) {
    return {
      verdict: "not_evaluated",
      reason: "missing_node_type",
      detail: scope === "control-plane" ? "no control-plane nodes on cluster" : "no nodes on cluster",
      toolVersion: "",
      nodeCount: 0,
      finishedAt: "",
      passingNodeCount: 0,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes,
    }
  }

  const nodeErrors: NodeInvalidRecord[] = []
  const nodeEntries: Array<{ rec: ValidKubeBenchRecord; entry: KubeBenchResultEntry }> = []

  for (const nodeName of expectedNodes) {
    const invalid = store.invalidByNode.get(nodeName)
    if (invalid) {
      nodeErrors.push(invalid)
      continue
    }
    const valid = store.validByNode.get(nodeName)
    if (!valid) {
      const fallback = store.unassignedErrors.length > 0
        ? pickPriorityFailure(store.unassignedErrors)
        : { reason: "benchmark_missing" as const, detail: `no kube-bench record for node ${nodeName}` }
      nodeErrors.push(fallback)
      continue
    }
    if (scope === "control-plane" && !hasControlPlaneRole(valid.nodeRoles)) {
      nodeErrors.push({
        reason: "benchmark_malformed",
        detail: `record for control-plane node ${nodeName} lacks control-plane nodeRole`,
      })
      continue
    }
    const entry = valid.resultsByControlId.get(controlId)
    if (!entry) {
      nodeErrors.push({
        reason: "benchmark_missing",
        detail: `control ${controlId} absent on node ${nodeName}`,
      })
      continue
    }
    nodeEntries.push({ rec: valid, entry })
  }

  if (nodeErrors.length > 0) {
    const chosen = pickPriorityFailure(nodeErrors)
    return {
      verdict: "not_evaluated",
      reason: chosen.reason,
      detail: chosen.detail,
      toolVersion: nodeEntries[0]?.rec.toolVersion ?? "",
      nodeCount: nodeEntries.length,
      finishedAt: newestFinishedAt,
      passingNodeCount: 0,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes,
    }
  }

  const maxRec = nodeEntries.reduce((a, b) => (b.rec.finishedMs > a.rec.finishedMs ? b : a)).rec
  if (nodeEntries.some((e) => e.entry.type === "manual" || e.entry.status === "WARN")) {
    return {
      verdict: "not_evaluated",
      reason: "manual",
      detail: `kube-bench v${maxRec.toolVersion}: WARN / manual`,
      toolVersion: maxRec.toolVersion,
      nodeCount: nodeEntries.length,
      finishedAt: maxRec.finishedAt,
      passingNodeCount: 0,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes,
    }
  }
  if (nodeEntries.some((e) => e.entry.status === "INFO")) {
    return {
      verdict: "not_evaluated",
      reason: "benchmark_info",
      detail: `kube-bench v${maxRec.toolVersion}: INFO`,
      toolVersion: maxRec.toolVersion,
      nodeCount: nodeEntries.length,
      finishedAt: maxRec.finishedAt,
      passingNodeCount: 0,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes,
    }
  }
  if (nodeEntries.every((e) => e.entry.status === "PASS" && e.entry.type === "automated")) {
    return {
      verdict: "passed",
      toolVersion: maxRec.toolVersion,
      nodeCount: nodeEntries.length,
      finishedAt: maxRec.finishedAt,
      passingNodeCount: nodeEntries.length,
      failFindings: [],
      actionableFailCount: 0,
      acceptedFailCount: 0,
      acceptedScopes,
    }
  }

  return {
    verdict: "not_evaluated",
    reason: "benchmark_malformed",
    detail: "unexpected kube-bench result state",
    toolVersion: maxRec.toolVersion,
    nodeCount: nodeEntries.length,
    finishedAt: maxRec.finishedAt,
    passingNodeCount: 0,
    failFindings: [],
    actionableFailCount: 0,
    acceptedFailCount: 0,
    acceptedScopes,
  }
}

const TRIVY_SPECIFIC_NON_INFRA_REASONS = new Set<ControlNotEvaluatedReason>([
  "excluded_namespace",
  "unsupported_kind",
  "unlinked_check_id",
  "not_in_catalogue",
  "no_report_in_scope_namespace",
  "scanner_disabled",
])

function mergeAcceptedScope(
  trivyAccepted: boolean | undefined,
  trivyScope: AcceptedScope | undefined,
  benchScopes: ReadonlySet<AcceptedClass>,
): AcceptedScope | undefined {
  const classes = new Set<string>()
  if (trivyAccepted && trivyScope) classes.add(trivyScope)
  for (const s of benchScopes) classes.add(s)
  if (classes.size === 0) return undefined
  if (classes.size === 1) return classes.values().next().value as AcceptedScope
  return "mixed"
}

/**
 * Merges a Trivy-evaluated CIS control with `kube-bench` test evidence under failure-first
 * precedence. Never lets a kube-bench PASS hide a Trivy failure or vice versa.
 */
export function mergeCisControlWithBenchmark(
  control: ComplianceControl,
  store: BenchmarkStore,
): ComplianceControl {
  const bench = evaluateBenchmarkControl(control.id, store)
  if (!bench) {
    return control
  }

  const trivyState: ControlEvaluationState = control.evaluationState ?? (control.failCount > 0 ? "failed" : "not_evaluated")
  const hasMetadata = bench.toolVersion.length > 0 && bench.finishedAt.length > 0

  const makeEvidence = (verdict: ControlEvaluationState, includeDisagreement: boolean): BenchmarkControlEvidence | undefined => {
    if (!hasMetadata && bench.failFindings.length === 0) return undefined
    const disagreement =
      includeDisagreement &&
      ((trivyState === "failed" && verdict === "passed") || (trivyState === "passed" && verdict === "failed"))
        ? { trivyVerdict: trivyState, benchmarkVerdict: verdict }
        : undefined
    return {
      tool: BENCHMARK_EXPECTED_TOOL,
      toolVersion: bench.toolVersion || "unknown",
      benchmarkId: BENCHMARK_EXPECTED_ID,
      nodeCount: bench.nodeCount,
      finishedAt: bench.finishedAt || "",
      verdict,
      ...(disagreement ? { sourceDisagreement: disagreement } : {}),
      ...(bench.failFindings.length > 0 ? { failFindings: bench.failFindings } : {}),
    }
  }

  // 1. Failure first across sources
  if (trivyState === "failed" || bench.verdict === "failed") {
    const trivyFail = control.failCount ?? 0
    const trivyActionable = control.actionableFailCount ?? trivyFail
    const trivyAccepted = control.acceptedFailCount ?? 0
    const totalFail = trivyFail + bench.failFindings.length
    const totalActionable = trivyActionable + bench.actionableFailCount
    const totalAccepted = trivyAccepted + bench.acceptedFailCount
    const accepted = totalFail > 0 && totalActionable === 0 && totalAccepted > 0
    const evidence = makeEvidence(bench.verdict, true)

    return {
      ...control,
      passCount: 0,
      failCount: totalFail,
      actionableFailCount: totalActionable,
      acceptedFailCount: totalAccepted,
      evaluationState: "failed",
      notEvaluatedReason: undefined,
      notEvaluatedDetail: undefined,
      accepted,
      ...(accepted ? { acceptedScope: mergeAcceptedScope(control.accepted, control.acceptedScope, bench.acceptedScopes) } : { acceptedScope: undefined }),
      ...(bench.verdict === "failed" ? { testedByBenchmark: true } : {}),
      ...(evidence ? { benchmarkEvidence: evidence } : {}),
    }
  }

  // 2. Benchmark passed (all expected nodes fresh & complete with PASS, zero Trivy failures)
  if (bench.verdict === "passed") {
    const evidence = makeEvidence("passed", false)
    return {
      ...control,
      passCount: bench.passingNodeCount,
      failCount: 0,
      actionableFailCount: 0,
      acceptedFailCount: 0,
      droppedFailCount: control.droppedFailCount ?? 0,
      evaluationState: "passed",
      testedByBenchmark: true,
      notEvaluatedReason: undefined,
      notEvaluatedDetail: undefined,
      accepted: false,
      ...(evidence ? { benchmarkEvidence: evidence } : {}),
    }
  }

  // 3. Trivy passed (no failure recorded on ConfigAudit/RBAC) and benchmark did not pass/fail (e.g. section 5 WARN/manual)
  if (trivyState === "passed") {
    const evidence = makeEvidence("not_evaluated", false)
    return {
      ...control,
      testedByBenchmark: false,
      ...(evidence ? { benchmarkEvidence: evidence } : {}),
    }
  }

  // 4. Both Trivy and kube-bench are not_evaluated
  const trivyReason = control.notEvaluatedReason
  const keepTrivyReason =
    trivyReason !== undefined &&
    TRIVY_SPECIFIC_NON_INFRA_REASONS.has(trivyReason) &&
    bench.reason === "manual"

  const finalReason = keepTrivyReason ? trivyReason : (bench.reason ?? trivyReason ?? "coverage_unknown")
  const finalDetail = keepTrivyReason
    ? control.notEvaluatedDetail
    : (bench.detail ?? control.notEvaluatedDetail)
  const evidence = makeEvidence("not_evaluated", false)

  return {
    ...control,
    evaluationState: "not_evaluated",
    notEvaluatedReason: finalReason,
    ...(finalDetail ? { notEvaluatedDetail: finalDetail } : {}),
    ...(evidence ? { benchmarkEvidence: evidence } : {}),
  }
}
