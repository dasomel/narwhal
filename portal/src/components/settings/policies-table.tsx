"use client"
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Card } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { useT } from "@/lib/i18n-client"
import { DataState } from "@/components/ui/data-state"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet"

interface KyvernoRule {
  name: string
  type: string
  // Optional: responses cached before these fields existed lack them.
  message?: string | null
  failureAction?: string | null
  matchKinds?: string[]
  excludeKinds?: string[]
  excludeNamespaces?: string[]
}

interface KyvernoPolicy {
  name: string
  namespace: string | null
  scope: "cluster" | "namespace"
  background: boolean
  validationFailureAction: string
  ready: boolean
  rulesCount: number
  rules: KyvernoRule[]
  title?: string | null
  description?: string | null
  category?: string | null
  severity?: string | null
}

type T = ReturnType<typeof useT>

function ruleTypeText(t: T, type: string): string {
  switch (type) {
    case "validate": return t("policies.ruleType.validate")
    case "mutate": return t("policies.ruleType.mutate")
    case "generate": return t("policies.ruleType.generate")
    case "verifyImages": return t("policies.ruleType.verifyImages")
    default: return t("policies.ruleType.unknown")
  }
}

// Kyverno accepts Enforce/Audit (older releases also lowercase); anything else
// (e.g. "Mixed" from per-rule failureAction, or a future value) gets a generic text.
function actionText(t: T, action: string): string {
  switch (action.toLowerCase()) {
    case "enforce": return t("policies.action.enforce")
    case "audit": return t("policies.action.audit")
    case "mixed": return t("policies.action.mixed")
    default: return t("policies.action.unknown")
  }
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-sm">
      <dt className="w-28 shrink-0 text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

export function PolicyDetail({ policy }: { policy: KyvernoPolicy }) {
  const t = useT()
  return (
    <>
      <SheetHeader>
        <SheetTitle className="text-base">{policy.title || policy.name}</SheetTitle>
        <SheetDescription className="whitespace-pre-line">
          {policy.description || t("policies.detail.noDescription")}
        </SheetDescription>
      </SheetHeader>
      <div className="space-y-4 px-4 pb-4">
        <dl className="space-y-1.5">
          {policy.title && <DetailRow label={t("policies.name")}>{policy.name}</DetailRow>}
          <DetailRow label={t("policies.detail.category")}>{policy.category || t("policies.detail.none")}</DetailRow>
          <DetailRow label={t("policies.detail.severity")}>{policy.severity || t("policies.detail.none")}</DetailRow>
          <DetailRow label={t("policies.detail.scope")}>{policy.scope}</DetailRow>
          <DetailRow label={t("policies.detail.action")}>
            <strong>{policy.validationFailureAction}</strong> — {actionText(t, policy.validationFailureAction)}
          </DetailRow>
          <DetailRow label={t("policies.detail.background")}>
            {policy.background ? t("policies.detail.on") : t("policies.detail.off")} — {t("policies.legend.background")}
          </DetailRow>
        </dl>
        <section aria-labelledby="policy-detail-rules">
          <h3 id="policy-detail-rules" className="mb-2 text-sm font-semibold">{t("policies.detail.rules")}</h3>
          <ul className="space-y-3">
            {policy.rules.map((r) => (
              <li key={r.name} className="rounded-md border p-3">
                <div className="text-sm font-medium">{r.name} <span className="text-xs text-muted-foreground">({r.type})</span></div>
                <p className="text-xs text-muted-foreground">{ruleTypeText(t, r.type)}</p>
                <dl className="mt-2 space-y-1">
                  {r.message && <DetailRow label={t("policies.detail.message")}>{r.message}</DetailRow>}
                  {r.failureAction && (
                    <DetailRow label={t("policies.detail.ruleAction")}>{r.failureAction} — {actionText(t, r.failureAction)}</DetailRow>
                  )}
                  <DetailRow label={t("policies.detail.matches")}>{r.matchKinds?.join(", ") || t("policies.detail.none")}</DetailRow>
                  <DetailRow label={t("policies.detail.excludes")}>{r.excludeKinds?.join(", ") || t("policies.detail.none")}</DetailRow>
                  <DetailRow label={t("policies.detail.excludeNamespaces")}>{r.excludeNamespaces?.join(", ") || t("policies.detail.none")}</DetailRow>
                </dl>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </>
  )
}

export function PoliciesTable() {
  const t = useT()
  const [selected, setSelected] = useState<string | null>(null)
  const { data: policies, isLoading, isError, refetch } = useQuery<KyvernoPolicy[]>({
    queryKey: ["settings-policies"],
    queryFn: () => fetch("/api/settings/policies").then((r) => r.json()),
  })

  const selectedPolicy = Array.isArray(policies) ? policies.find((p) => `${p.scope}/${p.name}` === selected) : undefined

  return (
    <Card className="p-5">
      <h2 className="font-semibold text-foreground mb-2">{t("policies.title")}</h2>
      <dl aria-label={t("policies.legend.title")} className="mb-4 space-y-0.5 text-xs text-muted-foreground">
        <div><dt className="inline font-medium text-narwhal-danger">Enforce: </dt><dd className="inline">{t("policies.action.enforce")}</dd></div>
        <div><dt className="inline font-medium text-narwhal-warning">Audit: </dt><dd className="inline">{t("policies.action.audit")}</dd></div>
        <div><dd>{t("policies.legend.background")}</dd></div>
      </dl>
      {isLoading ? <DataState state="loading" onRetry={() => { void refetch() }} />
        : isError ? <DataState state="error" onRetry={() => { void refetch() }} />
        : !policies?.length ? <DataState state="empty" /> : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="pb-2 font-medium">{t("policies.name")}</th>
              <th className="pb-2 font-medium">{t("policies.scope")}</th>
              <th className="pb-2 font-medium">{t("policies.action")}</th>
              <th className="pb-2 font-medium">{t("policies.rules")}</th>
              <th className="pb-2 font-medium">{t("policies.status")}</th>
            </tr>
          </thead>
          <tbody>
            {policies.map((p) => (
              <tr key={`${p.scope}/${p.name}`} className="border-b last:border-0">
                <td className="py-2.5 font-medium">
                  <button
                    type="button"
                    onClick={() => setSelected(`${p.scope}/${p.name}`)}
                    aria-label={t("policies.detail.open", { name: p.name })}
                    className="text-left underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
                  >
                    {p.name}
                  </button>
                </td>
                <td className="py-2.5">
                  <Badge className={p.scope === "cluster" ? "bg-purple-100 text-purple-700" : "bg-narwhal-accent/15 text-narwhal-accent"}>
                    {p.scope}
                  </Badge>
                </td>
                <td className="py-2.5">
                  <Badge
                    title={actionText(t, p.validationFailureAction)}
                    aria-describedby={`policy-action-${p.scope}-${p.name}`}
                    className={p.validationFailureAction.toLowerCase() === "enforce" ? "bg-narwhal-danger/15 text-narwhal-danger" : "bg-narwhal-warning/15 text-narwhal-warning"}
                  >
                    {p.validationFailureAction}
                  </Badge>
                  <span id={`policy-action-${p.scope}-${p.name}`} className="sr-only">{actionText(t, p.validationFailureAction)}</span>
                </td>
                <td className="py-2.5 text-muted-foreground">
                  {p.rulesCount} {t("policies.rulesCount")}
                  <div className="flex gap-1 mt-1 flex-wrap">
                    {p.rules.map((r) => (
                      <span key={r.name} title={ruleTypeText(t, r.type)} className="text-xs bg-muted text-muted-foreground px-1.5 py-0.5 rounded">
                        {r.name} ({r.type})
                      </span>
                    ))}
                  </div>
                </td>
                <td className="py-2.5">
                  <Badge className={p.ready ? "bg-narwhal-success/15 text-narwhal-success" : "bg-muted text-muted-foreground"}>
                    {p.ready ? t("policies.ready") : t("policies.notReady")}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Sheet open={!!selectedPolicy} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent className="w-full max-w-lg overflow-y-auto overflow-x-hidden">
          {selectedPolicy && (
            <>
              <PolicyDetail policy={selectedPolicy} />
            </>
          )}
        </SheetContent>
      </Sheet>
    </Card>
  )
}
