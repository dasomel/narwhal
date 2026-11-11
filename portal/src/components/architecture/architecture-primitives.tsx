"use client"

import type { LucideIcon } from "lucide-react"
import { useT } from "@/lib/i18n-client"
import { Card } from "@/components/ui/card"

export function IconChip({ icon: Icon }: { icon: LucideIcon }) {
  return <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-narwhal-accent/10 text-narwhal-accent"><Icon aria-hidden="true" className="size-4" /></span>
}

export function SummaryCard({ label, value, icon, sub }: { label: string; value: string | number; icon: LucideIcon; sub?: string }) {
  return <Card className="min-w-0 rounded-xl p-5">
    <div className="flex items-center gap-2.5"><IconChip icon={icon} /><span className="text-[13px] text-muted-foreground">{label}</span></div>
    <div className="mt-2 text-3xl font-semibold tracking-tight tabular-nums">{value}</div>
    {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
  </Card>
}

export function ServiceStatus({ status, badge = false }: { status: string; badge?: boolean }) {
  const t = useT()
  const color = status === "healthy" ? "bg-narwhal-success" : (status === "degraded" || status === "down") ? "bg-narwhal-danger" : "bg-muted-foreground"
  const label = status === "healthy" ? t("arch.healthy") : status === "degraded" ? t("arch.degraded") : status === "down" ? t("arch.offline") : t("arch.unknown")
  return <span className={`inline-flex items-center gap-1.5 text-xs ${badge ? "rounded-full border border-border px-2 py-1" : "text-muted-foreground"}`}><span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${color}`} />{label}</span>
}

const meterWidths = ["w-0", "w-1/12", "w-2/12", "w-3/12", "w-4/12", "w-5/12", "w-6/12", "w-7/12", "w-8/12", "w-9/12", "w-10/12", "w-11/12", "w-full"]

export function CapacityMeter({ label, percent, capacity }: { label: string; percent: number | null; capacity: string }) {
  const t = useT()
  // D2: Missing telemetry stays absent, never zero. Quantized widths match dashboard;
  // cost: visual rounding to twelfths; escape hatch: exact dynamic width if needed.
  const value = percent !== null && Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : null
  return <div className="space-y-2">
    <div className="flex items-center justify-between gap-2 text-xs"><span className="text-muted-foreground">{label}</span><span className="font-mono tabular-nums">{value === null ? "—" : `${value}%`}</span></div>
    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
      {value !== null && <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} className={`h-full rounded-full ${value >= 90 ? "bg-narwhal-danger" : value >= 70 ? "bg-narwhal-warning" : "bg-narwhal-accent"} ${meterWidths[Math.round(value / 100 * 12)]}`} />}
    </div>
    <p className="text-[11px] text-muted-foreground">{value === null ? t("arch640.noTelemetry") : capacity}</p>
  </div>
}
