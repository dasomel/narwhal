import { getNotificationResources, updateNotificationSecret, patchNotificationConfig } from "./k8s-client"
import { buildAlertmanagerSpec, decodeChannelSecret, mergeChannelValues, redactChannelSettings } from "./notification-channel-validation"
import { notificationKeys, type ChannelInput } from "@/types/notification-channels"

export class ChannelSaveError extends Error {
  constructor() { super("Settings may be partially applied; reload and verify before retrying") }
}

export async function getNotificationChannels() {
  const { secret, config } = await getNotificationResources()
  return redactChannelSettings(secret, config)
}

export async function saveNotificationChannels(input: ChannelInput) {
  const { secret, config } = await getNotificationResources()
  const values = mergeChannelValues(decodeChannelSecret(secret), input)
  const spec = buildAlertmanagerSpec(values, input)
  const data = { ...secret.data }
  for (const key of notificationKeys) {
    if (values[key] === undefined) delete data[key]
    else data[key] = Buffer.from(values[key], "utf8").toString("base64")
  }
  // D2 (#610): Kubernetes cannot atomically update two kinds. Read both first,
  // use resourceVersion preconditions, never retry/roll back credential writes.
  // Cost: partial saves require operator verification; escape: reload + resave.
  try {
    await updateNotificationSecret({ ...secret, data })
    await patchNotificationConfig(config.metadata.resourceVersion, spec)
  } catch {
    throw new ChannelSaveError()
  }
  return redactChannelSettings({ ...secret, data }, { ...config, spec })
}
