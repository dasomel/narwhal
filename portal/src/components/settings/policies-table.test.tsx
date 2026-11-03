import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@tanstack/react-query", () => ({ useQuery: vi.fn() }))
// Base UI dialog parts need an open Dialog root; render them as plain elements.
vi.mock("@/components/ui/sheet", () => ({
  Sheet: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  SheetTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  SheetDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}))

import { useQuery } from "@tanstack/react-query"
import { LocaleProvider } from "@/lib/i18n-client"
import { PoliciesTable, PolicyDetail } from "./policies-table"

function policy(name: string, action: string, extra: Record<string, unknown> = {}) {
  return {
    name, namespace: null, scope: "cluster", background: true, validationFailureAction: action,
    ready: true, rulesCount: 1, rules: [{ name: "r1", type: "validate" }], ...extra,
  }
}

function renderTable(policies: unknown[], locale: "ko" | "en" = "ko") {
  vi.mocked(useQuery).mockReturnValue({ data: policies, isLoading: false, isError: false, refetch: vi.fn() } as never)
  return renderToStaticMarkup(<LocaleProvider locale={locale}><PoliciesTable /></LocaleProvider>)
}

const KO_ENFORCE = "정책을 위반한 리소스는 어드미션 단계에서 거부됩니다 (생성·수정이 차단됨)."
const KO_AUDIT = "위반 리소스도 허용되며, 위반 내용은 PolicyReport에 기록되어 컴플라이언스에 표시됩니다."
const EN_ENFORCE = "A resource that violates the policy is rejected at admission (create/update is blocked)."
const EN_AUDIT = "The resource is allowed, and the violation is recorded in a PolicyReport and shown in compliance."

describe("PoliciesTable action explanations", () => {
  it("explains Enforce and Audit per badge in Korean, readable by screen readers (not hover only)", () => {
    const html = renderTable([policy("a", "Enforce"), policy("b", "Audit")])
    expect(html).toContain(`title="${KO_ENFORCE}"`)
    expect(html).toContain(`<span id="policy-action-cluster-a" class="sr-only">${KO_ENFORCE}</span>`)
    expect(html).toContain('aria-describedby="policy-action-cluster-a"')
    expect(html).toContain(`<span id="policy-action-cluster-b" class="sr-only">${KO_AUDIT}</span>`)
  })

  it("explains both actions and background scan in English", () => {
    const html = renderTable([policy("a", "Enforce"), policy("b", "Audit")], "en")
    expect(html).toContain(EN_ENFORCE)
    expect(html).toContain(EN_AUDIT)
    expect(html).toContain("Background scan: when on, existing resources are also checked periodically")
  })

  it("shows an always-visible legend with a label for assistive tech", () => {
    const html = renderTable([policy("a", "Audit")])
    expect(html).toContain('<dl aria-label="액션 안내"')
    expect(html).toContain("백그라운드 스캔:")
  })

  it("falls back to a generic text for unknown values and handles Mixed", () => {
    const html = renderTable([policy("a", "Quarantine"), policy("b", "Mixed")], "en")
    expect(html).toContain('id="policy-action-cluster-a" class="sr-only">Unrecognized action value.')
    expect(html).toContain('id="policy-action-cluster-b" class="sr-only">Rules use different actions.')
  })

  it("treats lowercase enforce as Enforce", () => {
    expect(renderTable([policy("a", "enforce")], "en")).toContain(`id="policy-action-cluster-a" class="sr-only">${EN_ENFORCE}`)
  })

  it("opens the detail panel from a native button (keyboard reachable) with a labelled name", () => {
    const html = renderTable([policy("a", "Audit")], "en")
    expect(html).toMatch(/<button type="button" aria-label="View details of policy a"[^>]*>a<\/button>/)
  })

  it("does not crash on an error payload instead of a list", () => {
    expect(() => renderTable({ error: "Forbidden" } as never)).not.toThrow()
  })
})

describe("PolicyDetail", () => {
  const full = policy("require-labels", "Audit", {
    title: "Require Labels", description: "Labels are needed.", category: "Best Practices", severity: "medium",
    background: false,
    rules: [{
      name: "check", type: "validate", message: "label required", failureAction: "Enforce",
      matchKinds: ["Pod", "Deployment"], excludeKinds: ["Job"], excludeNamespaces: ["kube-system"],
    }],
  })

  function detail(p: unknown, locale: "ko" | "en" = "en") {
    return renderToStaticMarkup(<LocaleProvider locale={locale}><PolicyDetail policy={p as never} /></LocaleProvider>)
  }

  it("shows title, description, category, severity, action, background and rule details", () => {
    const html = detail(full)
    expect(html).toContain("Require Labels")
    expect(html).toContain("Labels are needed.")
    expect(html).toContain("Best Practices")
    expect(html).toContain("medium")
    expect(html).toContain(EN_AUDIT)
    expect(html).toContain("Off")
    expect(html).toContain("label required")
    expect(html).toContain("Checks that a resource meets the condition.")
    expect(html).toContain(`Enforce — ${EN_ENFORCE}`) // per-rule action
    expect(html).toContain("Pod, Deployment")
    expect(html).toContain("Job")
    expect(html).toContain("kube-system")
  })

  it("shows the fallback text when there is no description", () => {
    expect(detail(policy("x", "Audit"))).toContain("This policy has no description")
    expect(detail(policy("x", "Audit"), "ko")).toContain("이 정책에는 설명")
  })

  it("renders old cached rules without the new fields", () => {
    const html = detail(policy("x", "Audit"))
    expect(html).toContain("r1")
    expect(html).toContain("—")
  })
})
