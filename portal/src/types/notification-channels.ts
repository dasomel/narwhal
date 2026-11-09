export const notificationKeys = [
  "slack_webhook_url", "slack_channel", "smtp_smarthost", "smtp_from", "smtp_to",
  "smtp_auth_username", "smtp_auth_password", "smtp_require_tls",
] as const
export type NotificationKey = typeof notificationKeys[number]
export type ChannelValues = Partial<Record<NotificationKey, string>>
export interface ChannelInput {
  slackEnabled: boolean
  emailEnabled: boolean
  // Omitted = keep, null = clear, string = replace. Never read credentials back.
  values: Partial<Record<NotificationKey, string | null>>
}
export interface ChannelStatus {
  slackEnabled: boolean
  emailEnabled: boolean
  configured: Record<NotificationKey, boolean>
}
export interface NotificationSecret {
  metadata: { resourceVersion: string; [key: string]: unknown }
  data?: Record<string, string>
}
export interface NotificationConfig {
  metadata: { resourceVersion: string }
  spec?: { receivers?: { name: string }[] }
}
