import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  return {
    ...actual,
    useQuery: vi.fn(),
    useMutation: () => ({ mutate: vi.fn(), isPending: false }),
    useQueryClient: vi.fn(),
  }
})

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { role: "developer" } } }),
}))

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
  useQueryClient,
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
import { ActivityFeed, type Alert } from "./activity-feed"
import type { TimelineEvent } from "@/app/api/events/route"

function makeResponse(
  body: unknown,
  options: { status?: number; scope?: string } = {},
): Response {
  const headers = new Headers()
  if (options.scope) headers.set(SCOPE_FINGERPRINT_HEADER, options.scope)
  return new Response(JSON.stringify(body), {
    status: options.status ?? 200,
    headers,
  })
}

async function waitUntil(condition: () => boolean, maxTurns = 50): Promise<void> {
  for (let i = 0; i < maxTurns; i++) {
    if (condition()) return
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  throw new Error("Condition was not satisfied within async turn budget")
}

const teamBAlert: Alert = {
  fingerprint: "fp-b",
  status: { state: "active" },
  labels: { alertname: "TeamBCrashLoop", namespace: "team-b-ns", severity: "critical" },
  annotations: { summary: "team-b pod restarting" },
  startsAt: "2026-11-18T00:00:00.000Z",
}

const teamAAlert: Alert = {
  fingerprint: "fp-a",
  status: { state: "active" },
  labels: { alertname: "TeamAHighLatency", namespace: "team-a-ns", severity: "warning" },
  annotations: { summary: "team-a p99 elevated" },
  startsAt: "2026-11-18T00:01:00.000Z",
}

const teamBEvent: TimelineEvent = {
  id: "deploy-team-b-1",
  type: "deploy",
  title: "team-b-api",
  description: "Deployed revision abc1234",
  timestamp: "2026-11-18T00:00:30.000Z",
  severity: "success",
}

const teamAEvent: TimelineEvent = {
  id: "deploy-team-a-1",
  type: "deploy",
  title: "team-a-web",
  description: "Deployed revision def5678",
  timestamp: "2026-11-18T00:01:30.000Z",
  severity: "success",
}

function mountActivityFeed(handler: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => handler(String(input))),
  )

  const prevIsServer = environmentManager.isServer
  environmentManager.setIsServer(() => false)

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  })
  const unbind = bindScopeQueryClient(queryClient)
  const selection = createScopedState<Alert | null>(null)
  const unsubSelection = selection.mount()

  vi.mocked(useQueryClient).mockReturnValue(queryClient)

  const captured = new Map<string, UseQueryOptions<unknown>>()
  const observers = new Map<string, QueryObserver<unknown>>()

  const renderWithResults = (overrides?: Record<string, unknown>) => {
    vi.mocked(useQuery).mockImplementation((opts) => {
      const key = JSON.stringify(opts.queryKey)
      captured.set(key, opts as UseQueryOptions<unknown>)
      const obs = observers.get(key)
      const res = obs?.getCurrentResult()
      return (
        overrides?.[key] ?? {
          data: res?.data,
          isLoading: res?.isLoading ?? false,
          isError: res?.isError ?? false,
          refetch: () => obs?.refetch() ?? Promise.resolve(),
        }
      ) as never
    })
    return renderToStaticMarkup(
      <LocaleProvider locale="en">
        <ActivityFeed selection={selection} />
      </LocaleProvider>,
    )
  }

  renderWithResults({
    '["alerts"]': { data: undefined, isLoading: true, isError: false, refetch: vi.fn() },
    '["events"]': { data: undefined, isLoading: true, isError: false, refetch: vi.fn() },
  })

  for (const [key, opts] of captured.entries()) {
    const obs = new QueryObserver(queryClient, opts)
    obs.subscribe(() => {})
    observers.set(key, obs)
  }

  return {
    queryClient,
    selection,
    alertsObserver: observers.get('["alerts"]')!,
    eventsObserver: observers.get('["events"]')!,
    render: () => renderWithResults(),
    dispose: () => {
      unsubSelection()
      unbind()
      resetScopeGuard()
      queryClient.clear()
      environmentManager.setIsServer(prevIsServer)
    },
  }
}

describe("ActivityFeed with real QueryClient", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockReset()
    resetScopeGuard()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    resetScopeGuard()
  })

  it("evicts old tenant data when X-Narwhal-Scope changes on the next response", async () => {
    let phase: "ab" | "a-only" = "ab"
    const app = mountActivityFeed((url) => {
      if (url === "/api/alerts") {
        return makeResponse(phase === "ab" ? [teamBAlert, teamAAlert] : [teamAAlert], {
          scope: phase === "ab" ? "11111111111111111111111111111111" : "22222222222222222222222222222222",
        })
      }
      return makeResponse(phase === "ab" ? [teamBEvent, teamAEvent] : [teamAEvent], {
        scope: phase === "ab" ? "11111111111111111111111111111111" : "22222222222222222222222222222222",
      })
    })

    try {
      await waitUntil(
        () =>
          app.alertsObserver.getCurrentResult().status === "success" &&
          app.eventsObserver.getCurrentResult().status === "success",
      )

      const initialHtml = app.render()
      expect(initialHtml).toContain("TeamBCrashLoop")
      expect(initialHtml).toContain("team-b-api")

      // Permission reduced: next /api/alerts poll returns a different X-Narwhal-Scope.
      phase = "a-only"
      await app.alertsObserver.refetch()

      await waitUntil(() => app.eventsObserver.getCurrentResult().status === "success")
      const nextHtml = app.render()
      expect(nextHtml).not.toContain("TeamBCrashLoop")
      expect(nextHtml).not.toContain("team-b-api")
      expect(nextHtml).toContain("TeamAHighLatency")
      expect(nextHtml).toContain("team-a-web")
      expect(JSON.stringify(app.queryClient.getQueryData(["events"]))).not.toContain("team-b-api")
    } finally {
      app.dispose()
    }
  })

  it("clears cached data and closes the open alert detail Sheet on 403 ACCESS_NOT_GRANTED", async () => {
    let revoked = false
    const app = mountActivityFeed((url) => {
      if (revoked) {
        return makeResponse({ error: "Forbidden", code: ACCESS_NOT_GRANTED_CODE }, { status: 403 })
      }
      return makeResponse(url === "/api/alerts" ? [teamBAlert] : [teamBEvent], {
        scope: "11111111111111111111111111111111",
      })
    })

    try {
      await waitUntil(
        () =>
          app.alertsObserver.getCurrentResult().status === "success" &&
          app.eventsObserver.getCurrentResult().status === "success",
      )

      // User opens the detail Sheet on a team-b alert.
      app.selection.set(teamBAlert)
      const openHtml = app.render()
      expect(openHtml).toContain('data-sheet-open="true"')
      expect(openHtml).toContain("team-b-ns")

      // Access is revoked; next refetch gets 403 ACCESS_NOT_GRANTED.
      revoked = true
      await app.alertsObserver.refetch()

      expect(app.queryClient.getQueryData(["alerts"])).toBeUndefined()
      expect(app.queryClient.getQueryData(["events"])).toBeUndefined()
      expect(app.selection.get()).toBeNull()

      const afterHtml = app.render()
      expect(afterHtml).not.toContain('data-sheet-open="true"')
      expect(afterHtml).not.toContain("TeamBCrashLoop")
      expect(afterHtml).not.toContain("team-b-api")
      expect(afterHtml).not.toContain("team-b-ns")
    } finally {
      app.dispose()
    }
  })
})
