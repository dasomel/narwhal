import { redirect } from "next/navigation"
import { AccessDeniedView } from "@/components/auth/access-denied-view"
import { auth } from "@/lib/auth"
import { hasSessionAccess } from "@/lib/scope"

export const dynamic = "force-dynamic"

export default async function AccessDeniedPage() {
  const session = await auth()
  if (!session) redirect("/login")
  if (await hasSessionAccess(session)) redirect("/")

  const account = session.user?.email ?? session.user?.name ?? null
  const role = session.user?.role ?? "guest"
  return <AccessDeniedView account={account} role={role} />
}
