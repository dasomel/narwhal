"use client"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { AlertTriangle, CheckCircle2, CircleHelp, Copy, Check, XCircle } from "lucide-react"
import { useT } from "@/lib/i18n-client"
import { DataState } from "@/components/ui/data-state"
import type { TrivyDbArtifactStatus, TrivyDbStatus } from "@/types/security"

// Same three steps scripts/cluster/08-5-registry.sh prints when the trivy-db project is
// empty. Shown as text to copy; the portal never executes them (see trivy-db-status.ts).
const FIX_STEPS = [
  { key: "fetch", command: "scripts/airgap/lib/fetch-security-db.sh --bundle <dir>" },
  { key: "promote", command: 'scripts/airgap/lib/promote-security-db.sh --bundle <dir> --approved-by "<name>"' },
  { key: "push", command: "scripts/airgap/lib/push-security-db.sh --bundle <dir> --ca-cert <narwhal ca.crt> --kube-context <ctx>" },
] as const

const statusStyle: Record<TrivyDbArtifactStatus, { cls: string; Icon: typeof CheckCircle2 }> = {
  fresh: { cls: "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800", Icon: CheckCircle2 },
  stale: { cls: "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800", Icon: AlertTriangle },
  missing: { cls: "bg-red-100 text-red-800 border-red-300 dark:bg-red-950/40 dark:text-red-400 dark:border-red-800", Icon: XCircle },
  unknown: { cls: "bg-gray-100 text-gray-700 border-gray-300 dark:bg-gray-800 dark:text-gray-300 dark:border-gray-700", Icon: CircleHelp },
}

function StatusBadge({ status }: { status: TrivyDbArtifactStatus }) {
  const t = useT()
  const { cls, Icon } = statusStyle[status]
  const label =
    status === "fresh" ? t("security.trivyDb.status.fresh")
    : status === "stale" ? t("security.trivyDb.status.stale")
    : status === "missing" ? t("security.trivyDb.status.missing")
    : t("security.trivyDb.status.unknown")
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-xs font-medium ${cls}`}>
      <Icon className="size-3" aria-hidden="true" />
      {label}
    </span>
  )
}

function CopyCommand({ label, command }: { label: string; command: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-foreground">{label}</div>
      <div className="flex items-start gap-2">
        <code className="flex-1 min-w-0 rounded bg-muted px-2 py-1 font-mono text-xs break-all">{command}</code>
        <button
          type="button"
          className="shrink-0 rounded border p-1.5 text-muted-foreground hover:text-foreground focus-visible:outline focus-visible:outline-2"
          aria-label={t("security.trivyDb.fix.copy", { step: label })}
          onClick={() => {
            void navigator.clipboard.writeText(command).then(() => {
              setCopied(true)
              setTimeout(() => setCopied(false), 2000)
            })
          }}
        >
          {copied ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
          <span className="sr-only" role="status">{copied ? t("security.trivyDb.fix.copied") : ""}</span>
        </button>
      </div>
    </div>
  )
}

const day = (iso: string) => iso.slice(0, 10)

export function TrivyDbStatusCard() {
  const t = useT()
  const { data, isLoading, isError, refetch } = useQuery<TrivyDbStatus>({
    queryKey: ["security-trivy-db"],
    queryFn: async () => {
      const r = await fetch("/api/security/trivy-db")
      if (!r.ok) throw new Error(String(r.status))
      return r.json()
    },
    refetchInterval: 60_000,
  })

  if (isLoading) return <DataState state="loading" />
  if (isError || !data) return <DataState state="unavailable" onRetry={() => void refetch()} />

  const { scanner } = data
  const needsFix = data.overall === "stale" || data.overall === "missing"
  const scannerLine =
    scanner.state === "failing"
      ? t("security.trivyDb.scanner.failing", { failures: scanner.recentScanFailures, time: scanner.lastFailureAt ? day(scanner.lastFailureAt) : "-", count: scanner.reportCount })
      : scanner.state === "no-reports"
        ? t("security.trivyDb.scanner.noReports")
        : t("security.trivyDb.scanner.ok", { count: scanner.reportCount, time: scanner.newestReportAt ? day(scanner.newestReportAt) : "-" })

  return (
    <section className="rounded-lg border bg-card p-4 space-y-4" aria-labelledby="trivy-db-title">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="trivy-db-title" className="text-sm font-semibold text-foreground">{t("security.trivyDb.title")}</h2>
          <p className="text-xs text-muted-foreground mt-0.5">{t("security.trivyDb.subtitle", { days: data.sloDays })}</p>
        </div>
        <StatusBadge status={data.overall} />
      </div>

      {!data.harborReachable && <DataState state="unavailable" variant="inline" reason={t("security.trivyDb.harborDown")} />}

      <ul className="divide-y text-sm">
        {data.artifacts.map((a) => (
          <li key={a.repository} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <code className="font-mono text-xs text-foreground flex-1 min-w-48">{a.repository.replace("aquasecurity/", "")}:{a.tag}</code>
            <span className="text-xs text-muted-foreground">
              {a.ageDays !== undefined && `${t("security.trivyDb.age", { days: a.ageDays })}`}
              {a.createdAt ? ` · ${t("security.trivyDb.built", { time: day(a.createdAt) })}` : a.pushedAt ? ` · ${t("security.trivyDb.pushed", { time: day(a.pushedAt) })}` : ""}
            </span>
            <StatusBadge status={a.status} />
          </li>
        ))}
      </ul>

      <div className="space-y-1 text-xs text-muted-foreground">
        <p role={scanner.state === "failing" ? "alert" : "status"} className={scanner.state === "failing" ? "text-red-700 dark:text-red-400" : undefined}>
          {scannerLine}
        </p>
        <p>{t("security.trivyDb.registry")}: <code className="font-mono">{data.dbRegistry}</code></p>
      </div>

      {needsFix && (
        <div className="rounded-md border border-dashed p-3 space-y-3">
          <h3 className="text-xs font-semibold text-foreground">{t("security.trivyDb.fix.title")}</h3>
          <p className="text-xs text-muted-foreground">{t("security.trivyDb.fix.why")}</p>
          {FIX_STEPS.map((s) => (
            <CopyCommand key={s.key} label={s.key === "fetch" ? t("security.trivyDb.fix.fetch") : s.key === "promote" ? t("security.trivyDb.fix.promote") : t("security.trivyDb.fix.push")} command={s.command} />
          ))}
        </div>
      )}
    </section>
  )
}
