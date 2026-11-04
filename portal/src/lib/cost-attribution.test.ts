import { describe, it, expect } from "vitest"
import { attributePods, podKey, podNameStem, UNCLASSIFIED_ID, type AttributionInput } from "./cost-attribution"

const base = (): AttributionInput => ({
  usage: [],
  labels: new Map(),
  owners: new Map(),
  replicaSetOwners: new Map(),
})
const usage = (namespace: string, pod: string, cpu = 1, memBytes = 1e9) => ({ namespace, pod, cpu, memBytes })

describe("attributePods chain", () => {
  it("prefers instance over every other identifier", () => {
    const i = base()
    i.usage = [usage("ns", "p-1")]
    i.labels.set(podKey("ns", "p-1"), { instance: "inst", name: "nm", app: "ap", k8sApp: "ka" })
    i.owners.set(podKey("ns", "p-1"), { kind: "StatefulSet", name: "sts" })
    expect(attributePods(i)).toMatchObject([{ id: "inst", source: "instance" }])
  })

  it.each([
    ["name", { name: "nm", app: "ap", k8sApp: "ka" }, "nm"],
    ["app", { app: "ap", k8sApp: "ka" }, "ap"],
    ["k8s-app", { k8sApp: "ka" }, "ka"],
  ])("falls back to %s label", (source, labels, name) => {
    const i = base()
    i.usage = [usage("ns", "p-1")]
    i.labels.set(podKey("ns", "p-1"), labels)
    expect(attributePods(i)).toMatchObject([{ id: `${source}:ns/${name}`, source, name, namespace: "ns" }])
  })

  it("treats empty and 'unknown' label values as missing", () => {
    const i = base()
    i.usage = [usage("ns", "p-1")]
    i.labels.set(podKey("ns", "p-1"), { instance: "unknown", name: "" })
    i.owners.set(podKey("ns", "p-1"), { kind: "DaemonSet", name: "ds" })
    expect(attributePods(i)[0]).toMatchObject({ source: "owner", kind: "DaemonSet", name: "ds" })
  })

  it("resolves ReplicaSet owners to the Deployment, or keeps the bare ReplicaSet", () => {
    const i = base()
    i.usage = [usage("ns", "a-abc12345-x2k4q"), usage("ns", "b-abc12345-x2k4q")]
    i.owners.set(podKey("ns", "a-abc12345-x2k4q"), { kind: "ReplicaSet", name: "a-abc12345" })
    i.owners.set(podKey("ns", "b-abc12345-x2k4q"), { kind: "ReplicaSet", name: "b-abc12345" })
    i.replicaSetOwners.set(podKey("ns", "a-abc12345"), { kind: "Deployment", name: "a" })
    const out = attributePods(i)
    expect(out.find((g) => g.name === "a")).toMatchObject({ source: "owner", kind: "Deployment" })
    expect(out.find((g) => g.name === "b-abc12345")).toMatchObject({ source: "owner", kind: "ReplicaSet" })
  })

  it("strips the CronJob timestamp from Job owners and groups their pods", () => {
    const i = base()
    i.usage = [usage("ns", "backup-29012345-aaaaa"), usage("ns", "backup-29012346-bbbbb")]
    i.owners.set(podKey("ns", "backup-29012345-aaaaa"), { kind: "Job", name: "backup-29012345" })
    i.owners.set(podKey("ns", "backup-29012346-bbbbb"), { kind: "Job", name: "backup-29012346" })
    expect(attributePods(i)).toMatchObject([{ source: "owner", kind: "Job", name: "backup", pods: 2, cpu: 2 }])
  })

  it("falls back to the pod-name stem for Node-owned and ownerless pods", () => {
    const i = base()
    i.usage = [usage("kube-system", "etcd-node1"), usage("ns", "lonely-5c8d9f7b6-q9w8e")]
    i.owners.set(podKey("kube-system", "etcd-node1"), { kind: "Node", name: "node1" })
    const out = attributePods(i)
    expect(out.map((g) => g.source)).toEqual(["pod", "pod"])
    expect(out.find((g) => g.namespace === "ns")?.name).toBe("lonely")
  })

  it("collects pod-less series into one unclassified row", () => {
    const i = base()
    i.usage = [usage("a", "", 0.5, 5e8), usage("b", "", 0.25, 2.5e8)]
    expect(attributePods(i)).toEqual([
      { id: UNCLASSIFIED_ID, source: "unclassified", name: UNCLASSIFIED_ID, pods: 2, cpu: 0.75, memBytes: 7.5e8 },
    ])
  })
})

describe("attributePods grouping and totals", () => {
  it("does not merge a fallback row with an instance row of the same name", () => {
    const i = base()
    i.usage = [usage("ns", "web-0"), usage("ns", "web-abc12345-x2k4q")]
    i.labels.set(podKey("ns", "web-0"), { instance: "web" })
    i.owners.set(podKey("ns", "web-abc12345-x2k4q"), { kind: "StatefulSet", name: "web" })
    const ids = attributePods(i).map((g) => g.id).sort()
    expect(ids).toEqual(["owner:ns/StatefulSet/web", "web"])
  })

  it("keeps the same fallback name in different namespaces separate", () => {
    const i = base()
    i.usage = [usage("a", "x-0"), usage("b", "x-0")]
    i.owners.set(podKey("a", "x-0"), { kind: "StatefulSet", name: "x" })
    i.owners.set(podKey("b", "x-0"), { kind: "StatefulSet", name: "x" })
    expect(attributePods(i)).toHaveLength(2)
  })

  it("conserves total cpu, memory and pod count across all levels", () => {
    const i = base()
    i.usage = [usage("n", "a-0", 1, 1e9), usage("n", "b-0", 2, 2e9), usage("n", "c-0", 3, 3e9), usage("n", "", 4, 4e9)]
    i.labels.set(podKey("n", "a-0"), { instance: "a" })
    i.labels.set(podKey("n", "b-0"), { app: "b" })
    i.owners.set(podKey("n", "c-0"), { kind: "StatefulSet", name: "c" })
    const out = attributePods(i)
    expect(out.reduce((s, g) => s + g.cpu, 0)).toBe(10)
    expect(out.reduce((s, g) => s + g.memBytes, 0)).toBe(10e9)
    expect(out.reduce((s, g) => s + g.pods, 0)).toBe(4)
  })
})

describe("podNameStem", () => {
  it.each([
    ["api-7d9f8c6b5-x2k4q", "api"],
    ["node-exporter-x2k4q", "node-exporter"],
    ["backup-29012345", "backup"],
    ["standalone", "standalone"],
  ])("%s -> %s", (pod, stem) => expect(podNameStem(pod)).toBe(stem))
})
