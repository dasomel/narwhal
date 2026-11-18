import { beforeEach, describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

const mockUseQuery = vi.fn()
const mockUseRole = vi.fn()

vi.mock("@tanstack/react-query", () => ({
  useQuery: (opts: unknown) => mockUseQuery(opts),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

vi.mock("@/lib/i18n-client", () => ({
  useT: () => (key: string) => key,
  useLocale: () => "en",
}))

vi.mock("@/hooks/use-role", () => ({
  useRole: () => mockUseRole(),
}))

import { ActivityFeed } from "./activity-feed"

describe("ActivityFeed React Query scope isolation", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseQuery.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    })
  })

  it("includes scopeFingerprint in queryKeys for alerts and events", () => {
    mockUseRole.mockReturnValue({
      role: "developer",
      can: () => true,
      scopeFingerprint: "fingerprint-alpha",
    })

    renderToStaticMarkup(<ActivityFeed />)

    const calls = mockUseQuery.mock.calls as Array<[{ queryKey: unknown[] }]>
    const keys = calls.map((c) => c[0].queryKey)

    expect(keys).toContainEqual(["alerts", "fingerprint-alpha"])
    expect(keys).toContainEqual(["events", "fingerprint-alpha"])
  })

  it("changes queryKeys when scopeFingerprint changes, isolating the browser cache across scopes", () => {
    mockUseRole.mockReturnValue({
      role: "developer",
      can: () => true,
      scopeFingerprint: "fingerprint-beta",
    })

    renderToStaticMarkup(<ActivityFeed />)

    const calls = mockUseQuery.mock.calls as Array<[{ queryKey: unknown[] }]>
    const keys = calls.map((c) => c[0].queryKey)

    expect(keys).toContainEqual(["alerts", "fingerprint-beta"])
    expect(keys).toContainEqual(["events", "fingerprint-beta"])
    expect(keys).not.toContainEqual(["alerts", "fingerprint-alpha"])
    expect(keys).not.toContainEqual(["events", "fingerprint-alpha"])
  })

  it("handles empty or undefined scopeFingerprint safely", () => {
    mockUseRole.mockReturnValue({
      role: "viewer",
      can: () => false,
      scopeFingerprint: "",
    })

    renderToStaticMarkup(<ActivityFeed />)

    const calls = mockUseQuery.mock.calls as Array<[{ queryKey: unknown[] }]>
    const keys = calls.map((c) => c[0].queryKey)

    expect(keys).toContainEqual(["alerts", ""])
    expect(keys).toContainEqual(["events", ""])
  })
})
