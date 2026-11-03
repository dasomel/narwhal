import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

// D-CERT-RENEW-ISSUING: renewal is a status-subresource write of the Issuing
// condition (cmctl renew), never a spec patch.
vi.mock("./config", () => ({ getK8sApiServer: () => "http://k8s.mock" }))
vi.mock("./valkey", () => ({ cacheGet: vi.fn(), cacheSet: vi.fn(), cacheDel: vi.fn() }))

import { renewCertificate, K8sHttpError } from "./k8s-client"

const PATH = "/apis/cert-manager.io/v1/namespaces/platform-system/certificates/narwhal-wildcard-tls"

function cert(conditions: Array<Record<string, unknown>> = [{ type: "Ready", status: "True", reason: "Ready" }]) {
  return {
    apiVersion: "cert-manager.io/v1",
    kind: "Certificate",
    metadata: { name: "narwhal-wildcard-tls", namespace: "platform-system", generation: 3, resourceVersion: "42" },
    spec: { renewBefore: "720h", dnsNames: ["a.example"] },
    status: { conditions, notAfter: "2027-01-01T00:00:00Z" },
  }
}

describe("renewCertificate", () => {
  beforeEach(() => vi.spyOn(console, "warn").mockImplementation(() => {}))
  afterEach(() => vi.unstubAllGlobals())

  it("PUTs the status subresource with an Issuing condition, keeps other conditions, leaves spec untouched", async () => {
    const original = cert()
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PUT" ? Response.json({}) : Response.json(original))
    vi.stubGlobal("fetch", fetchMock)

    await renewCertificate("narwhal-wildcard-tls", "platform-system")

    expect(fetchMock).toHaveBeenCalledTimes(2)
    const [url, init] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(url).toBe(`http://k8s.mock${PATH}/status`)
    expect(init.method).toBe("PUT")
    const body = JSON.parse(init.body as string)
    expect(body.spec).toEqual(original.spec)
    expect(body.metadata.resourceVersion).toBe("42")
    expect(body.status.conditions).toHaveLength(2)
    expect(body.status.conditions[0]).toEqual(original.status.conditions[0])
    expect(body.status.conditions[1]).toMatchObject({
      type: "Issuing",
      status: "True",
      reason: "ManuallyTriggered",
      observedGeneration: 3,
    })
    expect(body.status.conditions[1].message).toEqual(expect.any(String))
    expect(Number.isNaN(Date.parse(body.status.conditions[1].lastTransitionTime))).toBe(false)
    expect(body.status.notAfter).toBe("2027-01-01T00:00:00Z")
  })

  it("replaces an existing Issuing condition in place instead of duplicating it", async () => {
    const original = cert([
      { type: "Issuing", status: "False", reason: "Issued" },
      { type: "Ready", status: "True" },
    ])
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PUT" ? Response.json({}) : Response.json(original))
    vi.stubGlobal("fetch", fetchMock)

    await renewCertificate("narwhal-wildcard-tls", "platform-system")

    const body = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)
    expect(body.status.conditions.map((c: { type: string }) => c.type)).toEqual(["Issuing", "Ready"])
    expect(body.status.conditions[0]).toMatchObject({ status: "True", reason: "ManuallyTriggered" })
  })

  it("re-reads once on a 409 conflict and then succeeds", async () => {
    let puts = 0
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method !== "PUT") return Response.json(cert())
      return puts++ === 0 ? new Response(null, { status: 409 }) : Response.json({})
    })
    vi.stubGlobal("fetch", fetchMock)

    await renewCertificate("narwhal-wildcard-tls", "platform-system")
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it.each([403, 404, 422])("propagates a %i as K8sHttpError carrying the status and logs without bodies", async (status) => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PUT" ? new Response("secret-body-sentinel", { status }) : Response.json(cert()))
    vi.stubGlobal("fetch", fetchMock)

    const err = await renewCertificate("narwhal-wildcard-tls", "platform-system").catch((e) => e)
    expect(err).toBeInstanceOf(K8sHttpError)
    expect(err.status).toBe(status)
    const logged = JSON.stringify(vi.mocked(console.warn).mock.calls)
    expect(logged).toContain(String(status))
    expect(logged).not.toContain("secret-body-sentinel")
  })
})
