import { describe, expect, it, vi } from "vitest"
import { renderToStaticMarkup } from "react-dom/server"

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))
vi.mock("@/lib/i18n-client", () => ({ useT: () => (key: string) => key }))

import { useQuery } from "@tanstack/react-query"
import { GroupsTable } from "./groups-table"
import type { PlatformTool } from "@/lib/tools"

const tools: PlatformTool[] = [
  { id: "argocd", name: "ArgoCD", description: "", url: "", category: "gitops", icon: "", roles: ["cluster-admin", "developer"] },
  { id: "grafana", name: "Grafana", description: "", url: "", category: "monitoring", icon: "", roles: ["cluster-admin", "developer", "viewer"] },
  { id: "openbao", name: "OpenBao", description: "", url: "", category: "security", icon: "", roles: ["cluster-admin"] },
]

function group(name: string, attributes: Record<string, unknown> = {}) {
  return { pk: `pk-${name}`, name, num_pk: 0, is_superuser: false, parent: null, parent_name: null, users: [], attributes, roles_obj: [], members: [] }
}

function render(groups: unknown[]) {
  vi.mocked(useQuery).mockImplementation((opts: { queryKey: readonly unknown[] }) => ({
    data: opts.queryKey[0] === "settings-groups" ? groups : [],
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  }) as never)
  const html = renderToStaticMarkup(<GroupsTable tools={tools} />)
  const access: Record<string, string> = {}
  for (const m of html.matchAll(/data-access="(\w+)"[^>]*title="([^"/]+) \/ ([^"—]+?)(?: —[^"]*)?"/g)) {
    access[`${m[2]}/${m[3]}`] = m[1]
  }
  return access
}

describe("GroupsTable tool access", () => {
  // Live Kakao shape (2026-10-26): four groups and no allowed_tools attribute anywhere,
  // so the old table rendered every cell as a faint "off" icon and looked empty although
  // /api/tools grants each role its tools.ts defaults.
  it("shows each role's default tools as granted when no attribute is set", () => {
    const access = render(["cluster-admin", "developer", "viewer", "guest"].map((n) => group(n)))
    expect(access["cluster-admin/ArgoCD"]).toBe("role")
    expect(access["cluster-admin/OpenBao"]).toBe("role")
    expect(access["developer/Grafana"]).toBe("role")
    expect(access["developer/OpenBao"]).toBe("none")
    expect(access["viewer/Grafana"]).toBe("role")
    expect(access["viewer/ArgoCD"]).toBe("none")
    expect(access["guest/Grafana"]).toBe("none")
  })

  it("keeps attribute grants editable on top of the role defaults", () => {
    const access = render([group("developer", { allowed_tools: ["openbao"] })])
    expect(access["developer/OpenBao"]).toBe("granted")
    expect(access["developer/ArgoCD"]).toBe("role")
  })
})
