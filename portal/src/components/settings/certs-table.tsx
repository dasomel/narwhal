"use client"
import { useEffect, useState, useSyncExternalStore } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useSession } from "next-auth/react"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { DataState } from "@/components/ui/data-state"
import { useLocale, useT } from "@/lib/i18n-client"
import type { Certificate } from "@/lib/k8s-client"
import {
  CERT_PENDING_POLL_INTERVAL_MS,
  CERT_PENDING_TIMEOUT_MS,
  CERT_POLL_INTERVAL_MS,
  CERT_POLL_TIMEOUT_MS,
  CERT_RENEW_TIMEOUT_MS,
  bindCertRenewToQueryClient,
  certRowKey,
  createCertRenewController,
  createCertsQueryOptions,
  formatCertTimestamp,
  hasCertificateRenewed,
  postCertRenew,
  type CertRenewController,
  type RenewRowState,
} from "./certs-renew"

export {
  CERT_PENDING_POLL_INTERVAL_MS,
  CERT_PENDING_TIMEOUT_MS,
  CERT_POLL_INTERVAL_MS,
  CERT_POLL_TIMEOUT_MS,
  CERT_RENEW_TIMEOUT_MS,
  createCertRenewController,
  formatCertTimestamp,
  hasCertificateRenewed,
  type CertRenewController,
  type RenewRowState,
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

export function CertsTable({ controller: externalController }: { controller?: CertRenewController } = {}) {
  const t = useT()
  const locale = useLocale()
  const queryClient = useQueryClient()
  const { data: session } = useSession()
  const role = (session?.user as { role?: string })?.role ?? "guest"
  const isAdmin = role === "cluster-admin"

  const renewMutation = useMutation({
    mutationFn: postCertRenew,
  })

  const mutateRenewAsync = renewMutation.mutateAsync

  // D-CERT-RENEW-STATE: keep per-row feedback in a controller (keyed by namespace/name)
  // rather than inside a row button component so refetches that re-render the table
  // cannot drop in-flight polling or completion messages.
  const [internalController] = useState(() => createCertRenewController())
  const controller = externalController ?? internalController

  useEffect(() => {
    if (externalController) return
    return bindCertRenewToQueryClient(internalController, queryClient, mutateRenewAsync)
  }, [externalController, internalController, mutateRenewAsync, queryClient])

  const { renewStates, submittingKeys } = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  )

  const { data: certs, isLoading, isError, refetch } = useQuery<Certificate[]>(
    createCertsQueryOptions(controller, renewStates),
  )

  const certList = Array.isArray(certs) ? certs : []

  return (
    <Card className="p-5">
      <h2 className="font-semibold text-foreground mb-4">{t("certs.title")}</h2>
      {isLoading ? <DataState state="loading" onRetry={() => { void refetch() }} />
        : isError && !certs ? <DataState state="error" onRetry={() => { void refetch() }} />
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
                        onRenew: () => {
                          void controller.renew(cert)
                        },
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
