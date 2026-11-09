import { beforeEach, describe, expect, it, vi } from "vitest"
vi.mock("./k8s-client", () => ({ getNotificationResources: vi.fn(), updateNotificationSecret: vi.fn(), patchNotificationConfig: vi.fn() }))
import { getNotificationResources, patchNotificationConfig, updateNotificationSecret } from "./k8s-client"
import { getNotificationChannels, saveNotificationChannels } from "./notification-channels"

const input = { slackEnabled: true, emailEnabled: false, values: { slack_webhook_url: "https://hooks.example.com/private", slack_channel: "#ops" } }
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(getNotificationResources).mockResolvedValue({ secret: { metadata: { resourceVersion: "secret-rv" }, data: { unrelated: "a2VlcA==" } }, config: { metadata: { resourceVersion: "config-rv" }, spec: { receivers: [{ name: "null" }] } } })
})
describe("notification channel persistence", () => {
  it("writes the Secret first, retains unrelated keys, and returns only redacted settings", async () => {
    const result = await saveNotificationChannels(input)
    expect(updateNotificationSecret).toHaveBeenCalledWith({ metadata: { resourceVersion: "secret-rv" }, data: { unrelated: "a2VlcA==", slack_webhook_url: Buffer.from(input.values.slack_webhook_url).toString("base64"), slack_channel: Buffer.from("#ops").toString("base64") } })
    expect(patchNotificationConfig).toHaveBeenCalledWith("config-rv", expect.objectContaining({ receivers: expect.arrayContaining([expect.objectContaining({ name: "slack" })]) }))
    expect(vi.mocked(updateNotificationSecret).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(patchNotificationConfig).mock.invocationCallOrder[0])
    expect(JSON.stringify(result)).not.toContain("private")
    expect(result.configured.slack_webhook_url).toBe(true)
    expect(JSON.stringify(await getNotificationChannels())).not.toContain("a2VlcA")
  })
  it("does not write when either required resource is missing", async () => {
    vi.mocked(getNotificationResources).mockRejectedValue(new Error("404"))
    await expect(saveNotificationChannels(input)).rejects.toThrow()
    expect(updateNotificationSecret).not.toHaveBeenCalled()
    expect(patchNotificationConfig).not.toHaveBeenCalled()
  })
  it("does not write invalid configuration", async () => {
    await expect(saveNotificationChannels({ ...input, values: {} })).rejects.toThrow("Invalid")
    expect(updateNotificationSecret).not.toHaveBeenCalled()
  })
  it.each(["secret", "config"])("reports an unknown/partial outcome for a %s write failure without exposing data or retrying", async (kind) => {
    vi.mocked(kind === "secret" ? updateNotificationSecret : patchNotificationConfig).mockRejectedValue(new Error("credential-value"))
    await expect(saveNotificationChannels(input)).rejects.toThrow("partially applied")
    expect(updateNotificationSecret).toHaveBeenCalledTimes(1)
    expect(patchNotificationConfig).toHaveBeenCalledTimes(kind === "secret" ? 0 : 1)
  })
})
