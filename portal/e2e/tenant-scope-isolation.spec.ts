import { expect, test, type Page } from "@playwright/test"
import { ko } from "../src/lib/i18n"

const fixtureURL = process.env.PLAYWRIGHT_E2E_FIXTURE_URL
const valkeyStateURL = process.env.PLAYWRIGHT_E2E_VALKEY_URL

async function login(page: Page, label: string, callbackUrl: string) {
  expect((await page.request.get("/api/auth/csrf")).status()).toBe(200)
  await page.goto(`/login?callbackUrl=${encodeURIComponent(callbackUrl)}`)
  await page.waitForLoadState("networkidle")
  await page.getByRole("button", { name: `${label}으로 로그인`, exact: true }).click()
}

test("scoped developer gets scoped APIs, 403 on admin-only/cross-tenant routes, and clean UI states", async ({ page, request }) => {
  if (!fixtureURL || !valkeyStateURL) throw new Error("Playwright fixtures not configured")
  await request.post(`${fixtureURL}/__test/reset`)
  await request.post(`${valkeyStateURL}/__test/reset`)

  await login(page, "Platform Team Developer", "/governance")
  await expect(page).toHaveURL(/\/governance$/)

  // Admin-only routes return 403 without ACCESS_NOT_GRANTED
  for (const path of ["/api/governance/distribution", "/api/service-graph/stream"]) {
    const res = await page.request.get(path)
    expect(res.status()).toBe(403)
    expect(await res.json()).toEqual({ error: "Forbidden" })
  }

  // Cross-tenant per-resource routes return 403 without ACCESS_NOT_GRANTED
  for (const path of ["/api/service-graph/frontend-e2e", "/api/traces?service=frontend-e2e"]) {
    const res = await page.request.get(path)
    expect(res.status()).toBe(403)
    expect(await res.json()).toEqual({ error: "Forbidden" })
  }

  // /api/cluster returns aggregates + platform-e2e only, hiding nodes/controlPlane/frontend-e2e
  const clusterRes = await page.request.get("/api/cluster")
  expect(clusterRes.status()).toBe(200)
  const clusterBody = await clusterRes.json()
  expect(clusterBody.nodes).toEqual([])
  expect(clusterBody.controlPlane).toEqual([])
  expect(clusterBody.namespaces.map((n: { name: string }) => n.name)).toEqual(["platform-e2e"])
  const serializedCluster = JSON.stringify(clusterBody)
  expect(serializedCluster).not.toContain("frontend-e2e")
  expect(serializedCluster).not.toContain("worker-e2e-1")
  expect(serializedCluster).not.toContain("kube-apiserver")

  // Governance Distribution tab renders admin-required message cleanly (no error/spinner)
  await page.getByRole("tab", { name: ko["governance.tabDistribution"], exact: true }).click()
  await expect(page.getByText(ko["distribution.forbidden"], { exact: true })).toBeVisible()

  // Architecture page renders scoped cluster summary and hides live stream toggle
  await page.goto("/architecture")
  await expect(page.getByText("platform-e2e", { exact: true })).toBeVisible()
  await expect(page.getByText("frontend-e2e", { exact: true })).toHaveCount(0)
  await expect(page.getByText("worker-e2e-1", { exact: true })).toHaveCount(0)

  await page.getByRole("button", { name: ko["architecture.view.services"], exact: true }).click()
  await expect(page.getByRole("button", { name: new RegExp(ko["svcMap.live"]) })).toHaveCount(0)
})

test("cluster-admin retains full distribution, cluster infra, and live service-graph access", async ({ page, request }) => {
  if (!fixtureURL || !valkeyStateURL) throw new Error("Playwright fixtures not configured")
  await request.post(`${fixtureURL}/__test/reset`)
  await request.post(`${valkeyStateURL}/__test/reset`)

  await login(page, "Cluster Admin", "/architecture")
  await expect(page).toHaveURL(/\/architecture$/)

  const distRes = await page.request.get("/api/governance/distribution")
  expect(distRes.status()).toBe(200)

  const clusterRes = await page.request.get("/api/cluster")
  expect(clusterRes.status()).toBe(200)
  const clusterBody = await clusterRes.json()
  expect(clusterBody.nodes).toHaveLength(1)
  expect(clusterBody.controlPlane).toHaveLength(1)
  expect(clusterBody.namespaces.map((n: { name: string }) => n.name)).toEqual(["platform-e2e", "frontend-e2e"])

  await expect(page.getByText("worker-e2e-1", { exact: true })).toBeVisible()
  await page.getByRole("button", { name: ko["architecture.view.services"], exact: true }).click()
  await expect(page.getByRole("button", { name: new RegExp(ko["svcMap.live"]) })).toBeVisible()
})
