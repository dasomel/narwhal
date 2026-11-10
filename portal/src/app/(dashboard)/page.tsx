import { DashboardOverview } from "@/components/dashboard/dashboard-overview"
import { t } from "@/lib/i18n"
import { getLocale } from "@/lib/i18n-server"

export default async function HomePage() {
  const locale = await getLocale()
  return (
    <DashboardOverview title={t(locale, "dashboard.title")} description={t(locale, "dashboard.description")} />
  )
}
