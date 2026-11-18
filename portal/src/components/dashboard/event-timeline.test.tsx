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
  resetScopeGuard,
} from "@/lib/scope-guard"
import { EventTimeline } from "./event-timeline"
import type { TimelineEvent } from "@/app/api/events/route"

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

const teamBEvent: TimelineEvent = {
  id: "deploy-team-b-1",
  type: "deploy",
  title: "team-b-secret-svc",
  description: "Deployed team-b secret",
  timestamp: "2026-11-18T00:00:00.000Z",
  severity: "success",
}

const teamAEvent: TimelineEvent = {
  id: "deploy-team-a-1",
  type: "deploy",
  title: "team-a-svc",
  description: "Deployed team-a",
  timestamp: "2026-11-18T00:01:00.000Z",
  severity: "success",
}

function mountEventTimeline(handler: () => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(async () => handler()))

  const prevIsServer = environmentManager.isServer
  environmentManager.setIsServer(() => false)

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const unbind = bindScopeQueryClient(queryClient)
  const selection = createScopedState<TimelineEvent | null>(null)
  const unsubSelection = selection.mount()

  let capturedOptions: UseQueryOptions<TimelineEvent[]> | null = null
  let observer: QueryObserver<TimelineEvent[]> | null = null

  const renderWithResult = (override?: { data?: TimelineEvent[]; isLoading?: boolean; isError?: boolean }) => {
    vi.mocked(useQuery).mockImplementation((opts) => {
      capturedOptions = opts as UseQueryOptions<TimelineEvent[]>
      const cur = override ?? observer?.getCurrentResult()
      return {
        data: cur?.data,
        isLoading: cur?.isLoading ?? false,
        isError: cur?.isError ?? false,
        refetch: () => observer?.refetch() ?? Promise.resolve(),
      } as never
    })
    return renderToStaticMarkup(
      <LocaleProvider locale="en">
        <EventTimeline selection={selection} />
      </LocaleProvider>,
    )
  }

  renderWithResult({ isLoading: true })
  observer = new QueryObserver<TimelineEvent[]>(queryClient, capturedOptions!)
  observer.subscribe(() => {})

  return {
    queryClient,
    selection,
    observer,
    render: () => renderWithResult(),
    dispose: () => {
      unsubSelection()
      unbind()
      resetScopeGuard()
      queryClient.clear()
      environmentManager.setIsServer(prevIsServer)
    },
  }
}

describe("EventTimeline with real QueryClient", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockReset()
    resetScopeGuard()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetScopeGuard()
  })

  it("evicts old events and closes open detail Sheet when fingerprint rotates", async () => {
    let phase: "b" | "a" = "b"
    const view = mountEventTimeline(() =>
      makeResponse(phase === "b" ? [teamBEvent] : [teamAEvent], {
        scope: phase === "b" ? "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" : "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      }),
    )

    try {
      await waitUntil(() => view.observer.getCurrentResult().status === "success")
      view.selection.set(teamBEvent)

      const openHtml = view.render()
      expect(openHtml).toContain('data-sheet-open="true"')
      expect(openHtml).toContain("team-b-secret-svc")

      phase = "a"
      await view.observer.refetch()

      expect(view.selection.get()).toBeNull()
      const closedHtml = view.render()
      expect(closedHtml).toContain('data-sheet-open="false"')
      expect(closedHtml).not.toContain("team-b-secret-svc")
      expect(closedHtml).toContain("team-a-svc")
    } finally {
      view.dispose()
    }
  })

  it("clears event cache and closes open detail Sheet on 403 ACCESS_NOT_GRANTED", async () => {
    let revoked = false
    const view = mountEventTimeline(() =>
      revoked
        ? makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
        : makeResponse([teamBEvent], { scope: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }),
    )

    try {
      await waitUntil(() => view.observer.getCurrentResult().status === "success")
      view.selection.set(teamBEvent)
      expect(view.render()).toContain('data-sheet-open="true"')

      revoked = true
      await view.observer.refetch()

      expect(view.queryClient.getQueryData(["events"])).toBeUndefined()
      expect(view.selection.get()).toBeNull()
      const html = view.render()
      expect(html).toContain('data-sheet-open="false"')
      expect(html).not.toContain("team-b-secret-svc")
      expect(html).not.toContain("Deployed team-b secret")
    } finally {
      view.dispose()
    }
  })
})
