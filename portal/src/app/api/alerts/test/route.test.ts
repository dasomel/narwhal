import { beforeEach, describe, expect, it, vi } from "vitest"
vi.mock("@/lib/auth", () => ({ requireRole: vi.fn() }))
vi.mock("@/lib/firing-alerts", () => ({ sendNotificationTest: vi.fn() }))
import { requireRole } from "@/lib/auth"
import { sendNotificationTest } from "@/lib/firing-alerts"
import { POST } from "./route"
beforeEach(() => vi.resetAllMocks())
describe("send test alert", () => {
  it.each([["unauthorized", 401], ["forbidden", 403]] as const)("denies %s before sending", async (error, status) => {
    vi.mocked(requireRole).mockResolvedValue({ error })
    expect((await POST()).status).toBe(status)
    expect(requireRole).toHaveBeenCalledWith("cluster-admin")
    expect(sendNotificationTest).not.toHaveBeenCalled()
  })
  it("accepts admin requests but never exposes upstream failure details", async () => {
    vi.mocked(requireRole).mockResolvedValue({ session: { user: { role: "cluster-admin" } } } as never)
    expect((await POST()).status).toBe(200)
    vi.mocked(sendNotificationTest).mockRejectedValue(new Error("private URL"))
    const res = await POST()
    expect(res.status).toBe(502)
    expect(await res.text()).not.toContain("private URL")
  })
})
