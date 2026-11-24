import "server-only"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import { KISA_CATALOG } from "./kisa-controls"
import {
  checkEtcd02,
  checkImg01,
  checkSec01,
  checkTls01,
  type KisaCheckOutcome,
} from "./kisa-checks"
import { checkImg02, checkObs01, checkRbacLive } from "./kisa-checks-ops"
import type { Localized } from "./i18n-utils"
import type { KisaControl, KisaReasonKey, KisaResponse, KisaStatus } from "@/types/kisa"

const KISA_NET01_NAMESPACES = ["iam", "devtools", "monitoring", "storage", "database"]

// --- Live check helpers ---

async function checkImg03(): Promise<{ status: KisaStatus; detail: string }> {
  const { getSecuritySummary } = await import("./trivy")
  const summary = await getSecuritySummary()
  if (summary.totals.Critical > 0) {
    return { status: "fail", detail: `Critical CVE ${summary.totals.Critical}건` }
  }
  if (summary.totals.High > 0) {
    return { status: "warn", detail: `High CVE ${summary.totals.High}건` }
  }
  return { status: "pass", detail: "Critical/High CVE 없음" }
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
  return {
    status: "pass",
    detail: `전 프레임워크 ${totalControls}개 컨트롤 평가 완료·조치가능 실패 없음 (실패 기록 없음 ${evaluatedPassControls}, 수용 ${acceptedControls}) ${breakdown}`,
  }
}

async function checkTls02(): Promise<{ status: KisaStatus; detail: string }> {
  const { getCertificates } = await import("./k8s-client")
  const certs = await getCertificates()
  if (certs.length === 0) {
    return { status: "manual", detail: "인증서 데이터 없음" }
  }
  const now = Date.now()
  const thirtyDays = 30 * 24 * 60 * 60 * 1000
  const notReady = certs.filter((c) => !c.ready)
  const expiring = certs.filter(
    (c) => c.notAfter && new Date(c.notAfter).getTime() - now < thirtyDays,
  )
  if (notReady.length > 0 || expiring.length > 0) {
    const parts: string[] = []
    if (notReady.length > 0) parts.push(`미준비 ${notReady.length}건`)
    if (expiring.length > 0) parts.push(`30일 내 만료 ${expiring.length}건`)
    return { status: "fail", detail: parts.join(", ") }
  }
  return { status: "pass", detail: `전체 ${certs.length}건 정상` }
}

async function checkAdm01(): Promise<{ status: KisaStatus; detail: string }> {
  const { getKyvernoPolicies } = await import("./k8s-client")
  const policies = await getKyvernoPolicies()
  if (policies.length === 0) {
    return { status: "manual", detail: "Kyverno 정책 없음" }
  }
  const notReady = policies.filter((p) => !p.ready)
  const allAudit = policies.every((p) => p.validationFailureAction === "Audit")
  if (notReady.length > 0 || allAudit) {
    const parts: string[] = []
    if (notReady.length > 0) parts.push(`미준비 ${notReady.length}건`)
    if (allAudit) parts.push("전 정책 Audit 모드")
    return { status: "warn", detail: parts.join(", ") }
  }
  return { status: "pass", detail: `Enforce 정책 ${policies.filter((p) => p.validationFailureAction === "Enforce").length}건 활성` }
}

async function checkCp01(): Promise<{ status: KisaStatus; detail: string }> {
  const { getApiServerPods } = await import("./k8s-client")
  const pods = await getApiServerPods()
  if (pods.length === 0) return { status: "manual", detail: "API 서버 파드 정보 없음" }

  const hasBoth = pods.filter(
    (p) =>
      p.containerArgs.some((a) => a.includes("--audit-log-path")) &&
      p.containerArgs.some((a) => a.includes("--audit-policy-file")),
  )
  if (hasBoth.length === pods.length) {
    return { status: "pass", detail: `apiserver ${pods.length}건 모두 감사 로그 설정 확인` }
  }
  if (hasBoth.length > 0) {
    return { status: "warn", detail: `apiserver ${hasBoth.length}/${pods.length}건만 감사 로그 설정` }
  }
  return { status: "fail", detail: `apiserver ${pods.length}건 모두 --audit-log-path/--audit-policy-file 미설정` }
}

async function checkEtcd01(): Promise<{ status: KisaStatus; detail: string }> {
  const { getApiServerPods } = await import("./k8s-client")
  const pods = await getApiServerPods()
  if (pods.length === 0) return { status: "manual", detail: "API 서버 파드 정보 없음" }

  const encrypted = pods.filter((p) => p.containerArgs.some((a) => a.includes("--encryption-provider-config")))
  if (encrypted.length === pods.length) {
    return { status: "pass", detail: `apiserver ${pods.length}건 모두 --encryption-provider-config 설정` }
  }
  return { status: "fail", detail: `apiserver ${pods.length - encrypted.length}/${pods.length}건 암호화 미설정` }
}

async function checkPod01(): Promise<{ status: KisaStatus; detail: string }> {
  const { getNamespaces } = await import("./k8s-client")
  const namespaces = await getNamespaces()
  const considered = namespaces.filter((n) => n.name !== "kube-node-lease" && n.name !== "kube-public")
  if (considered.length === 0) return { status: "manual", detail: "네임스페이스 정보 없음" }

  const psaKeys = [
    "pod-security.kubernetes.io/enforce",
    "pod-security.kubernetes.io/audit",
    "pod-security.kubernetes.io/warn",
  ]
  const labeled = considered.filter((n) => psaKeys.some((k) => k in n.labels))
  const ratio = labeled.length / considered.length
  if (ratio === 1) {
    return { status: "pass", detail: `대상 네임스페이스 ${considered.length}건 모두 PSA 라벨 적용` }
  }
  if (ratio >= 0.5) {
    return { status: "warn", detail: `PSA 라벨 적용 ${labeled.length}/${considered.length}건` }
  }
  return { status: "fail", detail: `PSA 라벨 적용 ${labeled.length}/${considered.length}건` }
}

async function checkNet01(): Promise<{ status: KisaStatus; detail: string }> {
  const { getNetworkPolicies } = await import("./k8s-client")
  const policies = await getNetworkPolicies()

  const hasDefaultDeny = (ns: string) =>
    policies.some((p) => p.namespace === ns && p.policyTypes.includes("Ingress") && p.podSelectorEmpty)
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

    try {
      if (entry.id === "KISA-CP-01") {
        const r = await checkCp01()
        status = r.status
        live = r.status !== "manual"
        detail = r.detail
      } else if (entry.id === "KISA-ETCD-01") {
        const r = await checkEtcd01()
        status = r.status
        live = r.status !== "manual"
        detail = r.detail
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
        const r = await checkPod01()
        status = r.status
        live = r.status !== "manual"
        detail = r.detail
      } else if (entry.id === "KISA-NET-01") {
        const r = await checkNet01()
        status = r.status
        live = r.status !== "manual"
        detail = r.detail
      } else if (entry.id === "KISA-IMG-03") {
        const r = await checkImg03()
        status = r.status
        live = r.status !== "manual"
        detail = r.detail
      } else if (entry.id === "KISA-CFG-01") {
        const r = await checkCfg01()
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
      } else if (entry.id === "KISA-TLS-02") {
        const r = await checkTls02()
        status = r.status
        live = r.status !== "manual"
        detail = r.detail
      } else if (entry.id === "KISA-ADM-01") {
        const r = await checkAdm01()
        status = r.status
        live = r.status !== "manual"
        detail = r.detail
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
