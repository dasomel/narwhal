import "server-only"
import { matchRbacException } from "./compliance-accepted"
import { fetchErrorOutcome, makeOutcome, type KisaCheckOutcome } from "./kisa-checks"
import type {
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
  if (!image || image.includes("@sha256:") || image.includes("@sha512:")) return false
  const lastSlash = image.lastIndexOf("/")
  const tail = lastSlash >= 0 ? image.slice(lastSlash + 1) : image
  const colonIdx = tail.indexOf(":")
  if (colonIdx === -1) return true
  const tag = tail.slice(colonIdx + 1)
  return tag === "" || tag === "latest"
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

const FAILED_IMAGE_RE = /failed to verify image ([^\s:]+(?::[^\s:]+)?(?:@sha\d+:[a-fA-F0-9]+)?):/

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

  if (repRes.status !== "ok") {
    const partialReasonKo = repRes.status === "forbidden" ? "PolicyReport 조회 권한 없음" : `PolicyReport 조회 불가(${repRes.status})`
    const partialReasonEn = repRes.status === "forbidden" ? "PolicyReports read forbidden" : `PolicyReports unavailable (${repRes.status})`
    const failLatest = latestOffenders.length > 0 || latestAction !== "Enforce"
    return makeOutcome(
      failLatest ? "fail" : "warn",
      failLatest ? "compliance.kisa.reason.imgViolations" : "compliance.kisa.reason.imgPolicyReportsNotEvaluated",
      `:latest ${latestOffenders.length}/${uniqueImages.size}건 · disallow-latest-tag=${latestAction} · verify-image-signatures=${verifyAction} · ${partialReasonKo}`,
      `:latest ${latestOffenders.length}/${uniqueImages.size}; disallow-latest-tag=${latestAction}; verify-image-signatures=${verifyAction}; ${partialReasonEn}`,
      { partial: true },
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
          const m = FAILED_IMAGE_RE.exec(r.message ?? "")
          if (m && !verifyImageRefs.some((pat) => globMatchesImage(pat, m[1]))) {
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

  const baseKo = `:latest ${latestOffenders.length}/${uniqueImages.size}건 · disallow-latest-tag=${latestAction} · verify-image-signatures=${verifyAction} · 현재 위반 ${currentViolations}건${exclKo}`
  const baseEn = `:latest ${latestOffenders.length}/${uniqueImages.size}; disallow-latest-tag=${latestAction}; verify-image-signatures=${verifyAction}; current violations ${currentViolations}${exclEn}`

  if (latestOffenders.length > 0 || latestAction !== "Enforce" || currentViolations > 0) {
    return makeOutcome("fail", "compliance.kisa.reason.imgViolations", baseKo, baseEn)
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

const WRITE_VERBS = new Set(["create", "update", "patch", "delete", "deletecollection"])
const WORKLOAD_RESOURCES = new Set(["pods", "pods/exec", "deployments", "daemonsets", "statefulsets", "replicasets", "jobs", "cronjobs", "replicationcontrollers"])
const RBAC_RESOURCES = new Set(["roles", "clusterroles", "rolebindings", "clusterrolebindings"])
const NET_RESOURCES = new Set(["services", "endpoints", "endpointslices", "ingresses", "networkpolicies"])
const WEBHOOK_RESOURCES = new Set(["mutatingwebhookconfigurations", "validatingwebhookconfigurations"])

export interface RbacRuleEvaluation {
  criticalWildcard: boolean
  escalation: boolean
  writeAccess: boolean
  secretsAccess: boolean
  anyWildcard: boolean
  triggeredCheckIds: string[]
}

export function evaluateRbacRules(rules: ReadonlyArray<{ apiGroups?: string[]; resources?: string[]; verbs?: string[] }>): RbacRuleEvaluation {
  let criticalWildcard = false
  let escalation = false
  let writeAccess = false
  let secretsAccess = false
  let anyWildcard = false
  const checkIds = new Set<string>()

  for (const r of rules) {
    const apiGroups = r.apiGroups ?? []
    const resources = r.resources ?? []
    const verbs = r.verbs ?? []
    const verbsLower = verbs.map((v) => v.toLowerCase())
    const resourcesLower = resources.map((res) => res.toLowerCase())

    const hasWildcardVerb = verbs.includes("*")
    const hasWildcardResource = resources.includes("*")
    const hasCoreOrWildcardGroup = apiGroups.includes("*") || apiGroups.includes("")

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
      if (apiGroups.includes("*")) checkIds.add("KSV044")
    }

    if ((resourcesLower.includes("secrets") || hasWildcardResource) && hasCoreOrWildcardGroup) {
      secretsAccess = true
      checkIds.add("KSV041")
    }

    const hasWrite = hasWildcardVerb || verbsLower.some((v) => WRITE_VERBS.has(v))
    if (hasWrite) {
      writeAccess = true
      if (resourcesLower.some((res) => WORKLOAD_RESOURCES.has(res))) checkIds.add("KSV048")
      if (resourcesLower.some((res) => RBAC_RESOURCES.has(res))) checkIds.add("KSV049")
      if (resourcesLower.some((res) => NET_RESOURCES.has(res))) checkIds.add("KSV056")
      if (resourcesLower.some((res) => WEBHOOK_RESOURCES.has(res))) checkIds.add("KSV114")
    }

    if (verbsLower.includes("bind") || verbsLower.includes("escalate")) {
      escalation = true
      checkIds.add("KSV050")
    }
    if (verbsLower.includes("impersonate") || resourcesLower.includes("impersonate")) {
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
