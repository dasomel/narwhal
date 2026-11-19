import { redirect } from "next/navigation"
import { AccessDeniedView } from "@/components/auth/access-denied-view"
import { auth } from "@/lib/auth"
import { hasSessionAccess } from "@/lib/scope"

export const dynamic = "force-dynamic"

export default async function AccessDeniedPage({
  searchParams,
}: {
  searchParams: Promise<{ reason?: string | string[] }>
}) {
  const session = await auth()
  if (!session) redirect("/login")
  const { reason } = await searchParams
  // D-688-nav: the scope guard lands here when it may not reload (cooldown or no
  // sessionStorage). Do not bounce a user who still has access back to "/": that
  // would turn a replica fingerprint mismatch into a navigation loop. They reload by hand.
  const scopeChanged = reason === "scope-changed"
  if (!scopeChanged && (await hasSessionAccess(session))) redirect("/")

  const account = session.user?.email ?? session.user?.name ?? null
  const role = session.user?.role ?? "guest"
  return <AccessDeniedView account={account} role={role} variant={scopeChanged ? "scope-changed" : "denied"} />
}
