import "server-only"
import policyBaselineData from "@/data/kisa-policy-baseline.json"
import { cacheGet, cacheSet } from "./valkey"
import { cacheKeys, cacheTtl } from "./cache-keys"
import { KISA_CATALOG } from "./kisa-controls"
import {
  checkEtcd02,
  checkImg01,
  checkSec01,
  checkTls01,
  fetchErrorOutcome,
  makeOutcome,
  type KisaCheckOutcome,
} from "./kisa-checks"
import {
  checkImg02,
  checkObs01,
  checkRbacLive,
  type KisaPolicyBaseline,
} from "./kisa-checks-ops"
import type { KisaNetworkPolicyPeer, KisaRawNetworkPolicy } from "./k8s-client"
import type { Localized } from "./i18n-utils"
import type { KisaControl, KisaReasonKey, KisaResponse, KisaStatus } from "@/types/kisa"

// THREAT MODEL (D-KISA-CLOSED-WORLD):
// The KISA tab is a compliance indicator for operators; it must be correct under
// misconfiguration, drift, partial API failure and stale/incomplete objects, and it
// must not accept by name or pattern; it is NOT a tamper-proof detector against an
// adversary who already holds cluster-admin and forges Kubernetes object content
// byte-for-byte equal to the reviewed shapes.

const KISA_NET01_NAMESPACES = ["iam", "devtools", "monitoring", "storage", "database"]
const PSA_ENFORCED_LEVELS = new Set(["baseline", "restricted"])
const KISA_ADM_BASELINE: KisaPolicyBaseline = policyBaselineData as KisaPolicyBaseline

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

function isRestrictedIngressPeer(peer: KisaNetworkPolicyPeer): boolean {
  const hasPodSel =
    Boolean(peer.podSelector) &&
    (Object.keys(peer.podSelector?.matchLabels ?? {}).length > 0 ||
      (peer.podSelector?.matchExpressions?.length ?? 0) > 0)
  const hasNsSel =
    Boolean(peer.namespaceSelector) &&
    (Object.keys(peer.namespaceSelector?.matchLabels ?? {}).length > 0 ||
      (peer.namespaceSelector?.matchExpressions?.length ?? 0) > 0)
  const cidr = peer.ipBlock?.cidr?.trim() ?? ""
  const hasRestrictedIpBlock = Boolean(cidr) && cidr !== "0.0.0.0/0" && cidr !== "::/0"
  const sameNsAllPods = Boolean(peer.podSelector) && !hasPodSel && !peer.namespaceSelector && !peer.ipBlock
  if (!hasPodSel && !hasNsSel && !hasRestrictedIpBlock && !sameNsAllPods) {
    return false
  }
  return true
}

function hasAllowAllIngressRule(pol: KisaRawNetworkPolicy): boolean {
  const types = pol.spec?.policyTypes ?? (pol.spec?.ingress ? ["Ingress"] : [])
  if (!types.includes("Ingress")) return false
  for (const rule of pol.spec?.ingress ?? []) {
    const peers = rule.from
    if (!peers || peers.length === 0) return true
    if (peers.some((peer) => !isRestrictedIngressPeer(peer))) return true
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
  // ingress rule allows all sources (empty rule / missing from / empty peer {} / empty namespaceSelector:{} / 0.0.0.0/0 / ::/0).
  return !hasAllowAllIngressRule(pol)
}

// --- Live check helpers ---

async function checkImg03(): Promise<KisaCheckOutcome> {
  const { getSecuritySummary } = await import("./trivy")
  const summary = await getSecuritySummary()
  if (summary.totals.Critical > 0) {
    const partialSources = summary.partial ? [`vulnerabilityreports(${summary.partialReason ?? "truncated"})`] : undefined
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.imgCveCritical",
      `Critical CVE ${summary.totals.Critical}건${summary.partial ? " (목록 일부 잘림)" : ""}`,
      `Critical CVEs: ${summary.totals.Critical}${summary.partial ? " (list truncated)" : ""}`,
      { partial: Boolean(summary.partial), partialSources },
    )
  }
  if (summary.failed) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.apiUnavailable",
      "Trivy VulnerabilityReport 조회 실패 — 평가 불가",
      "Trivy VulnerabilityReport read failed — not evaluated",
      { live: false, partial: true, partialSources: ["vulnerabilityreports"] },
    )
  }
  if (summary.scannedWorkloads === 0 && summary.scannedImages === 0) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.imgCveNoReports",
      "Trivy 취약점 스캔 리포트 없음 — 평가 불가",
      "No Trivy vulnerability scan reports — not evaluated",
      { live: false },
    )
  }
  if (summary.totals.High > 0) {
    const partialSources = summary.partial ? [`vulnerabilityreports(${summary.partialReason ?? "truncated"})`] : undefined
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.imgCveHigh",
      `High CVE ${summary.totals.High}건${summary.partial ? " (목록 일부 잘림)" : ""}`,
      `High CVEs: ${summary.totals.High}${summary.partial ? " (list truncated)" : ""}`,
      { partial: Boolean(summary.partial), partialSources },
    )
  }
  if (summary.partial) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.imgCveTruncated",
      "Trivy VulnerabilityReport 목록 잘림 — 전체 스캔 통과 확정 불가",
      "Trivy VulnerabilityReport list truncated — full pass cannot be certified",
      { partial: true, partialSources: [`vulnerabilityreports(${summary.partialReason ?? "truncated"})`] },
    )
  }
  return makeOutcome(
    "pass",
    "compliance.kisa.reason.imgCveClean",
    `Critical/High CVE 없음 (스캔 이미지 ${summary.scannedImages}건)`,
    `No Critical/High CVEs (${summary.scannedImages} scanned image(s))`,
  )
}

async function checkCfg01(): Promise<KisaCheckOutcome> {
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
  const breakdownKo = `(config-audit 조치가능 ${actionableFailures}건, 위생 ${lowSeverityFailures}건, 수용 ${acceptedFailures}건; RBAC/infra 조치가능 ${auditFailures}건)`
  const breakdownEn = `(config-audit actionable ${actionableFailures}, hygiene ${lowSeverityFailures}, accepted ${acceptedFailures}; RBAC/infra actionable ${auditFailures})`
  const unavailableNoteKo =
    unavailableSources.length > 0 ? ` · 조회 불가 소스: ${unavailableSources.join(", ")}` : ""
  const unavailableNoteEn =
    unavailableSources.length > 0 ? `; unavailable source(s): ${unavailableSources.join(", ")}` : ""

  const totalControls = frameworks.reduce((acc, f) => acc + f.totalControls, 0)
  const actionableControls = frameworks.reduce((acc, f) => acc + (f.actionableFailCount ?? f.failCount), 0)
  const notEvaluatedControls = frameworks.reduce((acc, f) => acc + (f.notEvaluatedCount ?? Math.max(0, f.totalControls - f.failCount)), 0)
  const acceptedControls = frameworks.reduce((acc, f) => acc + (f.acceptedFailCount ?? 0), 0)
  const evaluatedPassControls = frameworks.reduce((acc, f) => acc + (f.evaluatedPassCount ?? 0), 0)
  const hasUnknownCoverage = frameworks.some((f) => (Boolean(f.coverageUnknown) || f.evaluatedPassCount === undefined || f.notEvaluatedCount === undefined))
  const partial = unavailableSources.length > 0

  // D3: Audit findings can precede CCR aggregation. Cost: two list reads; escape: shared snapshot API.
  if (actionableControls > 0 || actionableFailures > 0 || auditFailures > 0) {
    const failingSourcesKo: string[] = []
    const failingSourcesEn: string[] = []
    if (actionableControls > 0) {
      failingSourcesKo.push(`frameworks(${actionableControls}건)`)
      failingSourcesEn.push(`frameworks(${actionableControls})`)
    }
    if (actionableFailures > 0) {
      failingSourcesKo.push(`config-audit(${actionableFailures}건)`)
      failingSourcesEn.push(`config-audit(${actionableFailures})`)
    }
    if (rbacActionableFailures > 0) {
      failingSourcesKo.push(`rbac(${rbacActionableFailures}건)`)
      failingSourcesEn.push(`rbac(${rbacActionableFailures})`)
    }
    if (infraActionableFailures > 0) {
      failingSourcesKo.push(`infra(${infraActionableFailures}건)`)
      failingSourcesEn.push(`infra(${infraActionableFailures})`)
    }
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.cfgActionableFail",
      `조치가능 프레임워크 컨트롤 실패 ${actionableControls}건 [실패 소스: ${failingSourcesKo.join(", ")}] (실패 기록 없음 ${evaluatedPassControls}/${totalControls}, 수용 ${acceptedControls}, 미평가 ${notEvaluatedControls}) ${breakdownKo}${unavailableNoteKo}`,
      `${actionableControls} actionable framework control failure(s) [failing source(s): ${failingSourcesEn.join(", ")}] (no failure recorded ${evaluatedPassControls}/${totalControls}, accepted ${acceptedControls}, not evaluated ${notEvaluatedControls}) ${breakdownEn}${unavailableNoteEn}`,
      { partial, partialSources: unavailableSources },
    )
  }
  if (notEvaluatedControls > 0 || hasUnknownCoverage || unavailableSources.length > 0) {
    const leadKo =
      unavailableSources.length > 0 && notEvaluatedControls === 0
        ? `컴플라이언스 소스 일부 조회 불가 (${unavailableSources.join(", ")})`
        : `미평가 프레임워크 컨트롤 ${notEvaluatedControls}건`
    const leadEn =
      unavailableSources.length > 0 && notEvaluatedControls === 0
        ? `Compliance source(s) unavailable (${unavailableSources.join(", ")})`
        : `${notEvaluatedControls} unevaluated framework control(s)`
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.cfgUnevaluated",
      `${leadKo} (실패 기록 없음 ${evaluatedPassControls}/${totalControls}, 수용 ${acceptedControls}, 조치가능 0건) ${breakdownKo}${unavailableNoteKo}`,
      `${leadEn} (no failure recorded ${evaluatedPassControls}/${totalControls}, accepted ${acceptedControls}, actionable 0) ${breakdownEn}${unavailableNoteEn}`,
      { partial, partialSources: unavailableSources },
    )
  }
  if (frameworks.length === 0) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.cfgEmpty",
      `프레임워크 데이터 없음 ${breakdownKo}`,
      `No compliance framework data ${breakdownEn}`,
      { live: false },
    )
  }
  if (totalControls === 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.cfgUnevaluated",
      `평가된 프레임워크 컨트롤 0건 — 통과 불가 ${breakdownKo}`,
      `0 evaluated framework controls — cannot pass ${breakdownEn}`,
    )
  }
  return makeOutcome(
    "pass",
    "compliance.kisa.reason.cfgClean",
    `전 프레임워크 ${totalControls}개 컨트롤 평가 완료·조치가능 실패 없음 (실패 기록 없음 ${evaluatedPassControls}, 수용 ${acceptedControls}) ${breakdownKo}`,
    `All ${totalControls} framework control(s) evaluated with 0 actionable failures (no failure recorded ${evaluatedPassControls}, accepted ${acceptedControls}) ${breakdownEn}`,
  )
}

async function checkTls02(): Promise<KisaCheckOutcome> {
  const { getKisaCertificates } = await import("./k8s-client")
  const certRes = await getKisaCertificates()
  if (certRes.status !== "ok") {
    return fetchErrorOutcome(certRes, "cert-manager Certificate", "cert-manager Certificates")
  }
  const certs = certRes.data
  if (certs.length === 0) {
    return makeOutcome("manual", "compliance.kisa.reason.resourceMissing", "인증서 데이터 없음", "No certificate data", { live: false })
  }
  const now = Date.now()
  const thirtyDays = 30 * 24 * 60 * 60 * 1000
  const notReady = certs.filter((c) => !c.ready)
  const missingExpiry = certs.filter((c) => !c.notAfter || !Number.isFinite(new Date(c.notAfter).getTime()))
  const expiring = certs.filter(
    (c) => c.notAfter && Number.isFinite(new Date(c.notAfter).getTime()) && new Date(c.notAfter).getTime() - now < thirtyDays,
  )
  if (notReady.length > 0 || expiring.length > 0) {
    const partsKo: string[] = []
    const partsEn: string[] = []
    if (notReady.length > 0) {
      partsKo.push(`미준비 ${notReady.length}건`)
      partsEn.push(`not ready: ${notReady.length}`)
    }
    if (expiring.length > 0) {
      partsKo.push(`30일 내 만료 ${expiring.length}건`)
      partsEn.push(`expiring within 30d: ${expiring.length}`)
    }
    return makeOutcome("fail", "compliance.kisa.reason.tlsCertInvalid", partsKo.join(", "), partsEn.join("; "))
  }
  if (missingExpiry.length > 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.tlsCertMissingExpiry",
      `만료일(notAfter) 미확인 인증서 ${missingExpiry.length}/${certs.length}건 — 수동 검토 필요`,
      `${missingExpiry.length}/${certs.length} certificate(s) missing valid notAfter — manual review required`,
    )
  }
  return makeOutcome(
    "pass",
    "compliance.kisa.reason.tlsCertOk",
    `전체 ${certs.length}건 정상`,
    `All ${certs.length} certificate(s) healthy`,
  )
}

export async function checkAdm01(baseline: KisaPolicyBaseline = KISA_ADM_BASELINE): Promise<KisaCheckOutcome> {
  const { getKisaKyvernoPolicies } = await import("./k8s-client")
  const polRes = await getKisaKyvernoPolicies()
  if (polRes.status !== "ok") {
    return fetchErrorOutcome(polRes, "Kyverno 정책", "Kyverno policies")
  }
  const policies = polRes.data
  if (policies.length === 0) {
    return makeOutcome("manual", "compliance.kisa.reason.resourceMissing", "Kyverno 정책 없음", "No Kyverno policies found", { live: false })
  }

  // D-KISA-CLOSED-WORLD (R7): Compare against reviewed baseline entries marked admissionRelevant.
  // Arbitrary Enforce+Ready policies not in the baseline never count toward pass (listed as unreviewed).
  const baselineEntries = Object.values(baseline.policies).filter((e) => e.admissionRelevant === true)
  if (baselineEntries.length === 0) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.resourceMissing",
      "기준선 admission 정책 정의 없음 — 평가 불가",
      "No admission-relevant baseline policies defined — not evaluated",
      { live: false },
    )
  }

  const missing: string[] = []
  const notReady: string[] = []
  const disabled: string[] = []
  const differs: string[] = []
  const nonEnforce: string[] = []
  let enforcedCount = 0

  for (const entry of baselineEntries) {
    const p = policies.find((item) => item.scope === "cluster" && item.name === entry.name)
    if (!p) {
      missing.push(entry.name)
      continue
    }
    if (!p.ready) {
      notReady.push(entry.name)
      continue
    }
    if (p.effectiveEnforcement === "Disabled") {
      disabled.push(entry.name)
      continue
    }
    const hasValidateOrVerify = p.hasValidateOrVerifyRules !== false
    const digestMatches = Boolean(p.specSha256) && p.specSha256 === entry.specSha256
    const noOverrides = p.hasOverrides !== true && entry.hasOverrides !== true
    if (!hasValidateOrVerify || !digestMatches || !noOverrides) {
      differs.push(entry.name)
      continue
    }
    const liveAction = p.effectiveEnforcement ?? p.validationFailureAction
    if (entry.validationFailureAction !== "Enforce" || !entry.enforceEligible || liveAction !== "Enforce") {
      nonEnforce.push(entry.name)
      continue
    }
    enforcedCount++
  }

  const unreviewed = policies
    .filter((p) => p.scope !== "cluster" || !(p.name in baseline.policies))
    .map((p) => (p.namespace ? `${p.namespace}/${p.name}` : p.name))
    .sort()
  const unreviewedKo = unreviewed.length > 0 ? ` · 미검토(unreviewed): ${unreviewed.join(", ")}` : ""
  const unreviewedEn = unreviewed.length > 0 ? `; unreviewed: ${unreviewed.join(", ")}` : ""

  if (missing.length > 0 || notReady.length > 0 || disabled.length > 0 || differs.length > 0 || nonEnforce.length > 0) {
    const partsKo: string[] = [`Enforce ${enforcedCount}/${baselineEntries.length}건`]
    const partsEn: string[] = [`Enforce ${enforcedCount}/${baselineEntries.length}`]
    if (nonEnforce.length > 0) {
      partsKo.push(`Audit/Mixed ${nonEnforce.length}건`)
      partsEn.push(`Audit/Mixed ${nonEnforce.length}`)
    }
    if (differs.length > 0) {
      partsKo.push(`기준선 불일치 ${differs.length}건(${differs.join(", ")})`)
      partsEn.push(`differs from baseline ${differs.length} (${differs.join(", ")})`)
    }
    if (notReady.length > 0) {
      partsKo.push(`미준비 ${notReady.length}건(${notReady.join(", ")})`)
      partsEn.push(`not ready ${notReady.length} (${notReady.join(", ")})`)
    }
    if (disabled.length > 0) {
      partsKo.push(`admission=false ${disabled.length}건(${disabled.join(", ")})`)
      partsEn.push(`admission=false ${disabled.length} (${disabled.join(", ")})`)
    }
    if (missing.length > 0) {
      partsKo.push(`누락 ${missing.length}건(${missing.join(", ")})`)
      partsEn.push(`missing ${missing.length} (${missing.join(", ")})`)
    }
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.admPartialOrDiffers",
      `${partsKo.join(" · ")}${unreviewedKo}`,
      `${partsEn.join("; ")}${unreviewedEn}`,
    )
  }

  return makeOutcome(
    "pass",
    "compliance.kisa.reason.admEnforced",
    `기준선 검증 정책 ${enforcedCount}/${baselineEntries.length}건 SHA-256 일치·Enforce·Ready=True 활성${unreviewedKo}`,
    `All ${enforcedCount}/${baselineEntries.length} baseline admission policies match SHA-256, Enforce, Ready=True${unreviewedEn}`,
  )
}

async function checkCp01(): Promise<KisaCheckOutcome> {
  const { getKisaApiServerPods } = await import("./k8s-client")
  const podsRes = await getKisaApiServerPods()
  if (podsRes.status !== "ok") {
    return fetchErrorOutcome(podsRes, "API 서버 파드", "API server pods")
  }
  const pods = podsRes.data
  if (pods.length === 0) {
    return makeOutcome("manual", "compliance.kisa.reason.resourceMissing", "API 서버 파드 정보 없음", "No API server pod data", { live: false })
  }

  const missingContainer = pods.filter((p) => p.hasApiserverContainer === false)
  if (missingContainer.length > 0) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.apiserverContainerMissing",
      `kube-apiserver 컨테이너 미식별 파드 ${missingContainer.length}/${pods.length}건 — 평가 불가`,
      `kube-apiserver container not identified in ${missingContainer.length}/${pods.length} pod(s) — not evaluated`,
      { live: false },
    )
  }
  const notReady = pods.filter((p) => p.ready !== true)
  if (notReady.length === pods.length) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.cpPodNotReady",
      `kube-apiserver 파드 ${notReady.length}/${pods.length}건 Ready=True 미확인(status 누락 또는 미준비) — 평가 불가`,
      `kube-apiserver pod Ready=True missing/false on ${notReady.length}/${pods.length} pod(s) — not evaluated`,
      { live: false },
    )
  }
  const hasBoth = pods.filter(
    (p) =>
      hasCliFlagWithValue(p.containerArgs, "--audit-log-path") &&
      hasCliFlagWithValue(p.containerArgs, "--audit-policy-file"),
  )
  if (hasBoth.length === pods.length && notReady.length === 0) {
    return makeOutcome(
      "pass",
      "compliance.kisa.reason.cpAuditEnabled",
      `apiserver ${pods.length}건 모두 감사 로그 설정 확인`,
      `All ${pods.length} kube-apiserver pod(s) Ready=True with audit log flags configured`,
    )
  }
  if (hasBoth.length > 0) {
    const suffixKo = notReady.length > 0 ? ` · 미준비 ${notReady.length}건` : ""
    const suffixEn = notReady.length > 0 ? `; not ready: ${notReady.length}` : ""
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.cpAuditPartialOrUnready",
      `apiserver ${hasBoth.length}/${pods.length}건 감사 로그 설정${suffixKo}`,
      `Audit log flags configured on ${hasBoth.length}/${pods.length} kube-apiserver pod(s)${suffixEn}`,
    )
  }
  return makeOutcome(
    "fail",
    "compliance.kisa.reason.cpAuditMissing",
    `apiserver ${pods.length}건 모두 --audit-log-path/--audit-policy-file 미설정`,
    `All ${pods.length} kube-apiserver pod(s) missing --audit-log-path/--audit-policy-file`,
  )
}

async function checkEtcd01(): Promise<KisaCheckOutcome> {
  const { getKisaApiServerPods } = await import("./k8s-client")
  const podsRes = await getKisaApiServerPods()
  if (podsRes.status !== "ok") {
    return fetchErrorOutcome(podsRes, "API 서버 파드", "API server pods")
  }
  const pods = podsRes.data
  if (pods.length === 0) {
    return makeOutcome("manual", "compliance.kisa.reason.resourceMissing", "API 서버 파드 정보 없음", "No API server pod data", { live: false })
  }

  const missingContainer = pods.filter((p) => p.hasApiserverContainer === false)
  if (missingContainer.length > 0) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.apiserverContainerMissing",
      `kube-apiserver 컨테이너 미식별 파드 ${missingContainer.length}/${pods.length}건 — 평가 불가`,
      `kube-apiserver container not identified in ${missingContainer.length}/${pods.length} pod(s) — not evaluated`,
      { live: false },
    )
  }
  const notReady = pods.filter((p) => p.ready !== true)
  if (notReady.length === pods.length) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.cpPodNotReady",
      `kube-apiserver 파드 ${notReady.length}/${pods.length}건 Ready=True 미확인(status 누락 또는 미준비) — 평가 불가`,
      `kube-apiserver pod Ready=True missing/false on ${notReady.length}/${pods.length} pod(s) — not evaluated`,
      { live: false },
    )
  }
  const encrypted = pods.filter((p) => hasCliFlagWithValue(p.containerArgs, "--encryption-provider-config"))
  if (encrypted.length === pods.length && notReady.length === 0) {
    return makeOutcome(
      "pass",
      "compliance.kisa.reason.etcdEncEnabled",
      `apiserver ${pods.length}건 모두 --encryption-provider-config 설정`,
      `All ${pods.length} kube-apiserver pod(s) Ready=True with --encryption-provider-config`,
    )
  }
  if (encrypted.length > 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.etcdEncPartialOrUnready",
      `apiserver ${encrypted.length}/${pods.length}건 암호화 설정 · 미준비 ${notReady.length}건`,
      `Encryption configured on ${encrypted.length}/${pods.length} kube-apiserver pod(s); not ready: ${notReady.length}`,
    )
  }
  return makeOutcome(
    "fail",
    "compliance.kisa.reason.etcdEncMissing",
    `apiserver ${pods.length - encrypted.length}/${pods.length}건 암호화 미설정`,
    `${pods.length - encrypted.length}/${pods.length} kube-apiserver pod(s) missing --encryption-provider-config`,
  )
}

async function checkPod01(): Promise<KisaCheckOutcome> {
  const { getKisaNamespaces } = await import("./k8s-client")
  const nsRes = await getKisaNamespaces()
  if (nsRes.status !== "ok") {
    return fetchErrorOutcome(nsRes, "네임스페이스 목록", "Namespace list")
  }
  const considered = nsRes.data.filter((n) => n.name !== "kube-node-lease" && n.name !== "kube-public")
  if (considered.length === 0) {
    return makeOutcome("manual", "compliance.kisa.reason.resourceMissing", "네임스페이스 정보 없음", "No namespace data", { live: false })
  }

  const enforced = considered.filter((n) => PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/enforce"] ?? ""))
  const auditOrWarnNonPriv = considered.filter(
    (n) =>
      !PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/enforce"] ?? "") &&
      (PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/audit"] ?? "") ||
        PSA_ENFORCED_LEVELS.has(n.labels["pod-security.kubernetes.io/warn"] ?? "")),
  )
  const privilegedOnly = considered.length - enforced.length - auditOrWarnNonPriv.length

  if (enforced.length === considered.length) {
    return makeOutcome(
      "pass",
      "compliance.kisa.reason.podPsaEnforced",
      `대상 네임스페이스 ${considered.length}건 모두 PSA enforce(baseline/restricted) 적용`,
      `All ${considered.length} target namespace(s) enforce PSA baseline/restricted`,
    )
  }
  if (enforced.length > 0 || auditOrWarnNonPriv.length > 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.podPsaPartial",
      `PSA enforce(baseline/restricted) ${enforced.length}/${considered.length}건 · audit/warn만 ${auditOrWarnNonPriv.length}건 (privileged/미설정 ${privilegedOnly}건)`,
      `PSA enforce(baseline/restricted) ${enforced.length}/${considered.length}; audit/warn only ${auditOrWarnNonPriv.length} (privileged/unset ${privilegedOnly})`,
    )
  }
  return makeOutcome(
    "fail",
    "compliance.kisa.reason.podPsaMissing",
    `PSA baseline/restricted 라벨 적용 네임스페이스 없음 (0/${considered.length}건)`,
    `No namespace applies PSA baseline/restricted (0/${considered.length})`,
  )
}

async function checkNet01(): Promise<KisaCheckOutcome> {
  const { getKisaNetworkPolicies } = await import("./k8s-client")
  const npRes = await getKisaNetworkPolicies()
  if (npRes.status !== "ok") {
    return fetchErrorOutcome(npRes, "NetworkPolicy 목록", "NetworkPolicy list")
  }
  const policies = npRes.data

  // D-KISA-CLOSED-WORLD (R7): Kubernetes NetworkPolicies are additive — a namespace counts
  // as default-deny ONLY when it has a deny-all ingress policy AND zero policies in that
  // namespace declare an allow-all ingress rule.
  const covered: string[] = []
  const uncovered: string[] = []
  const allowAllNs: string[] = []
  for (const ns of KISA_NET01_NAMESPACES) {
    const nsPolicies = policies.filter((p) => p.metadata?.namespace === ns)
    const hasDefaultDeny = nsPolicies.some(isIngressDefaultDenyPolicy)
    const hasAllowAll = nsPolicies.some(hasAllowAllIngressRule)
    if (hasDefaultDeny && !hasAllowAll) {
      covered.push(ns)
    } else {
      uncovered.push(ns)
      if (hasAllowAll) allowAllNs.push(ns)
    }
  }

  const allowAllNoteKo = allowAllNs.length > 0 ? ` · 가산 allow-all 존재: ${allowAllNs.join(", ")}` : ""
  const allowAllNoteEn = allowAllNs.length > 0 ? `; additive allow-all present: ${allowAllNs.join(", ")}` : ""

  if (covered.length === KISA_NET01_NAMESPACES.length) {
    return makeOutcome(
      "pass",
      "compliance.kisa.reason.netDenyAll",
      `대상 네임스페이스 ${KISA_NET01_NAMESPACES.length}건 모두 default-deny 적용`,
      `All ${KISA_NET01_NAMESPACES.length} target namespace(s) enforce ingress default-deny`,
    )
  }
  if (covered.length > 0) {
    return makeOutcome(
      "warn",
      "compliance.kisa.reason.netDenyPartial",
      `default-deny 적용 ${covered.length}/${KISA_NET01_NAMESPACES.length}건 (미적용: ${uncovered.join(", ")})${allowAllNoteKo}`,
      `default-deny applied in ${covered.length}/${KISA_NET01_NAMESPACES.length} namespace(s) (uncovered: ${uncovered.join(", ")})${allowAllNoteEn}`,
    )
  }
  return makeOutcome(
    "fail",
    "compliance.kisa.reason.netDenyMissing",
    `대상 네임스페이스에 유효한 ingress default-deny 격리 없음 (미적용: ${uncovered.join(", ")})${allowAllNoteKo}`,
    `No target namespace has effective ingress default-deny isolation (uncovered: ${uncovered.join(", ")})${allowAllNoteEn}`,
  )
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
        applyRich(await checkCp01())
      } else if (entry.id === "KISA-ETCD-01") {
        applyRich(await checkEtcd01())
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
        applyRich(await checkPod01())
      } else if (entry.id === "KISA-NET-01") {
        applyRich(await checkNet01())
      } else if (entry.id === "KISA-IMG-03") {
        applyRich(await checkImg03())
      } else if (entry.id === "KISA-CFG-01") {
        applyRich(await checkCfg01())
      } else if (entry.id === "KISA-TLS-02") {
        applyRich(await checkTls02())
      } else if (entry.id === "KISA-ADM-01") {
        applyRich(await checkAdm01())
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
