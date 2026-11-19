"use client"
import { SessionProvider, signIn, useSession } from "next-auth/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ReactQueryDevtools } from "@tanstack/react-query-devtools"
import { useEffect, useRef, useState } from "react"
import { LocaleProvider } from "@/lib/i18n-client"
import { TooltipProvider } from "@/components/ui/tooltip"
import type { Locale } from "@/lib/i18n"
import { bindScopeQueryClient, shouldRetryQuery } from "@/lib/scope-guard"

const REFRESH_TOKEN_ERROR = "RefreshTokenError"

export function SessionErrorHandler() {
  const { data: session } = useSession()
  const triggeredRef = useRef(false)

  useEffect(() => {
    if (session?.error !== REFRESH_TOKEN_ERROR || triggeredRef.current) return
    triggeredRef.current = true
    const providerId = process.env.NEXT_PUBLIC_AUTH_MOCK === "true" ? "mock" : "keycloak"
    // Release the guard when the sign-in request itself fails (e.g. offline), so the next
    // session update retries instead of leaving the tab stuck.
    signIn(providerId).catch(() => {
      triggeredRef.current = false
    })
  }, [session?.error])

  return null
}

export function Providers({ locale, children }: { locale: Locale; children: React.ReactNode }) {
  const [queryClient] = useState(() => {
    const client = new QueryClient({
      defaultOptions: {
        queries: {
          staleTime: 30_000,
          refetchInterval: 30_000,
          retry: shouldRetryQuery,
        },
      },
    })
    if (typeof window !== "undefined") {
      bindScopeQueryClient(client)
    }
    return client
  })

  useEffect(() => bindScopeQueryClient(queryClient), [queryClient])
  return (
    <SessionProvider>
      <SessionErrorHandler />
      <QueryClientProvider client={queryClient}>
        <LocaleProvider locale={locale}>
          <TooltipProvider>
            {children}
            {process.env.NODE_ENV === "development" && (
              <ReactQueryDevtools initialIsOpen={false} />
            )}
          </TooltipProvider>
        </LocaleProvider>
      </QueryClientProvider>
    </SessionProvider>
  )
}
