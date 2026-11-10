"use client"

import { useState } from "react"
import { useSearchParams } from "next/navigation"
import { useT } from "@/lib/i18n-client"
import { ClusterInfraView } from "./cluster-infra-view"
import { ServiceMapView } from "./service-map-view"

type View = "infra" | "services"

export function ArchitectureView() {
  const t = useT()
  const params = useSearchParams()
  const initial: View = params.get("view") === "services" ? "services" : "infra"
  const focus = params.get("focus") ?? undefined
  const [view, setView] = useState<View>(initial)

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div><h1 className="text-2xl font-bold">{t("arch640.title")}</h1><p className="mt-1 text-sm text-muted-foreground">{t("arch640.description")}</p></div>
        <div className="flex w-fit shrink-0 rounded-lg bg-muted p-1" role="group" aria-label={t("arch640.view")}>
        {(["infra", "services"] as const).map((v) => (
          <button
            key={v}
            type="button"
            onClick={() => setView(v)}
            aria-pressed={view === v}
            className={`rounded-md px-3 py-1 text-xs transition-colors ${
              view === v
                ? "bg-card text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t(v === "infra" ? "architecture.view.infra" : "architecture.view.services")}
          </button>
        ))}
        </div>
      </div>
      {view === "infra" ? <ClusterInfraView /> : <ServiceMapView focusService={focus} />}
    </div>
  )
}
