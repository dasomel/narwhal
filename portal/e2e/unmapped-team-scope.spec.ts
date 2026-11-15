import { expect, test } from "@playwright/test"
import { ko } from "../src/lib/i18n"

for (const label of ["Developer", "Viewer"]) {
  test(`${label} without a team sees a mapping notice and no tenant apps`, async ({ page }) => {
    // D1: Real mock-provider session + real scope resolution; providers are local
    // fixtures. Cost: no live OIDC evidence. Escape hatch: run with real SSO separately.
    expect((await page.request.get("/api/auth/csrf")).status()).toBe(200)
    await page.goto("/login?callbackUrl=%2Fmy-apps")
    await page.getByRole("button", { name: `${label}으로 로그인`, exact: true }).click()
    await expect(page).toHaveURL(/\/my-apps$/)
    await expect(page.getByText(ko["myApps.empty.description"], { exact: true }).first()).toBeVisible()

    const response = await page.request.get("/api/my-apps")
    expect(response.status()).toBe(200)
    expect(await response.json()).toMatchObject({
      scope: { hasMapping: false, namespaces: [] }, scopedApps: [],
    })
    // Write authorization stays separate and cannot gain access from role defaults.
    expect((await page.request.post("/api/catalog/portal-e2e/sync")).status()).toBe(403)
  })
}
