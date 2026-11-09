"use client"

import { Suspense, useEffect } from "react"
import { useSearchParams } from "next/navigation"
import { signIn } from "next-auth/react"
import { Button } from "@/components/ui/button"
import { Narwhal } from "@/components/narwhal/narwhal"
import { useT } from "@/lib/i18n-client"

const isMock = process.env.NEXT_PUBLIC_AUTH_MOCK === "true"

const MOCK_ROLES = [
  { label: "Cluster Admin", role: "cluster-admin" },
  { label: "Developer", role: "developer" },
  { label: "Platform Team Developer", role: "developer", team: "platform-team" },
  { label: "Viewer", role: "viewer" },
]

const WAVE_OFFSETS = [0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => 423 + i * 46)

function ContourWaves() {
  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox="0 0 640 900"
      preserveAspectRatio="none"
      fill="none"
      aria-hidden="true"
    >
      {WAVE_OFFSETS.map((y, i) => (
        <path
          key={i}
          d={`M 0,${y} C 160,${y + 14} 280,${y + 18} 400,${y - 8} C 490,${y - 28} 570,${y - 22} 640,${y - 12}`}
          stroke="var(--narwhal-wave)"
          strokeWidth="1.2"
          strokeOpacity="0.28"
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  )
}

function BrandPanel() {
  const t = useT()

  return (
    <aside className="hidden min-[901px]:flex w-[clamp(360px,44vw,640px)] min-h-screen py-14 px-16 flex-col justify-between shrink-0 relative overflow-hidden [background:var(--narwhal-hero-bg)] border-r border-[var(--narwhal-hero-border)]">
      <ContourWaves />

      {/* Top: Wordmark */}
      <div className="relative z-10 flex items-center gap-[10px]">
        <Narwhal state="healthy" size={44} />
        <span className="text-[18px] font-bold tracking-tight text-foreground">
          Narwhal IDP
        </span>
      </div>

      {/* Middle: Mascot + Headline + Tagline */}
      <div className="relative z-10 flex flex-col gap-5 my-auto">
        <Narwhal state="healthy" size={260} />
        <div className="flex flex-col gap-5">
          <h1 className="text-[36px] leading-[48px] font-bold tracking-[-0.015em] text-foreground whitespace-pre-line">
            {t("login.headline")}
          </h1>
          <p className="text-[16px] leading-[24px] text-muted-foreground">
            {t("login.tagline")}
          </p>
        </div>
      </div>

      {/* Bottom: Footnote */}
      <div className="relative z-10">
        <p className="text-[13px] font-medium tracking-[0.02em] text-muted-foreground">
          Kubernetes · GitOps · OIDC SSO
        </p>
      </div>
    </aside>
  )
}

function LoginForm() {
  const t = useT()
  const searchParams = useSearchParams()
  const callbackUrl = searchParams.get("callbackUrl") || "/"

  useEffect(() => {
    if (!isMock) {
      signIn("keycloak", { callbackUrl })
    }
  }, [callbackUrl])

  return (
    <div className="w-full max-w-[400px] text-left">
      {/* Compact wordmark for <=900px viewport */}
      <div className="flex items-center gap-[10px] mb-8 min-[901px]:hidden">
        <Narwhal state="healthy" size={44} />
        <span className="text-[18px] font-bold tracking-tight text-foreground">
          Narwhal IDP
        </span>
      </div>

      <h2 className="text-[28px] leading-[36px] font-bold tracking-[-0.01em] text-foreground">
        {isMock ? t("login.welcome") : t("login.redirecting")}
      </h2>

      {isMock ? (
        <>
          <p className="text-[14px] leading-[20px] text-muted-foreground mt-2">
            {t("login.mockMode")}
          </p>
          <div className="flex flex-col gap-2.5 mt-6">
            {MOCK_ROLES.map(({ label, role, team }) => (
              <Button
                key={`${role}-${team ?? "default"}`}
                variant="outline"
                className="w-full justify-center h-11 text-[15px] font-medium rounded-lg"
                onClick={() => signIn("mock", { role, team, callbackUrl })}
              >
                {t("login.loginAs", { label })}
              </Button>
            ))}
          </div>
        </>
      ) : (
        <div className="flex items-center justify-center py-12">
          <div className="h-8 w-8 animate-spin motion-reduce:animate-none rounded-full border-2 border-muted border-t-foreground" />
        </div>
      )}
    </div>
  )
}

function LoginFallback() {
  return (
    <div className="w-full max-w-[400px] text-left">
      <div className="flex items-center gap-[10px] mb-8 min-[901px]:hidden">
        <Narwhal state="healthy" size={44} />
        <span className="text-[18px] font-bold tracking-tight text-foreground">
          Narwhal IDP
        </span>
      </div>
      <div className="flex items-center justify-center py-12">
        <div className="h-8 w-8 animate-spin motion-reduce:animate-none rounded-full border-2 border-muted border-t-foreground" />
      </div>
    </div>
  )
}

export default function LoginPage() {
  return (
    <div className="min-h-screen bg-background flex flex-col min-[901px]:flex-row font-sans">
      <BrandPanel />
      <main className="flex-1 min-h-screen flex flex-col items-center justify-center p-6 md:p-8 bg-background">
        <Suspense fallback={<LoginFallback />}>
          <LoginForm />
        </Suspense>
      </main>
    </div>
  )
}
