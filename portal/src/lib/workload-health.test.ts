import { beforeEach, describe, expect, it, vi } from "vitest"
vi.mock("./valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))
vi.mock("./k8s-client", () => ({ listBounded: vi.fn() }))
import { cacheGet, cacheSet } from "./valkey"
import { listBounded } from "./k8s-client"
import { getWorkloadSnapshot, readiness, workloadStatus } from "./workload-health"

beforeEach(() => { vi.resetAllMocks() })
describe("workload readiness", () => {
  it.each([[3, 3, "healthy"], [3, 1, "degraded"], [3, 0, "down"], [0, 0, "healthy"],
    [undefined, 0, undefined], [1, -1, undefined], [1, NaN, undefined], ["1", 1, undefined]])("maps %s/%s to %s", (desired, ready, expected) => {
    expect(readiness(desired, ready)).toBe(expected)
  })
  it("uses controller counts and hides Job owners and completed pods without listing Jobs", async () => {
    vi.mocked(listBounded).mockImplementation(async (path) => ({ truncated: false, pages: 1, items: path.endsWith("pods") ? [
      { metadata: { name: "backup-pod", namespace: "storage", ownerReferences: [{ kind: "Job", name: "backup" }] } },
      { metadata: { name: "completed", namespace: "storage" }, status: { phase: "Succeeded" } },
      { metadata: { name: "running", namespace: "storage" }, status: { phase: "Running" } },
    ] : [{ metadata: { name: "app", namespace: "devtools" }, spec: { replicas: 2 }, status: { readyReplicas: 1, desiredNumberScheduled: 2, numberReady: 2 } }] }))
    const snapshot = await getWorkloadSnapshot()
    expect(workloadStatus(snapshot, "app", "devtools", "Deployment")).toBe("degraded")
    expect(workloadStatus(snapshot, "app", "devtools", "DaemonSet")).toBe("healthy")
    expect(workloadStatus(snapshot, "app")).toBeUndefined()
    expect(snapshot.hidden.map((p) => p.name)).toEqual(["backup-pod", "backup", "completed"])
    expect(listBounded).toHaveBeenCalledTimes(4)
    expect(cacheSet).toHaveBeenCalledWith("architecture:workloads", snapshot, 30)
  })
  it("returns no evidence on API failure, empty lists or malformed resources", async () => {
    vi.mocked(listBounded).mockRejectedValueOnce(new Error("403"))
      .mockResolvedValue({ truncated: false, pages: 1, items: [null, {}, { metadata: { name: "bad", namespace: "ns" }, spec: { replicas: "bad" } }] } as never)
    expect(await getWorkloadSnapshot()).toEqual({ workloads: [], hidden: [] })
    expect(cacheSet).not.toHaveBeenCalled()
    vi.mocked(listBounded).mockResolvedValue({ truncated: false, pages: 1, items: [] })
    expect(await getWorkloadSnapshot()).toEqual({ workloads: [], hidden: [] })
  })
  it("serves cached evidence without any cluster list", async () => {
    const snapshot = { workloads: [], hidden: [] }
    vi.mocked(cacheGet).mockResolvedValue(snapshot)
    expect(await getWorkloadSnapshot()).toBe(snapshot)
    expect(listBounded).not.toHaveBeenCalled()
  })
})
