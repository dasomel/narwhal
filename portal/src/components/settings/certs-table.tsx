"use client"
import { useEffect, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSession } from "next-auth/react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { DataState } from "@/components/ui/data-state"
import { useLocale, useT } from "@/lib/i18n-client"
import type { Certificate } from "@/lib/k8s-client"

export const CERT_POLL_INTERVAL_MS = 2_000
export const CERT_POLL_TIMEOUT_MS = 30_000

interface RenewBaseline {
  notBefore: string | null
  revision: number | null
}

export type RenewRowState =
  | { phase: "issuing"; baseline: RenewBaseline; startedAt: number }
  | { phase: "renewed"; renewedAt: string }
  | { phase: "pending" }
  | { phase: "error" }

function certRowKey(cert: Pick<Certificate, "namespace" | "name">): string {
  return `${cert.namespace}/${cert.name}`
}

function formatCertTimestamp(dateStr: string | null, locale: string): string {
  if (!dateStr || Number.isNaN(Date.parse(dateStr))) return "—"
  return new Date(dateStr).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")
}

export function hasCertificateRenewed(
  cert: Pick<Certificate, "notBefore" | "revision" | "issuing">,
  baseline: RenewBaseline,
): boolean {
  if (cert.issuing) return false
  return cert.notBefore !== baseline.notBefore || (cert.revision ?? null) !== baseline.revision
}

function reconcileRenewStates(
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
      next[key] = { phase: "renewed", renewedAt: cert.notBefore ?? new Date(now).toISOString() }
      changed = true
    } else if (now - state.startedAt >= CERT_POLL_TIMEOUT_MS) {
      next[key] = { phase: "pending" }
      changed = true
    }
  }

  return changed ? next : prev
}

function expireTimedOutRenewStates(
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

function daysUntil(dateStr: string | null): number | null {
  if (!dateStr) return null
  const diff = new Date(dateStr).getTime() - Date.now()
  return Math.floor(diff / (1000 * 60 * 60 * 24))
}

function expiryBadge(days: number | null): { class: string; label: string } {
  if (days === null) return { class: "bg-muted text-muted-foreground", label: "—" }
  if (days < 0) return { class: "bg-narwhal-danger/15 text-narwhal-danger", label: "Expired" }
  if (days < 30) return { class: "bg-narwhal-danger/15 text-narwhal-danger", label: `${days}d` }
  if (days < 90) return { class: "bg-narwhal-warning/15 text-narwhal-warning", label: `${days}d` }
  return { class: "bg-narwhal-success/15 text-narwhal-success", label: `${days}d` }
}

function renderRenewAction({
  state,
  isSubmitting,
  locale,
  t,
  onRenew,
}: {
  state: RenewRowState | undefined
  isSubmitting: boolean
  locale: string
  t: ReturnType<typeof useT>
  onRenew: () => void
}) {
  if (state?.phase === "issuing") {
    return (
      <Badge role="status" className="bg-narwhal-warning/15 text-narwhal-warning">
        {t("certs.renewIssuing")}
      </Badge>
    )
  }
  if (state?.phase === "renewed") {
    return (
      <Badge role="status" className="bg-narwhal-success/15 text-narwhal-success">
        {t("certs.renewedAt", { time: formatCertTimestamp(state.renewedAt, locale) })}
      </Badge>
    )
  }
  if (state?.phase === "pending") {
    return (
      <Badge role="status" className="bg-narwhal-warning/15 text-narwhal-warning">
        {t("certs.renewStillPending")}
      </Badge>
    )
  }
  if (state?.phase === "error") {
    return (
      <Badge role="alert" className="bg-narwhal-danger/15 text-narwhal-danger">
        {t("certs.renewError")}
      </Badge>
    )
  }

  return (
    <button
      type="button"
      onClick={onRenew}
      disabled={isSubmitting}
      className="text-xs px-2 py-1 rounded border border-narwhal-accent/50 text-narwhal-accent hover:bg-narwhal-accent/10 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
    >
      {isSubmitting ? t("certs.renewing") : t("certs.renew")}
    </button>
  )
}

export function CertsTable() {
  const t = useT()
  const locale = useLocale()
  const queryClient = useQueryClient()
  const { data: session } = useSession()
  const role = (session?.user as { role?: string })?.role ?? "guest"
  const isAdmin = role === "cluster-admin"

  // D-CERT-RENEW-STATE: keep per-row feedback in CertsTable (keyed by namespace/name)
  // rather than inside a row button component so refetches that re-render the table
  // cannot drop in-flight polling or completion messages.
  const [renewStates, setRenewStates] = useState<Record<string, RenewRowState>>({})
  const [submittingKeys, setSubmittingKeys] = useState<Record<string, boolean>>({})
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    let earliestDeadline: number | null = null
    for (const state of Object.values(renewStates)) {
      if (state.phase !== "issuing") continue
      const deadline = state.startedAt + CERT_POLL_TIMEOUT_MS
      if (earliestDeadline === null || deadline < earliestDeadline) {
        earliestDeadline = deadline
      }
    }
    if (earliestDeadline === null) return

    const delay = Math.max(0, earliestDeadline - Date.now())
    const timer = setTimeout(() => {
      if (!mountedRef.current) return
      setRenewStates((prev) => expireTimedOutRenewStates(prev, Date.now()))
    }, delay)
    return () => clearTimeout(timer)
  }, [renewStates])

  const hasActiveIssuing = Object.values(renewStates).some((s) => s.phase === "issuing")

  const { data: certs, isLoading, isError, refetch } = useQuery<Certificate[]>({
    queryKey: ["settings-certs"],
    queryFn: async () => {
      const r = await fetch("/api/settings/certs")
      if (!r.ok) throw new Error(r.statusText)
      const data = (await r.json()) as Certificate[]
      if (mountedRef.current && Array.isArray(data)) {
        setRenewStates((prev) => reconcileRenewStates(prev, data, Date.now()))
      }
      return data
    },
    refetchInterval: hasActiveIssuing ? CERT_POLL_INTERVAL_MS : false,
  })

  const renewMutation = useMutation({
    mutationFn: async ({ name, namespace }: { name: string; namespace: string; baseline: RenewBaseline }) => {
      const r = await fetch("/api/settings/certs/renew", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, namespace }),
      })
      if (!r.ok) throw new Error(r.statusText)
      return r.json()
    },
    onMutate: ({ name, namespace }) => {
      const key = certRowKey({ name, namespace })
      if (!mountedRef.current) return
      setSubmittingKeys((prev) => ({ ...prev, [key]: true }))
    },
    onSuccess: (_data, { name, namespace, baseline }) => {
      if (!mountedRef.current) return
      const key = certRowKey({ name, namespace })
      setRenewStates((prev) => ({
        ...prev,
        [key]: { phase: "issuing", baseline, startedAt: Date.now() },
      }))
      void queryClient.invalidateQueries({ queryKey: ["settings-certs"] })
    },
    onError: (_err, { name, namespace }) => {
      if (!mountedRef.current) return
      const key = certRowKey({ name, namespace })
      setRenewStates((prev) => ({ ...prev, [key]: { phase: "error" } }))
    },
    onSettled: (_data, _err, { name, namespace }) => {
      if (!mountedRef.current) return
      const key = certRowKey({ name, namespace })
      setSubmittingKeys((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    },
  })

  const certList = Array.isArray(certs) ? certs : []

  return (
    <Card className="p-5">
      <h2 className="font-semibold text-foreground mb-4">{t("certs.title")}</h2>
      {isLoading ? <DataState state="loading" onRetry={() => { void refetch() }} />
        : isError ? <DataState state="error" onRetry={() => { void refetch() }} />
        : !certList.length ? <DataState state="empty" /> : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="pb-2 font-medium">{t("certs.name")}</th>
              <th className="pb-2 font-medium">{t("certs.namespace")}</th>
              <th className="pb-2 font-medium">{t("certs.dnsNames")}</th>
              <th className="pb-2 font-medium">{t("certs.issuer")}</th>
              <th className="pb-2 font-medium">{t("certs.issued")}</th>
              <th className="pb-2 font-medium">{t("certs.expiry")}</th>
              <th className="pb-2 font-medium">{t("certs.status")}</th>
              {isAdmin && <th className="pb-2 font-medium">{t("certs.action")}</th>}
            </tr>
          </thead>
          <tbody>
            {certList.map((cert) => {
              const key = certRowKey(cert)
              const days = daysUntil(cert.notAfter)
              const badge = expiryBadge(days)
              const expiringSoon = days !== null && days >= 0 && days < 30
              const revision = cert.revision ?? null
              return (
                <tr key={key} className="border-b last:border-0">
                  <td className="py-2.5 font-medium">
                    <span>{cert.name}</span>
                    {expiringSoon && (
                      <Badge className="ml-2 bg-narwhal-warning/15 text-narwhal-warning text-xs">
                        {t("certs.expiringSoon")}
                      </Badge>
                    )}
                  </td>
                  <td className="py-2.5 text-muted-foreground">{cert.namespace}</td>
                  <td className="py-2.5 text-muted-foreground font-mono text-xs">
                    {cert.dnsNames.length > 0 ? cert.dnsNames.join(", ") : "—"}
                  </td>
                  <td className="py-2.5 text-muted-foreground">{cert.issuer}</td>
                  <td className="py-2.5 text-muted-foreground text-xs whitespace-nowrap">
                    <span>{formatCertTimestamp(cert.notBefore, locale)}</span>
                    {revision !== null && (
                      <span className="ml-1.5 font-mono text-muted-foreground/80">
                        {t("certs.revision", { revision })}
                      </span>
                    )}
                  </td>
                  <td className="py-2.5">
                    <Badge className={badge.class}>{badge.label}</Badge>
                  </td>
                  <td className="py-2.5">
                    <Badge className={cert.ready ? "bg-narwhal-success/15 text-narwhal-success" : "bg-narwhal-danger/15 text-narwhal-danger"}>
                      {cert.ready ? t("certs.ready") : t("certs.notReady")}
                    </Badge>
                  </td>
                  {isAdmin && (
                    <td className="py-2.5">
                      {renderRenewAction({
                        state: renewStates[key],
                        isSubmitting: Boolean(submittingKeys[key]),
                        locale,
                        t,
                        onRenew: () =>
                          renewMutation.mutate({
                            name: cert.name,
                            namespace: cert.namespace,
                            baseline: { notBefore: cert.notBefore, revision },
                          }),
                      })}
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}
