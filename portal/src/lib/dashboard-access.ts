import "server-only"
import { redirect } from "next/navigation"
import type { Session } from "next-auth"
import { auth } from "@/lib/auth"
import { hasSessionAccess } from "@/lib/scope"

// D-PORTAL-UNMAPPED-DENY: Next.js renders layouts and pages in parallel and skips
// re-running shared layouts on soft navigation. Every server-rendered dashboard
// page that reads session/tenant/cluster data must call this before any data fetch.
export async function requireDashboardAccess(): Promise<Session> {
  const session = await auth()
  if (!session) redirect("/login")
  if (!(await hasSessionAccess(session))) redirect("/access-denied")
  return session
}
