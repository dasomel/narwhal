"use client"
import { useState } from "react"
import { useMutation, useQuery } from "@tanstack/react-query"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { DataState } from "@/components/ui/data-state"
import { useLocale, useT } from "@/lib/i18n-client"
import type { GapKind } from "@/lib/oss-updates/version"
import type { OssDrift, OssUpdateRow, OssUpdatesResponse, OssUpgradeRequestResponse } from "@/types/oss-updates"

const DOC_PATH = "docs/common/oss-update-process.md"

type T = ReturnType<typeof useT>

const gapLabels = (t: T): Record<GapKind, string> => ({
  none: t("ossUpdates.gapNone"),
  patch: t("ossUpdates.gapPatch"),
  minor: t("ossUpdates.gapMinor"),
  major: t("ossUpdates.gapMajor"),
  ahead: t("ossUpdates.gapAhead"),
  unknown: t("ossUpdates.unknown"),
})
const gapClass: Record<GapKind, string> = {
  none: "bg-narwhal-success/15 text-narwhal-success",
  patch: "bg-narwhal-warning/15 text-narwhal-warning",
  minor: "bg-narwhal-warning/15 text-narwhal-warning",
  major: "bg-narwhal-danger/15 text-narwhal-danger",
  ahead: "bg-muted text-muted-foreground",
  unknown: "bg-muted text-muted-foreground",
}
const driftLabels = (t: T): Record<OssDrift, string> => ({
  "in-sync": t("ossUpdates.driftInSync"),
  drift: t("ossUpdates.driftDrift"),
  unknown: t("ossUpdates.driftUnknown"),
})
const driftClass: Record<OssDrift, string> = {
  "in-sync": "bg-narwhal-success/15 text-narwhal-success",
  drift: "bg-narwhal-danger/15 text-narwhal-danger",
  unknown: "bg-muted text-muted-foreground",
}

function RequestDialog({ row, repo, onClose }: { row: OssUpdateRow; repo: string; onClose: () => void }) {
  const t = useT()
  const locale = useLocale()
  const [target, setTarget] = useState(row.upstream?.target ?? "")
  const [copied, setCopied] = useState<"ok" | "fail" | null>(null)

  const mutation = useMutation<OssUpgradeRequestResponse, Error & { code?: string }, string>({
    mutationFn: async (value) => {
      const res = await fetch("/api/settings/oss-updates/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ component: row.component, target: value }),
      })
      const json = await res.json().catch(() => ({}))
      if (!res.ok) throw Object.assign(new Error(res.statusText), { code: json?.error as string | undefined })
      return json as OssUpgradeRequestResponse
    },
  })

  const result = mutation.data
  const errorText = mutation.error
    ? mutation.error.code === "Cooling" ? t("ossUpdates.errorCooling")
      : mutation.error.code === "ValidationError" ? t("ossUpdates.errorInvalid")
      : t("ossUpdates.errorGeneric")
    : ""

  async function copy() {
    try {
      await navigator.clipboard.writeText(result?.issue.body ?? "")
      setCopied("ok")
    } catch {
      setCopied("fail")
    }
  }

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("ossUpdates.requestFor", { component: row.component })}</DialogTitle>
          <DialogDescription>{t("ossUpdates.dialogDescription", { component: row.component, from: row.declared.version })}</DialogDescription>
        </DialogHeader>

        {!result && (
          <form
            className="space-y-3"
            onSubmit={(e) => { e.preventDefault(); mutation.mutate(target.trim()) }}
          >
            <div className="space-y-1">
              <label htmlFor="oss-target" className="text-sm font-medium">{t("ossUpdates.targetLabel")}</label>
              <Input
                id="oss-target"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                placeholder="1.2.3"
                aria-describedby="oss-target-hint oss-target-error"
                aria-invalid={errorText ? true : undefined}
                required
              />
              <p id="oss-target-hint" className="text-xs text-muted-foreground">{t("ossUpdates.targetHint")}</p>
              <p id="oss-target-error" role="alert" className="text-xs text-destructive">{errorText}</p>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>{t("ossUpdates.cancel")}</Button>
              <Button type="submit" disabled={mutation.isPending || target.trim() === ""}>
                {mutation.isPending ? t("ossUpdates.submitting") : t("ossUpdates.submit")}
              </Button>
            </DialogFooter>
          </form>
        )}

        {result && (
          <div className="space-y-3">
            <p role="status" className={`text-sm ${result.duplicate ? "text-narwhal-warning" : "text-foreground"}`}>
              {result.duplicate
                ? t("ossUpdates.duplicate", { at: new Date(result.requestedAt).toLocaleString(locale), by: result.requestedBy })
                : t("ossUpdates.created")}
            </p>
            <div className="space-y-1">
              <label htmlFor="oss-issue-text" className="text-sm font-medium">{t("ossUpdates.issueText")}</label>
              <textarea
                id="oss-issue-text"
                aria-label={t("ossUpdates.issueText")}
                readOnly
                value={`${result.issue.title}\n\n${result.issue.body}`}
                className="h-64 w-full rounded-lg border border-input bg-transparent p-2 font-mono text-xs"
              />
            </div>
            <p role="status" className="min-h-4 text-xs text-muted-foreground">
              {copied === "ok" ? t("ossUpdates.copied") : copied === "fail" ? t("ossUpdates.copyFailed") : ""}
            </p>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>{t("ossUpdates.cancel")}</Button>
              {!result.duplicate && (
                <a
                  href={result.issue.newIssueUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-8 items-center rounded-lg border border-input px-2.5 text-sm hover:bg-muted"
                >
                  {t("ossUpdates.openIssue")} <span className="sr-only">({repo})</span>
                </a>
              )}
              <Button type="button" onClick={() => { void copy() }}>{t("ossUpdates.copy")}</Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function Cooling({ row, total }: { row: OssUpdateRow; total: number }) {
  const t = useT()
  const u = row.upstream
  if (!u) return null
  return u.cooling
    ? <Badge className="bg-narwhal-warning/15 text-narwhal-warning text-xs">{t("ossUpdates.cooling", { age: u.latestAgeDays ?? 0, total })}</Badge>
    : <span className="text-xs text-muted-foreground">{t("ossUpdates.cooled")}</span>
}

function Row({ row, coolingDays, onRequest }: { row: OssUpdateRow; coolingDays: number; onRequest: (r: OssUpdateRow) => void }) {
  const t = useT()
  const u = row.upstream
  const gap = gapLabels(t)
  const drift = driftLabels(t)
  const running = [
    row.running.chartVersion ? t("ossUpdates.chartLabel", { version: row.running.chartVersion }) : null,
    ...row.running.imageTags,
  ].filter(Boolean)
  return (
    <tr className="border-b last:border-0 align-top">
      <th scope="row" className="py-2.5 pr-2 text-left font-medium">
        {row.component}
        {row.highRisk && <Badge className="ml-2 bg-narwhal-danger/15 text-narwhal-danger text-xs">{t("ossUpdates.highRisk")}</Badge>}
      </th>
      <td className="py-2.5 pr-2 font-mono text-xs">
        {row.declared.version}
        {row.declared.chartVersion && <div className="text-muted-foreground">{t("ossUpdates.chartLabel", { version: row.declared.chartVersion })}</div>}
      </td>
      <td className="py-2.5 pr-2 font-mono text-xs">
        {running.length ? running.map((r) => <div key={r}>{r}</div>) : <span className="text-muted-foreground">{t("ossUpdates.unknown")}</span>}
      </td>
      <td className="py-2.5 pr-2"><Badge className={`${driftClass[row.drift]} text-xs`}>{drift[row.drift]}</Badge></td>
      <td className="py-2.5 pr-2 font-mono text-xs">
        {u ? (
          u.latestUrl
            ? <a href={u.latestUrl} target="_blank" rel="noopener noreferrer" className="underline">{u.latest}</a>
            : u.latest
        ) : <span className="font-sans text-muted-foreground">{t("ossUpdates.unknown")}</span>}
        {u && row.heldMinor && <div className="font-sans text-muted-foreground">{t("ossUpdates.heldMinor")}</div>}
      </td>
      <td className="py-2.5 pr-2">
        {u ? (
          <div className="space-y-1">
            <Badge className={`${gapClass[u.gap]} text-xs`}>{gap[u.gap]}</Badge>
            {u.daysBehind !== null && u.gap !== "none" && <div className="text-xs text-muted-foreground">{t("ossUpdates.daysBehind", { count: u.daysBehind })}</div>}
            <div><Cooling row={row} total={coolingDays} /></div>
          </div>
        ) : <span className="text-xs text-muted-foreground">{t("ossUpdates.unknown")}</span>}
      </td>
      <td className="py-2.5 pr-2 text-xs">
        {row.cves ? t("ossUpdates.cveCounts", { critical: row.cves.critical, high: row.cves.high }) : <span className="text-muted-foreground">{t("ossUpdates.noScan")}</span>}
      </td>
      <td className="py-2.5">
        <Button type="button" size="sm" variant="outline" onClick={() => onRequest(row)} aria-label={t("ossUpdates.requestFor", { component: row.component })}>
          {t("ossUpdates.request")}
        </Button>
      </td>
    </tr>
  )
}

export function OssUpdatesTable() {
  const t = useT()
  const locale = useLocale()
  const [selected, setSelected] = useState<OssUpdateRow | null>(null)

  const { data, isLoading, isError, refetch } = useQuery<OssUpdatesResponse>({
    queryKey: ["settings-oss-updates"],
    queryFn: async () => {
      const res = await fetch("/api/settings/oss-updates")
      if (!res.ok) throw new Error(res.statusText)
      return res.json()
    },
    staleTime: 60_000,
  })

  return (
    <Card className="p-5">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold text-foreground">{t("ossUpdates.title")}</h2>
          <p className="mt-1 max-w-3xl text-xs text-muted-foreground">{t("ossUpdates.description")}</p>
        </div>
        {data && (
          <a
            href={`https://github.com/${data.issueRepo}/blob/main/${DOC_PATH}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs underline"
          >
            {t("ossUpdates.processDoc")}
          </a>
        )}
      </div>

      {isLoading ? <DataState state="loading" />
        : isError || !data ? <DataState state="error" onRetry={() => { void refetch() }} />
        : (
        <>
          <ul className="mb-3 space-y-1 text-xs text-muted-foreground">
            {data.upstreamLookup === "disabled" && <li>{t("ossUpdates.upstreamOff")}</li>}
            {data.argocd === "unavailable" && <li>{t("ossUpdates.argoUnavailable")}</li>}
            {data.trivy === "unavailable" && <li>{t("ossUpdates.trivyUnavailable")}</li>}
            {data.trivy === "partial" && <li>{t("ossUpdates.trivyPartial")}</li>}
            {data.requestMode === "copy" && <li>{t("ossUpdates.requestModeCopy")}</li>}
            <li>{t("ossUpdates.refreshed", { time: new Date(data.generatedAt).toLocaleString(locale) })}</li>
          </ul>
          <div className="overflow-x-auto">
            <table className="w-full text-sm" aria-label={t("ossUpdates.tableLabel")}>
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th scope="col" className="pb-2 pr-2 font-medium">{t("ossUpdates.colComponent")}</th>
                  <th scope="col" className="pb-2 pr-2 font-medium">{t("ossUpdates.colDeclared")}</th>
                  <th scope="col" className="pb-2 pr-2 font-medium">{t("ossUpdates.colRunning")}</th>
                  <th scope="col" className="pb-2 pr-2 font-medium">{t("ossUpdates.colDrift")}</th>
                  <th scope="col" className="pb-2 pr-2 font-medium">{t("ossUpdates.colLatest")}</th>
                  <th scope="col" className="pb-2 pr-2 font-medium">{t("ossUpdates.colGap")}</th>
                  <th scope="col" className="pb-2 pr-2 font-medium">{t("ossUpdates.colCves")}</th>
                  <th scope="col" className="pb-2 font-medium">{t("ossUpdates.colAction")}</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => <Row key={row.component} row={row} coolingDays={data.coolingDays} onRequest={setSelected} />)}
              </tbody>
            </table>
          </div>
          {selected && <RequestDialog row={selected} repo={data.issueRepo} onClose={() => setSelected(null)} />}
        </>
      )}
    </Card>
  )
}
