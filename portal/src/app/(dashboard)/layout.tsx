import { Nav } from "@/components/nav"
import { CommandPalette } from "@/components/command-palette"
import { requireDashboardAccess } from "@/lib/dashboard-access"

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  // D-PORTAL-UNMAPPED-DENY: gate every dashboard route in one place before the
  // app shell mounts; unmapped non-admins redirect to /access-denied instead of
  // rendering navigation or triggering tenant data fetches.
  await requireDashboardAccess()

  return (
    <div className="min-h-screen bg-background">
      <Nav />
      <CommandPalette />
      <main className="px-4 py-7 sm:px-8 lg:ml-60">
        <div className="mx-auto max-w-7xl">{children}</div>
      </main>
    </div>
  )
}
