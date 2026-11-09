import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { getFiringAlerts, sendNotificationTest } from "./firing-alerts"
beforeEach(() => { vi.stubEnv("ALERTMANAGER_URL", "http://alertmanager.test:9093"); vi.stubGlobal("fetch", vi.fn()) })
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.useRealTimers() })
describe("Alertmanager firing integration", () => {
  it("includes silenced and inhibited alerts in the upstream read", async () => {
    const alert = { labels: { alertname: "DiskFull", severity: "warning", namespace: "apps" }, annotations: { summary: "Disk full" }, fingerprint: "x", startsAt: "2026-11-09T01:00:00Z", status: { state: "suppressed", silencedBy: ["silence-id"], inhibitedBy: ["alert-id"] } }
    vi.mocked(fetch).mockResolvedValue(Response.json([alert]))
    expect(await getFiringAlerts()).toEqual({ alerts: [alert], degraded: false })
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("active=true&silenced=true&inhibited=true")
  })
  it.each(["network", "503", "malformed"])("reports degraded on %s instead of false healthy", async (kind) => {
    if (kind === "network") vi.mocked(fetch).mockRejectedValue(new Error("private error"))
    else vi.mocked(fetch).mockResolvedValue(kind === "503" ? new Response("", { status: 503 }) : Response.json({ not: "an array" }))
    expect(await getFiringAlerts()).toEqual({ alerts: [], degraded: true })
  })
  it("posts a five-minute info test alert exactly once", async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-09T00:00:00Z"))
    vi.mocked(fetch).mockResolvedValue(new Response("", { status: 200 }))
    await sendNotificationTest()
    const [url, options] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe("http://alertmanager.test:9093/api/v2/alerts")
    expect(options?.method).toBe("POST")
    expect(JSON.parse(options?.body as string)).toEqual([{ labels: { alertname: "NarwhalNotificationTest", severity: "info" }, annotations: { summary: "Narwhal notification channel test" }, startsAt: "2026-11-09T00:00:00.000Z", endsAt: "2026-11-09T00:05:00.000Z" }])
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
