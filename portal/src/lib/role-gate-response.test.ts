import { describe, expect, it } from "vitest"
import { roleGateErrorResponse } from "./role-gate-response"

describe("roleGateErrorResponse", () => {
  it("maps unauthorized to 401 without a code", async () => {
    const res = roleGateErrorResponse({ error: "unauthorized" })
    expect(res.status).toBe(401)
    expect(await res.json()).toEqual({ error: "Unauthorized" })
  })

  it("maps plain forbidden to 403 without a code", async () => {
    const res = roleGateErrorResponse({ error: "forbidden" })
    expect(res.status).toBe(403)
    expect(await res.json()).toEqual({ error: "Forbidden" })
  })

  it("preserves gate.code on 403 responses", async () => {
    const res = roleGateErrorResponse({ error: "forbidden", code: "ACCESS_NOT_GRANTED" })
    expect(res.status).toBe(403)
    expect(await res.json()).toEqual({ error: "Forbidden", code: "ACCESS_NOT_GRANTED" })
  })

  it("supports custom forbiddenMessage, extra body fields, and response headers", async () => {
    const res = roleGateErrorResponse(
      { error: "forbidden", code: "ACCESS_NOT_GRANTED" },
      {
        forbiddenMessage: "Forbidden: cluster-admin only",
        extra: { ok: false },
        headers: { "Cache-Control": "no-store" },
      },
    )
    expect(res.status).toBe(403)
    expect(res.headers.get("cache-control")).toBe("no-store")
    expect(await res.json()).toEqual({
      ok: false,
      error: "Forbidden: cluster-admin only",
      code: "ACCESS_NOT_GRANTED",
    })
  })
})
