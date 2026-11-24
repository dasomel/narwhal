import "server-only"
import type { Localized } from "./i18n-utils"
import type { KisaReasonKey, KisaStatus } from "@/types/kisa"
import type {
  KisaFetchResult,
  KisaNetworkPolicyPeer,
  KisaRawNetworkPolicy,
  KisaRawPeerAuthentication,
} from "./k8s-client"

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

function selectorMatchesLabels(
  selector: { matchLabels?: Record<string, string>; matchExpressions?: Array<{ key: string; operator: string; values?: string[] }> } | undefined,
  labels: Record<string, string>,
): boolean {
  if (!selector) return true
  for (const [k, v] of Object.entries(selector.matchLabels ?? {})) {
    if (labels[k] !== v) return false
  }
  for (const expr of selector.matchExpressions ?? []) {
    const val = labels[expr.key]
    const vals = expr.values ?? []
    if (expr.operator === "In" && (!val || !vals.includes(val))) return false
    if (expr.operator === "NotIn" && val !== undefined && vals.includes(val)) return false
    if (expr.operator === "Exists" && val === undefined) return false
    if (expr.operator === "DoesNotExist" && val !== undefined) return false
  }
  return true
}

function isApisixGatewayPeer(peer: KisaNetworkPolicyPeer): boolean {
  if (peer.ipBlock) return false
  if (peer.namespaceSelector) {
    const nsLabels = peer.namespaceSelector.matchLabels ?? {}
    if (
      nsLabels["kubernetes.io/metadata.name"] !== "platform-system" ||
      (peer.namespaceSelector.matchExpressions && peer.namespaceSelector.matchExpressions.length > 0)
    ) {
      return false
    }
  }
  const podLabels = peer.podSelector?.matchLabels
  if (!podLabels || Object.keys(podLabels).length === 0) return false
  if (peer.podSelector?.matchExpressions && peer.podSelector.matchExpressions.length > 0) return false
  return podLabels["app.kubernetes.io/name"] === "apisix" || podLabels["app"] === "apisix"
}

export async function checkEtcd02(): Promise<KisaCheckOutcome> {
  const { getKisaDeployment, getKisaNamespacedNetworkPolicies } = await import("./k8s-client")
  const [depRes, npRes] = await Promise.all([
    getKisaDeployment("platform-system", "apisix-etcd"),
    getKisaNamespacedNetworkPolicies("platform-system"),
  ])

  if (depRes.status !== "ok") return fetchErrorOutcome(depRes, "Deployment platform-system/apisix-etcd", "Deployment platform-system/apisix-etcd")
  if (npRes.status !== "ok") return fetchErrorOutcome(npRes, "platform-system NetworkPolicy", "platform-system NetworkPolicies")

  const dep = depRes.data
  const podLabels = dep.spec?.template?.metadata?.labels ?? { app: "apisix-etcd" }
  const containers = dep.spec?.template?.spec?.containers ?? []
  // D-KISA-CLOSED-WORLD: inspect ONLY the exact "etcd" container defined in
  // gitops/charts/narwhal-platform/templates/apisix-infra.yaml; never merge args across
  // containers (a sidecar's TLS flags must never satisfy ETCD-02).
  const etcdContainer = containers.find((c) => c.name === "etcd")
  if (!etcdContainer) {
    return makeOutcome(
      "manual",
      "compliance.kisa.reason.etcdContainerMissing",
      "Deployment platform-system/apisix-etcd에 'etcd' 컨테이너가 없음 — 평가 불가",
      "Container 'etcd' not found in Deployment platform-system/apisix-etcd — not evaluated",
      { live: false },
    )
  }
  const tokens = [...(etcdContainer.command ?? []), ...(etcdContainer.args ?? [])]

  const clientUrls = extractFlagValues(tokens, "--listen-client-urls")
    .flatMap((v) => v.split(","))
    .map((v) => v.trim())
    .filter(Boolean)
  const hasHttpsClient = clientUrls.length > 0 && clientUrls.every((u) => u.startsWith("https://"))
  const hasCertFiles = extractFlagValues(tokens, "--cert-file").length > 0 && extractFlagValues(tokens, "--key-file").length > 0
  const hasClientAuth = hasEnabledFlag(tokens, "--client-cert-auth") && extractFlagValues(tokens, "--trusted-ca-file").length > 0
  const tlsAuthEnabled = hasHttpsClient && hasCertFiles && hasClientAuth

  const policies: KisaRawNetworkPolicy[] = npRes.data
  const selectingIngress = policies.filter((p) => {
    const types = p.spec?.policyTypes ?? (p.spec?.ingress ? ["Ingress"] : [])
    return types.includes("Ingress") && selectorMatchesLabels(p.spec?.podSelector, podLabels)
  })

  if (selectingIngress.length === 0) {
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.etcdPolicyMissing",
      `apisix-etcd를 선택하는 Ingress NetworkPolicy 없음 (${tlsAuthEnabled ? "TLS·인증 활성" : "etcd 클라이언트 TLS·인증 미활성화 (평문 HTTP)"})`,
      `No Ingress NetworkPolicy selects apisix-etcd (${tlsAuthEnabled ? "client TLS/auth enabled" : "etcd client TLS/auth not enabled (plain HTTP)"})`,
    )
  }

  let hasBroadOrAllowAll = false
  let hasApisix2379Allow = false
  const policyNames = selectingIngress.map((p) => p.metadata?.name ?? "unknown")

  for (const pol of selectingIngress) {
    const ingressRules = pol.spec?.ingress ?? []
    for (const rule of ingressRules) {
      const fromPeers = rule.from
      if (!fromPeers || fromPeers.length === 0) {
        hasBroadOrAllowAll = true
        continue
      }
      if (!fromPeers.every(isApisixGatewayPeer)) {
        hasBroadOrAllowAll = true
        continue
      }
      const ports = rule.ports
      if (!ports || ports.length === 0 || ports.some((pt) => String(pt.port) === "2379")) {
        hasApisix2379Allow = true
      }
    }
  }

  if (hasBroadOrAllowAll) {
    return makeOutcome(
      "fail",
      "compliance.kisa.reason.etcdPolicyBroad",
      `apisix-etcd NetworkPolicy(${policyNames.join(", ")})가 전체 허용이거나 APISIX 외 피어를 허용함 (${tlsAuthEnabled ? "TLS·인증 활성" : "평문 HTTP"})`,
      `apisix-etcd NetworkPolicy (${policyNames.join(", ")}) allows all sources or non-APISIX peers (${tlsAuthEnabled ? "TLS/auth enabled" : "plain HTTP"})`,
    )
  }

  const isolationNoteKo = hasApisix2379Allow
    ? `NetworkPolicy(${policyNames.join(", ")})가 2379/TCP 인바운드를 APISIX 게이트웨이로만 제한`
    : `NetworkPolicy(${policyNames.join(", ")})가 apisix-etcd 인바운드를 차단(default-deny)`
  const isolationNoteEn = hasApisix2379Allow
    ? `NetworkPolicy (${policyNames.join(", ")}) restricts TCP 2379 ingress strictly to APISIX gateway pods`
    : `NetworkPolicy (${policyNames.join(", ")}) denies ingress to apisix-etcd`

  if (tlsAuthEnabled) {
    const specReplicas = dep.spec?.replicas
    const readyReplicas = dep.status?.readyReplicas
    const statusReplicas = dep.status?.replicas
    if (typeof specReplicas !== "number" || typeof readyReplicas !== "number") {
      return makeOutcome(
        "manual",
        "compliance.kisa.reason.etcdWorkloadNotReady",
        `${isolationNoteKo} · etcd 클라이언트 TLS·인증 활성화 · Deployment replicas/readyReplicas 상태 미확인 — 평가 불가`,
        `${isolationNoteEn}; etcd client TLS/auth enabled; Deployment replicas/readyReplicas status missing — not evaluated`,
        { live: false },
      )
    }
    if (readyReplicas < 1 || specReplicas !== readyReplicas || (statusReplicas !== undefined && statusReplicas !== readyReplicas)) {
      return makeOutcome(
        "warn",
        "compliance.kisa.reason.etcdWorkloadNotReady",
        `${isolationNoteKo} · etcd 클라이언트 TLS·인증 설정됨 · 워크로드 미준비(readyReplicas ${readyReplicas}/${specReplicas})`,
        `${isolationNoteEn}; etcd client TLS/auth configured; workload not ready (readyReplicas ${readyReplicas}/${specReplicas})`,
      )
    }
    return makeOutcome(
      "pass",
      "compliance.kisa.reason.etcdIsolatedTls",
      `${isolationNoteKo} · etcd 클라이언트 TLS·인증(--client-cert-auth) 활성화 (ready ${readyReplicas}/${specReplicas})`,
      `${isolationNoteEn}; etcd client TLS and --client-cert-auth enabled (ready ${readyReplicas}/${specReplicas})`,
    )
  }

  return makeOutcome(
    "warn",
    "compliance.kisa.reason.etcdIsolatedPlainHttp",
    `${isolationNoteKo} · 잔여 위험: etcd 클라이언트 TLS·인증 미활성화 (평문 HTTP: ${clientUrls.join(", ") || "기본값"})`,
    `${isolationNoteEn}; remaining gap: etcd client TLS/auth not enabled (plain HTTP: ${clientUrls.join(", ") || "default"})`,
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
