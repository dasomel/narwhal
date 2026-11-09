import { beforeEach, describe, expect, it, vi } from "vitest"
vi.mock("@/lib/auth", () => ({ requireRole: vi.fn() }))
vi.mock("@/lib/notification-channels", () => ({ getNotificationChannels: vi.fn(), saveNotificationChannels: vi.fn(), ChannelSaveError: class extends Error {} }))
import { requireRole } from "@/lib/auth"
import { getNotificationChannels, saveNotificationChannels, ChannelSaveError } from "@/lib/notification-channels"
import { ChannelValidationError } from "@/lib/notification-channel-validation"
import { GET, PUT } from "./route"
const request = (body: unknown) => new Request("http://localhost/api/settings/notification-channels", { method: "PUT", body: JSON.stringify(body) })
beforeEach(() => { vi.resetAllMocks(); vi.mocked(requireRole).mockResolvedValue({ session: { user: { role: "cluster-admin" } } } as never) })

describe("notification settings authorization and errors", () => {
  it.each([["unauthorized", 401], ["forbidden", 403]] as const)("denies %s before any access", async (error, status) => {
    vi.mocked(requireRole).mockResolvedValue({ error })
    expect((await GET()).status).toBe(status)
    expect((await PUT(request({}))).status).toBe(status)
    expect(requireRole).toHaveBeenCalledWith("cluster-admin")
    expect(getNotificationChannels).not.toHaveBeenCalled()
    expect(saveNotificationChannels).not.toHaveBeenCalled()
  })
  it("returns redacted status with no-store on reads and saves", async () => {
    const redacted = { slackEnabled: false, emailEnabled: false, configured: { slack_webhook_url: true } } as never
    vi.mocked(getNotificationChannels).mockResolvedValue(redacted)
    vi.mocked(saveNotificationChannels).mockResolvedValue(redacted)
    for (const res of [await GET(), await PUT(request({ slackEnabled: false, emailEnabled: false, values: {} }))]) {
      expect(res.status).toBe(200)
      expect(res.headers.get("cache-control")).toBe("no-store")
      expect(await res.json()).toEqual(redacted)
    }
  })
  it("rejects malformed JSON and invalid schema before writes", async () => {
    expect((await PUT(new Request("http://x", { method: "PUT", body: "{" }))).status).toBe(400)
    expect((await PUT(request({ values: {} }))).status).toBe(400)
    expect(saveNotificationChannels).not.toHaveBeenCalled()
  })
  it("maps validation to 400 and upstream/partial writes to redacted 502", async () => {
    const input = { slackEnabled: false, emailEnabled: false, values: {} }
    vi.mocked(saveNotificationChannels).mockRejectedValue(new ChannelValidationError())
    expect((await PUT(request(input))).status).toBe(400)
    vi.mocked(saveNotificationChannels).mockRejectedValue(new ChannelSaveError())
    expect(await (await PUT(request(input))).json()).toMatchObject({ partial: true })
    vi.mocked(getNotificationChannels).mockRejectedValue(new Error("secret-password"))
    const res = await GET()
    expect(res.status).toBe(502)
    expect(await res.text()).not.toContain("secret-password")
  })
})
