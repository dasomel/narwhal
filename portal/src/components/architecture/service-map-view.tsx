"use client"

import { useQuery } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import type { ServiceGraphResponse } from "@/app/api/service-graph/route"
import type { TranslationKey } from "@/lib/i18n"
import { useT } from "@/lib/i18n-client"
import { Badge } from "@/components/ui/badge"
import { DataState } from "@/components/ui/data-state"
import { ReactFlowMap } from "./service-map-diagram"
import { NodeDetailPanel } from "./service-map-detail"
import { ServiceGroups, ServiceSummary } from "./service-map-groups"
import { sumTraffic } from "./service-map-model"

type GraphWindow = "1m" | "1h" | "1d" | "7d" | "30d"
interface Props { initialNamespace?: string; focusService?: string }
const WINDOW_OPTIONS: { value: GraphWindow; key: TranslationKey }[] = [
  { value: "1h", key: "time.1h" },
  { value: "1d", key: "time.1d" },
  { value: "7d", key: "time.7d" },
  { value: "30d", key: "time.30d" },
]

export function ServiceMapView({ initialNamespace, focusService }: Props) {
  const t = useT()
  const [window, setWindow] = useState<GraphWindow>("7d")
  const [namespace, setNamespace] = useState(initialNamespace ?? "")
  const [errorThreshold, setErrorThreshold] = useState(5) // % 단위
  const [selectedId, setSelectedId] = useState<string | null>(null)
  // 실시간 모드: 최근 1분 순간 rate를 5초마다 폴링 (Prometheus 스크레이프 주기상 준실시간)
  const [live, setLive] = useState(false)
  const effWindow: GraphWindow = live ? "1m" : window

  // hubble-relay 흐름 스트림 (SSE) — 실시간 모드에서 엣지 활동 오버레이 (⚡ flows/s)
  const [liveFlows, setLiveFlows] = useState<Map<string, number>>(new Map())
  useEffect(() => {
    if (!live) {
      setLiveFlows(new Map())
      return
    }
    const es = new EventSource("/api/service-graph/stream")
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data) as { edges: Array<{ source: string; destination: string; flowsPerSec: number }> }
        setLiveFlows(new Map(d.edges.map((e) => [`${e.source}|${e.destination}`, e.flowsPerSec])))
      } catch {
        // 잘못된 이벤트는 무시
      }
    }
    // 오류 시 EventSource가 자동 재연결 — 그동안 오버레이만 비움
    es.onerror = () => setLiveFlows(new Map())
    return () => es.close()
  }, [live])

  const queryKey = ["service-graph", effWindow, namespace]
  const { data, isLoading, isError, refetch } = useQuery<ServiceGraphResponse>({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams({ window: effWindow })
      if (namespace) params.set("namespace", namespace)
      return fetch(`/api/service-graph?${params}`).then((r) => {
        if (!r.ok) throw new Error(String(r.status))
        return r.json()
      })
    },
    refetchInterval: live ? 5_000 : 60_000,
    staleTime: live ? 0 : 30_000,
  })

  // 에러율 임계 슬라이더 기반 엣지 필터
  const filteredData = useMemo(() => {
    if (!data) return null
    const threshold = errorThreshold / 100
    return {
      ...data,
      edges: data.edges.filter((e) => e.errorRate <= threshold || threshold >= 1),
    }
  }, [data, errorThreshold])

  // 네임스페이스 목록 — 서버가 내려주는 "필터 전 전체" 목록 사용.
  // (필터된 노드에서 추출하면 ns 선택 후 드롭다운이 이웃 ns만 남는 문제)
  const namespaces = useMemo(() => {
    if (!data) return []
    if (data.namespaces && data.namespaces.length > 0) return data.namespaces
    // 구버전 캐시 응답(namespaces 없음) 폴백
    return Array.from(new Set(data.nodes.map((n) => n.namespace)))
      .filter((ns) => ns && ns !== "unknown")
      .sort()
  }, [data])

  // sticky: Prometheus 타임아웃 등으로 빈 응답이 와도 드롭다운이 사라지지 않게
  // 마지막 정상 목록을 유지한다 (선택 ns가 목록에 없어도 표시 유지).
  const [nsOptions, setNsOptions] = useState<string[]>([])
  useEffect(() => {
    if (namespaces.length > 0) setNsOptions(namespaces)
  }, [namespaces])

  const isEmpty = !isLoading && !isError && filteredData && filteredData.nodes.length === 0

  return (
    <div className="space-y-4">
      {/* 컨트롤 바 */}
      <div className="flex flex-wrap items-center gap-3">
        {/* 윈도 셀렉터 (실시간 모드 중에는 잠금) */}
        <div className={`flex items-center gap-1 rounded-lg bg-muted p-1 ${live ? "opacity-40 pointer-events-none" : ""}`}>
          {WINDOW_OPTIONS.map((opt) => (
            <button
              type="button"
              key={opt.value}
              onClick={() => setWindow(opt.value)}
              className={`px-3 py-1 text-xs rounded-md transition-colors ${
                !live && window === opt.value
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {t(opt.key)}
            </button>
          ))}
        </div>

        {/* 실시간 토글 */}
        <button
          type="button"
          onClick={() => setLive((v) => !v)}
          title={t("svcMap.liveHint")}
          className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-semibold transition-colors ${
            live
              ? "border-narwhal-danger/60 bg-narwhal-danger/10 text-narwhal-danger"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          <span className={`inline-block w-2 h-2 rounded-full ${live ? "bg-narwhal-danger animate-pulse" : "bg-muted-foreground/50"}`} />
          {t("svcMap.live")}
          {live && data?.generatedAt && (
            <span className="font-mono font-normal text-narwhal-danger">
              {new Date(data.generatedAt).toLocaleTimeString("en-GB", { hour12: false })}
            </span>
          )}
        </button>

        {/* 네임스페이스 필터 (sticky 목록 — 빈 응답에도 유지) */}
        {nsOptions.length > 0 && (
          <select
            aria-label={t("svcMap.allNamespaces")}
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
            className="text-xs rounded border border-border bg-background px-2 py-1.5 text-foreground"
          >
            <option value="">{t("svcMap.allNamespaces")}</option>
            {nsOptions.map((ns) => (
              <option key={ns} value={ns}>
                {ns}
              </option>
            ))}
            {namespace && !nsOptions.includes(namespace) && (
              <option value={namespace}>{namespace}</option>
            )}
          </select>
        )}

        {/* 에러율 임계 슬라이더 */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>{t("svcMap.errorThreshold")}</span>
          <input
            type="range"
            aria-label={t("svcMap.errorThreshold")}
            min={0}
            max={100}
            step={1}
            value={errorThreshold}
            onChange={(e) => setErrorThreshold(Number(e.target.value))}
            className="w-24 accent-primary"
          />
          <span className="w-10 text-right font-mono">{errorThreshold}%</span>
        </div>

        {/* 범례 */}
        <div className="flex items-center gap-3 text-xs text-muted-foreground ml-auto">
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-0.5 bg-narwhal-success" /> {t("svcMap.legend.normal")}
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-0.5 bg-narwhal-warning" /> {t("svcMap.legend.warning")}
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-0.5 bg-narwhal-danger" /> {t("svcMap.legend.error")}
          </span>
        </div>
      </div>

      {data && (data.hiddenOneOffCount ?? 0) > 0 && <p className="text-xs text-muted-foreground">{t("svcMap.hiddenOneOff", { count: data.hiddenOneOffCount ?? 0 })}</p>}

      {/* notice 배너 (Prometheus 미응답 등) */}
      {data?.notice && (
        <div className="rounded border border-narwhal-warning/30 bg-narwhal-warning/10 px-4 py-2 text-xs text-narwhal-warning">
          {data.notice}
        </div>
      )}

      {/* 그래프 영역 */}
      {isLoading ? (
        <DataState state="loading" onRetry={() => { void refetch() }} />
      ) : isError ? (
        <DataState state="error" onRetry={() => { void refetch() }} />
      ) : isEmpty ? (
        <div className="space-y-3"><DataState state="empty" onRetry={() => { void refetch() }} /><a href="/onboarding" className="inline-flex rounded-md border border-border px-3 py-2 text-xs font-medium hover:bg-muted">{t("svcMap.empty.guide")}</a></div>
      ) : filteredData ? (
        <>
          <ServiceSummary data={filteredData} />
          <div className={`grid min-w-0 items-start gap-4 ${selectedId ? "xl:grid-cols-[minmax(0,1fr)_320px]" : ""}`}>
            <ServiceGroups data={filteredData} selectedId={selectedId} focusService={focusService} onSelect={setSelectedId} />
            {selectedId && <NodeDetailPanel data={filteredData} nodeId={selectedId} onClose={() => setSelectedId(null)} onSelect={setSelectedId} t={t} />}
          </div>
          <details className="min-w-0 rounded-xl border border-border bg-card p-5">
            <summary className="cursor-pointer text-sm font-medium">{t("arch640.connections")} <span className="ml-2 text-xs text-muted-foreground">{filteredData.edges.length}</span></summary>
            <div className="mt-4 overflow-x-auto">
              <ReactFlowMap data={filteredData} focusService={focusService} selectedId={selectedId} onSelect={setSelectedId} liveFlows={live ? liveFlows : undefined} />
            </div>
          </details>
          {/* 통계 */}
          <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
            <span>{t("svcMap.stats.services")} <strong className="text-foreground">{filteredData.nodes.length}</strong></span>
            <span>{t("svcMap.stats.connections")} <strong className="text-foreground">{filteredData.edges.length}</strong></span>
            <span>
              {t("svcMap.stats.traffic")}{" "}
              <strong className="text-foreground font-mono">
                {sumTraffic(filteredData.edges, filteredData.metricKind)}
              </strong>
            </span>
            {filteredData.nodes.some((n) => n.id.startsWith("unmapped-pods:")) && (
              <Badge variant="outline" className="text-xs">
                {t("svcMap.stats.unmapped")}
              </Badge>
            )}
            {!selectedId && (
              <span className="ml-auto">{t("svcMap.detail.clickHint")}</span>
            )}
          </div>
        </>
      ) : null}
    </div>
  )
}
