export interface FiringAlert {
  labels: Record<string, string>
  annotations: Record<string, string>
  startsAt: string
  fingerprint: string
  status: { state: string; silencedBy: string[]; inhibitedBy: string[] }
}
export interface FiringAlertsResponse { alerts: FiringAlert[]; degraded: boolean }
