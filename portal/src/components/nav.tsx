"use client"

import { useEffect, useState } from "react"
import { LogOut, Menu, Search, X } from "lucide-react"
import { useSession } from "next-auth/react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { DependencyHealthIndicator } from "@/components/dependency-health-indicator"
import { Narwhal } from "@/components/narwhal/narwhal"
import { getVisibleNavGroups, isNavActive } from "@/components/shell/navigation"
import { ThemeToggle } from "@/components/theme-toggle"
import { Button } from "@/components/ui/button"
import { Sheet, SheetClose, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { getAppVersion } from "@/lib/app-version"
import { LocaleSwitcher, useT } from "@/lib/i18n-client"

export { getVisibleNavGroups, getVisibleNavItems } from "@/components/shell/navigation"

export function NavVersion({ label }: { label: string }) {
  const appVersion = getAppVersion()
  return (
    <span aria-label={label} className="font-mono text-[10px] leading-3 text-muted-foreground" title={appVersion.commit}>
      {appVersion.display}
    </span>
  )
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { data: session } = useSession()
  const pathname = usePathname()
  const role = session?.user?.role ?? "guest"
  const t = useT()
  const name = session?.user?.name ?? t("shell.guest")
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => Array.from(part)[0]).join("").toUpperCase()

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Link href="/" onClick={onNavigate} className="flex h-[76px] shrink-0 items-center gap-2.5 px-5">
        <span aria-hidden="true"><Narwhal state="healthy" size={36} /></span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="text-base font-bold">{t("shell.brand")}</span>
          <NavVersion label={t("nav.versionLabel")} />
        </span>
      </Link>
      <nav aria-label={t("shell.navigation")} className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 pb-4">
        {getVisibleNavGroups(role).map((group) => (
          <div key={group.labelKey}>
            <div className="mb-1 text-[11px] leading-3 font-medium tracking-[.04em] text-muted-foreground">{t(group.labelKey)}</div>
            <div className="space-y-0.5">
              {group.items.map(({ href, labelKey, icon: Icon }) => {
                const active = isNavActive(href, pathname)
                return (
                  <Link key={href} data-nav-link href={href} onClick={onNavigate} aria-current={active ? "page" : undefined}
                    className={`relative flex h-[34px] shrink-0 items-center gap-3 whitespace-nowrap rounded-lg px-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-ring ${active
                      ? "bg-narwhal-accent-soft text-narwhal-detail before:absolute before:inset-y-2 before:left-0 before:w-[3px] before:rounded-full before:bg-narwhal-accent dark:text-narwhal-accent"
                      : "text-foreground hover:bg-narwhal-accent-soft"}`}>
                    <Icon aria-hidden="true" className={`size-4 shrink-0 ${active ? "" : "text-muted-foreground"}`} />
                    {t(labelKey)}
                  </Link>
                )
              })}
            </div>
          </div>
        ))}
      </nav>
      <div className="m-4 mt-auto flex shrink-0 items-center gap-2.5 rounded-lg border bg-card p-2.5">
        <span aria-hidden="true" className="flex size-8 shrink-0 items-center justify-center rounded-full bg-narwhal-accent-soft text-xs font-semibold text-narwhal-detail dark:text-narwhal-accent">{initials}</span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" title={name}>{name}</div>
          <div className="truncate font-mono text-[10px] text-muted-foreground">{role}</div>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label={t("nav.logout")} title={t("nav.logout")}
          onClick={async () => {
            // Preserve CSRF-verified federated logout and the local fallback.
            try {
              const res = await fetch("/api/auth/federated-logout", {
                method: "POST", headers: { "Content-Type": "application/json" },
              })
              const data = await res.json().catch(() => null)
              window.location.href = data?.url || "/login"
            } catch {
              window.location.href = "/login"
            }
          }}>
          <LogOut aria-hidden="true" className="size-4" />
        </Button>
      </div>
    </div>
  )
}

export function Nav() {
  const { data: session } = useSession()
  const pathname = usePathname()
  const t = useT()
  const [open, setOpen] = useState(false)
  const groups = getVisibleNavGroups(session?.user?.role ?? "guest")
  const group = groups.find((group) => group.items.some((item) => isNavActive(item.href, pathname)))
  const item = group?.items.find((item) => isNavActive(item.href, pathname))

  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)")
    const closeOnDesktop = () => { if (media.matches) setOpen(false) }
    media.addEventListener("change", closeOnDesktop)
    return () => media.removeEventListener("change", closeOnDesktop)
  }, [])

  return (
    <>
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-60 border-r border-[var(--narwhal-hero-border)] bg-[image:var(--narwhal-hero-bg)] lg:block">
        <Sidebar />
      </aside>
      <header className="flex h-14 min-w-0 items-center justify-between gap-2 border-b bg-background px-4 lg:ml-60 lg:px-8">
        <div className="flex min-w-0 items-center gap-2">
          {/* D2: Base UI Sheet supplies modal focus/Esc handling, with no custom
              trap code. Cost: a second sidebar while open; replace via Sheet API. */}
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger data-nav-more render={<Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label={t("nav.more")} title={t("shell.openMenu")} aria-expanded={open} />}>
              <Menu aria-hidden="true" className="size-4" />
            </SheetTrigger>
            <SheetContent side="left" showCloseButton={false} aria-describedby={undefined}
              className="data-[side=left]:w-60 gap-0 border-[var(--narwhal-hero-border)] bg-[image:var(--narwhal-hero-bg)]">
              <SheetTitle className="sr-only">{t("shell.navigation")}</SheetTitle>
              <SheetClose render={<Button variant="ghost" size="icon-sm" className="absolute right-1 top-1" aria-label={t("common.close")} />}>
                <X aria-hidden="true" className="size-4" />
              </SheetClose>
              <Sidebar onNavigate={() => setOpen(false)} />
            </SheetContent>
          </Sheet>
          <div aria-label={t("shell.breadcrumb")} className="flex min-w-0 items-center gap-2 text-xs sm:text-sm">
            {group && <><span className="hidden text-muted-foreground sm:inline">{t(group.labelKey)}</span><span aria-hidden="true" className="hidden text-muted-foreground sm:inline">/</span></>}
            <span className="truncate font-medium">{item ? t(item.labelKey) : t("shell.brand")}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <button type="button" aria-label={t("search.hint")}
            onClick={() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
            className="flex h-8 items-center gap-2 rounded-lg bg-muted px-2.5 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring lg:w-[280px]">
            <Search aria-hidden="true" className="size-4 shrink-0" />
            <span className="hidden whitespace-nowrap md:inline">{t("shell.searchPlaceholder")}</span>
            <kbd aria-hidden="true" className="ml-auto hidden rounded border bg-background px-1 font-mono text-[10px] md:inline">⌘K</kbd>
          </button>
          <div className="hidden sm:block [&_[role=status]]:rounded-full [&_[role=status]]:border [&_[role=status]]:px-2.5 [&_[role=status]]:py-1.5 [&_svg]:size-2 [&_svg]:rounded-full [&_svg]:bg-current [&_svg_*]:invisible">
            <DependencyHealthIndicator />
          </div>
          <LocaleSwitcher />
          <div className="rounded-lg border"><ThemeToggle /></div>
        </div>
      </header>
    </>
  )
}
