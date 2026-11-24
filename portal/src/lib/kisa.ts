import "server-only"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import { KISA_CATALOG } from "./kisa-controls"
import {
  checkEtcd02,
  checkImg01,
  checkSec01,
  checkTls01,
  fetchErrorOutcome,
  type KisaCheckOutcome,
} from "./kisa-checks"
import { checkImg02, checkObs01, checkRbacLive } from "./kisa-checks-ops"
import type { KisaRawNetworkPolicy } from "./k8s-client"
import type { Localized } from "./i18n-utils"
import type { KisaControl, KisaReasonKey, KisaResponse, KisaStatus } from "@/types/kisa"

const KISA_NET01_NAMESPACES = ["iam", "devtools", "monitoring", "storage", "database"]
const PSA_ENFORCED_LEVELS = new Set(["baseline", "restricted"])

function hasCliFlagWithValue(args: string[], flag: string): boolean {
  for (let i = 0; i < args.length; i++) {
    const a = args[i]
    if (a.startsWith(`${flag}=`)) {
      return a.slice(flag.length + 1).trim().length > 0
    }
    if (a === flag && i + 1 < args.length) {
      const next = args[i + 1].trim()
      return next.length > 0 && !next.startsWith("-")
    }
  }
  return false
}

function isIngressDefaultDenyPolicy(pol: KisaRawNetworkPolicy): boolean {
  const types = pol.spec?.policyTypes ?? (pol.spec?.ingress ? ["Ingress"] : [])
  if (!types.includes("Ingress")) return false
  const sel = pol.spec?.podSelector
  const podSelectorEmpty =
    !sel ||
    ((!sel.matchLabels || Object.keys(sel.matchLabels).length === 0) &&
      (!sel.matchExpressions || sel.matchExpressions.length === 0))
  if (!podSelectorEmpty) return false

  // D-KISA-CLOSED-WORLD: podSelector:{} + policyTypes:[Ingress] is NOT default-deny if any
  // ingress rule allows all sources (empty rule / missing from / empty peer {} / empty namespaceSelector:{}).
  const ingressRules = pol.spec?.ingress ?? []
  for (const rule of ingressRules) {
    const peers = rule.from
    if (!peers || peers.length === 0) return false
    for (const peer of peers) {
      const hasPodSel =
        Boolean(peer.podSelector) &&
        (Object.keys(peer.podSelector?.matchLabels ?? {}).length > 0 ||
          (peer.podSelector?.matchExpressions?.length ?? 0) > 0)
      const hasNsSel =
        Boolean(peer.namespaceSelector) &&
        (Object.keys(peer.namespaceSelector?.matchLabels ?? {}).length > 0 ||
          (peer.namespaceSelector?.matchExpressions?.length ?? 0) > 0)
      const hasRestrictedIpBlock =
        Boolean(peer.ipBlock?.cidr) &&
        (peer.ipBlock?.cidr !== "0.0.0.0/0" || (peer.ipBlock?.except?.length ?? 0) > 0)
      const sameNsAllPods = Boolean(peer.podSelector) && !hasPodSel && !peer.namespaceSelector && !peer.ipBlock
      if (!hasPodSel && !hasNsSel && !hasRestrictedIpBlock && !sameNsAllPods) {
        return false
      }
    }
  }
  return true
}

// --- Live check helpers ---

async function checkImg03(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getSecuritySummary } = await import("./trivy")
  const summary = await getSecuritySummary()
  if (summary.totals.Critical > 0) {
    return {
      status: "fail",
      detail: `Critical CVE ${summary.totals.Critical}건${summary.partial ? " (목록 일부 잘림)" : ""}`,
      ...(summary.partial ? { partial: true, partialSources: [`vulnerabilityreports(${summary.partialReason ?? "truncated"})`] } : {}),
    }
  }
  if (summary.failed) {
    return {
      status: "manual",
      detail: "Trivy VulnerabilityReport 조회 실패 — 평가 불가",
      partial: true,
      partialSources: ["vulnerabilityreports"],
    }
  }
  if (summary.scannedWorkloads === 0 && summary.scannedImages === 0) {
    return {
      status: "manual",
      detail: "Trivy 취약점 스캔 리포트 없음 — 평가 불가",
    }
  }
  if (summary.totals.High > 0) {
    return {
      status: "warn",
      detail: `High CVE ${summary.totals.High}건${summary.partial ? " (목록 일부 잘림)" : ""}`,
      ...(summary.partial ? { partial: true, partialSources: [`vulnerabilityreports(${summary.partialReason ?? "truncated"})`] } : {}),
    }
  }
  if (summary.partial) {
    return {
      status: "warn",
      detail: "Trivy VulnerabilityReport 목록 잘림 — 전체 스캔 통과 확정 불가",
      partial: true,
      partialSources: [`vulnerabilityreports(${summary.partialReason ?? "truncated"})`],
    }
  }
  return { status: "pass", detail: `Critical/High CVE 없음 (스캔 이미지 ${summary.scannedImages}건)` }
}

async function checkCfg01(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getComplianceFrameworks, getConfigAuditList, getRbacAuditList, getInfraAuditList } =
    await import("./compliance")
  // D-CFG01-ALLSETTLED: Evaluate all 4 compliance sources independently so one unreadable source
  // (e.g. infra 403) never cancels an already-observed actionable failure or degrades CFG-01 into
  // a reasonless `manual`. Known actionable failures always win (-> `fail`, naming failing and
  // unreadable sources separately); unreadable sources with zero known failures yield `warn` with
  // the failed source list. Cost: 4 settled calls; escape hatch: migrate to a shared snapshot API.
  const [fwRes, cfgRes, rbacRes, infraRes] = await Promise.allSettled([
    getComplianceFrameworks(), getConfigAuditList(), getRbacAuditList(), getInfraAuditList(),
  ])

  const unavailableSources: string[] = []
  if (fwRes.status === "rejected") unavailableSources.push("frameworks")
  if (cfgRes.status === "rejected") unavailableSources.push("configAudit")
  if (rbacRes.status === "rejected") unavailableSources.push("rbac")
  if (infraRes.status === "rejected") unavailableSources.push("infra")

  const frameworks = fwRes.status === "fulfilled" ? fwRes.value : []
  const configAuditRows = cfgRes.status === "fulfilled" ? cfgRes.value : []
  const rbacRows = rbacRes.status === "fulfilled" ? rbacRes.value : []
  const infraRows = infraRes.status === "fulfilled" ? infraRes.value : []

  // D-CFG01-EVALUATED: `getComplianceFrameworks()` derives `actionableFailCount` and
  // `notEvaluatedCount` from `status.detailReport.results[].checks`
  // (`checkID` + `target`) when `reportType=all` plus positive per-check report evidence.
  // Do NOT grade CFG-01 on raw `f.passRate` (or exception-adjusted rate thresholds), because Trivy emits placeholder passes
  // when controls were never evaluated. Rule: any actionable failure -> fail; otherwise any
  // not-evaluated control (or unknown coverage / unreadable source) -> warn with reason; pass ONLY when every source
  // succeeded, every control is evaluated, and zero actionable failures remain. Escape hatch: restore threshold grading only
  // after positive check evidence covers 100% of framework controls.
  // D-P2-1: No namespace-blanket acceptance — rely on exact per-row actionableSummary/acceptedSummary.
  let actionableFailures = 0
  let lowSeverityFailures = 0
  let acceptedFailures = 0
  for (const r of configAuditRows) {
    const actionable = r.actionableSummary ?? (r.accepted ? { Critical: 0, High: 0, Medium: 0, Low: 0 } : r.summary)
    const accepted = r.acceptedSummary ?? (r.accepted ? r.summary : { Critical: 0, High: 0, Medium: 0, Low: 0 })
    actionableFailures += actionable.Critical + actionable.High + actionable.Medium + actionable.Low
    lowSeverityFailures += r.summary.Low
    acceptedFailures += accepted.Critical + accepted.High + accepted.Medium
  }
  const sumActionableRows = (
    rows: ReadonlyArray<{ summary: { Critical: number; High: number; Medium: number; Low: number }; actionableSummary?: { Critical: number; High: number; Medium: number; Low: number }; accepted?: boolean }>,
  ) =>
    rows.reduce((sum, row) => {
      const counts = row.actionableSummary ?? (row.accepted ? { Critical: 0, High: 0, Medium: 0, Low: 0 } : row.summary)
      return sum + counts.Critical + counts.High + counts.Medium + counts.Low
    }, 0)
  const rbacActionableFailures = sumActionableRows(rbacRows)
  const infraActionableFailures = sumActionableRows(infraRows)
  const auditFailures = rbacActionableFailures + infraActionableFailures
  const breakdown = `(config-audit 조치가능 ${actionableFailures}건, 위생 ${lowSeverityFailures}건, 수용 ${acceptedFailures}건; RBAC/infra 조치가능 ${auditFailures}건)`
  const unavailableNote =
    unavailableSources.length > 0 ? ` · 조회 불가 소스: ${unavailableSources.join(", ")}` : ""

  const totalControls = frameworks.reduce((acc, f) => acc + f.totalControls, 0)
  const actionableControls = frameworks.reduce((acc, f) => acc + (f.actionableFailCount ?? f.failCount), 0)
  const notEvaluatedControls = frameworks.reduce((acc, f) => acc + (f.notEvaluatedCount ?? Math.max(0, f.totalControls - f.failCount)), 0)
  const acceptedControls = frameworks.reduce((acc, f) => acc + (f.acceptedFailCount ?? 0), 0)
  const evaluatedPassControls = frameworks.reduce((acc, f) => acc + (f.evaluatedPassCount ?? 0), 0)
  const hasUnknownCoverage = frameworks.some((f) => (Boolean(f.coverageUnknown) || f.evaluatedPassCount === undefined || f.notEvaluatedCount === undefined))
  const partial = unavailableSources.length > 0

  // D3: Audit findings can precede CCR aggregation. Cost: two list reads; escape: shared snapshot API.
  if (actionableControls > 0 || actionableFailures > 0 || auditFailures > 0) {
    const failingSources: string[] = []
    if (actionableControls > 0) failingSources.push(`frameworks(${actionableControls}건)`)
    if (actionableFailures > 0) failingSources.push(`config-audit(${actionableFailures}건)`)
    if (rbacActionableFailures > 0) failingSources.push(`rbac(${rbacActionableFailures}건)`)
    if (infraActionableFailures > 0) failingSources.push(`infra(${infraActionableFailures}건)`)
    return {
      status: "fail",
      detail: `조치가능 프레임워크 컨트롤 실패 ${actionableControls}건 [실패 소스: ${failingSources.join(", ")}] (실패 기록 없음 ${evaluatedPassControls}/${totalControls}, 수용 ${acceptedControls}, 미평가 ${notEvaluatedControls}) ${breakdown}${unavailableNote}`,
      partial,
      partialSources: unavailableSources,
    }
  }
  if (notEvaluatedControls > 0 || hasUnknownCoverage || unavailableSources.length > 0) {
    const lead =
      unavailableSources.length > 0 && notEvaluatedControls === 0
        ? `컴플라이언스 소스 일부 조회 불가 (${unavailableSources.join(", ")})`
        : `미평가 프레임워크 컨트롤 ${notEvaluatedControls}건`
    return {
      status: "warn",
      detail: `${lead} (실패 기록 없음 ${evaluatedPassControls}/${totalControls}, 수용 ${acceptedControls}, 조치가능 0건) ${breakdown}${unavailableNote}`,
      partial,
      partialSources: unavailableSources,
    }
  }
  if (frameworks.length === 0) {
    return { status: "manual", detail: `프레임워크 데이터 없음 ${breakdown}` }
  }
  if (totalControls === 0) {
    return { status: "warn", detail: `평가된 프레임워크 컨트롤 0건 — 통과 불가 ${breakdown}` }
  }
  return {
    status: "pass",
    detail: `전 프레임워크 ${totalControls}개 컨트롤 평가 완료·조치가능 실패 없음 (실패 기록 없음 ${evaluatedPassControls}, 수용 ${acceptedControls}) ${breakdown}`,
  }
}

async function checkTls02(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getKisaCertificates } = await import("./k8s-client")
  const certRes = await getKisaCertificates()
  if (certRes.status !== "ok") {
    const errOut = fetchErrorOutcome(certRes, "cert-manager Certificate", "cert-manager Certificates")
    return { status: errOut.status, detail: errOut.detail, partial: errOut.partial, partialSources: errOut.partialSources }
  }
  const certs = certRes.data
  if (certs.length === 0) {
    return { status: "manual", detail: "인증서 데이터 없음" }
  }
  const now = Date.now()
  const thirtyDays = 30 * 24 * 60 * 60 * 1000
  const notReady = certs.filter((c) => !c.ready)
  const missingExpiry = certs.filter((c) => !c.notAfter || !Number.isFinite(new Date(c.notAfter).getTime()))
  const expiring = certs.filter(
    (c) => c.notAfter && Number.isFinite(new Date(c.notAfter).getTime()) && new Date(c.notAfter).getTime() - now < thirtyDays,
  )
  if (notReady.length > 0 || expiring.length > 0) {
    const parts: string[] = []
    if (notReady.length > 0) parts.push(`미준비 ${notReady.length}건`)
    if (expiring.length > 0) parts.push(`30일 내 만료 ${expiring.length}건`)
    return { status: "fail", detail: parts.join(", ") }
  }
  if (missingExpiry.length > 0) {
    return { status: "warn", detail: `만료일(notAfter) 미확인 인증서 ${missingExpiry.length}/${certs.length}건 — 수동 검토 필요` }
  }
  return { status: "pass", detail: `전체 ${certs.length}건 정상` }
}

async function checkAdm01(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getKisaKyvernoPolicies } = await import("./k8s-client")
  const polRes = await getKisaKyvernoPolicies()
  if (polRes.status !== "ok") {
    const errOut = fetchErrorOutcome(polRes, "Kyverno 정책", "Kyverno policies")
    return { status: errOut.status, detail: errOut.detail, partial: errOut.partial, partialSources: errOut.partialSources }
  }
  const policies = polRes.data
  if (policies.length === 0) {
    return { status: "manual", detail: "Kyverno 정책 없음" }
  }
  const notReady = policies.filter((p) => !p.ready)
  const disabled = policies.filter((p) => p.effectiveEnforcement === "Disabled")
  const validateOrVerify = policies.filter((p) => p.hasValidateOrVerifyRules !== false)
  const nonEnforce = validateOrVerify.filter(
    (p) => (p.effectiveEnforcement ?? p.validationFailureAction) !== "Enforce",
  )
  const enforcedCount = validateOrVerify.length - nonEnforce.length

  if (notReady.length > 0 || disabled.length > 0 || validateOrVerify.length === 0 || nonEnforce.length > 0) {
    const parts: string[] = []
    if (notReady.length > 0) parts.push(`미준비 ${notReady.length}건`)
    if (disabled.length > 0) parts.push(`admission=false ${disabled.length}건`)
    if (validateOrVerify.length === 0) parts.push("validate/verifyImages 정책 없음")
    else if (nonEnforce.length > 0) parts.push(`Enforce ${enforcedCount}/${validateOrVerify.length}건 (Audit/Mixed ${nonEnforce.length}건)`)
    return { status: "warn", detail: parts.join(" · ") }
  }
  return { status: "pass", detail: `전체 검증 정책 ${enforcedCount}건 Enforce·Ready=True 활성` }
}

async function checkCp01(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getKisaApiServerPods } = await import("./k8s-client")
  const podsRes = await getKisaApiServerPods()
  if (podsRes.status !== "ok") {
    const errOut = fetchErrorOutcome(podsRes, "API 서버 파드", "API server pods")
    return { status: errOut.status, detail: errOut.detail, partial: errOut.partial, partialSources: errOut.partialSources }
  }
  const pods = podsRes.data
  if (pods.length === 0) return { status: "manual", detail: "API 서버 파드 정보 없음" }

  const missingContainer = pods.filter((p) => p.hasApiserverContainer === false)
  if (missingContainer.length > 0) {
    return { status: "manual", detail: `kube-apiserver 컨테이너 미식별 파드 ${missingContainer.length}/${pods.length}건 — 평가 불가` }
  }
  const notReady = pods.filter((p) => p.ready === false)
  const hasBoth = pods.filter(
    (p) =>
      hasCliFlagWithValue(p.containerArgs, "--audit-log-path") &&
      hasCliFlagWithValue(p.containerArgs, "--audit-policy-file"),
  )
  if (hasBoth.length === pods.length && notReady.length === 0) {
    return { status: "pass", detail: `apiserver ${pods.length}건 모두 감사 로그 설정 확인` }
  }
  if (hasBoth.length > 0) {
    const suffix = notReady.length > 0 ? ` · 미준비 ${notReady.length}건` : ""
    return { status: "warn", detail: `apiserver ${hasBoth.length}/${pods.length}건 감사 로그 설정${suffix}` }
  }
  return { status: "fail", detail: `apiserver ${pods.length}건 모두 --audit-log-path/--audit-policy-file 미설정` }
}

async function checkEtcd01(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getKisaApiServerPods } = await import("./k8s-client")
  const podsRes = await getKisaApiServerPods()
  if (podsRes.status !== "ok") {
    const errOut = fetchErrorOutcome(podsRes, "API 서버 파드", "API server pods")
    return { status: errOut.status, detail: errOut.detail, partial: errOut.partial, partialSources: errOut.partialSources }
  }
  const pods = podsRes.data
  if (pods.length === 0) return { status: "manual", detail: "API 서버 파드 정보 없음" }

  const missingContainer = pods.filter((p) => p.hasApiserverContainer === false)
  if (missingContainer.length > 0) {
    return { status: "manual", detail: `kube-apiserver 컨테이너 미식별 파드 ${missingContainer.length}/${pods.length}건 — 평가 불가` }
  }
  const notReady = pods.filter((p) => p.ready === false)
  const encrypted = pods.filter((p) => hasCliFlagWithValue(p.containerArgs, "--encryption-provider-config"))
  if (encrypted.length === pods.length && notReady.length === 0) {
    return { status: "pass", detail: `apiserver ${pods.length}건 모두 --encryption-provider-config 설정` }
  }
  if (encrypted.length > 0) {
    return { status: "warn", detail: `apiserver ${encrypted.length}/${pods.length}건 암호화 설정 · 미준비 ${notReady.length}건` }
  }
  return { status: "fail", detail: `apiserver ${pods.length - encrypted.length}/${pods.length}건 암호화 미설정` }
}

async function checkPod01(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getKisaNamespaces } = await import("./k8s-client")
  const nsRes = await getKisaNamespaces()
  if (nsRes.status !== "ok") {
    const errOut = fetchErrorOutcome(nsRes, "네임스페이스 목록", "Namespace list")
    return { status: errOut.status, detail: errOut.detail, partial: errOut.partial, partialSources: errOut.partialSources }
  }
  const considered = nsRes.data.filter((n) => n.name !== "kube-node-lease" && n.name !== "kube-public")
  if (considered.length === 0) return { status: "manual", detail: "네임스페이스 정보 없음" }

  const enforced = considered.filter((n) => PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/enforce"] ?? ""))
  const auditOrWarnNonPriv = considered.filter(
    (n) =>
      !PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/enforce"] ?? "") &&
      (PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/audit"] ?? "") ||
        PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/warn"] ?? "")),
  )
  const privilegedOnly = considered.length - enforced.length - auditOrWarnNonPriv.length

  if (enforced.length === considered.length) {
    return { status: "pass", detail: `대상 네임스페이스 ${considered.length}건 모두 PSA enforce(baseline/restricted) 적용` }
  }
  if (enforced.length > 0 || auditOrWarnNonPriv.length > 0) {
    return {
      status: "warn",
      detail: `PSA enforce(baseline/restricted) ${enforced.length}/${considered.length}건 · audit/warn만 ${auditOrWarnNonPriv.length}건 (privileged/미설정 ${privilegedOnly}건)`,
    }
  }
  return { status: "fail", detail: `PSA baseline/restricted 라벨 적용 네임스페이스 없음 (0/${considered.length}건)` }
}

async function checkNet01(): Promise<{ status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }> {
  const { getKisaNetworkPolicies } = await import("./k8s-client")
  const npRes = await getKisaNetworkPolicies()
  if (npRes.status !== "ok") {
    const errOut = fetchErrorOutcome(npRes, "NetworkPolicy 목록", "NetworkPolicy list")
    return { status: errOut.status, detail: errOut.detail, partial: errOut.partial, partialSources: errOut.partialSources }
  }
  const policies = npRes.data

  const hasDefaultDeny = (ns: string) =>
    policies.some((p) => p.metadata?.namespace === ns && isIngressDefaultDenyPolicy(p))
  const covered = KISA_NET01_NAMESPACES.filter(hasDefaultDeny)

  if (covered.length === KISA_NET01_NAMESPACES.length) {
    return { status: "pass", detail: `대상 네임스페이스 ${KISA_NET01_NAMESPACES.length}건 모두 default-deny 적용` }
  }
  if (covered.length > 0) {
    return { status: "warn", detail: `default-deny 적용 ${covered.length}/${KISA_NET01_NAMESPACES.length}건` }
  }
  return { status: "fail", detail: "대상 네임스페이스에 ingress default-deny NetworkPolicy 없음" }
}

// --- Main export ---

export async function getKisaControls(): Promise<KisaResponse> {
  const cacheKey = cacheKeys.kisaComplianceList()
  const cached = await cacheGet<KisaResponse>(cacheKey)
  if (cached) return cached

  const controls: KisaControl[] = []
  let hasPartialCheck = false
  const allPartialSources = new Set<string>()

  for (const entry of KISA_CATALOG) {
    if (entry.id === "KISA-IMG-01") {
      const r = checkImg01()
      controls.push({
        ...entry,
        status: r.status,
        live: r.live,
        detail: r.detail,
        detailLocalized: r.detailLocalized,
        reasonKey: r.reasonKey,
      })
      continue
    }

    let status: KisaStatus = "manual"
    let live = false
    let detail: string | undefined
    let detailLocalized: Localized | undefined
    let reasonKey: KisaReasonKey | undefined
    let partial = false
    let partialSources: string[] | undefined

    const applyRich = (r: KisaCheckOutcome) => {
      status = r.status
      live = r.live
      detail = r.detail
      detailLocalized = r.detailLocalized
      reasonKey = r.reasonKey
      if (r.partial) {
        hasPartialCheck = true
        partial = true
      }
      if (r.partialSources && r.partialSources.length > 0) {
        partialSources = r.partialSources
        for (const s of r.partialSources) allPartialSources.add(s)
      }
    }

    const applySimple = (r: { status: KisaStatus; detail: string; partial?: boolean; partialSources?: string[] }) => {
      status = r.status
      live = r.status !== "manual"
      detail = r.detail
      if (r.partial) {
        hasPartialCheck = true
        partial = true
      }
      if (r.partialSources && r.partialSources.length > 0) {
        partialSources = r.partialSources
        for (const s of r.partialSources) allPartialSources.add(s)
      }
    }

    try {
      if (entry.id === "KISA-CP-01") {
        applySimple(await checkCp01())
      } else if (entry.id === "KISA-ETCD-01") {
        applySimple(await checkEtcd01())
      } else if (entry.id === "KISA-SEC-01") {
        applyRich(await checkSec01())
      } else if (entry.id === "KISA-ETCD-02") {
        applyRich(await checkEtcd02())
      } else if (entry.id === "KISA-TLS-01") {
        applyRich(await checkTls01())
      } else if (entry.id === "KISA-OBS-01") {
        applyRich(await checkObs01())
      } else if (entry.id === "KISA-IMG-02") {
        applyRich(await checkImg02())
      } else if (entry.id === "KISA-POD-01") {
        applySimple(await checkPod01())
      } else if (entry.id === "KISA-NET-01") {
        applySimple(await checkNet01())
      } else if (entry.id === "KISA-IMG-03") {
        applySimple(await checkImg03())
      } else if (entry.id === "KISA-CFG-01") {
        applySimple(await checkCfg01())
      } else if (entry.id === "KISA-TLS-02") {
        applySimple(await checkTls02())
      } else if (entry.id === "KISA-ADM-01") {
        applySimple(await checkAdm01())
      } else if (entry.id === "KISA-RBAC-01" || entry.id === "KISA-RBAC-02") {
        applyRich(await checkRbacLive(entry.id))
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.warn(`[kisa] Live check ${entry.id} failed (non-fatal):`, msg)
      status = "manual"
      live = false
      detail = `점검 실행 오류로 평가 불가 (${entry.id})`
      detailLocalized = {
        ko: `점검 실행 오류로 평가 불가 (${entry.id})`,
        en: `Check execution failed; not evaluated (${entry.id})`,
      }
      reasonKey = "compliance.kisa.reason.apiUnavailable"
      hasPartialCheck = true
      partial = true
      partialSources = [entry.id]
      allPartialSources.add(entry.id)
    }

    controls.push({
      ...entry,
      status,
      live,
      detail,
      ...(detailLocalized ? { detailLocalized } : {}),
      ...(reasonKey ? { reasonKey } : {}),
      ...(partial ? { partial: true } : {}),
      ...(partialSources && partialSources.length > 0 ? { partialSources } : {}),
    })
  }

  const summary = {
    total: controls.length,
    pass: controls.filter((c) => c.status === "pass").length,
    fail: controls.filter((c) => c.status === "fail").length,
    warn: controls.filter((c) => c.status === "warn").length,
    manual: controls.filter((c) => c.status === "manual").length,
    lastUpdated: new Date().toISOString(),
  }

  const result: KisaResponse = {
    controls,
    summary,
    ...(hasPartialCheck ? { partial: true } : {}),
    ...(allPartialSources.size > 0 ? { partialSources: [...allPartialSources] } : {}),
  }
  if (!hasPartialCheck) {
    await cacheSet(cacheKey, result, cacheTtl("kisaComplianceList"))
  }
  return result
}
