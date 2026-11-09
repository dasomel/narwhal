import { getDependencyUrl } from "./config"
import { fetchWithPolicy, readJsonWithPolicy } from "./http-client"
import type { FiringAlert, FiringAlertsResponse } from "@/types/firing-alerts"

const alertsUrl = () => `${getDependencyUrl("ALERTMANAGER_URL", "http://prometheus-stack-kube-prom-alertmanager.monitoring.svc:9093").replace(/\/$/, "")}/api/v2/alerts`

export async function getFiringAlerts(): Promise<FiringAlertsResponse> {
  try {
    // D4 (#610): include suppressed firing alerts; the existing dashboard cache
    // excludes silenced alerts. Cost: separate bounded read; remove it if the
    // shared dashboard response adopts the full status/degraded contract.
    const res = await fetchWithPolicy(`${alertsUrl()}?active=true&silenced=true&inhibited=true&unprocessed=false`, { cache: "no-store" }, { timeoutMs: 5000, retry: false })
    if (!res.ok) throw new Error("Alertmanager unavailable")
    const alerts = await readJsonWithPolicy<FiringAlert[]>(res)
    if (!Array.isArray(alerts) || alerts.some((a) => !a || !a.labels || !a.annotations || !a.status || !Array.isArray(a.status.silencedBy) || !Array.isArray(a.status.inhibitedBy) || typeof a.startsAt !== "string")) throw new Error("Malformed alerts")
    return { alerts, degraded: false }
  } catch {
    return { alerts: [], degraded: true }
  }
}

export async function sendNotificationTest() {
  const now = new Date()
  const res = await fetchWithPolicy(alertsUrl(), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify([{
      labels: { alertname: "NarwhalNotificationTest", severity: "info" },
      annotations: { summary: "Narwhal notification channel test" },
      startsAt: now.toISOString(), endsAt: new Date(now.getTime() + 5 * 60_000).toISOString(),
    }]),
  }, { timeoutMs: 5000, retry: false })
  if (!res.ok) throw new Error("Test alert was not accepted")
}
