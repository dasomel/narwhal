import type { Localized } from "@/lib/i18n-utils"
import type { Severity } from "./security"

export type KisaStatus = "pass" | "fail" | "warn" | "manual"

export type KisaReasonKey =
  | "compliance.kisa.reason.apiForbidden"
  | "compliance.kisa.reason.apiUnavailable"
  | "compliance.kisa.reason.listTruncated"
  | "compliance.kisa.reason.resourceMissing"
  | "compliance.kisa.reason.etcdNoTlsBaseline"
  | "compliance.kisa.reason.etcdIsolatedPlainHttp"
  | "compliance.kisa.reason.etcdPolicyMissing"
  | "compliance.kisa.reason.etcdPolicyBroad"
  | "compliance.kisa.reason.etcdPolicyUnreviewed"
  | "compliance.kisa.reason.etcdPolicySelectorMismatch"
  | "compliance.kisa.reason.etcdContainerMissing"
  | "compliance.kisa.reason.secCronJobNotEvaluated"
  | "compliance.kisa.reason.secNoExternalKms"
  | "compliance.kisa.reason.secSecretMetaNotEvaluated"
  | "compliance.kisa.reason.secCronJobWeak"
  | "compliance.kisa.reason.secSecretExposed"
  | "compliance.kisa.reason.imgNodeManual"
  | "compliance.kisa.reason.tlsStrictNoExceptions"
  | "compliance.kisa.reason.tlsStrictWithExceptions"
  | "compliance.kisa.reason.tlsNotStrict"
  | "compliance.kisa.reason.obsConfigured"
  | "compliance.kisa.reason.obsNoChannel"
  | "compliance.kisa.reason.obsInvalidDestination"
  | "compliance.kisa.reason.obsFalcoDown"
  | "compliance.kisa.reason.obsConfigMissing"
  | "compliance.kisa.reason.imgPolicyEnforced"
  | "compliance.kisa.reason.imgVerifyAudit"
  | "compliance.kisa.reason.imgPolicyBaselineDiff"
  | "compliance.kisa.reason.imgPolicyNotReady"
  | "compliance.kisa.reason.imgPolicyInvalidRule"
  | "compliance.kisa.reason.imgViolations"
  | "compliance.kisa.reason.imgPolicyReportsNotEvaluated"
  | "compliance.kisa.reason.imgInventoryNotEvaluated"
  | "compliance.kisa.reason.rbacClean"
  | "compliance.kisa.reason.rbacHighBindings"
  | "compliance.kisa.reason.rbacCriticalBindings"
  | "compliance.kisa.reason.cpAuditEnabled"
  | "compliance.kisa.reason.cpAuditPartialOrUnready"
  | "compliance.kisa.reason.cpAuditMissing"
  | "compliance.kisa.reason.cpPodNotReady"
  | "compliance.kisa.reason.apiserverContainerMissing"
  | "compliance.kisa.reason.etcdEncEnabled"
  | "compliance.kisa.reason.etcdEncPartialOrUnready"
  | "compliance.kisa.reason.etcdEncMissing"
  | "compliance.kisa.reason.podPsaEnforced"
  | "compliance.kisa.reason.podPsaPartial"
  | "compliance.kisa.reason.podPsaMissing"
  | "compliance.kisa.reason.netDenyAll"
  | "compliance.kisa.reason.netDenyPartial"
  | "compliance.kisa.reason.netDenyMissing"
  | "compliance.kisa.reason.imgCveClean"
  | "compliance.kisa.reason.imgCveCritical"
  | "compliance.kisa.reason.imgCveHigh"
  | "compliance.kisa.reason.imgCveNoReports"
  | "compliance.kisa.reason.imgCveTruncated"
  | "compliance.kisa.reason.cfgClean"
  | "compliance.kisa.reason.cfgActionableFail"
  | "compliance.kisa.reason.cfgUnevaluated"
  | "compliance.kisa.reason.cfgEmpty"
  | "compliance.kisa.reason.tlsCertOk"
  | "compliance.kisa.reason.tlsCertInvalid"
  | "compliance.kisa.reason.tlsCertMissingExpiry"
  | "compliance.kisa.reason.admEnforced"
  | "compliance.kisa.reason.admPartialOrDiffers"
  | "compliance.kisa.reason.etcdWorkloadNotReady"

export interface KisaControl {
  id: string
  domain: string
  title: string
  severity: Severity
  status: KisaStatus
  standardRefs: string[]
  evidence: string
  evidenceLocalized?: Localized
  remediation: string
  live: boolean
  detail?: string
  detailLocalized?: Localized
  reasonKey?: KisaReasonKey
  partial?: boolean
  partialSources?: string[]
}

export interface KisaSummary {
  total: number
  pass: number
  fail: number
  warn: number
  manual: number
  lastUpdated: string
}

export interface KisaResponse {
  controls: KisaControl[]
  summary: KisaSummary
  partial?: boolean
  partialSources?: string[]
}
