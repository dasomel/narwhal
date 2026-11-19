import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@/lib/i18n-client", () => ({
  useT: () => (key: string) => key,
  LocaleSwitcher: () => null,
}))
vi.mock("@/components/theme-toggle", () => ({ ThemeToggle: () => null }))

import { AccessDeniedView } from "./access-denied-view"

describe("AccessDeniedView", () => {
  it("shows the scope-changed copy and a manual reload link for the scope-changed variant", () => {
    const html = renderToStaticMarkup(<AccessDeniedView account="a@b" role="developer" variant="scope-changed" />)
    expect(html).toContain("accessDenied.scopeChangedTitle")
    expect(html).toContain('href="/"')
    expect(html).toContain("accessDenied.reload")
    expect(html).not.toContain("accessDenied.title<")
  })

  it("keeps the original copy and no reload link by default", () => {
    const html = renderToStaticMarkup(<AccessDeniedView account={null} role="guest" />)
    expect(html).toContain("accessDenied.title")
    expect(html).not.toContain("accessDenied.reload")
  })
})
