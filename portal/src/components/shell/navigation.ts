import {
  Activity, Bell, Box, Cable, ClipboardCheck, House, KeyRound, LayoutGrid,
  Radio, Scale, Settings, Shield, Wallet, Wrench, type LucideIcon,
} from "lucide-react"
import type { UserRole } from "@/lib/auth"
import type { TranslationKey } from "@/lib/i18n"

interface MenuItem {
  href: string
  labelKey: TranslationKey
  roles: UserRole[]
  icon: LucideIcon
}

interface MenuGroup {
  labelKey: TranslationKey
  items: MenuItem[]
}

const members: UserRole[] = ["cluster-admin", "developer", "viewer"]
const everyone: UserRole[] = [...members, "guest"]

// D1: Keep existing per-route roles in grouped metadata; no auth changes or
// extra requests. Escape hatch: regroup these entries without changing roles.
const groups: MenuGroup[] = [
  { labelKey: "shell.overview", items: [
    { href: "/", labelKey: "nav.home", roles: members, icon: House },
    { href: "/status", labelKey: "nav.status", roles: everyone, icon: Activity },
    { href: "/alerts", labelKey: "nav.alerts", roles: ["cluster-admin"], icon: Bell },
    { href: "/live", labelKey: "shell.live", roles: everyone, icon: Radio },
  ] },
  { labelKey: "shell.delivery", items: [
    { href: "/my-apps", labelKey: "nav.myApps", roles: ["developer", "cluster-admin"], icon: Box },
    { href: "/catalog", labelKey: "nav.catalog", roles: members, icon: LayoutGrid },
    { href: "/architecture", labelKey: "nav.architecture", roles: members, icon: Cable },
    { href: "/tools", labelKey: "nav.tools", roles: members, icon: Wrench },
  ] },
  { labelKey: "shell.governance", items: [
    { href: "/governance", labelKey: "nav.governance", roles: members, icon: Scale },
    { href: "/cost", labelKey: "nav.cost", roles: members, icon: Wallet },
    { href: "/security", labelKey: "nav.security", roles: ["cluster-admin"], icon: Shield },
    { href: "/compliance", labelKey: "nav.compliance", roles: ["cluster-admin"], icon: ClipboardCheck },
  ] },
  { labelKey: "shell.getStarted", items: [
    { href: "/onboarding", labelKey: "nav.onboarding", roles: members, icon: KeyRound },
    { href: "/settings", labelKey: "nav.settings", roles: ["cluster-admin"], icon: Settings },
  ] },
]

export function getVisibleNavGroups(role: UserRole): MenuGroup[] {
  return groups.map((group) => ({ ...group, items: group.items.filter((item) => item.roles.includes(role)) }))
    .filter((group) => group.items.length > 0)
}

export function getVisibleNavItems(role: UserRole) {
  return getVisibleNavGroups(role).flatMap((group) => group.items)
}

export function isNavActive(href: string, pathname: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`)
}
