import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"

vi.mock("./config", () => ({ getK8sApiServer: () => "http://k8s.mock" }))
vi.mock("./valkey", () => ({ cacheGet: vi.fn().mockResolvedValue(null), cacheSet: vi.fn(), cacheDel: vi.fn() }))
vi.mock("@/lib/auth", () => ({ requireRole: vi.fn() }))

import { getCertificates } from "./k8s-client"
import { requireRole } from "@/lib/auth"
import { GET } from "@/app/api/settings/certs/route"

function rawCert(overrides: {
  name?: string
  namespace?: string
  conditions?: Array<{ type: string; status: string }>
  revision?: unknown
  notBefore?: string
  notAfter?: string
  renewalTime?: string
} = {}) {
  return {
    metadata: { name: overrides.name ?? "openbao-tls", namespace: overrides.namespace ?? "openbao" },
    spec: {
      dnsNames: ["openbao.narwhal.local"],
      issuerRef: { name: "narwhal-ca", kind: "ClusterIssuer" },
    },
    status: {
      conditions: overrides.conditions ?? [{ type: "Ready", status: "True" }],
      ...(overrides.revision !== undefined ? { revision: overrides.revision } : {}),
      notBefore: overrides.notBefore ?? "2026-11-18T00:00:00.000Z",
      notAfter: overrides.notAfter ?? "2027-02-16T00:00:00.000Z",
      renewalTime: overrides.renewalTime ?? "2027-01-17T00:00:00.000Z",
    },
  }
}

describe("getCertificates projection (revision, issuing)", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("projects status.revision and Issuing condition while preserving existing fields", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({
        items: [
          rawCert({
            name: "openbao-tls",
            revision: 2,
            conditions: [
              { type: "Ready", status: "True" },
              { type: "Issuing", status: "True" },
            ],
          }),
          rawCert({
            name: "argocd-tls",
            revision: 1,
            conditions: [
              { type: "Ready", status: "True" },
              { type: "Issuing", status: "False" },
            ],
          }),
          rawCert({
            name: "fresh-tls",
            conditions: [{ type: "Ready", status: "False" }],
          }),
        ],
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    const certs = await getCertificates()
    expect(certs).toHaveLength(3)
    expect(certs[0]).toEqual({
      name: "openbao-tls",
      namespace: "openbao",
      ready: true,
      issuing: true,
      revision: 2,
      notBefore: "2026-11-18T00:00:00.000Z",
      notAfter: "2027-02-16T00:00:00.000Z",
      dnsNames: ["openbao.narwhal.local"],
      issuer: "narwhal-ca",
      renewalTime: "2027-01-17T00:00:00.000Z",
    })
    expect(certs[1]).toMatchObject({ name: "argocd-tls", ready: true, issuing: false, revision: 1 })
    expect(certs[2]).toMatchObject({ name: "fresh-tls", ready: false, issuing: false, revision: null })
  })

  it("treats non-numeric revision as null and preserves revision 0", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({
        items: [
          rawCert({ name: "zero-rev", revision: 0 }),
          rawCert({ name: "bad-rev", revision: "2" }),
        ],
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    const certs = await getCertificates()
    expect(certs[0].revision).toBe(0)
    expect(certs[1].revision).toBeNull()
  })

  it("GET /api/settings/certs enforces cluster-admin gate and returns revision and issuing", async () => {
    vi.mocked(requireRole).mockResolvedValueOnce({ error: "forbidden" } as never)
    const denied = await GET()
    expect(denied.status).toBe(403)

    vi.mocked(requireRole).mockResolvedValueOnce({ session: { user: { role: "cluster-admin" } } } as never)
    vi.stubGlobal("fetch", vi.fn(async () =>
      Response.json({
        items: [
          rawCert({
            name: "openbao-tls",
            revision: 3,
            conditions: [
              { type: "Ready", status: "True" },
              { type: "Issuing", status: "True" },
            ],
          }),
        ],
      }),
    ))

    const ok = await GET()
    expect(ok.status).toBe(200)
    const body = await ok.json()
    expect(body[0]).toMatchObject({ name: "openbao-tls", revision: 3, issuing: true })
  })
})
