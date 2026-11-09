"use client"
import { useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Button } from "@/components/ui/button"
import { TestAlertButton } from "@/components/alerts/test-alert-button"
import { useT } from "@/lib/i18n-client"
import { notificationKeys, type ChannelInput, type ChannelStatus, type NotificationKey } from "@/types/notification-channels"

const queryKey = ["settings", "notification-channels"]
export function NotificationChannels() {
  const t = useT()
  const client = useQueryClient()
  const [changes, setChanges] = useState<ChannelInput["values"]>({})
  const [enabled, setEnabled] = useState<Partial<Pick<ChannelInput, "slackEnabled" | "emailEnabled">>>({})
  const query = useQuery<ChannelStatus>({
    queryKey,
    queryFn: async () => {
      const res = await fetch("/api/settings/notification-channels", { cache: "no-store" })
      if (!res.ok) throw new Error("Settings unavailable")
      return res.json()
    },
    staleTime: 0,
  })
  const mutation = useMutation({
    mutationFn: async (input: ChannelInput): Promise<ChannelStatus> => {
      const res = await fetch("/api/settings/notification-channels", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) })
      if (!res.ok) throw new Error(res.status === 400 ? "invalid" : "unknown")
      return res.json()
    },
    onSuccess: (status) => { client.setQueryData(queryKey, status); setChanges({}); setEnabled({}) },
  })
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!query.data) return
    mutation.mutate({ slackEnabled: enabled.slackEnabled ?? query.data.slackEnabled, emailEnabled: enabled.emailEnabled ?? query.data.emailEnabled, values: changes })
  }
  const labels: Record<NotificationKey, string> = {
    slack_webhook_url: t("notifications.slack_webhook_url"),
    slack_channel: t("notifications.slack_channel"),
    smtp_smarthost: t("notifications.smtp_smarthost"),
    smtp_from: t("notifications.smtp_from"),
    smtp_to: t("notifications.smtp_to"),
    smtp_auth_username: t("notifications.smtp_auth_username"),
    smtp_auth_password: t("notifications.smtp_auth_password"),
    smtp_require_tls: t("notifications.smtp_require_tls"),
  }
  function field(key: NotificationKey) {
    const isSet = query.data?.configured[key] ?? false
    return <div key={key} className="space-y-1">
      <label htmlFor={key} className="text-sm font-medium">{labels[key]} <span className="text-muted-foreground">({isSet ? t("notifications.set") : t("notifications.notSet")})</span></label>
      <div className="flex flex-wrap gap-2 items-center">
        {key === "smtp_require_tls" ? <select id={key} aria-label={labels[key]} className="rounded border bg-background p-2" value={changes[key] ?? ""} onChange={(e) => setChanges({ ...changes, [key]: e.target.value || undefined })}>
          <option value="">{t("notifications.keep")}</option><option value="true">{t("notifications.tlsYes")}</option><option value="false">{t("notifications.tlsNo")}</option>
        </select> : <input id={key} aria-label={labels[key]} className="min-w-0 flex-1 rounded border bg-background p-2 text-sm" type={key === "slack_webhook_url" || key === "smtp_auth_password" ? "password" : "text"} autoComplete="off" value={changes[key] ?? ""} disabled={changes[key] === null} placeholder={t("notifications.replace")} onChange={(e) => setChanges({ ...changes, [key]: e.target.value || undefined })} />}
        <label className="flex gap-2 items-center text-sm"><input type="checkbox" aria-label={t("notifications.clear")} checked={changes[key] === null} onChange={(e) => setChanges({ ...changes, [key]: e.target.checked ? null : undefined })} />{t("notifications.clear")}</label>
      </div>
    </div>
  }
  return <section className="rounded-lg border bg-card p-4 space-y-4">
    <h2 className="text-lg font-semibold">{t("notifications.title")}</h2>
    <p className="text-sm text-muted-foreground">{t("notifications.secretHint")}</p>
    {query.isPending && <p role="status">{t("alerts.loading")}</p>}
    {query.isError && <div role="alert"><p>{t("notifications.unavailable")}</p><Button variant="outline" onClick={() => void query.refetch()}>{t("alerts.refresh")}</Button></div>}
    <form onSubmit={submit} className="space-y-5">
      <fieldset disabled={!query.data || query.isError || mutation.isPending} className="space-y-5 disabled:opacity-60">
        <div className="space-y-3"><label className="flex items-center gap-2 font-medium"><input type="checkbox" aria-label={t("notifications.slackEnabled")} checked={enabled.slackEnabled ?? query.data?.slackEnabled ?? false} onChange={(e) => setEnabled({ ...enabled, slackEnabled: e.target.checked })} />{t("notifications.slackEnabled")}</label>{notificationKeys.filter((k) => k.startsWith("slack_")).map(field)}</div>
        <div className="space-y-3"><label className="flex items-center gap-2 font-medium"><input type="checkbox" aria-label={t("notifications.emailEnabled")} checked={enabled.emailEnabled ?? query.data?.emailEnabled ?? false} onChange={(e) => setEnabled({ ...enabled, emailEnabled: e.target.checked })} />{t("notifications.emailEnabled")}</label>{notificationKeys.filter((k) => k.startsWith("smtp_")).map(field)}</div>
        <Button type="submit">{t("notifications.save")}</Button>
      </fieldset>
    </form>
    <div role="status" className="text-sm">{mutation.isSuccess ? t("notifications.saved") : mutation.isError ? (mutation.error.message === "invalid" ? t("notifications.invalid") : t("notifications.saveFailed")) : null}</div>
    <TestAlertButton />
  </section>
}
