import { hasRole } from "@/lib/auth"
import { requireDashboardAccess } from "@/lib/dashboard-access"
import { getLocale } from "@/lib/i18n-server"
import { t } from "@/lib/i18n"
import { StatusView } from "@/components/status/status-view"

export default async function StatusPage() {
  const session = await requireDashboardAccess()

  const locale = await getLocale()
  const isOperator = hasRole(session, "cluster-admin", "developer")

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t(locale, "status.pageTitle")}</h1>
        <p className="text-muted-foreground text-sm mt-1">{t(locale, "status.pageDescription")}</p>
      </div>
      <StatusView isOperator={isOperator} />
    </div>
  )
}
