import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: vi.fn(),
  }
})

vi.mock("@/components/ui/sheet", () => ({
  Sheet: ({ open, children }: { open?: boolean; children: React.ReactNode }) => (
    <div data-sheet-open={String(Boolean(open))}>{open ? children : null}</div>
  ),
  SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  SheetTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
}))

import {
  QueryClient,
  QueryObserver,
  environmentManager,
  useQuery,
  type UseQueryOptions,
} from "@tanstack/react-query"
import { LocaleProvider } from "@/lib/i18n-client"
import {
  ACCESS_NOT_GRANTED_CODE,
  SCOPE_FINGERPRINT_HEADER,
  bindScopeQueryClient,
  createScopedState,
  defaultScopeGuard,
  resetScopeGuard,
} from "@/lib/scope-guard"
import { ResourceDetailDrawer } from "./resource-detail-drawer"

function makeResponse(body: unknown, options: { status?: number; scope?: string } = {}): Response {
  const headers = new Headers()
  if (options.scope) headers.set(SCOPE_FINGERPRINT_HEADER, options.scope)
  return new Response(JSON.stringify(body), { status: options.status ?? 200, headers })
}

async function waitUntil(condition: () => boolean, maxTurns = 50): Promise<void> {
  for (let i = 0; i < maxTurns; i++) {
    if (condition()) return
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  throw new Error("Condition was not satisfied within async turn budget")
}

describe("ResourceDetailDrawer with real QueryClient", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockReset()
    resetScopeGuard()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetScopeGuard()
  })

  it("evicts pod list, detail, and events queries and resets selectedPod on 403 ACCESS_NOT_GRANTED", async () => {
    let revoked = false
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (revoked) {
          return makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
        }
        const url = String(input)
        const scope = "11111111111111111111111111111111"
        if (url.startsWith("/api/k8s/pods")) {
          return makeResponse(
            {
              pods: [{ name: "team-b-pod", ready: "1/1", phase: "Running", restarts: 0, node: "w1", age: "2026-11-18T00:00:00.000Z" }],
              freshness: { source: "live", observedAt: "2026-11-18T00:00:00.000Z" },
            },
            { scope },
          )
        }
        if (url.startsWith("/api/k8s/resource")) {
          return makeResponse(
            {
              name: "team-b-pod",
              namespace: "team-b-ns",
              phase: "Running",
              podIP: "10.0.0.12",
              node: "w1",
              qosClass: "BestEffort",
              serviceAccount: "default",
              createdAt: "2026-11-18T00:00:00.000Z",
              labels: { app: "secret-app" },
              containers: [],
              conditions: [],
            },
            { scope },
          )
        }
        return makeResponse({ events: [], freshness: { source: "live", observedAt: "2026-11-18T00:00:00.000Z" } }, { scope })
      }),
    )

    const prevIsServer = environmentManager.isServer
    environmentManager.setIsServer(() => false)

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    })
    const unbind = bindScopeQueryClient(queryClient)
    const selectedPodState = createScopedState<string | null>(null)
    const unsubSelectedPod = selectedPodState.mount()
    const drawerOpen = createScopedState(false)
    const unsubOpen = drawerOpen.mount()
    drawerOpen.set(true)
    const onOpenChange = vi.fn((next: boolean) => drawerOpen.set(next))
    const unsubGuardClose = defaultScopeGuard.subscribe(() => onOpenChange(false))

    const captured = new Map<string, UseQueryOptions<unknown>>()
    const observers = new Map<string, QueryObserver<unknown>>()

    const renderDrawer = (overrideLoading = false) => {
      vi.mocked(useQuery).mockImplementation((opts) => {
        const key = JSON.stringify(opts.queryKey)
        captured.set(key, opts as UseQueryOptions<unknown>)
        const obs = observers.get(key)
        const cur = obs?.getCurrentResult()
        return {
          data: overrideLoading ? undefined : cur?.data,
          isLoading: overrideLoading ? true : (cur?.isLoading ?? false),
          isError: overrideLoading ? false : (cur?.isError ?? false),
          error: overrideLoading ? null : (cur?.error ?? null),
          refetch: () => obs?.refetch() ?? Promise.resolve(),
        } as never
      })
      return renderToStaticMarkup(
        <LocaleProvider locale="en">
          <ResourceDetailDrawer
            namespace="team-b-ns"
            open={drawerOpen.get()}
            onOpenChange={onOpenChange}
            selectedPodState={selectedPodState}
          />
        </LocaleProvider>,
      )
    }

    try {
      // First pass registers pod list query, then select a pod to register detail + events queries.
      renderDrawer(true)
      selectedPodState.set("team-b-pod")
      renderDrawer(true)

      for (const [key, opts] of captured.entries()) {
        const obs = new QueryObserver(queryClient, opts)
        obs.subscribe(() => {})
        observers.set(key, obs)
      }

      const podsKey = '["governance-pods","team-b-ns",null]'
      const detailKey = '["governance-pod-detail","team-b-ns","team-b-pod"]'
      const eventsKey = '["governance-pod-events","team-b-ns","team-b-pod"]'

      await waitUntil(
        () =>
          observers.get(podsKey)?.getCurrentResult().status === "success" &&
          observers.get(detailKey)?.getCurrentResult().status === "success" &&
          observers.get(eventsKey)?.getCurrentResult().status === "success",
      )

      const openHtml = renderDrawer()
      expect(openHtml).toContain('data-sheet-open="true"')
      expect(openHtml).toContain("team-b-pod")
      expect(openHtml).toContain("10.0.0.12")

      // Access revoked -> 403 ACCESS_NOT_GRANTED on refetch.
      revoked = true
      await observers.get(podsKey)!.refetch()

      expect(onOpenChange).toHaveBeenCalledWith(false)
      expect(drawerOpen.get()).toBe(false)
      expect(selectedPodState.get()).toBeNull()
      expect(queryClient.getQueryData(["governance-pods", "team-b-ns", undefined])).toBeUndefined()
      expect(queryClient.getQueryData(["governance-pod-detail", "team-b-ns", "team-b-pod"])).toBeUndefined()
      expect(queryClient.getQueryData(["governance-pod-events", "team-b-ns", "team-b-pod"])).toBeUndefined()

      const closedHtml = renderDrawer()
      expect(closedHtml).toContain('data-sheet-open="false"')
      expect(closedHtml).not.toContain("team-b-pod")
      expect(closedHtml).not.toContain("10.0.0.12")
    } finally {
      unsubGuardClose()
      unsubOpen()
      unsubSelectedPod()
      unbind()
      resetScopeGuard()
      queryClient.clear()
      environmentManager.setIsServer(prevIsServer)
    }
  })
})
