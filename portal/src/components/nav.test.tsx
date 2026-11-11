import { renderToString } from "react-dom/server"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const session = vi.hoisted(() => ({
  user: { name: "admin narwhal", role: "cluster-admin" },
}))
vi.mock("next-auth/react", () => ({ useSession: () => ({ data: session }) }))
vi.mock("next/navigation", () => ({ usePathname: () => "/" }))
vi.mock("@/lib/i18n-client", () => ({
  useT: () => (key: string) => key,
  LocaleSwitcher: () => null,
}))
vi.mock("@/components/theme-toggle", () => ({ ThemeToggle: () => null }))
vi.mock("@/components/dependency-health-indicator", () => ({ DependencyHealthIndicator: () => <span role="status">모든 시스템 정상</span> }))

beforeEach(() => {
  session.user.role = "cluster-admin"
  vi.stubEnv("APP_VERSION", "1.0.17")
  vi.stubEnv("APP_COMMIT", "abcdef1234567")
})
afterEach(() => vi.unstubAllEnvs())

import { getVisibleNavGroups, getVisibleNavItems, Nav, NavVersion } from "./nav"

describe("Nav", () => {
  it("renders grouped sidebar links, account, breadcrumb and top-bar controls", () => {
    const html = renderToString(<Nav />)
    const menuLinks = [...html.matchAll(/<a\b([^>]*)>/g)]
      .map(([_, attributes]) => attributes)
      .filter((attributes) => attributes.includes("text-sm"))

    expect(menuLinks).toHaveLength(14)
    for (const attributes of menuLinks) {
      expect(attributes).toContain("whitespace-nowrap")
      expect(attributes).toContain("shrink-0")
    }
    expect(html).toContain('role="status">모든 시스템 정상</span>')
    expect(html).toContain("admin narwhal")
    expect(html).toContain('aria-label="nav.more"')
    expect(html).toContain('aria-expanded="false"')
    expect(html).toContain('aria-current="page"')
    expect(html).toContain("shell.searchPlaceholder")
    for (const key of ["shell.overview", "shell.delivery", "shell.governance", "shell.getStarted"]) {
      expect(html).toContain(key)
    }
  })

  it.each(["developer", "viewer", "guest"])("hides admin settings in the rendered %s navigation", (role) => {
    const adminHtml = renderToString(<Nav />)
    expect(adminHtml).toContain('href="/settings"')
    session.user.role = role
    const html = renderToString(<Nav />)
    expect(html).toContain('href="/status"')
    expect(html).not.toContain('href="/settings"')
    expect(html).not.toContain("nav.settings")
  })

  it("retains role restrictions and removes empty groups", () => {
    expect(getVisibleNavGroups("guest").map((group) => group.labelKey)).toEqual(["shell.overview"])
    expect(getVisibleNavItems("guest").map((item) => item.href)).toEqual(["/status", "/live"])
    for (const href of ["/settings", "/security", "/alerts", "/compliance", "/my-apps"]) {
      expect(getVisibleNavItems("viewer").map((item) => item.href)).not.toContain(href)
    }
    expect(getVisibleNavItems("developer").some((item) => item.href === "/my-apps")).toBe(true)
    expect(getVisibleNavItems("cluster-admin")).toHaveLength(14)
  })
})

describe("NavVersion", () => {
  it("renders the short build version with an accessible label and full SHA title", () => {
    const html = renderToString(<NavVersion label="Portal version and commit" />)
    expect(html).toContain("v1.0.17 · abcdef1")
    expect(html).toContain('aria-label="Portal version and commit"')
    expect(html).toContain('title="abcdef1234567"')
  })
})
