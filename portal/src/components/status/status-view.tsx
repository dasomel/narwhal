"use client"

import { useQuery } from "@tanstack/react-query"
import { OverallBanner } from "./overall-banner"
import { ComponentGrid } from "./component-grid"
import { IncidentList } from "./incident-list"
import type { PlatformStatus } from "@/types/api"
import { DataState } from "@/components/ui/data-state"
import { apiFetchJson } from "@/lib/scope-guard"

export function StatusView({ isOperator }: { isOperator: boolean }) {
  const { data, isLoading, isError, refetch } = useQuery<PlatformStatus>({
    queryKey: ["platform-status"],
    queryFn: ({ signal }) => apiFetchJson<PlatformStatus>("/api/status", { signal }),
    refetchInterval: 15_000,
  })

  if (isLoading) {
    return <DataState state="loading" onRetry={() => { void refetch() }} />
  }

  if (isError) {
    return <DataState state="error" onRetry={() => { void refetch() }} />
  }

  if (!data) {
    return <DataState state="empty" />
  }

  return (
    <div className="space-y-6">
      <OverallBanner status={data} />
      <ComponentGrid status={data} isOperator={isOperator} />
      <IncidentList incidents={data.incidents} />
    </div>
  )
}
