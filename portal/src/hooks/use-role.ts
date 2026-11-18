"use client"

import { useContext } from "react"
import { SessionContext, useSession } from "next-auth/react"
import type { Session } from "next-auth"

export type Role = "cluster-admin" | "developer" | "viewer" | "guest"

type PrivilegedAction = "silence" | "sync" | "rollback" | "cordon" | "drain"

const ACTION_ROLES: Record<PrivilegedAction, Role[]> = {
  silence: ["cluster-admin", "developer"],
  sync: ["cluster-admin", "developer"],
  rollback: ["cluster-admin", "developer"],
  cordon: ["cluster-admin"],
  drain: ["cluster-admin"],
}

export function useSafeSession(): Session | null {
  const context = useContext(SessionContext)
  const isMock = typeof (useSession as unknown as { mock?: unknown }).mock !== "undefined"
  if (context !== undefined || isMock) {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return useSession()?.data ?? null
  }
  return null
}

export function useRole(): { role: Role; can: (action: PrivilegedAction) => boolean; scopeFingerprint: string } {
  const session = useSafeSession()
  const role = ((session?.user as { role?: string })?.role ?? "guest") as Role
  const scopeFingerprint = session?.scopeFingerprint ?? role

  function can(action: PrivilegedAction): boolean {
    return ACTION_ROLES[action].includes(role)
  }

  return { role, can, scopeFingerprint }
}

export function useScopeFingerprint(): string {
  const session = useSafeSession()
  return session?.scopeFingerprint ?? (session?.user as { role?: string })?.role ?? "guest"
}
