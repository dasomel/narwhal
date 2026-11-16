import { expect, test } from "@playwright/test"
import { ko } from "../src/lib/i18n"

for (const label of ["Developer", "Viewer"]) {
  test(`${label} without a team is denied portal access, gets 403 on data APIs, and can sign out`, async ({ page }) => {
    // D1: Real mock-provider session + real scope resolution; providers are local
    // fixtures. Cost: no live OIDC evidence. Escape hatch: run with real SSO separately.
    expect((await page.request.get("/api/auth/csrf")).status()).toBe(200)
    await page.goto("/login?callbackUrl=%2Fmy-apps")
    await page.getByRole("button", { name: `${label}으로 로그인`, exact: true }).click()
    await expect(page).toHaveURL(/\/access-denied$/)

    await expect(page.getByRole("heading", { name: ko["accessDenied.title"], exact: true })).toBeVisible()
    await expect(page.getByText(ko["accessDenied.description"], { exact: true })).toBeVisible()
    await expect(page.getByText(ko["accessDenied.hint"], { exact: true })).toBeVisible()
    await expect(page.getByRole("navigation")).toHaveCount(0)

    for (const path of ["/api/my-apps", "/api/catalog", "/api/events", "/api/hero"]) {
      const response = await page.request.get(path)
      expect(response.status()).toBe(403)
      expect(await response.json()).toEqual({ error: "Forbidden", code: "ACCESS_NOT_GRANTED" })
    }
    expect((await page.request.post("/api/catalog/portal-e2e/sync")).status()).toBe(403)
    expect((await page.request.get("/api/health/summary")).status()).toBe(200)

    await page.getByRole("button", { name: ko["accessDenied.signOut"], exact: true }).click()
    await expect(page).toHaveURL(/\/login$/)
  })
}
