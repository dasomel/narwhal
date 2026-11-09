import { isIP } from "node:net"
import { notificationKeys, type ChannelInput, type ChannelStatus, type ChannelValues, type NotificationConfig, type NotificationSecret } from "@/types/notification-channels"

export class ChannelValidationError extends Error {
  constructor() { super("Invalid notification channel settings") }
}

const invalid = (): never => { throw new ChannelValidationError() }
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value)

export function parseChannelInput(body: unknown): ChannelInput {
  if (!record(body) || typeof body.slackEnabled !== "boolean" || typeof body.emailEnabled !== "boolean" || !record(body.values)) return invalid()
  if (Object.keys(body).some((key) => !["slackEnabled", "emailEnabled", "values"].includes(key))) return invalid()
  const values: ChannelInput["values"] = {}
  for (const [key, value] of Object.entries(body.values)) {
    if (!notificationKeys.includes(key as typeof notificationKeys[number])) return invalid()
    if (value !== null && (typeof value !== "string" || value.length > 4096 || /[\r\n\x00]/.test(value))) return invalid()
    values[key as typeof notificationKeys[number]] = value === null ? null : value as string
  }
  return { slackEnabled: body.slackEnabled, emailEnabled: body.emailEnabled, values }
}

export function decodeChannelSecret(secret: NotificationSecret): ChannelValues {
  return Object.fromEntries(notificationKeys.flatMap((key) => secret.data?.[key] ? [[key, Buffer.from(secret.data[key], "base64").toString("utf8")]] : []))
}

export function mergeChannelValues(current: ChannelValues, input: ChannelInput): ChannelValues {
  const values = { ...current }
  for (const key of notificationKeys) {
    const value = input.values[key]
    if (value === null || value === "") delete values[key]
    else if (value !== undefined) values[key] = key === "smtp_auth_password" ? value : value.trim()
  }
  validateChannelValues(values, input)
  return values
}

const email = (value: string) => /^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$/.test(value)
function smarthost(value: string): boolean {
  const match = /^(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+):(\d{1,5})$/.exec(value)
  if (!match || Number(match[2]) < 1 || Number(match[2]) > 65535) return false
  const host = match[1]
  if (host.startsWith("[")) return isIP(host.slice(1, -1)) === 6
  return host.length <= 253 && host.split(".").every((label) => /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label))
}

export function validateChannelValues(v: ChannelValues, enabled: Pick<ChannelInput, "slackEnabled" | "emailEnabled">): void {
  if (v.slack_webhook_url) {
    let url: URL
    try { url = new URL(v.slack_webhook_url) } catch { return invalid() }
    if (url.protocol !== "https:" || !url.hostname || url.username || url.password || url.hash) return invalid()
  }
  if (v.smtp_smarthost && !smarthost(v.smtp_smarthost)) return invalid()
  if (v.smtp_from && !email(v.smtp_from)) return invalid()
  if (v.smtp_to && !v.smtp_to.split(",").every((address) => email(address.trim()))) return invalid()
  if (v.smtp_require_tls && !["true", "false"].includes(v.smtp_require_tls)) return invalid()
  if (enabled.slackEnabled && (!v.slack_webhook_url || !v.slack_channel)) return invalid()
  if (enabled.emailEnabled && (!v.smtp_smarthost || !v.smtp_from || !v.smtp_to)) return invalid()
  if (enabled.emailEnabled && Boolean(v.smtp_auth_username) !== Boolean(v.smtp_auth_password)) return invalid()
}

export function redactChannelSettings(secret: NotificationSecret, config: NotificationConfig): ChannelStatus {
  const values = decodeChannelSecret(secret)
  return {
    slackEnabled: Boolean(config.spec?.receivers?.some((r) => r.name === "slack")),
    emailEnabled: Boolean(config.spec?.receivers?.some((r) => r.name === "email")),
    configured: Object.fromEntries(notificationKeys.map((key) => [key, Boolean(values[key])])) as ChannelStatus["configured"],
  }
}

export function buildAlertmanagerSpec(values: ChannelValues, enabled: Pick<ChannelInput, "slackEnabled" | "emailEnabled">) {
  validateChannelValues(values, enabled)
  const receivers: { name: string; slackConfigs?: object[]; emailConfigs?: object[] }[] = [{ name: "null" }]
  const routes: { receiver: string; continue: boolean }[] = []
  const secret = (key: string) => ({ name: "narwhal-notification-channels", key })
  if (enabled.slackEnabled) {
    receivers.push({ name: "slack", slackConfigs: [{ apiURL: secret("slack_webhook_url"), channel: values.slack_channel, sendResolved: true }] })
    routes.push({ receiver: "slack", continue: true })
  }
  if (enabled.emailEnabled) {
    receivers.push({ name: "email", emailConfigs: [{
      to: values.smtp_to, from: values.smtp_from, smarthost: values.smtp_smarthost,
      ...(values.smtp_auth_username ? { authUsername: values.smtp_auth_username, authPassword: secret("smtp_auth_password") } : {}),
      requireTLS: values.smtp_require_tls !== "false",
    }] })
    routes.push({ receiver: "email", continue: true })
  }
  return { receivers, route: { receiver: "null", routes, groupBy: ["alertname", "namespace"], groupWait: "30s", groupInterval: "5m", repeatInterval: "4h" } }
}
