import { beforeEach, describe, expect, it, vi } from "vitest"

const redirect = vi.fn((url: string) => {
  throw new Error(`REDIRECT:${url}`)
})
vi.mock("next/navigation", () => ({ redirect: (url: string) => redirect(url) }))
const auth = vi.fn()
vi.mock("@/lib/auth", () => ({ auth: () => auth() }))
const hasSessionAccess = vi.fn()
vi.mock("@/lib/scope", () => ({ hasSessionAccess: (s: unknown) => hasSessionAccess(s) }))
vi.mock("@/components/auth/access-denied-view", () => ({ AccessDeniedView: () => null }))

import AccessDeniedPage from "./page"

describe("/access-denied", () => {
  beforeEach(() => {
    redirect.mockClear()
    auth.mockResolvedValue({ user: { email: "a@b", role: "developer" } })
    hasSessionAccess.mockResolvedValue(true)
  })

  it("redirects a user who still has access back to / by default", async () => {
    await expect(AccessDeniedPage({ searchParams: Promise.resolve({}) })).rejects.toThrow("REDIRECT:/")
  })

  it("does not redirect for reason=scope-changed, so a replica mismatch cannot loop", async () => {
    const el = await AccessDeniedPage({ searchParams: Promise.resolve({ reason: "scope-changed" }) })
    expect(redirect).not.toHaveBeenCalled()
    expect((el as { props: { variant: string } }).props.variant).toBe("scope-changed")
  })

  it("still sends a signed-out visitor to /login", async () => {
    auth.mockResolvedValue(null)
    await expect(AccessDeniedPage({ searchParams: Promise.resolve({ reason: "scope-changed" }) })).rejects.toThrow("REDIRECT:/login")
  })
})
