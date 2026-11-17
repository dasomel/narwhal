import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { ReactNode } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { QueryClient } from "@tanstack/react-query"

// Lightweight Node-only React + React Query hook harness so CertsTable's real
// useState / useEffect / useQuery / useMutation / polling lifecycle can run
// under vitest + fake timers without jsdom.
const hookEnv = vi.hoisted(() => {
  interface HookState {
    mounted: boolean
    callingDirect: boolean
    locale: "ko" | "en"
    cursor: number
    states: unknown[]
    refs: Array<{ current: unknown }>
    effects: Array<{ deps?: readonly unknown[]; cleanup?: () => void; pending?: () => void | (() => void) }>
    queryClient: QueryClient | null
    queryUnsub?: () => void
    scheduleRender?: () => void
  }

  const state: HookState = {
    mounted: false,
    callingDirect: false,
    locale: "en",
    cursor: 0,
    states: [],
    refs: [],
    effects: [],
    queryClient: null,
  }

  return state
})

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>()
  return {
    ...actual,
    useContext: <T,>(ctx: React.Context<T>) => {
      if (hookEnv.callingDirect) return hookEnv.locale as unknown as T
      return actual.useContext(ctx)
    },
    useCallback: <T extends (...args: never[]) => unknown>(fn: T, deps: React.DependencyList) => {
      if (hookEnv.callingDirect) return fn
      return actual.useCallback(fn, deps)
    },
    useState: <T,>(initial: T | (() => T)) => {
      if (!hookEnv.mounted) return actual.useState(initial)
      const idx = hookEnv.cursor++
      if (!(idx in hookEnv.states)) {
        hookEnv.states[idx] = typeof initial === "function" ? (initial as () => T)() : initial
      }
      const setState = (updater: T | ((prev: T) => T)) => {
        const prev = hookEnv.states[idx] as T
        const next = typeof updater === "function" ? (updater as (p: T) => T)(prev) : updater
        if (!Object.is(prev, next)) {
          hookEnv.states[idx] = next
          hookEnv.scheduleRender?.()
        }
      }
      return [hookEnv.states[idx] as T, setState] as const
    },
    useRef: <T,>(initial: T) => {
      if (!hookEnv.mounted) return actual.useRef(initial)
      const idx = hookEnv.cursor++
      if (!(idx in hookEnv.refs)) {
        hookEnv.refs[idx] = { current: initial }
      }
      return hookEnv.refs[idx] as { current: T }
    },
    useEffect: (fn: () => void | (() => void), deps?: readonly unknown[]) => {
      if (!hookEnv.mounted) return actual.useEffect(fn, deps)
      const idx = hookEnv.cursor++
      const prev = hookEnv.effects[idx]
      const changed =
        !prev ||
        !deps ||
        !prev.deps ||
        deps.length !== prev.deps.length ||
        deps.some((d, i) => !Object.is(d, prev.deps![i]))
      if (changed) {
        hookEnv.effects[idx] = { deps, cleanup: prev?.cleanup, pending: fn }
      }
    },
  }
})

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>()
  actual.environmentManager.setIsServer(() => false)
  return {
    ...actual,
    useQueryClient: () => {
      if (!hookEnv.queryClient) throw new Error("QueryClient not initialized")
      return hookEnv.queryClient
    },
    useQuery: (options: {
      queryKey: readonly unknown[]
      queryFn: () => Promise<unknown>
      refetchInterval?: number | false
    }) => {
      const qc = hookEnv.queryClient
      if (!qc) throw new Error("QueryClient not initialized")
      const observerRef = (globalThis as Record<string, unknown>).__certQueryObserver as {
        observer?: import("@tanstack/react-query").QueryObserver<unknown, Error>
        setTick?: (n: number) => void
      }

      if (!observerRef.observer) {
        const obs = new actual.QueryObserver(qc, {
          queryKey: options.queryKey,
          queryFn: options.queryFn,
          refetchInterval: options.refetchInterval,
          retry: false,
        })
        observerRef.observer = obs
        hookEnv.queryUnsub = obs.subscribe(() => {
          observerRef.setTick?.(Date.now())
        })
      } else {
        observerRef.observer.setOptions({
          queryKey: options.queryKey,
          queryFn: options.queryFn,
          refetchInterval: options.refetchInterval,
          retry: false,
        })
      }

      const res = observerRef.observer.getCurrentResult()
      return {
        data: res.data,
        isLoading: res.isLoading,
        isError: res.isError,
        refetch: () => observerRef.observer!.refetch(),
      }
    },
    useMutation: (options: {
      mutationFn: (vars: unknown) => Promise<unknown>
      onMutate?: (vars: unknown) => void
      onSuccess?: (data: unknown, vars: unknown) => void
      onError?: (err: unknown, vars: unknown) => void
      onSettled?: (data: unknown, err: unknown, vars: unknown) => void
    }) => {
      return {
        isPending: false,
        mutate: (vars: unknown) => {
          void (async () => {
            options.onMutate?.(vars)
            try {
              const data = await options.mutationFn(vars)
              options.onSuccess?.(data, vars)
              options.onSettled?.(data, undefined, vars)
            } catch (err) {
              options.onError?.(err, vars)
              options.onSettled?.(undefined, err, vars)
            }
          })()
        },
      }
    },
  }
})

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: { user: { role: "cluster-admin" } } }),
}))

import { LocaleProvider } from "@/lib/i18n-client"
import { CertsTable } from "./certs-table"
import type { Certificate } from "@/lib/k8s-client"

interface LooseElement {
  type?: unknown
  props: Record<string, unknown>
}

function isElement(node: unknown): node is LooseElement {
  return typeof node === "object" && node !== null && "props" in node
}

function makeCert(overrides: Partial<Certificate> = {}): Certificate {
  return {
    name: "openbao-tls",
    namespace: "openbao",
    ready: true,
    issuing: false,
    revision: 1,
    notBefore: "2026-11-17T00:00:00.000Z",
    notAfter: "2027-02-15T00:00:00.000Z",
    dnsNames: ["openbao.narwhal.local"],
    issuer: "narwhal-ca",
    renewalTime: "2027-01-16T00:00:00.000Z",
    ...overrides,
  }
}

function mountCertsTable(locale: "ko" | "en" = "en") {
  hookEnv.mounted = true
  hookEnv.callingDirect = false
  hookEnv.locale = locale
  hookEnv.cursor = 0
  hookEnv.states = []
  hookEnv.refs = []
  hookEnv.effects = []
  hookEnv.queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  ;(globalThis as Record<string, unknown>).__certQueryObserver = {}

  const flushEffects = () => {
    for (const eff of hookEnv.effects) {
      if (eff?.pending) {
        eff.cleanup?.()
        const cleanup = eff.pending()
        eff.cleanup = typeof cleanup === "function" ? cleanup : undefined
        eff.pending = undefined
      }
    }
  }

  const renderHtml = () => {
    hookEnv.cursor = 0
    const html = renderToStaticMarkup(
      <LocaleProvider locale={locale}>
        <CertsTable />
      </LocaleProvider>,
    )
    flushEffects()
    return html
  }

  let renderQueued = false
  hookEnv.scheduleRender = () => {
    if (renderQueued || !hookEnv.mounted) return
    renderQueued = true
    queueMicrotask(() => {
      renderQueued = false
      if (hookEnv.mounted) renderHtml()
    })
  }

  const clickRenew = (rowIndex = 0) => {
    // Render with the same hook slots to collect the current button element's onClick.
    hookEnv.cursor = 0
    hookEnv.callingDirect = true
    let tree: ReactNode
    try {
      tree = CertsTable()
    } finally {
      hookEnv.callingDirect = false
    }
    flushEffects()
    const buttons: LooseElement[] = []
    const collect = (node: ReactNode) => {
      if (Array.isArray(node)) {
        node.forEach(collect)
        return
      }
      if (!isElement(node)) return
      if (node.type === "button" && typeof node.props.onClick === "function") {
        buttons.push(node)
      }
      collect(node.props.children as ReactNode)
    }
    collect(tree)
    const btn = buttons[rowIndex]
    if (!btn) throw new Error(`Renew button at index ${rowIndex} not found`)
    ;(btn.props.onClick as () => void)()
  }

  const unmount = () => {
    for (const eff of hookEnv.effects) {
      eff?.cleanup?.()
    }
    hookEnv.queryUnsub?.()
    hookEnv.queryClient?.clear()
    hookEnv.mounted = false
  }

  return { renderHtml, clickRenew, unmount }
}

async function flushMicrotasks(rounds = 6) {
  for (let i = 0; i < rounds; i++) {
    await Promise.resolve()
  }
}

describe("CertsTable renew flow", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-11-18T00:00:00.000Z"))
  })

  afterEach(() => {
    hookEnv.queryUnsub?.()
    hookEnv.queryClient?.clear()
    hookEnv.mounted = false
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it("shows issued timestamp and revision, polls through Issuing, then shows Renewed at T2 and updated cell", async () => {
    const T1 = "2026-11-17T00:00:00.000Z"
    const T2 = "2026-11-18T00:00:04.000Z"
    const formattedT1 = new Date(T1).toLocaleString("en-US")
    const formattedT2 = new Date(T2).toLocaleString("en-US")

    const getResponses: Certificate[][] = [
      [makeCert({ revision: 1, notBefore: T1, issuing: false })],
      [makeCert({ revision: 1, notBefore: T1, issuing: true })],
      [makeCert({ revision: 2, notBefore: T1, issuing: true })],
      [makeCert({ revision: 2, notBefore: T2, issuing: false })],
    ]
    let getCalls = 0
    let postCalls = 0

    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/settings/certs/renew" && init?.method === "POST") {
        postCalls++
        return Response.json({ success: true, pending: true })
      }
      if (url === "/api/settings/certs") {
        const payload = getResponses[Math.min(getCalls, getResponses.length - 1)]
        getCalls++
        return Response.json(payload)
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const view = mountCertsTable("en")
    view.renderHtml()
    await flushMicrotasks()

    const initialHtml = view.renderHtml()
    expect(initialHtml).toContain("Issued")
    expect(initialHtml).toContain(formattedT1)
    expect(initialHtml).toContain("rev 1")
    expect(initialHtml).toContain("Renew")

    view.clickRenew(0)
    await flushMicrotasks()

    expect(postCalls).toBe(1)
    expect(getCalls).toBe(2)

    const issuingHtml = view.renderHtml()
    expect(issuingHtml).toContain("Issuing…")
    expect(issuingHtml).not.toContain("Renewed at")

    await vi.advanceTimersByTimeAsync(2000)
    await flushMicrotasks()
    expect(getCalls).toBe(3)
    expect(view.renderHtml()).toContain("Issuing…")

    await vi.advanceTimersByTimeAsync(2000)
    await flushMicrotasks()
    expect(getCalls).toBe(4)

    const renewedHtml = view.renderHtml()
    expect(renewedHtml).toContain(`Renewed at ${formattedT2}`)
    expect(renewedHtml).toContain(formattedT2)
    expect(renewedHtml).toContain("rev 2")
    expect(renewedHtml).not.toContain("Issuing…")

    // Polling must stop once renewed.
    await vi.advanceTimersByTimeAsync(10_000)
    await flushMicrotasks()
    expect(getCalls).toBe(4)

    view.unmount()
  })

  it("stops polling and shows still-pending message when timeout window elapses without change", async () => {
    const T1 = "2026-11-17T00:00:00.000Z"
    let getCalls = 0
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/settings/certs/renew" && init?.method === "POST") {
        return Response.json({ success: true, pending: true })
      }
      if (url === "/api/settings/certs") {
        getCalls++
        return Response.json([makeCert({ revision: 1, notBefore: T1, issuing: getCalls > 1 })])
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const view = mountCertsTable("en")
    view.renderHtml()
    await flushMicrotasks()

    view.clickRenew(0)
    await flushMicrotasks()
    expect(view.renderHtml()).toContain("Issuing…")

    await vi.advanceTimersByTimeAsync(30_000)
    await flushMicrotasks()

    const timedOutHtml = view.renderHtml()
    expect(timedOutHtml).toContain("Still pending — check again later")
    expect(timedOutHtml).not.toContain("Issuing…")

    const callsAtTimeout = getCalls
    await vi.advanceTimersByTimeAsync(10_000)
    await flushMicrotasks()
    expect(getCalls).toBe(callsAtTimeout)

    view.unmount()
  })

  it("shows existing error message and does not poll when renew POST fails", async () => {
    let getCalls = 0
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/settings/certs/renew" && init?.method === "POST") {
        return new Response(JSON.stringify({ error: "boom" }), { status: 500, statusText: "Internal Server Error" })
      }
      if (url === "/api/settings/certs") {
        getCalls++
        return Response.json([makeCert()])
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const view = mountCertsTable("en")
    view.renderHtml()
    await flushMicrotasks()
    expect(getCalls).toBe(1)

    view.clickRenew(0)
    await flushMicrotasks()

    const errorHtml = view.renderHtml()
    expect(errorHtml).toContain("Renewal failed")

    await vi.advanceTimersByTimeAsync(10_000)
    await flushMicrotasks()
    expect(getCalls).toBe(1)

    view.unmount()
  })

  it("stops polling on unmount and keeps row feedback isolated to the renewed row across re-renders", async () => {
    const T1 = "2026-11-17T00:00:00.000Z"
    let getCalls = 0
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/settings/certs/renew" && init?.method === "POST") {
        return Response.json({ success: true, pending: true })
      }
      if (url === "/api/settings/certs") {
        getCalls++
        return Response.json([
          makeCert({ name: "openbao-tls", namespace: "openbao", notBefore: T1, revision: 1, issuing: getCalls > 1 }),
          makeCert({ name: "argocd-tls", namespace: "argocd", notBefore: T1, revision: 1, issuing: false }),
        ])
      }
      throw new Error(`Unexpected fetch: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)

    const view = mountCertsTable("ko")
    view.renderHtml()
    await flushMicrotasks()

    const koInitial = view.renderHtml()
    expect(koInitial).toContain("발급일")

    view.clickRenew(0)
    await flushMicrotasks()

    const koIssuing = view.renderHtml()
    expect(koIssuing).toContain("발급 중...")
    // Second row must still retain its Renew button.
    expect(koIssuing).toContain("갱신")

    const callsBeforeUnmount = getCalls
    view.unmount()

    await vi.advanceTimersByTimeAsync(10_000)
    await flushMicrotasks()
    expect(getCalls).toBe(callsBeforeUnmount)
  })
})
