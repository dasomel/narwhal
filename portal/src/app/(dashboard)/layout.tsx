import { Nav } from "@/components/nav"
import { CommandPalette } from "@/components/command-palette"

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
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
