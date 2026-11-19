import type { ReactElement, ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"

type HookHarness = {
  refs: Array<{ current: unknown }>
  effects: Array<{ deps: unknown[] | undefined; cleanup?: void | (() => void) }>
  cursor: number
}

const harness: HookHarness = {
  refs: [],
  effects: [],
  cursor: 0,
}

function depsChanged(prev: unknown[] | undefined, next: unknown[] | undefined): boolean {
  if (!prev || !next) return true
  if (prev.length !== next.length) return true
  return next.some((v, i) => !Object.is(v, prev[i]))
}

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react")>()
  return {
    ...actual,
    useState: <T,>(initial: T | (() => T)) => {
      const idx = harness.cursor++
      if (!(idx in harness.refs)) {
        harness.refs[idx] = {
          current: typeof initial === "function" ? (initial as () => T)() : initial,
        }
      }
      return [harness.refs[idx].current as T, vi.fn()] as const
    },
    useRef: <T,>(initial: T) => {
      const idx = harness.cursor++
      if (!(idx in harness.refs)) {
        harness.refs[idx] = { current: initial }
      }
      return harness.refs[idx] as { current: T }
    },
    useEffect: (effect: () => void | (() => void), deps?: unknown[]) => {
      const idx = harness.cursor++
      const prev = harness.effects[idx]
      if (!prev || depsChanged(prev.deps, deps)) {
        if (typeof prev?.cleanup === "function") prev.cleanup()
        const cleanup = effect()
        harness.effects[idx] = { deps, cleanup }
      }
    },
  }
})

const sessionState: { data: { user?: { role: string }; error?: string } | null } = {
  data: null,
}
const mockSignIn = vi.fn(async () => undefined)

vi.mock("next-auth/react", () => ({
  SessionProvider: ({ children }: { children: ReactNode }) => children,
  useSession: () => ({ data: sessionState.data }),
  signIn: mockSignIn,
}))
vi.mock("@tanstack/react-query", () => ({
  QueryClient: class {},
  QueryClientProvider: ({ children }: { children: ReactNode }) => children,
}))
vi.mock("@tanstack/react-query-devtools", () => ({ ReactQueryDevtools: () => null }))
vi.mock("@/lib/i18n-client", () => ({ LocaleProvider: ({ children }: { children: ReactNode }) => children }))
vi.mock("@/components/ui/tooltip", () => ({ TooltipProvider: ({ children }: { children: ReactNode }) => children }))
vi.mock("@/lib/scope-guard", () => ({
  bindScopeQueryClient: vi.fn(() => () => {}),
  shouldRetryQuery: vi.fn(() => false),
}))

const { Providers, SessionErrorHandler } = await import("./providers")

function resetHarness() {
  harness.refs = []
  harness.effects = []
  harness.cursor = 0
}

function renderTree(node: ReactNode): void {
  if (!node || typeof node !== "object") return
  if (Array.isArray(node)) {
    for (const child of node) renderTree(child)
    return
  }
  const el = node as ReactElement<{ children?: ReactNode }>
  if (typeof el.type === "function") {
    const out = (el.type as (props: Record<string, unknown>) => ReactNode)(el.props)
    renderTree(out)
    return
  }
  if (el.props?.children !== undefined) {
    renderTree(el.props.children)
  }
}

describe("Providers / SessionErrorHandler (narwhal#734)", () => {
  beforeEach(() => {
    resetHarness()
    sessionState.data = null
    mockSignIn.mockClear()
    delete process.env.NEXT_PUBLIC_AUTH_MOCK
  })

  it("does not trigger signIn when the session has no RefreshTokenError", () => {
    sessionState.data = { user: { role: "developer" } }
    renderTree(<Providers locale="en">{null}</Providers>)
    expect(mockSignIn).not.toHaveBeenCalled()
  })

  it("calls signIn once when session.error is RefreshTokenError and does not loop on re-renders", () => {
    sessionState.data = { user: { role: "guest" }, error: "RefreshTokenError" }

    harness.cursor = 0
    SessionErrorHandler()
    expect(mockSignIn).toHaveBeenCalledTimes(1)
    expect(mockSignIn).toHaveBeenCalledWith("keycloak")

    // Simulate re-renders with the same RefreshTokenError or transient state changes
    harness.cursor = 0
    SessionErrorHandler()
    sessionState.data = { user: { role: "guest" }, error: undefined }
    harness.cursor = 0
    SessionErrorHandler()
    sessionState.data = { user: { role: "guest" }, error: "RefreshTokenError" }
    harness.cursor = 0
    SessionErrorHandler()

    expect(mockSignIn).toHaveBeenCalledTimes(1)
  })

  it("releases the guard when signIn fails so a later session update retries", async () => {
    sessionState.data = { user: { role: "guest" }, error: "RefreshTokenError" }
    mockSignIn.mockRejectedValueOnce(new Error("offline"))
    harness.cursor = 0
    SessionErrorHandler()
    expect(mockSignIn).toHaveBeenCalledTimes(1)
    // Let the rejected signIn promise settle (macrotask, not a microtask count).
    await new Promise((resolve) => setTimeout(resolve, 0))
    sessionState.data = { user: { role: "guest" }, error: undefined }
    harness.cursor = 0
    SessionErrorHandler()
    sessionState.data = { user: { role: "guest" }, error: "RefreshTokenError" }
    harness.cursor = 0
    SessionErrorHandler()
    expect(mockSignIn).toHaveBeenCalledTimes(2)
  })

  it("mounts SessionErrorHandler inside Providers so RefreshTokenError initiates sign-in", () => {
    sessionState.data = { user: { role: "guest" }, error: "RefreshTokenError" }
    renderTree(<Providers locale="ko">{null}</Providers>)
    expect(mockSignIn).toHaveBeenCalledTimes(1)
    expect(mockSignIn).toHaveBeenCalledWith("keycloak")
  })
})
