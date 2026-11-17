import type { Certificate } from "@/lib/k8s-client"

export const CERT_RENEW_TIMEOUT_MS = 15_000
export const CERT_POLL_INTERVAL_MS = 2_000
export const CERT_POLL_TIMEOUT_MS = 30_000
export const CERT_PENDING_POLL_INTERVAL_MS = 10_000
export const CERT_PENDING_TIMEOUT_MS = 300_000
export const CERTS_QUERY_KEY = ["settings-certs"] as const

export interface RenewBaseline {
  notBefore?: string | null
  revision?: number | null
}

interface NormalizedRenewBaseline {
  notBefore: string | null
  revision: number | null
}

export type RenewRowState =
  | { phase: "issuing"; baseline: NormalizedRenewBaseline; startedAt: number }
  | { phase: "pending"; baseline: NormalizedRenewBaseline; startedAt: number; polling: boolean }
  | { phase: "renewed"; renewedAt: string }
  | { phase: "error" }

export interface CertRenewSnapshot {
  renewStates: Record<string, RenewRowState>
  submittingKeys: Record<string, boolean>
}

export interface CertRenewControllerOptions {
  postRenew?: (params: { name: string; namespace: string; signal?: AbortSignal }) => Promise<unknown>
  onRenewAccepted?: () => void
  now?: () => number
}

export interface CertRenewController {
  mount: () => void
  dispose: () => void
  setHandlers: (handlers: Pick<CertRenewControllerOptions, "postRenew" | "onRenewAccepted">) => void
  subscribe: (listener: () => void) => () => void
  getSnapshot: () => CertRenewSnapshot
  renew: (cert: Pick<Certificate, "name" | "namespace" | "notBefore" | "revision">) => Promise<void>
  reconcile: (certs: Certificate[], now?: number) => void
}

export function certRowKey(cert: Pick<Certificate, "namespace" | "name">): string {
  return `${cert.namespace}/${cert.name}`
}

export function formatCertTimestamp(dateStr: string | null, locale: string): string {
  if (!dateStr || Number.isNaN(Date.parse(dateStr))) return "—"
  return new Date(dateStr).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")
}

function normalizeNotBefore(value: string | null | undefined): string | null {
  return typeof value === "string" && value.length > 0 ? value : null
}

function normalizeRevision(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

export function hasCertificateRenewed(
  cert: Pick<Certificate, "notBefore" | "revision" | "issuing">,
  baseline: RenewBaseline,
): boolean {
  if (cert.issuing) return false

  const nextNotBefore = normalizeNotBefore(cert.notBefore)
  const baseNotBefore = normalizeNotBefore(baseline.notBefore)
  const notBeforeChanged = nextNotBefore !== null && nextNotBefore !== baseNotBefore

  const nextRevision = normalizeRevision(cert.revision)
  const baseRevision = normalizeRevision(baseline.revision)
  const revisionIncreased =
    baseRevision !== null && nextRevision !== null && nextRevision > baseRevision

  return notBeforeChanged || revisionIncreased
}

function expireRowState(state: RenewRowState, now: number): RenewRowState {
  if (state.phase === "issuing" && now - state.startedAt >= CERT_POLL_TIMEOUT_MS) {
    return {
      phase: "pending",
      baseline: state.baseline,
      startedAt: state.startedAt,
      polling: now - state.startedAt < CERT_PENDING_TIMEOUT_MS,
    }
  }
  if (state.phase === "pending" && state.polling && now - state.startedAt >= CERT_PENDING_TIMEOUT_MS) {
    return {
      phase: "pending",
      baseline: state.baseline,
      startedAt: state.startedAt,
      polling: false,
    }
  }
  return state
}

export function reconcileRenewStates(
  prev: Record<string, RenewRowState>,
  certs: Certificate[],
  now: number,
): Record<string, RenewRowState> {
  let changed = false
  const next: Record<string, RenewRowState> = { ...prev }
  const byKey = new Map(certs.map((c) => [certRowKey(c), c]))

  for (const [key, state] of Object.entries(prev)) {
    if (state.phase !== "issuing" && state.phase !== "pending") continue
    const cert = byKey.get(key)
    if (cert && hasCertificateRenewed(cert, state.baseline)) {
      next[key] = {
        phase: "renewed",
        renewedAt: normalizeNotBefore(cert.notBefore) ?? new Date(now).toISOString(),
      }
      changed = true
      continue
    }
    const expired = expireRowState(state, now)
    if (expired !== state) {
      next[key] = expired
      changed = true
    }
  }

  return changed ? next : prev
}

export function expireTimedOutRenewStates(
  prev: Record<string, RenewRowState>,
  now: number,
): Record<string, RenewRowState> {
  let changed = false
  const next: Record<string, RenewRowState> = { ...prev }
  for (const [key, state] of Object.entries(prev)) {
    const expired = expireRowState(state, now)
    if (expired !== state) {
      next[key] = expired
      changed = true
    }
  }
  return changed ? next : prev
}

export function getCertPollIntervalMs(
  renewStates: Record<string, RenewRowState>,
): number | false {
  let hasPendingPolling = false
  for (const state of Object.values(renewStates)) {
    if (state.phase === "issuing") return CERT_POLL_INTERVAL_MS
    if (state.phase === "pending" && state.polling) hasPendingPolling = true
  }
  return hasPendingPolling ? CERT_PENDING_POLL_INTERVAL_MS : false
}

export async function postCertRenew({
  name,
  namespace,
  signal,
}: {
  name: string
  namespace: string
  signal?: AbortSignal
}): Promise<unknown> {
  const r = await fetch("/api/settings/certs/renew", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, namespace }),
    signal,
  })
  if (!r.ok) throw new Error(r.statusText)
  return r.json()
}

export async function fetchCertificates(
  controller?: Pick<CertRenewController, "reconcile">,
): Promise<Certificate[]> {
  const r = await fetch("/api/settings/certs")
  if (!r.ok) throw new Error(r.statusText)
  const data = (await r.json()) as Certificate[]
  if (Array.isArray(data) && controller) {
    controller.reconcile(data)
  }
  return data
}

export function createCertsQueryOptions(
  controller: CertRenewController,
  renewStates: Record<string, RenewRowState> = controller.getSnapshot().renewStates,
) {
  return {
    queryKey: CERTS_QUERY_KEY,
    queryFn: () => fetchCertificates(controller),
    refetchInterval: getCertPollIntervalMs(renewStates),
  }
}

export function bindCertRenewToQueryClient(
  controller: CertRenewController,
  queryClient: {
    invalidateQueries: (filters: { queryKey: readonly unknown[] }) => Promise<unknown> | unknown
  },
  postRenew: CertRenewControllerOptions["postRenew"] = postCertRenew,
): () => void {
  controller.mount()
  controller.setHandlers({
    postRenew,
    onRenewAccepted: () => {
      void queryClient.invalidateQueries({ queryKey: CERTS_QUERY_KEY })
    },
  })
  return () => controller.dispose()
}

function raceWithAbort<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) {
    promise.catch(() => {})
    return Promise.reject(signal.reason ?? new Error("Aborted"))
  }
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      // Prevent unhandled rejection if the underlying request ignores signal and settles later.
      promise.catch(() => {})
      reject(signal.reason ?? new Error("Aborted"))
    }
    signal.addEventListener("abort", onAbort, { once: true })
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort)
        resolve(value)
      },
      (err) => {
        signal.removeEventListener("abort", onAbort)
        reject(err)
      },
    )
  })
}

export function createCertRenewController(
  options: CertRenewControllerOptions = {},
): CertRenewController {
  let postRenew = options.postRenew ?? postCertRenew
  let onRenewAccepted = options.onRenewAccepted
  const now = options.now ?? Date.now

  let snapshot: CertRenewSnapshot = { renewStates: {}, submittingKeys: {} }
  const listeners = new Set<() => void>()
  const inflightPostControllers = new Set<AbortController>()
  let timeoutId: ReturnType<typeof setTimeout> | null = null
  let scheduledDeadline: number | null = null
  let disposed = false

  const clearPendingTimer = () => {
    if (timeoutId !== null) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
    scheduledDeadline = null
  }

  const scheduleTimeout = (states: Record<string, RenewRowState>) => {
    if (disposed) {
      clearPendingTimer()
      return
    }
    let earliestDeadline: number | null = null
    for (const state of Object.values(states)) {
      let deadline: number | null = null
      if (state.phase === "issuing") {
        deadline = state.startedAt + CERT_POLL_TIMEOUT_MS
      } else if (state.phase === "pending" && state.polling) {
        deadline = state.startedAt + CERT_PENDING_TIMEOUT_MS
      }
      if (deadline !== null && (earliestDeadline === null || deadline < earliestDeadline)) {
        earliestDeadline = deadline
      }
    }
    if (earliestDeadline === null) {
      clearPendingTimer()
      return
    }
    if (timeoutId !== null && scheduledDeadline === earliestDeadline) {
      return
    }
    clearPendingTimer()
    scheduledDeadline = earliestDeadline
    const delay = Math.max(0, earliestDeadline - now())
    timeoutId = setTimeout(() => {
      timeoutId = null
      scheduledDeadline = null
      if (disposed) return
      const nextStates = expireTimedOutRenewStates(snapshot.renewStates, now())
      if (nextStates !== snapshot.renewStates) {
        commit({ renewStates: nextStates, submittingKeys: snapshot.submittingKeys })
      }
    }, delay)
  }

  const commit = (next: CertRenewSnapshot) => {
    if (disposed) return
    snapshot = next
    scheduleTimeout(snapshot.renewStates)
    for (const listener of listeners) {
      listener()
    }
  }

  const mount = () => {
    disposed = false
    scheduleTimeout(snapshot.renewStates)
  }

  const setHandlers = (
    handlers: Pick<CertRenewControllerOptions, "postRenew" | "onRenewAccepted">,
  ) => {
    if (handlers.postRenew) postRenew = handlers.postRenew
    if ("onRenewAccepted" in handlers) onRenewAccepted = handlers.onRenewAccepted
  }

  const dispose = () => {
    if (disposed) return
    disposed = true
    clearPendingTimer()
    for (const abortController of inflightPostControllers) {
      abortController.abort(new Error("Disposed"))
    }
    inflightPostControllers.clear()
    listeners.clear()
  }

  const subscribe = (listener: () => void) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }

  const getSnapshot = () => snapshot

  const renew = async (
    cert: Pick<Certificate, "name" | "namespace" | "notBefore" | "revision">,
  ) => {
    if (disposed) return
    const key = certRowKey(cert)
    if (snapshot.submittingKeys[key] || snapshot.renewStates[key]?.phase === "issuing") return

    const baseline: NormalizedRenewBaseline = {
      notBefore: normalizeNotBefore(cert.notBefore),
      revision: normalizeRevision(cert.revision),
    }

    commit({
      renewStates: snapshot.renewStates,
      submittingKeys: { ...snapshot.submittingKeys, [key]: true },
    })

    const abortController = new AbortController()
    inflightPostControllers.add(abortController)
    const postTimerId = setTimeout(() => {
      abortController.abort(new Error("Certificate renewal request timed out"))
    }, CERT_RENEW_TIMEOUT_MS)

    try {
      const postPromise = Promise.resolve(
        postRenew({
          name: cert.name,
          namespace: cert.namespace,
          signal: abortController.signal,
        }),
      )
      await raceWithAbort(postPromise, abortController.signal)
      if (disposed) return
      const nextSubmitting = { ...snapshot.submittingKeys }
      delete nextSubmitting[key]
      commit({
        submittingKeys: nextSubmitting,
        renewStates: {
          ...snapshot.renewStates,
          [key]: { phase: "issuing", baseline, startedAt: now() },
        },
      })
      onRenewAccepted?.()
    } catch {
      if (disposed) return
      const nextSubmitting = { ...snapshot.submittingKeys }
      delete nextSubmitting[key]
      commit({
        submittingKeys: nextSubmitting,
        renewStates: {
          ...snapshot.renewStates,
          [key]: { phase: "error" },
        },
      })
    } finally {
      clearTimeout(postTimerId)
      inflightPostControllers.delete(abortController)
    }
  }

  const reconcile = (certs: Certificate[], currentNow = now()) => {
    if (disposed) return
    const nextStates = reconcileRenewStates(snapshot.renewStates, certs, currentNow)
    if (nextStates !== snapshot.renewStates) {
      commit({ renewStates: nextStates, submittingKeys: snapshot.submittingKeys })
    }
  }

  return { mount, dispose, setHandlers, subscribe, getSnapshot, renew, reconcile }
}
