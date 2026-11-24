import { describe, expect, it } from "vitest"
import { mapApiServerPod, mapPolicy } from "./k8s-client"

type Item = Parameters<typeof mapPolicy>[0]

describe("mapPolicy (Kyverno policies)", () => {
  it("maps policies.kyverno.io annotations, match/exclude and validate.message", () => {
    const p = mapPolicy({
      metadata: {
        name: "p",
        annotations: {
          "policies.kyverno.io/title": "T",
          "policies.kyverno.io/description": "  D  ",
          "policies.kyverno.io/category": "C",
          "policies.kyverno.io/severity": "high",
        },
      },
      spec: {
        validationFailureAction: "Enforce",
        rules: [{
          name: "r",
          match: { any: [{ resources: { kinds: ["Pod"] } }, { resources: { kinds: ["Pod", "Job"] } }], resources: { kinds: ["Service"] } },
          exclude: { any: [{ resources: { kinds: ["CronJob"], namespaces: ["kube-system", "devtools"] } }] },
          validate: { message: " nope " },
        }],
      },
    } as Item, "cluster")
    expect(p).toMatchObject({ title: "T", description: "D", category: "C", severity: "high", validationFailureAction: "Enforce", rulesCount: 1 })
    expect(p.rules[0]).toEqual({
      name: "r", type: "validate", message: "nope", failureAction: null,
      matchKinds: ["Service", "Pod", "Job"], excludeKinds: ["CronJob"], excludeNamespaces: ["kube-system", "devtools"],
    })
  })

  it("returns null annotations and empty lists when absent", () => {
    const p = mapPolicy({ metadata: { name: "p" }, spec: { rules: [{ name: "r", mutate: {} }] } } as Item, "namespace")
    expect(p).toMatchObject({ title: null, description: null, category: null, severity: null, background: true, validationFailureAction: "Audit" })
    expect(p.rules[0]).toMatchObject({ type: "mutate", message: null, matchKinds: [], excludeKinds: [], excludeNamespaces: [] })
  })

  it("derives the action from per-rule failureAction (Kyverno 1.13+)", () => {
    const rule = (name: string, failureAction?: string) => ({ name, validate: { failureAction } })
    expect(mapPolicy({ metadata: { name: "p" }, spec: { rules: [rule("a", "Enforce"), rule("b", "Enforce")] } } as Item, "cluster").validationFailureAction).toBe("Enforce")
    expect(mapPolicy({ metadata: { name: "p" }, spec: { rules: [rule("a", "Enforce"), rule("b", "Audit")] } } as Item, "cluster").validationFailureAction).toBe("Mixed")
    expect(mapPolicy({ metadata: { name: "p" }, spec: { rules: [rule("a")] } } as Item, "cluster").validationFailureAction).toBe("Audit")
    const p = mapPolicy({ metadata: { name: "p" }, spec: { validationFailureAction: "Audit", rules: [rule("a", "Enforce")] } } as Item, "cluster")
    expect(p.validationFailureAction).toBe("Audit")
    expect(p.rules[0].failureAction).toBe("Enforce")
  })
})

describe("mapApiServerPod readiness (R7 closed-world)", () => {
  const baseSpec = {
    containers: [{ name: "kube-apiserver", command: ["kube-apiserver", "--audit-log-path=/var/log/a.log"] }],
  }

  it("reproduction (R7): marks Pod without status or without Ready=True condition as ready=false", () => {
    expect(mapApiServerPod({ metadata: { name: "apiserver-no-status" }, spec: baseSpec }).ready).toBe(false)
    expect(
      mapApiServerPod({ metadata: { name: "apiserver-no-conds" }, spec: baseSpec, status: { phase: "Running" } }).ready,
    ).toBe(false)
    expect(
      mapApiServerPod({
        metadata: { name: "apiserver-ready-false" },
        spec: baseSpec,
        status: { phase: "Running", conditions: [{ type: "Ready", status: "False" }] },
      }).ready,
    ).toBe(false)
    expect(
      mapApiServerPod({
        metadata: { name: "apiserver-pending" },
        spec: baseSpec,
        status: { phase: "Pending", conditions: [{ type: "Ready", status: "True" }] },
      }).ready,
    ).toBe(false)
  })

  it("marks Pod Running with explicit Ready=True condition as ready=true", () => {
    const info = mapApiServerPod({
      metadata: { name: "apiserver-ready" },
      spec: baseSpec,
      status: { phase: "Running", conditions: [{ type: "Ready", status: "True" }] },
    })
    expect(info.ready).toBe(true)
    expect(info.hasApiserverContainer).toBe(true)
  })
})
