import "server-only"
import { matchRbacException } from "./compliance-accepted"
import { fetchErrorOutcome, makeOutcome, type KisaCheckOutcome } from "./kisa-checks"
import { parseImageRefStrict } from "./oss-updates/version"
import type {
  KisaFetchResult,
  KisaRawAlertmanagerReceiver,
  KisaRawAlertmanagerRoute,
  KisaRawClusterPolicy,
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

function extractVerifyImageRefs(policy: KisaRawClusterPolicy | undefined): string[] {
  if (!policy) return []
  const refs: string[] = []
  for (const rule of policy.spec?.rules ?? []) {
    for (const vi of rule.verifyImages ?? []) {
      for (const ref of vi.imageReferences ?? []) {
        if (typeof ref === "string" && ref.length > 0) refs.push(ref)
      }
    }
  }
  return refs
}

const FAILED_IMAGE_PREFIX_RE = /failed to verify image\s+(\S+)/

function extractFailedImageRef(message: string): string | null {
  const m = FAILED_IMAGE_PREFIX_RE.exec(message)
  if (!m) return null
  const candidate = m[1].endsWith(":") ? m[1].slice(0, -1) : m[1]
  return parseImageRefStrict(candidate) ? candidate : null
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
  const latestPol = policies.find((p) => p.metadata?.name === "disallow-latest-tag")
  const verifyPol = policies.find((p) => p.metadata?.name === "verify-image-signatures")
  const latestAction = latestPol?.spec?.validationFailureAction ?? "Missing"
  const verifyAction = verifyPol?.spec?.validationFailureAction ?? "Missing"
  const verifyImageRefs = extractVerifyImageRefs(verifyPol)

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

  if (repRes.status !== "ok") {
    const partialReasonKo = repRes.status === "forbidden" ? "PolicyReport 조회 권한 없음" : `PolicyReport 조회 불가(${repRes.status})`
    const partialReasonEn = repRes.status === "forbidden" ? "PolicyReports read forbidden" : `PolicyReports unavailable (${repRes.status})`
    const invSuffixKo = failedInvSources.length > 0 ? ` · 조회 불가 소스: ${failedInvSources.join(", ")}` : ""
    const invSuffixEn = failedInvSources.length > 0 ? `; unavailable source(s): ${failedInvSources.join(", ")}` : ""
    const failLatest = latestOffenders.length > 0 || latestAction !== "Enforce"
    const partialSources = [`policyreports(${repRes.status})`, ...failedInvSources]
    return makeOutcome(
      failLatest ? "fail" : "warn",
      failLatest ? "compliance.kisa.reason.imgViolations" : "compliance.kisa.reason.imgPolicyReportsNotEvaluated",
      `:latest ${latestOffenders.length}/${uniqueImages.size}건 · disallow-latest-tag=${latestAction} · verify-image-signatures=${verifyAction} · ${partialReasonKo}${invSuffixKo}`,
      `:latest ${latestOffenders.length}/${uniqueImages.size}; disallow-latest-tag=${latestAction}; verify-image-signatures=${verifyAction}; ${partialReasonEn}${invSuffixEn}`,
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
      if (pol !== "disallow-latest-tag" && pol !== "verify-image-signatures") continue
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
        if (pol === "verify-image-signatures" && verifyImageRefs.length > 0) {
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

  const baseKo = `:latest ${latestOffenders.length}/${uniqueImages.size}건 · disallow-latest-tag=${latestAction} · verify-image-signatures=${verifyAction} · 현재 위반 ${currentViolations}건${exclKo}${partialNoteKo}`
  const baseEn = `:latest ${latestOffenders.length}/${uniqueImages.size}; disallow-latest-tag=${latestAction}; verify-image-signatures=${verifyAction}; current violations ${currentViolations}${exclEn}${partialNoteEn}`

  if (latestOffenders.length > 0 || latestAction !== "Enforce" || currentViolations > 0) {
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.imgViolations",
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

  if (verifyAction !== "Enforce") {
    const scopeNoteKo = verifyImageRefs.length > 0 ? ` [범위: ${verifyImageRefs.join(", ")}]` : ""
    const scopeNoteEn = verifyImageRefs.length > 0 ? ` [scope: ${verifyImageRefs.join(", ")}]` : ""
    return makeOutcome("warn", "compliance.kisa.reason.imgVerifyAudit", `${baseKo}${scopeNoteKo}`, `${baseEn}${scopeNoteEn}`)
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
// A 'parent/*' query is used internally by evaluateRbacRules to detect any concrete
// 'parent/<key>' grant (e.g. 'userextras/scopes', '*', or '*/scopes').
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
        (res.startsWith(`${parent}/`) && res.length > parent.length + 1 && res !== `${parent}/*`) ||
        (res.startsWith("*/") && res.length > 2 && res !== "*/*"),
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
