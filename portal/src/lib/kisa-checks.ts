import "server-only"
import policyBaselineData from "@/data/kisa-policy-baseline.json"
import { computeNetworkPolicyDigest } from "./kisa-policy-canonical.mjs"
import type { Localized } from "./i18n-utils"
import type { KisaReasonKey, KisaStatus } from "@/types/kisa"
import type {
  KisaFetchResult,
  KisaRawCiliumClusterwideNetworkPolicy,
  KisaRawCiliumNetworkPolicy,
  KisaRawDeployment,
  KisaRawNetworkPolicy,
  KisaRawPeerAuthentication,
} from "./k8s-client"
import type { KisaPolicyBaseline } from "./kisa-checks-ops"

export interface KisaCheckOutcome {
  status: KisaStatus
  live: boolean
  detail: string
  detailLocalized: Localized
  reasonKey: KisaReasonKey
  partial?: boolean
  partialSources?: string[]
}

export function makeOutcome(
  status: KisaStatus,
  reasonKey: KisaReasonKey,
  ko: string,
  en: string,
  opts?: { live?: boolean; partial?: boolean; partialSources?: string[] },
): KisaCheckOutcome {
  return {
    status,
    live: opts?.live ?? status !== "manual",
    detail: ko,
    detailLocalized: { ko, en },
    reasonKey,
    partial: opts?.partial,
    ...(opts?.partialSources && opts.partialSources.length > 0 ? { partialSources: opts.partialSources } : {}),
  }
}

export function fetchErrorOutcome(
  res: Exclude<KisaFetchResult<unknown>, { status: "ok" }>,
  labelKo: string,
  labelEn: string,
): KisaCheckOutcome {
  if (res.status === "forbidden") {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.apiForbidden",
      `${labelKo} 조회 권한 없음 (${res.resource}) — 평가 불가`,
      `${labelEn} read forbidden (${res.resource}) — not evaluated`,
      { live: false, partial: true, partialSources: [res.resource] },
    )
  }
  if (res.status === "truncated") {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.listTruncated",
      `${labelKo} 목록이 잘림 (${res.resource}) — 전체 평가 불가`,
      `${labelEn} list truncated (${res.resource}) — full evaluation unavailable`,
      { live: false, partial: true, partialSources: [res.resource] },
    )
  }
  if (res.status === "not-found") {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.resourceMissing",
      `${labelKo} 리소스를 찾을 수 없음`,
      `${labelEn} resource not found`,
      { live: false },
    )
  }
  return makeOutcome(
    "manual",
    "compliance.kisa.reason.apiUnavailable",
    `${labelKo} 조회 실패 (${res.resource}) — 평가 불가`,
    `${labelEn} read failed (${res.resource}) — not evaluated`,
    { live: false, partial: true, partialSources: [res.resource] },
  )
}

// --- KISA-ETCD-02: APISIX etcd isolation + client TLS/auth ---

function extractFlagValues(tokens: string[], flag: string): string[] {
  const vals: string[] = []
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    if (t === flag && i + 1 < tokens.length) {
      const next = tokens[i + 1].trim()
      if (next.length > 0 && !next.startsWith("-")) {
        vals.push(next)
      }
    } else if (t.startsWith(`${flag}=`)) {
      const val = t.slice(flag.length + 1).trim()
      if (val.length > 0) {
        vals.push(val)
      }
    }
  }
  return vals
}

function hasEnabledFlag(tokens: string[], flag: string): boolean {
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    if (t === flag) {
      const next = tokens[i + 1]
      if (next && !next.startsWith("-") && (next === "false" || next === "0")) return false
      return true
    }
    if (t.startsWith(`${flag}=`)) {
      const val = t.slice(flag.length + 1).toLowerCase()
      return val !== "false" && val !== "0" && val !== ""
    }
  }
  return false
}

export interface K8sLabelSelector {
  matchLabels?: Record<string, string>
  matchExpressions?: Array<{ key: string; operator: string; values?: string[] }>
}

const hasOwn = (obj: Record<string, string>, key: string): boolean =>
  Object.prototype.hasOwnProperty.call(obj, key)

/**
 * Evaluates exact Kubernetes `metav1.LabelSelector` semantics against a Pod's label map.
 * Matches `k8s.io/apimachinery/pkg/apis/meta/v1.LabelSelectorAsSelector`:
 * `null`/`undefined` selects nothing (`false`), `{}` selects everything (`true`),
 * and unknown operators fail closed (`false`).
 */
export function matchesLabelSelector(
  selector: K8sLabelSelector | null | undefined,
  labels: Record<string, string> | null | undefined,
): boolean {
  const safeLabels = labels && typeof labels === "object" && !Array.isArray(labels) ? labels : {}
  if (!selector || typeof selector !== "object" || Array.isArray(selector)) return false
  for (const [k, v] of Object.entries(selector.matchLabels ?? {})) {
    if (!hasOwn(safeLabels, k) || safeLabels[k] !== v) return false
  }
  for (const expr of selector.matchExpressions ?? []) {
    if (!expr || typeof expr.key !== "string" || typeof expr.operator !== "string") return false
    const present = hasOwn(safeLabels, expr.key)
    const val = present ? safeLabels[expr.key] : undefined
    const vals = Array.isArray(expr.values) ? expr.values : []
    if (expr.operator === "In") {
      if (!present || vals.length === 0 || !vals.includes(val!)) return false
    } else if (expr.operator === "NotIn") {
      if (vals.length === 0) return false
      if (present && vals.includes(val!)) return false
    } else if (expr.operator === "Exists") {
      if (vals.length > 0 || !present) return false
    } else if (expr.operator === "DoesNotExist") {
      if (vals.length > 0 || present) return false
    } else {
      return false
    }
  }
  return true
}

export function evaluateClusterwideNetworkPolicies(
  ccnps: KisaRawCiliumClusterwideNetworkPolicy[],
  baseline: KisaPolicyBaseline,
): string[] {
  const ccnpBaseline = baseline.clusterwidePolicies ?? {}
  const unreviewed: string[] = []
  for (const ccnp of ccnps) {
    const name = ccnp.metadata?.name ?? "unknown"
    const key = `CiliumClusterwideNetworkPolicy/${name}`
    const entry = ccnpBaseline[key]
    const digest = computeNetworkPolicyDigest("CiliumClusterwideNetworkPolicy", ccnp)
    if (!entry) {
      unreviewed.push(key)
    } else if (digest !== entry.specSha256) {
      unreviewed.push(`${key}(differs)`)
    }
  }
  return unreviewed.sort()
}

// D-KISA-ETCD02-NO-PASS: ETCD-02 has no pass path yet (same closed-world stance as SEC-01).
// Reason: CLI TLS flags cannot prove runtime protection (--config-file overrides CLI flags,
// ETCD_* env vars can override defaults, and Cilium implicit labels can bypass selector inference),
// and there is no reviewed known-good shape for a TLS-enabled apisix-etcd workload today (plain HTTP).
// Cost: even if TLS flags are manually added to apisix-etcd, ETCD-02 stays "warn" with observed facts.
// Escape hatch: add the reviewed TLS apisix-etcd workload spec digest to kisa-policy-baseline.json
// together with etcd TLS enablement and gate the pass path on canonical workload spec digest equality.
export async function checkEtcd02(
  baseline: KisaPolicyBaseline = policyBaselineData as KisaPolicyBaseline,
): Promise<KisaCheckOutcome> {
  const etcdPolicyKey = baseline.etcd02PolicyKey ?? "NetworkPolicy/platform-system/apisix-etcd-ingress-policy"
  const etcdBaselineEntry = baseline.networkPolicies?.[etcdPolicyKey]
  const etcdWorkload = baseline.etcd02Workload
  if (!etcdBaselineEntry || !etcdWorkload) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.resourceMissing",
      `기준선 정책(${etcdPolicyKey}) 또는 대상 워크로드 정의 없음 — 평가 불가`,
      `Baseline policy (${etcdPolicyKey}) or target workload not defined — not evaluated`,
      { live: false },
    )
  }

  const {
    getKisaDeployment,
    getKisaNamespacedNetworkPolicies,
    getKisaNamespacedCiliumNetworkPolicies,
    getKisaCiliumClusterwideNetworkPolicies,
  } = await import("./k8s-client")
  const [depRes, npRes, cnpRes, ccnpRes] = await Promise.all([
    getKisaDeployment(etcdWorkload.namespace, etcdWorkload.name),
    getKisaNamespacedNetworkPolicies(etcdWorkload.namespace),
    getKisaNamespacedCiliumNetworkPolicies(etcdWorkload.namespace),
    getKisaCiliumClusterwideNetworkPolicies(),
  ])

  if (depRes.status !== "ok") return fetchErrorOutcome(depRes, `Deployment ${etcdWorkload.namespace}/${etcdWorkload.name}`, `Deployment ${etcdWorkload.namespace}/${etcdWorkload.name}`)
  if (npRes.status !== "ok") return fetchErrorOutcome(npRes, `${etcdWorkload.namespace} NetworkPolicy`, `${etcdWorkload.namespace} NetworkPolicies`)
  if (cnpRes.status !== "ok") return fetchErrorOutcome(cnpRes, `${etcdWorkload.namespace} CiliumNetworkPolicy`, `${etcdWorkload.namespace} CiliumNetworkPolicies`)
  if (ccnpRes.status !== "ok") return fetchErrorOutcome(ccnpRes, "CiliumClusterwideNetworkPolicy", "CiliumClusterwideNetworkPolicies")

  const dep: KisaRawDeployment = depRes.data
  const podLabels = dep.spec?.template?.metadata?.labels ?? {}
  const containers = dep.spec?.template?.spec?.containers ?? []
  // D-KISA-CLOSED-WORLD: inspect ONLY the exact "etcd" container defined in
  // gitops/charts/narwhal-platform/templates/apisix-infra.yaml; never merge args across
  // containers (a sidecar's TLS flags must never satisfy ETCD-02).
  const etcdContainer = containers.find((c) => c.name === "etcd")
  if (!etcdContainer) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.etcdContainerMissing",
      `Deployment ${etcdWorkload.namespace}/${etcdWorkload.name}에 'etcd' 컨테이너가 없음 — 평가 불가`,
      `Container 'etcd' not found in Deployment ${etcdWorkload.namespace}/${etcdWorkload.name} — not evaluated`,
      { live: false },
    )
  }
  const tokens = [...(etcdContainer.command ?? []), ...(etcdContainer.args ?? [])]

  const configFiles = extractFlagValues(tokens, "--config-file")
  const hasConfigFileFlag =
    configFiles.length > 0 || tokens.some((t) => t === "--config-file" || t.startsWith("--config-file="))
  const clientUrls = extractFlagValues(tokens, "--listen-client-urls")
    .flatMap((v) => v.split(","))
    .map((v) => v.trim())
    .filter(Boolean)
  const hasHttpsClient = clientUrls.length > 0 && clientUrls.every((u) => u.startsWith("https://"))
  const hasCertFiles = extractFlagValues(tokens, "--cert-file").length > 0 && extractFlagValues(tokens, "--key-file").length > 0
  const hasClientAuth = hasEnabledFlag(tokens, "--client-cert-auth") && extractFlagValues(tokens, "--trusted-ca-file").length > 0
  const hasAnyTlsFlag = hasHttpsClient || hasCertFiles || hasClientAuth

  let observedFactsKo: string
  let observedFactsEn: string
  if (hasConfigFileFlag) {
    const cfgLabel = configFiles.join(", ") || "지정됨"
    const cfgLabelEn = configFiles.join(", ") || "present"
    observedFactsKo = `--config-file(${cfgLabel}) 관측 — 설정 파일이 CLI 인자를 덮어쓸 수 있어 플래그 미평가 (관측 URL: ${clientUrls.join(", ") || "기본값"})`
    observedFactsEn = `--config-file (${cfgLabelEn}) present — flags may be overridden by the config file; not evaluated (observed URLs: ${clientUrls.join(", ") || "default"})`
  } else if (hasAnyTlsFlag) {
    const observedFlags: string[] = []
    if (clientUrls.length > 0) observedFlags.push(`--listen-client-urls=${clientUrls.join(",")}`)
    if (hasCertFiles) observedFlags.push("--cert-file/--key-file")
    if (hasClientAuth) observedFlags.push("--client-cert-auth/--trusted-ca-file")
    observedFactsKo = `관측된 CLI 플래그(${observedFlags.join(", ")}) — 검토된 TLS 워크로드 스펙 기준선 다이제스트 없음`
    observedFactsEn = `observed CLI flags (${observedFlags.join(", ")}) — no reviewed TLS workload spec baseline digest`
  } else {
    observedFactsKo = `etcd 클라이언트 TLS·인증 미활성화 (평문 HTTP: ${clientUrls.join(", ") || "기본값"})`
    observedFactsEn = `etcd client TLS/auth not enabled (plain HTTP: ${clientUrls.join(", ") || "default"})`
  }

  // D-KISA-CLOSED-WORLD: Verify the reviewed baseline apisix-etcd-ingress-policy exists and
  // selects the running etcd workload pods, require EVERY NetworkPolicy and CiliumNetworkPolicy
  // in etcdWorkload.namespace (same rule as NET-01, no selector filtering) to match the
  // baseline canonical SHA-256 digest, and require every CiliumClusterwideNetworkPolicy to
  // match the reviewed baseline.
  const policies: KisaRawNetworkPolicy[] = npRes.data
  const ciliumPolicies: KisaRawCiliumNetworkPolicy[] = cnpRes.data
  const clusterwidePolicies: KisaRawCiliumClusterwideNetworkPolicy[] = ccnpRes.data
  const baselinePolicyObj = policies.find((p) => p.metadata?.name === etcdBaselineEntry.name)
  if (!baselinePolicyObj) {
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.etcdPolicyMissing",
      `기준선 보호 정책(${etcdBaselineEntry.name}) 없음 (${observedFactsKo})`,
      `Baseline isolation policy (${etcdBaselineEntry.name}) missing (${observedFactsEn})`,
    )
  }

  const nsPolicies: Array<{ kind: "NetworkPolicy" | "CiliumNetworkPolicy"; name: string; raw: unknown }> = [
    ...policies.map((p) => ({ kind: "NetworkPolicy" as const, name: p.metadata?.name ?? "unknown", raw: p })),
    ...ciliumPolicies.map((c) => ({ kind: "CiliumNetworkPolicy" as const, name: c.metadata?.name ?? "unknown", raw: c })),
  ]
  const unreviewedOrDrifted: string[] = []
  for (const pol of nsPolicies) {
    const key = `${pol.kind}/${etcdWorkload.namespace}/${pol.name}`
    const entry = baseline.networkPolicies?.[key]
    if (!entry) {
      unreviewedOrDrifted.push(key)
    } else if (computeNetworkPolicyDigest(pol.kind, pol.raw) !== entry.specSha256) {
      unreviewedOrDrifted.push(`${key}(differs)`)
    }
  }
  const unreviewedClusterwide = evaluateClusterwideNetworkPolicies(clusterwidePolicies, baseline)

  if (unreviewedClusterwide.length > 0 || unreviewedOrDrifted.length > 0) {
    const partsKo: string[] = []
    const partsEn: string[] = []
    if (unreviewedOrDrifted.length > 0) {
      partsKo.push(`네임스페이스(${etcdWorkload.namespace})에 미검토/불일치 정책 존재: ${unreviewedOrDrifted.join(", ")}`)
      partsEn.push(`unreviewed policy present in namespace ${etcdWorkload.namespace}: ${unreviewedOrDrifted.join(", ")}`)
    }
    if (unreviewedClusterwide.length > 0) {
      partsKo.push(`미검토 클러스터 전역 정책 존재(unreviewed cluster-wide policy present): ${unreviewedClusterwide.join(", ")}`)
      partsEn.push(`unreviewed cluster-wide policy present: ${unreviewedClusterwide.join(", ")}`)
    }
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.etcdPolicyUnreviewed",
      `${partsKo.join(" · ")} · ${observedFactsKo}`,
      `${partsEn.join("; ")}; ${observedFactsEn}`,
    )
  }

  const workloadMatchesBaseline =
    dep.metadata?.namespace === etcdWorkload.namespace &&
    dep.metadata?.name === etcdWorkload.name
  if (!workloadMatchesBaseline || !matchesLabelSelector(baselinePolicyObj.spec?.podSelector, podLabels)) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.etcdPolicySelectorMismatch",
      `검토된 정책(${etcdBaselineEntry.name})이 실행 중인 etcd 파드를 선택하지 않음 (${dep.metadata?.namespace ?? "?"}/${dep.metadata?.name ?? "?"}) · ${observedFactsKo}`,
      `reviewed policy does not select the running etcd pods (${etcdBaselineEntry.name} vs ${dep.metadata?.namespace ?? "?"}/${dep.metadata?.name ?? "?"}); ${observedFactsEn}`,
    )
  }

  const isolationNoteKo = `기준선 격리 정책(${etcdBaselineEntry.name} 및 ${etcdWorkload.namespace} 전체 NetworkPolicy·CiliumNetworkPolicy·클러스터 전역 CCNP) SHA-256·podSelector 일치`
  const isolationNoteEn = `Baseline isolation matched (all ${etcdWorkload.namespace} NetworkPolicy/CiliumNetworkPolicy and cluster-wide CCNPs match SHA-256; ${etcdBaselineEntry.name} selects running etcd pods)`

  const generation = dep.metadata?.generation
  const observedGeneration = dep.status?.observedGeneration
  const specReplicas = dep.spec?.replicas
  const updatedReplicas = dep.status?.updatedReplicas
  const readyReplicas = dep.status?.readyReplicas
  const availableReplicas = dep.status?.availableReplicas
  const statusReplicas = dep.status?.replicas

  if (
    typeof generation !== "number" ||
    typeof observedGeneration !== "number" ||
    typeof specReplicas !== "number" ||
    typeof updatedReplicas !== "number" ||
    typeof readyReplicas !== "number" ||
    typeof availableReplicas !== "number"
  ) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.etcdWorkloadNotReady",
      `${isolationNoteKo} · ${observedFactsKo} · Deployment generation/rollout 상태 필드 미확인 — 평가 불가`,
      `${isolationNoteEn}; ${observedFactsEn}; Deployment generation/rollout status fields missing — not evaluated`,
      { live: false },
    )
  }
  if (dep.spec?.paused === true) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.etcdWorkloadNotReady",
      `${isolationNoteKo} · ${observedFactsKo} · Deployment 일시중지(spec.paused=true)로 현재 템플릿 적용 미확인`,
      `${isolationNoteEn}; ${observedFactsEn}; Deployment paused (spec.paused=true) — running template unverified`,
    )
  }
  if (generation !== observedGeneration) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.etcdWorkloadNotReady",
      `${isolationNoteKo} · ${observedFactsKo} · 세대 불일치(generation ${generation} != observedGeneration ${observedGeneration})`,
      `${isolationNoteEn}; ${observedFactsEn}; rollout generation mismatch (generation ${generation} != observedGeneration ${observedGeneration})`,
    )
  }
  if (
    specReplicas < 1 ||
    updatedReplicas !== specReplicas ||
    readyReplicas !== specReplicas ||
    availableReplicas !== specReplicas ||
    (statusReplicas !== undefined && statusReplicas !== specReplicas)
  ) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.etcdWorkloadNotReady",
      `${isolationNoteKo} · ${observedFactsKo} · 워크로드 롤아웃/준비 미완료(updated ${updatedReplicas}, ready ${readyReplicas}, available ${availableReplicas}/${specReplicas})`,
      `${isolationNoteEn}; ${observedFactsEn}; workload rollout/readiness incomplete (updated ${updatedReplicas}, ready ${readyReplicas}, available ${availableReplicas}/${specReplicas})`,
    )
  }

  const reasonKey = (hasConfigFileFlag || hasAnyTlsFlag)
    ? "compliance.kisa.reason.etcdNoTlsBaseline"
    : "compliance.kisa.reason.etcdIsolatedPlainHttp"
  const passNoteKo = "etcd TLS 전환 시 검토된 워크로드 스펙의 기준선 다이제스트와 함께 pass 경로가 추가될 예정"
  const passNoteEn = "the pass path will be added together with etcd TLS as a baseline digest of the reviewed workload spec"

  return makeOutcome(
    "warn",
    reasonKey,
    `${isolationNoteKo} (gen ${generation}, updated/ready/available ${readyReplicas}/${specReplicas}) · 관측: ${observedFactsKo} · ${passNoteKo}`,
    `${isolationNoteEn} (gen ${generation}, updated/ready/available ${readyReplicas}/${specReplicas}); observed: ${observedFactsEn}; ${passNoteEn}`,
  )
}

// --- KISA-SEC-01: OpenBao unseal key isolation ---
// D-KISA-SEC01-NO-KMS-INFER: Never infer external KMS from StatefulSet env var names (actual
// seal config lives in OpenBao HCL/Secret which the portal cannot read with read-only RBAC).
// SEC-01 can never return "pass" from this code: best case is "warn" with verified CronJob /
// Secret metadata hygiene facts, and missing/suspended CronJob or unreadable objects degrade
// to "manual" (not evaluated with reason) unless an exposed Secret already proves "fail".

export async function checkSec01(): Promise<KisaCheckOutcome> {
  const { getKisaCronJob, getKisaStatefulSet, getKisaSecretMetadata } = await import("./k8s-client")
  const [cjRes, stsRes] = await Promise.all([
    getKisaCronJob("storage", "openbao-auto-unseal"),
    getKisaStatefulSet("storage", "openbao"),
  ])

  if (cjRes.status === "forbidden" || cjRes.status === "error" || cjRes.status === "truncated") {
    return fetchErrorOutcome(cjRes, "CronJob storage/openbao-auto-unseal", "CronJob storage/openbao-auto-unseal")
  }
  if (stsRes.status !== "ok") {
    return fetchErrorOutcome(stsRes, "StatefulSet storage/openbao", "StatefulSet storage/openbao")
  }

  let secretRefName = "openbao-init"
  const cjPresent = cjRes.status === "ok" && !cjRes.data.spec?.suspend
  const cjIssuesKo: string[] = []
  const cjIssuesEn: string[] = []
  let saName = ""

  if (cjRes.status === "ok") {
    const podSpec = cjRes.data.spec?.jobTemplate?.spec?.template?.spec
    saName = podSpec?.serviceAccountName ?? podSpec?.serviceAccount ?? ""
    const dedicatedSa = Boolean(saName && saName !== "default")
    const automountDisabled = podSpec?.automountServiceAccountToken === false
    const containers = [...(podSpec?.containers ?? []), ...(podSpec?.initContainers ?? [])]
    let hasSkipVerify = false
    for (const c of containers) {
      for (const e of c.env ?? []) {
        if (e.name === "BAO_SKIP_VERIFY" || e.name === "VAULT_SKIP_VERIFY") {
          const v = (e.value ?? "").trim().toLowerCase()
          if (v !== "false" && v !== "0" && v !== "") hasSkipVerify = true
        }
        if (e.valueFrom?.secretKeyRef?.name) {
          secretRefName = e.valueFrom.secretKeyRef.name
        }
      }
    }
    if (cjPresent) {
      if (!dedicatedSa) {
        cjIssuesKo.push("전용 ServiceAccount 미지정")
        cjIssuesEn.push("dedicated ServiceAccount missing")
      }
      if (!automountDisabled) {
        cjIssuesKo.push("automountServiceAccountToken!=false")
        cjIssuesEn.push("automountServiceAccountToken!=false")
      }
      if (hasSkipVerify) {
        cjIssuesKo.push("BAO_SKIP_VERIFY 활성화")
        cjIssuesEn.push("BAO_SKIP_VERIFY enabled")
      }
    }
  }

  const secRes = await getKisaSecretMetadata("storage", secretRefName)
  const secretMetaNotEvaluated = secRes.status === "forbidden" || secRes.status === "error" || secRes.status === "truncated"
  const secretPartial = secRes.status === "error" || secRes.status === "truncated"
  const secretPartialSources = secretPartial ? [`secrets/storage/${secretRefName}(${secRes.status})`] : undefined

  const secIssuesKo: string[] = []
  const secIssuesEn: string[] = []
  if (secRes.status === "ok") {
    const excludeBackup = secRes.data.labels["velero.io/exclude-from-backup"] === "true"
    const hasLastApplied = secRes.data.annotationKeys.includes("kubectl.kubernetes.io/last-applied-configuration")
    if (!excludeBackup) {
      secIssuesKo.push("velero.io/exclude-from-backup=true 라벨 누락")
      secIssuesEn.push("velero.io/exclude-from-backup=true label missing")
    }
    if (hasLastApplied) {
      secIssuesKo.push("last-applied-configuration 주석 잔존")
      secIssuesEn.push("last-applied-configuration annotation present")
    }
  }

  if (!cjPresent) {
    if (secIssuesKo.length > 0) {
      return makeOutcome(
        "fail",
        "compliance.kisa.reason.secSecretExposed",
        `Secret storage/${secretRefName} 메타데이터 취약: ${secIssuesKo.join(", ")}`,
        `Secret storage/${secretRefName} metadata issues: ${secIssuesEn.join(", ")}`,
        { partial: secretPartial, partialSources: secretPartialSources },
      )
    }
    const cjStateKo = cjRes.status === "not-found" ? "CronJob storage/openbao-auto-unseal 없음" : "CronJob storage/openbao-auto-unseal 중지(suspend=true)"
    const cjStateEn = cjRes.status === "not-found" ? "CronJob storage/openbao-auto-unseal not found" : "CronJob storage/openbao-auto-unseal suspended (suspend=true)"
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.secCronJobNotEvaluated",
      `${cjStateKo} · OpenBao seal 구성은 API로 읽을 수 없어 외부 KMS 여부 평가 불가`,
      `${cjStateEn}; OpenBao seal config is not readable via K8s API — external KMS status not evaluated`,
      { live: false, partial: secretPartial, partialSources: secretPartialSources },
    )
  }

  if (cjIssuesKo.length > 0) {
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.secCronJobWeak",
      `CronJob storage/openbao-auto-unseal 보강 미흡: ${cjIssuesKo.join(", ")}`,
      `CronJob storage/openbao-auto-unseal hardening defects: ${cjIssuesEn.join(", ")}`,
      { partial: secretPartial, partialSources: secretPartialSources },
    )
  }
  if (secRes.status === "ok") {
    if (secIssuesKo.length > 0) {
      return makeOutcome(
        "fail",
        "compliance.kisa.reason.secSecretExposed",
        `Secret storage/${secretRefName} 메타데이터 취약: ${secIssuesKo.join(", ")}`,
        `Secret storage/${secretRefName} metadata issues: ${secIssuesEn.join(", ")}`,
      )
    }
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.secNoExternalKms",
      `CronJob 보강 확인(SA=${saName}, automount=false, BAO_SKIP_VERIFY 없음) · Secret storage/${secretRefName} 메타데이터 확인(exclude-from-backup=true, last-applied 없음) · OpenBao seal 구성은 API로 검증 불가`,
      `CronJob hardened (SA=${saName}, automount=false, no BAO_SKIP_VERIFY); Secret storage/${secretRefName} metadata verified (exclude-from-backup=true, no last-applied); OpenBao seal config not observable via API`,
    )
  }
  if (secretMetaNotEvaluated) {
    const reasonKo = secRes.status === "forbidden" ? "Secret 메타데이터 미평가(권한 없음)" : "Secret 메타데이터 미평가(조회 실패)"
    const reasonEn = secRes.status === "forbidden" ? "Secret metadata not evaluated (no read permission)" : "Secret metadata not evaluated (API error)"
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.secSecretMetaNotEvaluated",
      `CronJob 보강 확인(SA=${saName}, automount=false, BAO_SKIP_VERIFY 없음) · ${reasonKo} · OpenBao seal 구성 API 검증 불가`,
      `CronJob hardened (SA=${saName}, automount=false, no BAO_SKIP_VERIFY); ${reasonEn}; OpenBao seal config not observable via API`,
      { partial: secretPartial, partialSources: secretPartialSources },
    )
  }
  return makeOutcome(
    "warn",
    "compliance.kisa.reason.secNoExternalKms",
    `CronJob 보강 확인(SA=${saName}, automount=false, BAO_SKIP_VERIFY 없음) · 참조 Secret storage/${secretRefName} 없음 · OpenBao seal 구성 API 검증 불가`,
    `CronJob hardened (SA=${saName}, automount=false, no BAO_SKIP_VERIFY); referenced Secret storage/${secretRefName} not found; OpenBao seal config not observable via API`,
  )
}

// --- KISA-IMG-01: Node containerd Harbor TLS verification (manual — host file) ---

export function checkImg01(): KisaCheckOutcome {
  // #765 (08-6b-harbor-containerd-tls-node.sh) writes /etc/containerd/certs.d/harbor.*/{ca.crt,hosts.toml}
  // on each node directly and publishes no in-cluster ConfigMap/annotation. Never invent a signal.
  return makeOutcome(
    "manual",
    "compliance.kisa.reason.imgNodeManual",
    "노드 /etc/containerd/certs.d/harbor.*/hosts.toml은 K8s API로 조회 불가(#765 미게시) — 노드에서 확인: grep -E 'ca =|skip_verify' /etc/containerd/certs.d/harbor.*/hosts.toml",
    "Host /etc/containerd/certs.d/harbor.*/hosts.toml is not readable via K8s API (#765 publishes no cluster object) — verify on nodes: grep -E 'ca =|skip_verify' /etc/containerd/certs.d/harbor.*/hosts.toml",
    { live: false },
  )
}

// --- KISA-TLS-01: Istio PeerAuthentication mTLS coverage ---

function formatPaException(pa: KisaRawPeerAuthentication): string | null {
  const ns = pa.metadata?.namespace ?? ""
  const name = pa.metadata?.name ?? "unknown"
  const mode = (pa.spec?.mtls?.mode ?? "UNSET").toUpperCase()
  const selLabels = pa.spec?.selector?.matchLabels
  const selStr = selLabels && Object.keys(selLabels).length > 0
    ? Object.entries(selLabels).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join(",")
    : "ns-wide"

  const portExceptions: string[] = []
  for (const [port, cfg] of Object.entries(pa.spec?.portLevelMtls ?? {}).sort(([a], [b]) => a.localeCompare(b))) {
    const pMode = (cfg?.mode ?? "").toUpperCase()
    if (pMode === "PERMISSIVE" || pMode === "DISABLE") {
      portExceptions.push(`port ${port}:${pMode}`)
    }
  }

  if (mode === "PERMISSIVE" || mode === "DISABLE") {
    return `${ns}/${name}(${mode}, ${selStr})`
  }
  if (portExceptions.length > 0) {
    return `${ns}/${name}(${portExceptions.join("/")}, ${selStr})`
  }
  return null
}

export async function checkTls01(): Promise<KisaCheckOutcome> {
  const { getKisaPeerAuthentications } = await import("./k8s-client")
  const paRes = await getKisaPeerAuthentications()
  if (paRes.status !== "ok") {
    return fetchErrorOutcome(paRes, "Istio PeerAuthentication", "Istio PeerAuthentications")
  }

  const items = paRes.data
  const isMeshWideSelector = (p: KisaRawPeerAuthentication) => {
    const sel = p.spec?.selector as { matchLabels?: Record<string, string>; matchExpressions?: unknown[] } | undefined
    return !sel || (Object.keys(sel.matchLabels ?? {}).length === 0 && (sel.matchExpressions ?? []).length === 0)
  }
  const rootPa =
    items.find((p) => p.metadata?.namespace === "istio-system" && p.metadata?.name === "default" && isMeshWideSelector(p)) ??
    items.find((p) => p.metadata?.namespace === "istio-system" && isMeshWideSelector(p))

  const meshMode = (rootPa?.spec?.mtls?.mode ?? "UNSET").toUpperCase()
  const rootPortExceptions = rootPa && meshMode === "STRICT" ? formatPaException(rootPa) : null
  const exceptions = [
    ...items.filter((p) => p !== rootPa).map(formatPaException),
    rootPortExceptions,
  ]
    .filter((x): x is string => x !== null)
    .sort()

  if (meshMode !== "STRICT") {
    const excSuffixKo = exceptions.length > 0 ? ` · 추가 예외 ${exceptions.length}건: ${exceptions.join(", ")}` : ""
    const excSuffixEn = exceptions.length > 0 ? `; additional exceptions (${exceptions.length}): ${exceptions.join(", ")}` : ""
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.tlsNotStrict",
      `메시 전역 PeerAuthentication(istio-system) 모드=${meshMode} (STRICT 아님)${excSuffixKo}`,
      `Mesh-wide PeerAuthentication (istio-system) mode=${meshMode} (not STRICT)${excSuffixEn}`,
    )
  }

  if (exceptions.length === 0) {
    return makeOutcome(
      "pass",
      "compliance.kisa.reason.tlsStrictNoExceptions",
      `메시 전역 STRICT(istio-system/${rootPa?.metadata?.name ?? "default"}), PERMISSIVE/DISABLE 예외 0건`,
      `Mesh-wide STRICT (istio-system/${rootPa?.metadata?.name ?? "default"}), 0 PERMISSIVE/DISABLE exceptions`,
    )
  }

  return makeOutcome(
    "warn",
    "compliance.kisa.reason.tlsStrictWithExceptions",
    `메시 전역 STRICT(istio-system/${rootPa?.metadata?.name ?? "default"}) · 예외 ${exceptions.length}건: ${exceptions.join(", ")}`,
    `Mesh-wide STRICT (istio-system/${rootPa?.metadata?.name ?? "default"}); ${exceptions.length} exceptions: ${exceptions.join(", ")}`,
  )
}
