import type { Session } from "next-auth"

// D1 (#610): Kubernetes platform-admin binds oidc:cluster-admin (see
// gitops/resources/rbac-policies.yaml); its Portal role is cluster-admin.
// Reuse that identity boundary at no extra role-mapping cost; revisit only
// if the owning identity contract introduces a distinct platform-admin group.
export function canReadAlerts(session: Pick<Session, "user"> | null | undefined): boolean {
  return session?.user?.role === "cluster-admin"
}
