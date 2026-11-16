import { beforeEach, describe, expect, it, vi } from "vitest"

const mockRedirect = vi.hoisted(() =>
  vi.fn((url: string): never => {
    throw new Error(`NEXT_REDIRECT:${url}`)
  }),
)
const mockAuth = vi.hoisted(() => vi.fn())
const mockHasSessionAccess = vi.hoisted(() => vi.fn())
const mockGetNodeDetail = vi.hoisted(() => vi.fn())
const mockGetPodsByNode = vi.hoisted(() => vi.fn())
const mockGetNodeMetrics = vi.hoisted(() => vi.fn())
const mockGetNodePodCount = vi.hoisted(() => vi.fn())

vi.mock("server-only", () => ({}))
vi.mock("next/navigation", () => ({ redirect: mockRedirect }))
vi.mock("@/lib/auth", () => ({ auth: mockAuth }))
vi.mock("@/lib/scope", () => ({ hasSessionAccess: mockHasSessionAccess }))
vi.mock("@/lib/k8s-client", () => ({
  getNodeDetail: mockGetNodeDetail,
  getPodsByNode: mockGetPodsByNode,
}))
vi.mock("@/lib/prometheus", () => ({
  getNodeMetrics: mockGetNodeMetrics,
  getNodePodCount: mockGetNodePodCount,
}))
vi.mock("@/lib/i18n-server", () => ({ getLocale: vi.fn(async () => "ko") }))
vi.mock("@/components/dashboard/metric-chart", () => ({ MetricChart: () => null }))
vi.mock("@/components/nodes/audit/audit-tab-client", () => ({ AuditTabClient: () => null }))

const { default: NodeDetailPage } = await import("./page")

describe("NodeDetailPage server guard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("redirects to /access-denied before fetching node or pod data when scope is empty", async () => {
    mockAuth.mockResolvedValue({ groups: ["developer"], teams: [], user: { role: "developer" } })
    mockHasSessionAccess.mockResolvedValue(false)

    await expect(
      NodeDetailPage({
        params: Promise.resolve({ name: "node-1" }),
        searchParams: Promise.resolve({}),
      }),
    ).rejects.toThrow("NEXT_REDIRECT:/access-denied")

    expect(mockGetNodeDetail).not.toHaveBeenCalled()
    expect(mockGetPodsByNode).not.toHaveBeenCalled()
    expect(mockGetNodeMetrics).not.toHaveBeenCalled()
    expect(mockGetNodePodCount).not.toHaveBeenCalled()
  })

  it("redirects to /login before fetching node or pod data when unauthenticated", async () => {
    mockAuth.mockResolvedValue(null)

    await expect(
      NodeDetailPage({
        params: Promise.resolve({ name: "node-1" }),
        searchParams: Promise.resolve({}),
      }),
    ).rejects.toThrow("NEXT_REDIRECT:/login")

    expect(mockGetNodeDetail).not.toHaveBeenCalled()
    expect(mockGetPodsByNode).not.toHaveBeenCalled()
    expect(mockGetNodeMetrics).not.toHaveBeenCalled()
    expect(mockGetNodePodCount).not.toHaveBeenCalled()
  })
})
