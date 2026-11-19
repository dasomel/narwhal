"use client"

import Link from "next/link"
import { useState } from "react"
import { LogOut } from "lucide-react"
import { Narwhal } from "@/components/narwhal/narwhal"
import { ThemeToggle } from "@/components/theme-toggle"
import { Button, buttonVariants } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { DataState } from "@/components/ui/data-state"
import { LocaleSwitcher, useT } from "@/lib/i18n-client"
import { cn } from "@/lib/utils"

interface AccessDeniedViewProps {
  account: string | null
  role: string
  variant?: "denied" | "scope-changed"
}

export function AccessDeniedView({ account, role, variant = "denied" }: AccessDeniedViewProps) {
  const scopeChanged = variant === "scope-changed"
  const t = useT()
  const [signingOut, setSigningOut] = useState(false)

  async function handleSignOut() {
    setSigningOut(true)
    try {
      const payload = process.env.NEXT_PUBLIC_AUTH_MOCK === "true"
        ? { redirectUri: `${window.location.origin}/login` }
        : undefined
      const res = await fetch("/api/auth/federated-logout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        ...(payload ? { body: JSON.stringify(payload) } : {}),
      })
      const data = await res.json().catch(() => null)
      window.location.href = data?.url || "/login"
    } catch {
      window.location.href = "/login"
    }
  }

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="flex h-14 items-center justify-between border-b px-4 sm:px-8">
        <div className="flex items-center gap-2.5">
          <span aria-hidden="true"><Narwhal state="healthy" size={32} /></span>
          <span className="text-base font-bold text-foreground">{t("shell.brand")}</span>
        </div>
        <div className="flex items-center gap-2">
          <LocaleSwitcher />
          <div className="rounded-lg border"><ThemeToggle /></div>
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center p-6">
        <Card className="w-full max-w-lg p-6 sm:p-8 space-y-5">
          <div className="flex flex-col items-center gap-3 text-center">
            <Narwhal state="warning" size={72} />
            <h1 className="text-xl font-bold text-foreground">{scopeChanged ? t("accessDenied.scopeChangedTitle") : t("accessDenied.title")}</h1>
          </div>

          <DataState state="unauthorized" reason={scopeChanged ? t("accessDenied.scopeChangedDescription") : t("accessDenied.description")} />

          <p className="text-center text-sm text-muted-foreground">
            {scopeChanged ? t("accessDenied.scopeChangedHint") : t("accessDenied.hint")}
          </p>

          {scopeChanged && (
            <Link href="/" className={cn(buttonVariants(), "w-full h-10")}>
              {t("accessDenied.reload")}
            </Link>
          )}

          {account && (
            <div className="flex items-center justify-between rounded-lg border bg-muted/40 px-3.5 py-2.5 text-xs">
              <span className="text-muted-foreground">{t("accessDenied.accountLabel")}</span>
              <span className="font-mono text-foreground">{account} ({role})</span>
            </div>
          )}

          <Button
            type="button"
            variant="outline"
            className="w-full h-10"
            disabled={signingOut}
            onClick={() => { void handleSignOut() }}
          >
            <LogOut aria-hidden="true" className="size-4" />
            {t("accessDenied.signOut")}
          </Button>
        </Card>
      </main>
    </div>
  )
}
