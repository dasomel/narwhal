import type { Certificate } from "@/lib/k8s-client"

export const CERT_POLL_INTERVAL_MS = 2_000
export const CERT_POLL_TIMEOUT_MS = 30_000

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
  | { phase: "renewed"; renewedAt: string }
  | { phase: "pending" }
  | { phase: "error" }

export interface CertRenewSnapshot {
  renewStates: Record<string, RenewRowState>
  submittingKeys: Record<string, boolean>
}

export interface CertRenewControllerOptions {
  postRenew?: (params: { name: string; namespace: string }) => Promise<unknown>
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

export function reconcileRenewStates(
  prev: Record<string, RenewRowState>,
  certs: Certificate[],
  now: number,
): Record<string, RenewRowState> {
  let changed = false
  const next: Record<string, RenewRowState> = { ...prev }
  const byKey = new Map(certs.map((c) => [certRowKey(c), c]))

  for (const [key, state] of Object.entries(prev)) {
    if (state.phase !== "issuing") continue
    const cert = byKey.get(key)
    if (cert && hasCertificateRenewed(cert, state.baseline)) {
      next[key] = {
        phase: "renewed",
        renewedAt: normalizeNotBefore(cert.notBefore) ?? new Date(now).toISOString(),
      }
      changed = true
    } else if (now - state.startedAt >= CERT_POLL_TIMEOUT_MS) {
      next[key] = { phase: "pending" }
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
    if (state.phase === "issuing" && now - state.startedAt >= CERT_POLL_TIMEOUT_MS) {
      next[key] = { phase: "pending" }
      changed = true
    }
  }
  return changed ? next : prev
}

async function defaultPostRenew({ name, namespace }: { name: string; namespace: string }) {
  const r = await fetch("/api/settings/certs/renew", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, namespace }),
  })
  if (!r.ok) throw new Error(r.statusText)
  return r.json()
}

export function createCertRenewController(
  options: CertRenewControllerOptions = {},
): CertRenewController {
  let postRenew = options.postRenew ?? defaultPostRenew
  let onRenewAccepted = options.onRenewAccepted
  const now = options.now ?? Date.now

  let snapshot: CertRenewSnapshot = { renewStates: {}, submittingKeys: {} }
  const listeners = new Set<() => void>()
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
      if (state.phase !== "issuing") continue
      const deadline = state.startedAt + CERT_POLL_TIMEOUT_MS
      if (earliestDeadline === null || deadline < earliestDeadline) {
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

    try {
      await postRenew({ name: cert.name, namespace: cert.namespace })
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
