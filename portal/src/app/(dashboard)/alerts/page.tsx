import { redirect } from "next/navigation"
import { auth } from "@/lib/auth"
import { canReadAlerts } from "@/lib/alert-access"
import { getLocale } from "@/lib/i18n-server"
import { t } from "@/lib/i18n"
import { FiringAlerts } from "@/components/alerts/firing-alerts"

export default async function AlertsPage() {
  const session = await auth()
  if (!session) redirect("/login")
  if (!canReadAlerts(session)) redirect("/")
  const locale = await getLocale()
  return <div className="space-y-6">
    <div><h1 className="text-2xl font-bold">{t(locale, "alerts.pageTitle")}</h1><p className="text-sm text-muted-foreground mt-1">{t(locale, "alerts.pageDescription")}</p></div>
    <FiringAlerts canTest={session.user.role === "cluster-admin"} />
  </div>
}
