import "server-only"
import { matchRbacException } from "./compliance-accepted"
import { fetchErrorOutcome, makeOutcome, type KisaCheckOutcome } from "./kisa-checks"
import { parseImageRefStrict } from "./oss-updates/version"
import type {
  KisaFetchResult,
  KisaRawAlertmanagerReceiver,
  KisaRawAlertmanagerRoute,
  KisaRawClusterPolicy,
  KisaRawClusterPolicyRule,
  KisaRawClusterPolicyVerifyImage,
  KisaRawPodStatus,
  KisaRawReplicaSet,
} from "./k8s-client"

// --- KISA-OBS-01: AlertmanagerConfig notification channels + Falco/falcosidekick ---

const RECEIVER_CONFIG_KEYS: Array<keyof KisaRawAlertmanagerReceiver> = [
  "slackConfigs",
  "emailConfigs",
  "webhookConfigs",
  "pagerdutyConfigs",
  "opsgenieConfigs",
  "msteamsConfigs",
  "wechatConfigs",
  "victoropsConfigs",
  "pushoverConfigs",
  "snsConfigs",
  "telegramConfigs",
  "webexConfigs",
  "discordConfigs",
  "jiraConfigs",
]

function collectRoutedReceiverNames(route: KisaRawAlertmanagerRoute | undefined, out: Set<string>): void {
  if (!route) return
  if (typeof route.receiver === "string" && route.receiver.length > 0) out.add(route.receiver)
  for (const child of route.routes ?? []) {
    collectRoutedReceiverNames(child, out)
  }
}

function isPodRunningAndReady(pod: KisaRawPodStatus): boolean {
  if (pod.metadata?.deletionTimestamp) return false
  if (pod.status?.phase !== "Running") return false
  const cs = pod.status?.containerStatuses ?? []
  if (cs.length > 0) return cs.every((c) => c.ready === true)
  return (pod.status?.conditions ?? []).some((c) => c.type === "Ready" && c.status === "True")
}

export async function checkObs01(): Promise<KisaCheckOutcome> {
  const { getKisaNotificationConfig, getKisaNamespacedPods } = await import("./k8s-client")
  const [cfgRes, podsRes] = await Promise.all([
    getKisaNotificationConfig(),
    getKisaNamespacedPods("security-system"),
  ])

  const falcoOk = podsRes.status === "ok"
  let falcoReady = 0
  let sidekickReady = 0
  if (podsRes.status === "ok") {
    for (const p of podsRes.data) {
      if (!isPodRunningAndReady(p)) continue
      const name = p.metadata?.name ?? ""
      const appName = p.metadata?.labels?.["app.kubernetes.io/name"] ?? p.metadata?.labels?.["app"] ?? ""
      if (appName === "falcosidekick" || name.includes("falcosidekick")) {
        sidekickReady++
      } else if (appName === "falco" || name.startsWith("falco-")) {
        falcoReady++
      }
    }
  }

  const falcoSummaryKo = falcoOk
    ? `Falco ${falcoReady} Ready, falcosidekick ${sidekickReady} Ready`
    : `Falco/falcosidekick 파드 미평가(${podsRes.status})`
  const falcoSummaryEn = falcoOk
    ? `Falco ${falcoReady} Ready, falcosidekick ${sidekickReady} Ready`
    : `Falco/falcosidekick pods not evaluated (${podsRes.status})`

  if (cfgRes.status === "not-found") {
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.obsConfigMissing",
      `AlertmanagerConfig(monitoring/narwhal-notification-channels) 없음 · ${falcoSummaryKo}`,
      `AlertmanagerConfig monitoring/narwhal-notification-channels not found; ${falcoSummaryEn}`,
      { partial: !falcoOk },
    )
  }
  if (cfgRes.status !== "ok") {
    return fetchErrorOutcome(cfgRes, "AlertmanagerConfig(monitoring/narwhal-notification-channels)", "AlertmanagerConfig monitoring/narwhal-notification-channels")
  }

  const receivers = cfgRes.data.spec?.receivers ?? []
  const routed = new Set<string>()
  collectRoutedReceiverNames(cfgRes.data.spec?.route, routed)

  const configuredActive = receivers
    .filter((r) => {
      const name = r.name ?? ""
      if (!name || name === "null") return false
      const hasExternalConfig = RECEIVER_CONFIG_KEYS.some((k) => Array.isArray(r[k]) && (r[k] as unknown[]).length > 0)
      return hasExternalConfig && routed.has(name)
    })
    .map((r) => r.name!)

  if (configuredActive.length === 0) {
    const bothDown = falcoOk && (falcoReady === 0 || sidekickReady === 0)
    return makeOutcome(
      bothDown ? "fail" : "warn",
      "compliance.kisa.reason.obsNoChannel",
      `알림 채널 미설정(null 수신자만 존재) · ${falcoSummaryKo}`,
      `No notification channel configured (null receiver only); ${falcoSummaryEn}`,
      { partial: !falcoOk },
    )
  }

  if (!falcoOk) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.apiUnavailable",
      `알림 수신자 ${configuredActive.length}건 활성(${configuredActive.join(", ")}) · ${falcoSummaryKo}`,
      `${configuredActive.length} notification receiver(s) active (${configuredActive.join(", ")}); ${falcoSummaryEn}`,
      { partial: true },
    )
  }

  if (falcoReady === 0 || sidekickReady === 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.obsFalcoDown",
      `알림 수신자 ${configuredActive.length}건 활성(${configuredActive.join(", ")}) · 런타임 탐지 미준비(${falcoSummaryKo})`,
      `${configuredActive.length} notification receiver(s) active (${configuredActive.join(", ")}); runtime detection not ready (${falcoSummaryEn})`,
    )
  }

  return makeOutcome(
    "pass",
    "compliance.kisa.reason.obsConfigured",
    `알림 수신자 ${configuredActive.length}건 활성(${configuredActive.join(", ")}) · ${falcoSummaryKo}`,
    `${configuredActive.length} notification receiver(s) active (${configuredActive.join(", ")}); ${falcoSummaryEn}`,
  )
}

// --- KISA-IMG-02: Image tag / signature policy + current PolicyReport violations ---

function isLatestOrUntagged(image: string): boolean {
  const parsed = parseImageRefStrict(image)
  if (!parsed) return true
  if (parsed.digest) return false
  return parsed.rawTag === null || parsed.rawTag === "latest"
}

function globMatchesImage(pattern: string, image: string): boolean {
  const escaped = pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*")
  return new RegExp(`^${escaped}$`).test(image)
}

function buildSupersededReplicaSetKeys(rss: KisaRawReplicaSet[]): Set<string> {
  const maxRev = new Map<string, number>()
  const entries = rss
    .map((rs) => {
      const ns = rs.metadata?.namespace ?? ""
      const name = rs.metadata?.name ?? ""
      const revRaw = rs.metadata?.annotations?.["deployment.kubernetes.io/revision"]
      const rev = revRaw && /^\d+$/.test(revRaw) ? Number(revRaw) : null
      const owners = (rs.metadata?.ownerReferences ?? [])
        .filter((o) => o.kind === "Deployment" && typeof o.name === "string" && o.name.length > 0)
        .map((o) => o.name!)
      if (rev !== null) {
        for (const owner of owners) {
          const k = `${ns}/${owner}`
          const prev = maxRev.get(k)
          if (prev === undefined || rev > prev) maxRev.set(k, rev)
        }
      }
      return {
        key: `${ns}/${name}`,
        ns,
        owners,
        rev,
        specReplicas: typeof rs.spec?.replicas === "number" ? rs.spec.replicas : null,
        statusReplicas: typeof rs.status?.replicas === "number" ? rs.status.replicas : null,
        terminatingReplicas: typeof rs.status?.terminatingReplicas === "number" ? rs.status.terminatingReplicas : null,
      }
    })
    .filter((e) => e.ns && e.key !== "/")

  const superseded = new Set<string>()
  for (const e of entries) {
    const hasHigher = e.rev !== null && e.owners.some((o) => (maxRev.get(`${e.ns}/${o}`) ?? -1) > e.rev!)
    if (
      e.owners.length > 0 &&
      e.specReplicas === 0 &&
      e.statusReplicas === 0 &&
      e.terminatingReplicas === 0 &&
      hasHigher
    ) {
      superseded.add(e.key)
    }
  }
  return superseded
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function normalizePolicyAction(raw: unknown): "Enforce" | "Audit" | null {
  if (typeof raw !== "string") return null
  const lower = raw.trim().toLowerCase()
  if (lower === "enforce") return "Enforce"
  if (lower === "audit") return "Audit"
  return null
}

function actionFromValidationActions(actions: unknown): "Enforce" | "Audit" | null {
  if (!Array.isArray(actions) || actions.length === 0) return null
  const normalized = actions
    .filter((a): a is string => typeof a === "string")
    .map((a) => a.trim().toLowerCase())
  if (normalized.length === 0) return null
  return normalized.some((a) => a === "deny" || a === "enforce") ? "Enforce" : "Audit"
}

function hasAuditOverride(overrides: unknown): boolean {
  if (!Array.isArray(overrides)) return false
  return overrides.some((o) => isRecord(o) && normalizePolicyAction(o.action) === "Audit")
}

// Honour Kyverno 1.18 per-rule/validate/verifyImages action fields (failureAction,
// validationFailureAction, validationActions) and failureActionOverrides/validationFailureActionOverrides.
// Policy-level Enforce with any Audit override or rule-level Audit is NOT Enforce.
function resolveRuleEffectiveAction(
  policy: KisaRawClusterPolicy,
  rule: KisaRawClusterPolicyRule,
  entry?: {
    failureAction?: string
    validationFailureAction?: string
    validationActions?: string[]
    failureActionOverrides?: Array<{ action?: string }>
  },
): "Enforce" | "Audit" {
  const baseAction =
    normalizePolicyAction(entry?.failureAction) ??
    normalizePolicyAction(entry?.validationFailureAction) ??
    actionFromValidationActions(entry?.validationActions) ??
    normalizePolicyAction(rule.failureAction) ??
    normalizePolicyAction(rule.validationFailureAction) ??
    actionFromValidationActions(rule.validationActions) ??
    normalizePolicyAction(policy.spec?.validationFailureAction) ??
    normalizePolicyAction(policy.spec?.failureAction) ??
    "Audit"

  if (baseAction !== "Enforce") return "Audit"
  if (hasAuditOverride(entry?.failureActionOverrides) || hasAuditOverride(policy.spec?.validationFailureActionOverrides)) {
    return "Audit"
  }
  return "Enforce"
}

function isPodKind(kind: unknown): boolean {
  if (typeof kind !== "string") return false
  const lower = kind.trim().toLowerCase()
  return lower === "pod" || lower === "v1/pod" || lower === "*"
}

function matchBlockTargetsPod(block: unknown): boolean {
  if (!isRecord(block)) return false
  const res = block.resources
  if (isRecord(res) && Array.isArray(res.kinds) && res.kinds.some(isPodKind)) return true
  for (const branchKey of ["any", "all"] as const) {
    const branches = block[branchKey]
    if (Array.isArray(branches) && branches.some(matchBlockTargetsPod)) return true
  }
  return false
}

function excludeBlockUnconditionallyExcludesPods(block: unknown): boolean {
  if (!isRecord(block)) return false
  const res = block.resources
  if (isRecord(res) && Array.isArray(res.kinds) && res.kinds.some(isPodKind)) {
    const hasNarrowing =
      (Array.isArray(res.namespaces) && res.namespaces.length > 0) ||
      (Array.isArray(res.names) && res.names.length > 0) ||
      isRecord(res.selector) ||
      isRecord(res.namespaceSelector)
    if (!hasNarrowing) return true
  }
  for (const branchKey of ["any", "all"] as const) {
    const branches = block[branchKey]
    if (Array.isArray(branches) && branches.some(excludeBlockUnconditionallyExcludesPods)) return true
  }
  return false
}

export function ruleMatchesPod(rule: KisaRawClusterPolicyRule): boolean {
  if (!matchBlockTargetsPod(rule.match)) return false
  if (excludeBlockUnconditionallyExcludesPods(rule.exclude)) return false
  return true
}

function imagePatternRejectsLatest(pattern: unknown): boolean {
  if (typeof pattern !== "string") return false
  const branches = pattern.split("|").map((s) => s.trim()).filter(Boolean)
  if (branches.length === 0) return false
  return branches.every((branch) =>
    branch
      .split("&")
      .map((c) => c.trim())
      .some((clause) => /^![^|&]*:latest\*?$/i.test(clause) || /^[^!|&]*@sha256:\*$/i.test(clause)),
  )
}

function patternNodeChecksContainersImage(node: unknown): boolean {
  if (!isRecord(node)) return false
  const spec = node.spec
  if (!isRecord(spec)) return false
  const containers = spec.containers ?? spec["=(containers)"]
  if (!Array.isArray(containers) || containers.length === 0) return false
  return containers.some((c) => isRecord(c) && imagePatternRejectsLatest(c.image))
}

function collectDenyConditions(node: unknown, out: Array<Record<string, unknown>>): void {
  if (Array.isArray(node)) {
    for (const item of node) collectDenyConditions(item, out)
    return
  }
  if (!isRecord(node)) return
  if ("key" in node || "operator" in node || "value" in node) {
    out.push(node)
    return
  }
  if ("conditions" in node) collectDenyConditions(node.conditions, out)
  if ("any" in node) collectDenyConditions(node.any, out)
  if ("all" in node) collectDenyConditions(node.all, out)
}

function denyConditionsRejectLatest(denyObj: unknown, insideContainerForeach: boolean): boolean {
  const conds: Array<Record<string, unknown>> = []
  collectDenyConditions(denyObj, conds)
  return conds.some((c) => {
    const keyStr = typeof c.key === "string" ? c.key : ""
    const valStr = JSON.stringify(c.value ?? "")
    const refsImage = insideContainerForeach
      ? /\b(?:element(?:\.\w+)*\.image|image)\b/i.test(keyStr)
      : /containers/i.test(keyStr) && /image/i.test(keyStr)
    if (!refsImage) return false
    return /:latest\b/i.test(keyStr) || /:latest\b/i.test(valStr) || /!contains\([^)]*,\s*['"]:['"]\)/i.test(keyStr)
  })
}

function celRejectsLatest(celObj: unknown, insideContainerForeach: boolean): boolean {
  if (!isRecord(celObj) || !Array.isArray(celObj.expressions)) return false
  return celObj.expressions.some((item) => {
    if (!isRecord(item) || typeof item.expression !== "string") return false
    const expr = item.expression
    const refsImage = insideContainerForeach
      ? /\bimage\b/i.test(expr)
      : /containers/i.test(expr) && /image/i.test(expr)
    return refsImage && /:latest/i.test(expr) && (/!/.test(expr) || /endsWith/i.test(expr))
  })
}

// Accepted Pod :latest/untagged rejection rule shapes:
// 1. validate.pattern (repo gitops/resources/kyverno-policies.yaml shape: spec.containers[].image = "!*:latest")
// 2. validate.anyPattern (every branch enforces containers[].image rejecting :latest)
// 3. validate.deny (conditions referencing containers.*.image and :latest or missing tag)
// 4. validate.foreach over spec.containers / images.containers with pattern, deny, or cel checking :latest
// 5. validate.cel expressions referencing containers + image + :latest
export function ruleHasLatestTagValidation(rule: KisaRawClusterPolicyRule): boolean {
  if (!ruleMatchesPod(rule) || !isRecord(rule.validate)) return false
  const v = rule.validate
  if (patternNodeChecksContainersImage(v.pattern)) return true
  if (Array.isArray(v.anyPattern) && v.anyPattern.length > 0 && v.anyPattern.every(patternNodeChecksContainersImage)) {
    return true
  }
  if (denyConditionsRejectLatest(v.deny, false)) return true
  if (Array.isArray(v.foreach)) {
    const hasForeachMatch = v.foreach.some((fe) => {
      if (!isRecord(fe)) return false
      if (typeof fe.list !== "string" || !/containers/i.test(fe.list)) return false
      if (isRecord(fe.pattern) && imagePatternRejectsLatest(fe.pattern.image)) return true
      if (denyConditionsRejectLatest(fe.deny, true)) return true
      if (celRejectsLatest(fe.cel, true)) return true
      return false
    })
    if (hasForeachMatch) return true
  }
  if (celRejectsLatest(v.cel, false)) return true
  return false
}

function hasValidAttestorEntry(entry: unknown): boolean {
  if (!isRecord(entry)) return false
  if (isRecord(entry.keys)) {
    if (typeof entry.keys.publicKeys === "string" && entry.keys.publicKeys.trim().length > 0) return true
    if (typeof entry.keys.kms === "string" && entry.keys.kms.trim().length > 0) return true
    if (isRecord(entry.keys.secret) && typeof entry.keys.secret.name === "string" && entry.keys.secret.name.trim().length > 0) return true
  }
  if (isRecord(entry.keyless)) return true
  if (isRecord(entry.certificates)) {
    if (typeof entry.certificates.cert === "string" && entry.certificates.cert.trim().length > 0) return true
    if (typeof entry.certificates.certChain === "string" && entry.certificates.certChain.trim().length > 0) return true
    if (typeof entry.certificates.rootCert === "string" && entry.certificates.rootCert.trim().length > 0) return true
  }
  return false
}

function isValidVerifyImageEntry(vi: KisaRawClusterPolicyVerifyImage): boolean {
  const refs = (vi.imageReferences ?? []).filter((r): r is string => typeof r === "string" && r.trim().length > 0)
  if (refs.length === 0) return false
  if (typeof vi.key === "string" && vi.key.trim().length > 0) return true
  if (!Array.isArray(vi.attestors)) return false
  return vi.attestors.some((att) => isRecord(att) && Array.isArray(att.entries) && att.entries.some(hasValidAttestorEntry))
}

function isGlobalImageScopePattern(pattern: string): boolean {
  return pattern === "*" || pattern === "*:*" || pattern === "*/*" || pattern === "*/*:*"
}

const FAILED_IMAGE_PREFIX_RE = /failed to verify image\s+(\S+)/

function extractFailedImageRef(message: string): string | null {
  const m = FAILED_IMAGE_PREFIX_RE.exec(message)
  if (!m) return null
  const candidate = m[1].endsWith(":") ? m[1].slice(0, -1) : m[1]
  return parseImageRefStrict(candidate) ? candidate : null
}

interface ImgLatestPolicyEval {
  status: "enforced" | "audit" | "invalid-rule" | "missing"
  actionLabel: string
  policyNames: Set<string>
  invalidReasonKo?: string
  invalidReasonEn?: string
}

interface ImgVerifyPolicyEval {
  status: "enforced" | "audit" | "invalid-rule" | "missing"
  actionLabel: string
  requiredFlag: "true" | "false" | "default" | "unknown"
  imageRefs: string[]
  policyNames: Set<string>
  invalidReasonKo?: string
  invalidReasonEn?: string
}

function evaluateLatestPolicies(policies: KisaRawClusterPolicy[]): ImgLatestPolicyEval {
  const candidates: Array<{ name: string; action: "Enforce" | "Audit" }> = []
  for (const pol of policies) {
    const matchingRules = (pol.spec?.rules ?? []).filter(ruleHasLatestTagValidation)
    if (matchingRules.length === 0) continue
    const action: "Enforce" | "Audit" = matchingRules.every((rule) =>
      resolveRuleEffectiveAction(pol, rule, rule.validate) === "Enforce",
    )
      ? "Enforce"
      : "Audit"
    candidates.push({ name: pol.metadata?.name ?? "unnamed", action })
  }

  if (candidates.length > 0) {
    const best = candidates.find((c) => c.action === "Enforce") ?? candidates[0]
    return {
      status: best.action === "Enforce" ? "enforced" : "audit",
      actionLabel: `${best.name}=${best.action}`,
      policyNames: new Set(candidates.map((c) => c.name)),
    }
  }

  const named = policies.find((p) => p.metadata?.name === "disallow-latest-tag")
  if (named) {
    return {
      status: "invalid-rule",
      actionLabel: "disallow-latest-tag=Invalid",
      policyNames: new Set(["disallow-latest-tag"]),
      invalidReasonKo: "policy disallow-latest-tag 존재하나 Pod :latest 차단 validate 규칙 없음",
      invalidReasonEn: "policy disallow-latest-tag present but does not contain a Pod :latest/untagged validate rule",
    }
  }

  return {
    status: "missing",
    actionLabel: "disallow-latest-tag=Missing",
    policyNames: new Set(["disallow-latest-tag"]),
  }
}

function evaluateVerifyPolicies(policies: KisaRawClusterPolicy[]): ImgVerifyPolicyEval {
  const candidates: Array<{
    name: string
    action: "Enforce" | "Audit"
    requiredFlag: "true" | "false" | "default"
    imageRefs: string[]
  }> = []

  for (const pol of policies) {
    const matchedEntries: Array<{
      action: "Enforce" | "Audit"
      required: boolean | undefined
      refs: string[]
    }> = []

    for (const rule of pol.spec?.rules ?? []) {
      if (!ruleMatchesPod(rule)) continue
      for (const vi of rule.verifyImages ?? []) {
        if (!isValidVerifyImageEntry(vi)) continue
        const refs = (vi.imageReferences ?? []).filter((r): r is string => typeof r === "string" && r.trim().length > 0)
        matchedEntries.push({
          action: resolveRuleEffectiveAction(pol, rule, vi),
          required: vi.required,
          refs,
        })
      }
    }

    if (matchedEntries.length === 0) continue
    const action: "Enforce" | "Audit" = matchedEntries.every((e) => e.action === "Enforce") ? "Enforce" : "Audit"
    const requiredFlag: "true" | "false" | "default" = matchedEntries.some((e) => e.required === false)
      ? "false"
      : matchedEntries.every((e) => e.required === true)
        ? "true"
        : "default"
    const imageRefs = [...new Set(matchedEntries.flatMap((e) => e.refs))]
    candidates.push({ name: pol.metadata?.name ?? "unnamed", action, requiredFlag, imageRefs })
  }

  if (candidates.length > 0) {
    const best =
      candidates.find((c) => c.action === "Enforce" && c.requiredFlag !== "false") ??
      candidates.find((c) => c.action === "Enforce") ??
      candidates[0]
    return {
      status: best.action === "Enforce" ? "enforced" : "audit",
      actionLabel: `${best.name}=${best.action}(required=${best.requiredFlag})`,
      requiredFlag: best.requiredFlag,
      imageRefs: best.imageRefs,
      policyNames: new Set(candidates.map((c) => c.name)),
    }
  }

  const named = policies.find((p) => p.metadata?.name === "verify-image-signatures")
  if (named) {
    return {
      status: "invalid-rule",
      actionLabel: "verify-image-signatures=Invalid",
      requiredFlag: "unknown",
      imageRefs: [],
      policyNames: new Set(["verify-image-signatures"]),
      invalidReasonKo: "policy verify-image-signatures 존재하나 imageReferences·attestors/keys 포함 verifyImages 규칙 없음",
      invalidReasonEn: "policy verify-image-signatures present but does not contain a verifyImages rule with imageReferences and attestors/keys",
    }
  }

  return {
    status: "missing",
    actionLabel: "verify-image-signatures=Missing",
    requiredFlag: "unknown",
    imageRefs: [],
    policyNames: new Set(["verify-image-signatures"]),
  }
}

export async function checkImg02(): Promise<KisaCheckOutcome> {
  const { getKisaKyvernoClusterPolicies, getKisaPolicyReports, getKisaWorkloadInventory } = await import("./k8s-client")
  const [polRes, repRes, inv] = await Promise.all([
    getKisaKyvernoClusterPolicies(),
    getKisaPolicyReports(),
    getKisaWorkloadInventory(),
  ])

  if (polRes.status !== "ok") {
    return fetchErrorOutcome(polRes, "Kyverno ClusterPolicy", "Kyverno ClusterPolicies")
  }
  if (inv.pods.status !== "ok") {
    return fetchErrorOutcome(inv.pods, "클러스터 Pod 목록", "Cluster Pod list")
  }

  const policies = polRes.data
  const latestEval = evaluateLatestPolicies(policies)
  const verifyEval = evaluateVerifyPolicies(policies)
  const verifyImageRefs = verifyEval.imageRefs

  const uniqueImages = new Set<string>()
  const latestOffenders: string[] = []
  for (const pod of inv.pods.data) {
    if (pod.metadata?.deletionTimestamp) continue
    const phase = pod.status?.phase
    if (phase !== "Running" && phase !== "Pending") continue
    const ns = pod.metadata?.namespace ?? ""
    const name = pod.metadata?.name ?? ""
    for (const c of [...(pod.spec?.containers ?? []), ...(pod.spec?.initContainers ?? [])]) {
      const img = c.image ?? ""
      if (!img) continue
      uniqueImages.add(img)
      if (isLatestOrUntagged(img)) {
        latestOffenders.push(`${ns}/${name}(${img})`)
      }
    }
  }

  const uncoveredImagesCount = verifyImageRefs.length > 0
    ? [...uniqueImages].filter((img) => !verifyImageRefs.some((pat) => globMatchesImage(pat, img))).length
    : uniqueImages.size
  const fullVerifyScope = uniqueImages.size > 0
    ? uncoveredImagesCount === 0
    : verifyImageRefs.some(isGlobalImageScopePattern)

  const invalidNotesKo = [latestEval.invalidReasonKo, verifyEval.invalidReasonKo].filter((s): s is string => Boolean(s))
  const invalidNotesEn = [latestEval.invalidReasonEn, verifyEval.invalidReasonEn].filter((s): s is string => Boolean(s))
  const invalidSuffixKo = invalidNotesKo.length > 0 ? ` · ${invalidNotesKo.join(" · ")}` : ""
  const invalidSuffixEn = invalidNotesEn.length > 0 ? `; ${invalidNotesEn.join("; ")}` : ""
  const scopeNoteKo = verifyImageRefs.length > 0
    ? ` [범위: ${verifyImageRefs.join(", ")}${uncoveredImagesCount > 0 ? ` · 미적용 이미지 ${uncoveredImagesCount}/${uniqueImages.size}건` : ""}]`
    : ""
  const scopeNoteEn = verifyImageRefs.length > 0
    ? ` [scope: ${verifyImageRefs.join(", ")}${uncoveredImagesCount > 0 ? `; uncovered images ${uncoveredImagesCount}/${uniqueImages.size}` : ""}]`
    : ""

  const invChecks: Array<[string, KisaFetchResult<unknown>]> = [
    ["replicasets", inv.replicasets],
    ["deployments", inv.deployments],
    ["statefulsets", inv.statefulsets],
    ["daemonsets", inv.daemonsets],
    ["cronjobs", inv.cronjobs],
  ]
  const failedInvSources = invChecks
    .filter(([, res]) => res.status !== "ok")
    .map(([label, res]) => `${label}(${res.status})`)

  const hasInvalidRule = latestEval.status === "invalid-rule" || verifyEval.status === "invalid-rule"

  if (repRes.status !== "ok") {
    const partialReasonKo = repRes.status === "forbidden" ? "PolicyReport 조회 권한 없음" : `PolicyReport 조회 불가(${repRes.status})`
    const partialReasonEn = repRes.status === "forbidden" ? "PolicyReports read forbidden" : `PolicyReports unavailable (${repRes.status})`
    const invSuffixKo = failedInvSources.length > 0 ? ` · 조회 불가 소스: ${failedInvSources.join(", ")}` : ""
    const invSuffixEn = failedInvSources.length > 0 ? `; unavailable source(s): ${failedInvSources.join(", ")}` : ""
    const failEarly = latestOffenders.length > 0 || latestEval.status !== "enforced" || verifyEval.status === "invalid-rule"
    const partialSources = [`policyreports(${repRes.status})`, ...failedInvSources]
    const reasonKey = failEarly
      ? (hasInvalidRule && latestOffenders.length === 0 ? "compliance.kisa.reason.imgPolicyInvalidRule" : "compliance.kisa.reason.imgViolations")
      : "compliance.kisa.reason.imgPolicyReportsNotEvaluated"
    return makeOutcome(
      failEarly ? "fail" : "warn",
      reasonKey,
      `:latest ${latestOffenders.length}/${uniqueImages.size}건 · ${latestEval.actionLabel} · ${verifyEval.actionLabel} · ${partialReasonKo}${invalidSuffixKo}${scopeNoteKo}${invSuffixKo}`,
      `:latest ${latestOffenders.length}/${uniqueImages.size}; ${latestEval.actionLabel}; ${verifyEval.actionLabel}; ${partialReasonEn}${invalidSuffixEn}${scopeNoteEn}${invSuffixEn}`,
      { partial: true, partialSources },
    )
  }

  const podKeys = new Set(inv.pods.data.map((p) => `${p.metadata?.namespace ?? ""}/${p.metadata?.name ?? ""}`))
  const rsKeys = inv.replicasets.status === "ok"
    ? new Set(inv.replicasets.data.map((r) => `${r.metadata?.namespace ?? ""}/${r.metadata?.name ?? ""}`))
    : null
  const supersededRs = inv.replicasets.status === "ok"
    ? buildSupersededReplicaSetKeys(inv.replicasets.data)
    : new Set<string>()
  const depKeys = inv.deployments.status === "ok" ? new Set(inv.deployments.data.map((d) => `${d.metadata?.namespace ?? ""}/${d.metadata?.name ?? ""}`)) : null
  const stsKeys = inv.statefulsets.status === "ok" ? new Set(inv.statefulsets.data.map((s) => `${s.metadata?.namespace ?? ""}/${s.metadata?.name ?? ""}`)) : null
  const dsKeys = inv.daemonsets.status === "ok" ? new Set(inv.daemonsets.data.map((d) => `${d.metadata?.namespace ?? ""}/${d.metadata?.name ?? ""}`)) : null
  const cjKeys = inv.cronjobs.status === "ok" ? new Set(inv.cronjobs.data.map((c) => `${c.metadata?.namespace ?? ""}/${c.metadata?.name ?? ""}`)) : null

  const lookupByKind: Record<string, Set<string> | null> = {
    Pod: podKeys,
    ReplicaSet: rsKeys,
    Deployment: depKeys,
    StatefulSet: stsKeys,
    DaemonSet: dsKeys,
    CronJob: cjKeys,
  }

  let currentViolations = 0
  let excludedMissingOrZeroRs = 0
  let excludedOutOfScope = 0

  for (const pr of repRes.data) {
    const prNs = pr.metadata?.namespace ?? ""
    for (const r of pr.results ?? []) {
      const pol = r.policy ?? ""
      const isLatestPol = latestEval.policyNames.has(pol)
      const isVerifyPol = verifyEval.policyNames.has(pol)
      if (!isLatestPol && !isVerifyPol) continue
      if (r.result !== "fail" && r.result !== "error") continue

      const targets = (r.resources && r.resources.length > 0 ? r.resources : pr.scope ? [pr.scope] : [])
      if (targets.length === 0) {
        currentViolations++
        continue
      }

      for (const t of targets) {
        const kind = t.kind ?? ""
        const tNs = t.namespace ?? prNs
        const tName = t.name ?? ""
        const key = `${tNs}/${tName}`
        const kindSet = kind in lookupByKind ? lookupByKind[kind] : null

        if (kindSet !== null && !kindSet.has(key)) {
          excludedMissingOrZeroRs++
          continue
        }
        if (kind === "ReplicaSet" && supersededRs.has(key)) {
          excludedMissingOrZeroRs++
          continue
        }
        if (isVerifyPol && verifyImageRefs.length > 0) {
          const parsedImg = extractFailedImageRef(r.message ?? "")
          if (parsedImg !== null && !verifyImageRefs.some((pat) => globMatchesImage(pat, parsedImg))) {
            excludedOutOfScope++
            continue
          }
        }
        currentViolations++
      }
    }
  }

  const totalExcluded = excludedMissingOrZeroRs + excludedOutOfScope
  const exclKo = totalExcluded > 0
    ? ` (제외 ${totalExcluded}건: 삭제/0-replica RS ${excludedMissingOrZeroRs}건, 구 정책 범위 외 잔재 ${excludedOutOfScope}건)`
    : ""
  const exclEn = totalExcluded > 0
    ? ` (excluded ${totalExcluded}: ${excludedMissingOrZeroRs} deleted/0-replica RS, ${excludedOutOfScope} stale out-of-scope policy remnants)`
    : ""
  const partialNoteKo = failedInvSources.length > 0 ? ` · 조회 불가 소스: ${failedInvSources.join(", ")}` : ""
  const partialNoteEn = failedInvSources.length > 0 ? `; unavailable source(s): ${failedInvSources.join(", ")}` : ""

  const baseKo = `:latest ${latestOffenders.length}/${uniqueImages.size}건 · ${latestEval.actionLabel} · ${verifyEval.actionLabel} · 현재 위반 ${currentViolations}건${exclKo}${invalidSuffixKo}${scopeNoteKo}${partialNoteKo}`
  const baseEn = `:latest ${latestOffenders.length}/${uniqueImages.size}; ${latestEval.actionLabel}; ${verifyEval.actionLabel}; current violations ${currentViolations}${exclEn}${invalidSuffixEn}${scopeNoteEn}${partialNoteEn}`

  if (latestOffenders.length > 0 || latestEval.status !== "enforced" || currentViolations > 0 || verifyEval.status === "invalid-rule") {
    const reasonKey = hasInvalidRule && latestOffenders.length === 0 && currentViolations === 0
      ? "compliance.kisa.reason.imgPolicyInvalidRule"
      : "compliance.kisa.reason.imgViolations"
    return makeOutcome(
      "fail",
      reasonKey,
      baseKo,
      baseEn,
      failedInvSources.length > 0 ? { partial: true, partialSources: failedInvSources } : undefined,
    )
  }

  if (failedInvSources.length > 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.imgInventoryNotEvaluated",
      baseKo,
      baseEn,
      { partial: true, partialSources: failedInvSources },
    )
  }

  if (verifyEval.status !== "enforced" || verifyEval.requiredFlag === "false" || !fullVerifyScope) {
    return makeOutcome("warn", "compliance.kisa.reason.imgVerifyAudit", baseKo, baseEn)
  }

  return makeOutcome("pass", "compliance.kisa.reason.imgPolicyEnforced", baseKo, baseEn)
}

// --- KISA-RBAC-01 & KISA-RBAC-02: single-rule apiGroups check + exact acceptance ---

function isUnavoidableRbacBuiltin(b: { name: string; subjects: Array<{ kind: string; name: string }> }): boolean {
  if (b.name === "cluster-admin") return true
  if (b.name.startsWith("system:") || b.name.startsWith("kubeadm:")) return true
  if (
    b.subjects.length > 0 &&
    b.subjects.every((s) => s.kind === "Group" && (s.name === "system:masters" || s.name.startsWith("system:")))
  ) {
    return true
  }
  return false
}

export interface RbacPolicyRule {
  apiGroups?: string[]
  resources?: string[]
  verbs?: string[]
  resourceNames?: string[]
  nonResourceURLs?: string[]
}

const WRITE_VERBS = ["create", "update", "patch", "delete", "deletecollection"] as const
const WRITE_VERB_SET = new Set<string>(WRITE_VERBS)
const SECRET_ACCESS_VERBS = ["get", "list", "watch", ...WRITE_VERBS] as const
// Upstream pkg/registry/rbac/escalation_check.go: bind/escalate apply only to roles/clusterroles.
const RBAC_ESCALATION_RESOURCES = ["roles", "clusterroles"] as const
const RBAC_WRITE_RESOURCES = ["roles", "clusterroles", "rolebindings", "clusterrolebindings"] as const
const CORE_WORKLOAD_RESOURCES = ["pods", "pods/exec", "replicationcontrollers"] as const
const APPS_WORKLOAD_RESOURCES = ["deployments", "daemonsets", "statefulsets", "replicasets"] as const
const BATCH_WORKLOAD_RESOURCES = ["jobs", "cronjobs"] as const
const CORE_NET_RESOURCES = ["services", "endpoints"] as const
const NETWORKING_NET_RESOURCES = ["ingresses", "networkpolicies"] as const
const WEBHOOK_RESOURCES = ["mutatingwebhookconfigurations", "validatingwebhookconfigurations"] as const
const CORE_IMPERSONATE_RESOURCES = ["users", "groups", "serviceaccounts"] as const

// Evaluates Kubernetes RBAC PolicyRule matching within a single rule:
// (apiGroup in apiGroups or '*') && (verb in verbs or '*') && (resource matches resources).
// Concrete subresources follow Kubernetes pkg/apis/rbac/v1/evaluation_helpers.go ResourceMatches
// (only '*', exact 'resource/subresource', or '*/subresource' — never 'resource/*').
// A 'parent/*' query is used for both the literal '*' extra key (upstream impersonation.go allows
// impersonating key '*' via 'userextras/*' or '*/*') and to detect any concrete 'parent/<key>'
// grant (e.g. 'userextras/scopes', 'userextras/*', '*', '*/*', or '*/scopes').
// resourceNames is intentionally not exempted (fail-closed: a named privileged target
// such as 'system:admin' or 'cluster-admin' still confers the dangerous capability).
export function ruleGrants(
  rule: RbacPolicyRule,
  apiGroup: string,
  resource: string,
  verb: string,
): boolean {
  const targetGroup = apiGroup.toLowerCase()
  const targetResource = resource.toLowerCase()
  const targetVerb = verb.toLowerCase()

  const groups = (rule.apiGroups ?? []).map((g) => g.toLowerCase())
  if (!groups.includes("*") && !groups.includes(targetGroup)) return false

  const verbs = (rule.verbs ?? []).map((v) => v.toLowerCase())
  if (!verbs.includes("*") && !verbs.includes(targetVerb)) return false

  const resources = (rule.resources ?? []).map((res) => res.toLowerCase())
  const slashIdx = targetResource.indexOf("/")
  if (slashIdx === -1) {
    return resources.some((res) => res === "*" || res === targetResource)
  }

  const parent = targetResource.slice(0, slashIdx)
  const sub = targetResource.slice(slashIdx + 1)
  if (sub === "*") {
    return resources.some(
      (res) =>
        res === "*" ||
        (res.startsWith(`${parent}/`) && res.length > parent.length + 1) ||
        (res.startsWith("*/") && res.length > 2),
    )
  }

  return resources.some(
    (res) =>
      res === "*" ||
      res === targetResource ||
      res === `*/${sub}`,
  )
}

export interface RbacRuleEvaluation {
  criticalWildcard: boolean
  escalation: boolean
  writeAccess: boolean
  secretsAccess: boolean
  anyWildcard: boolean
  triggeredCheckIds: string[]
}

function grantsAnyVerb(
  rule: RbacPolicyRule,
  apiGroup: string,
  resources: readonly string[],
  verbs: readonly string[],
): boolean {
  return resources.some((res) => verbs.some((v) => ruleGrants(rule, apiGroup, res, v)))
}

export function evaluateRbacRules(rules: ReadonlyArray<RbacPolicyRule>): RbacRuleEvaluation {
  let criticalWildcard = false
  let escalation = false
  let writeAccess = false
  let secretsAccess = false
  let anyWildcard = false
  const checkIds = new Set<string>()

  for (const r of rules) {
    const apiGroupsLower = (r.apiGroups ?? []).map((g) => g.toLowerCase())
    const resourcesLower = (r.resources ?? []).map((res) => res.toLowerCase())
    const verbsLower = (r.verbs ?? []).map((v) => v.toLowerCase())

    // Rules without both apiGroups and resources (e.g. nonResourceURLs-only rules such as
    // { nonResourceURLs: ["/healthz"], verbs: ["*"] }) match zero Kubernetes API resources
    // and must not feed resource-based categories (anyWildcard, writeAccess, escalation).
    if (apiGroupsLower.length === 0 || resourcesLower.length === 0) continue

    const hasWildcardGroup = apiGroupsLower.includes("*")
    const hasWildcardVerb = verbsLower.includes("*")
    const hasWildcardResource = resourcesLower.includes("*")
    const hasCoreOrWildcardGroup = hasWildcardGroup || apiGroupsLower.includes("")

    if (hasWildcardVerb || hasWildcardResource) {
      anyWildcard = true
      if (hasWildcardVerb || hasCoreOrWildcardGroup) {
        checkIds.add("KSV046")
      }
    }
    // Single-rule wildcard check: must combine *, *, and core/wildcard apiGroup inside the SAME rule.
    if (hasWildcardVerb && hasWildcardResource && hasCoreOrWildcardGroup) {
      criticalWildcard = true
      checkIds.add("KSV046")
      if (hasWildcardGroup) checkIds.add("KSV044")
    }

    if (grantsAnyVerb(r, "", ["secrets"], SECRET_ACCESS_VERBS)) {
      secretsAccess = true
      checkIds.add("KSV041")
    }

    if (hasWildcardVerb || verbsLower.some((v) => WRITE_VERB_SET.has(v))) {
      writeAccess = true
      if (
        grantsAnyVerb(r, "", CORE_WORKLOAD_RESOURCES, WRITE_VERBS) ||
        grantsAnyVerb(r, "apps", APPS_WORKLOAD_RESOURCES, WRITE_VERBS) ||
        grantsAnyVerb(r, "batch", BATCH_WORKLOAD_RESOURCES, WRITE_VERBS)
      ) {
        checkIds.add("KSV048")
      }
      if (grantsAnyVerb(r, "rbac.authorization.k8s.io", RBAC_WRITE_RESOURCES, WRITE_VERBS)) {
        checkIds.add("KSV049")
      }
      if (
        grantsAnyVerb(r, "", CORE_NET_RESOURCES, WRITE_VERBS) ||
        grantsAnyVerb(r, "discovery.k8s.io", ["endpointslices"], WRITE_VERBS) ||
        grantsAnyVerb(r, "networking.k8s.io", NETWORKING_NET_RESOURCES, WRITE_VERBS)
      ) {
        checkIds.add("KSV056")
      }
      if (grantsAnyVerb(r, "admissionregistration.k8s.io", WEBHOOK_RESOURCES, WRITE_VERBS)) {
        checkIds.add("KSV114")
      }
    }

    if (grantsAnyVerb(r, "rbac.authorization.k8s.io", RBAC_ESCALATION_RESOURCES, ["bind", "escalate"])) {
      escalation = true
      checkIds.add("KSV050")
    }

    const impersonatesCore = grantsAnyVerb(r, "", CORE_IMPERSONATE_RESOURCES, ["impersonate"])
    const impersonatesAuth = grantsAnyVerb(r, "authentication.k8s.io", ["uids", "userextras/*"], ["impersonate"])
    if (impersonatesCore || impersonatesAuth) {
      escalation = true
      checkIds.add("KSV053")
    }
  }

  return {
    criticalWildcard,
    escalation,
    writeAccess,
    secretsAccess,
    anyWildcard,
    triggeredCheckIds: [...checkIds],
  }
}

function isRoleAcceptedByExactRules(
  kind: string,
  namespace: string,
  name: string,
  triggeredCheckIds: string[],
): boolean {
  if (triggeredCheckIds.length === 0) return false
  const roleNs = kind === "ClusterRole" ? "" : namespace
  return triggeredCheckIds.every((cid) => matchRbacException(name, cid, roleNs, kind) !== null)
}

export async function checkRbacLive(id: "KISA-RBAC-01" | "KISA-RBAC-02"): Promise<KisaCheckOutcome> {
  const { getKisaRbacSnapshot } = await import("./k8s-client")
  const snap = await getKisaRbacSnapshot()

  if (snap.clusterRoleBindings.status !== "ok") return fetchErrorOutcome(snap.clusterRoleBindings, "ClusterRoleBinding", "ClusterRoleBindings")
  if (snap.roleBindings.status !== "ok") return fetchErrorOutcome(snap.roleBindings, "RoleBinding", "RoleBindings")
  if (snap.clusterRoles.status !== "ok") return fetchErrorOutcome(snap.clusterRoles, "ClusterRole", "ClusterRoles")
  if (snap.roles.status !== "ok") return fetchErrorOutcome(snap.roles, "Role", "Roles")

  const bindings = [
    ...snap.clusterRoleBindings.data.map((i) => ({
      name: i.metadata.name,
      namespace: null as string | null,
      scope: "cluster" as const,
      roleRef: i.roleRef,
      subjects: i.subjects ?? [],
    })),
    ...snap.roleBindings.data.map((i) => ({
      name: i.metadata.name,
      namespace: i.metadata.namespace,
      scope: "namespace" as const,
      roleRef: i.roleRef,
      subjects: i.subjects ?? [],
    })),
  ]

  const nonBuiltin = bindings.filter((b) => !isUnavoidableRbacBuiltin(b))

  if (id === "KISA-RBAC-01") {
    const clusterAdminBindings = nonBuiltin.filter((b) => b.roleRef.name === "cluster-admin")
    if (clusterAdminBindings.length > 0) {
      const names = clusterAdminBindings.map((b) => b.name).join(", ")
      return makeOutcome(
        "fail",
        "compliance.kisa.reason.rbacCriticalBindings",
        `불필요한 cluster-admin 바인딩 ${clusterAdminBindings.length}건 (${names})`,
        `${clusterAdminBindings.length} non-builtin cluster-admin binding(s) (${names})`,
      )
    }
    return makeOutcome(
      "pass",
      "compliance.kisa.reason.rbacClean",
      "built-in 외 cluster-admin 바인딩 없음",
      "No non-builtin cluster-admin bindings",
    )
  }

  const clusterRolesMap = new Map(snap.clusterRoles.data.map((r) => [r.metadata.name, r.rules ?? []]))
  const rolesMap = new Map<string, Map<string, typeof snap.roles.data[number]["rules"]>>()
  for (const r of snap.roles.data) {
    let nsMap = rolesMap.get(r.metadata.namespace)
    if (!nsMap) {
      nsMap = new Map()
      rolesMap.set(r.metadata.namespace, nsMap)
    }
    nsMap.set(r.metadata.name, r.rules ?? [])
  }

  const remaining = nonBuiltin.filter((b) => b.roleRef.name !== "cluster-admin")
  const criticalActionable: string[] = []
  let criticalAccepted = 0
  let highActionable = 0
  let highAccepted = 0

  for (const b of remaining) {
    const rules = b.roleRef.kind === "ClusterRole"
      ? clusterRolesMap.get(b.roleRef.name) ?? []
      : (b.namespace ? rolesMap.get(b.namespace)?.get(b.roleRef.name) ?? [] : [])

    const ev = evaluateRbacRules(rules)
    const isCritical = ev.criticalWildcard || ev.escalation
    const isHigh = !isCritical && b.scope === "cluster" && (ev.writeAccess || ev.secretsAccess || ev.anyWildcard)
    if (!isCritical && !isHigh) continue

    const accepted = isRoleAcceptedByExactRules(b.roleRef.kind, b.namespace ?? "", b.roleRef.name, ev.triggeredCheckIds)
    if (isCritical) {
      if (accepted) criticalAccepted++
      else criticalActionable.push(b.namespace ? `${b.namespace}/${b.name}` : b.name)
    } else if (isHigh) {
      if (accepted) highAccepted++
      else highActionable++
    }
  }

  const accTotal = criticalAccepted + highAccepted
  const accSuffixKo = accTotal > 0 ? ` (정확 수용 ${accTotal}건)` : ""
  const accSuffixEn = accTotal > 0 ? ` (${accTotal} exact-accepted)` : ""

  if (criticalActionable.length > 0) {
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.rbacCriticalBindings",
      `조치가능 위험 바인딩 Critical ${criticalActionable.length}건 (${criticalActionable.join(", ")}) · High ${highActionable}건${accSuffixKo}`,
      `${criticalActionable.length} actionable Critical binding(s) (${criticalActionable.join(", ")}); ${highActionable} actionable High${accSuffixEn}`,
    )
  }
  if (highActionable > 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.rbacHighBindings",
      `조치가능 위험 바인딩 High ${highActionable}건 (Critical 0건)${accSuffixKo}`,
      `${highActionable} actionable High risk binding(s) (0 Critical)${accSuffixEn}`,
    )
  }
  return makeOutcome(
    "pass",
    "compliance.kisa.reason.rbacClean",
    `조치가능 위험 바인딩 없음${accSuffixKo}`,
    `No actionable risky bindings${accSuffixEn}`,
  )
}
