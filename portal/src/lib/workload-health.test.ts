import { beforeEach, describe, expect, it, vi } from "vitest"
vi.mock("./valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn() }))
vi.mock("./k8s-client", () => ({ listBounded: vi.fn() }))
import { cacheGet, cacheSet } from "./valkey"
import { listBounded } from "./k8s-client"
import { getWorkloadSnapshot, livePodStatus, readiness, workloadStatus } from "./workload-health"

beforeEach(() => { vi.resetAllMocks() })
describe("workload readiness", () => {
  it.each([[3, 3, "healthy"], [3, 1, "degraded"], [3, 0, "down"], [0, 0, undefined], [0, 1, undefined],
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
    expect(snapshot.complete).toBe(true)
    expect(snapshot.hidden.map((p) => p.name)).toEqual(["backup-pod", "backup", "completed"])
    expect(listBounded).toHaveBeenCalledTimes(4)
    expect(cacheSet).toHaveBeenCalledWith("architecture:workloads", snapshot, 30)
  })
  it("records only live operator/standalone pods and uses Pod Ready conditions", async () => {
    const pod = (name: string, phase: string, kind?: string, ready?: string) => ({
      metadata: { name, namespace: "database", ownerReferences: kind ? [{ kind, name: "narwhal-db" }] : [] },
      status: { phase, conditions: ready ? [{ type: "Ready", status: ready }] : [] },
    })
    vi.mocked(listBounded).mockImplementation(async (path) => ({ truncated: false, pages: 1, items: path.endsWith("pods") ? [
      pod("narwhal-db-1", "Running", "Cluster", "True"),
      pod("narwhal-db-2", "Running", "Cluster", "False"),
      pod("standalone", "Pending"),
      pod("backup", "Running", "Job", "True"),
      ...["ReplicaSet", "StatefulSet", "DaemonSet"].map((kind) => pod(kind, "Running", kind, "True")),
      pod("completed", "Succeeded", "Cluster", "True"),
      pod("unknown", "Unknown"),
    ] : [] }))
    const snapshot = await getWorkloadSnapshot()
    expect(snapshot.livePods).toEqual([
      { namespace: "database", name: "narwhal-db-1", ownerNames: ["narwhal-db"], status: "healthy" },
      { namespace: "database", name: "narwhal-db-2", ownerNames: ["narwhal-db"], status: "down" },
      { namespace: "database", name: "standalone", ownerNames: [], status: "down" },
    ])
    expect(livePodStatus(snapshot, "narwhal-db-1", "database")).toBe("healthy")
    expect(livePodStatus(snapshot, "narwhal-db", "database")).toBe("down")
    expect(livePodStatus(snapshot, "narwhal-db", "other")).toBeUndefined()
    expect(livePodStatus(snapshot, "narwhal-db")).toBe("down")
    expect(snapshot.hidden).toContainEqual({ namespace: "database", name: "backup" })
  })
  it("refreshes legacy cached snapshots without live pod evidence", async () => {
    vi.mocked(cacheGet).mockResolvedValue({ complete: true, workloads: [], hidden: [] })
    vi.mocked(listBounded).mockResolvedValue({ truncated: false, pages: 1, items: [] })
    expect((await getWorkloadSnapshot()).livePods).toEqual([])
    expect(listBounded).toHaveBeenCalledTimes(4)
  })
  it("returns no health on API failure, empty lists or malformed resources", async () => {
    vi.mocked(listBounded).mockRejectedValueOnce(new Error("403"))
      .mockResolvedValue({ truncated: false, pages: 1, items: [null, {}, { metadata: { name: "bad", namespace: "ns" }, spec: { replicas: "bad" } }] } as never)
    const partial = await getWorkloadSnapshot()
    expect(partial.complete).toBe(false)
    expect(partial.workloads.every((w) => w.status === undefined)).toBe(true)
    expect(cacheSet).not.toHaveBeenCalled()
    vi.mocked(listBounded).mockResolvedValue({ truncated: false, pages: 1, items: [] })
    expect(await getWorkloadSnapshot()).toEqual({ complete: true, workloads: [], livePods: [], hidden: [] })
  })
  it("marks truncated lists incomplete and does not cache them", async () => {
    vi.mocked(listBounded).mockResolvedValue({ truncated: false, pages: 1, items: [] })
      .mockResolvedValueOnce({ truncated: true, pages: 1, items: [] })
    expect((await getWorkloadSnapshot()).complete).toBe(false)
    expect(cacheSet).not.toHaveBeenCalled()
  })
  it("retains scaled-to-zero controller identity without a serving status", async () => {
    vi.mocked(listBounded).mockResolvedValue({ truncated: false, pages: 1, items: [] })
      .mockResolvedValueOnce({ truncated: false, pages: 1, items: [{ metadata: { name: "sleeping", namespace: "ns" }, spec: { replicas: 0 } }] })
    const snapshot = await getWorkloadSnapshot()
    expect(snapshot.workloads).toEqual([{ name: "sleeping", namespace: "ns", kind: "Deployment" }])
    expect(workloadStatus(snapshot, "sleeping", "ns")).toBeUndefined()
  })
  it("serves cached evidence without any cluster list", async () => {
    const snapshot = { complete: true, workloads: [], livePods: [], hidden: [] }
    vi.mocked(cacheGet).mockResolvedValue(snapshot)
    expect(await getWorkloadSnapshot()).toBe(snapshot)
    expect(listBounded).not.toHaveBeenCalled()
  })
})
