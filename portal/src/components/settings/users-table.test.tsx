import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

import { useQuery } from "@tanstack/react-query"
import { LocaleProvider } from "@/lib/i18n-client"
import type { KeycloakUser } from "@/lib/keycloak-client"
import { UsersTable } from "./users-table"

const user: KeycloakUser = {
  pk: "u1", username: "one", email: "one@example.com", name: "One",
  is_active: true, last_login: null, last_login_source: null,
}

describe("UsersTable login source", () => {
  it.each([
    ["ko", "최근 로그인", "세션", "기록 없음"],
    ["en", "Last Login", "Session", "No record"],
  ] as const)("renders truthful login labels in %s", (locale, heading, hint, none) => {
    for (const source of ["event", "session", null] as const) {
      const lastLogin = source ? "2026-11-11T00:00:00.000Z" : null
      vi.mocked(useQuery).mockReturnValue({
        data: [{ ...user, last_login: lastLogin, last_login_source: source }],
        isLoading: false, isError: false, refetch: vi.fn(),
      } as never)
      const html = renderToStaticMarkup(<LocaleProvider locale={locale}><UsersTable /></LocaleProvider>)
      expect(html).toContain(heading)
      expect(html.includes(`(${hint})`)).toBe(source === "session")
      expect(html.includes(none)).toBe(source === null)
      if (lastLogin) {
        expect(html).toContain(new Date(lastLogin).toLocaleString(locale === "ko" ? "ko-KR" : "en-US"))
      }
    }
  })
})
