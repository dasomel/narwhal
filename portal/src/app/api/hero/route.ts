import { NextResponse } from "next/server"
import { auth } from "@/lib/auth"
import { getHeroResponse } from "@/lib/hero"
import { requireSessionAccess } from "@/lib/scope"
import type { HeroResponse } from "@/types/api"

export const dynamic = "force-dynamic"

export async function GET(): Promise<NextResponse<HeroResponse | { error: string; code?: string }>> {
  const session = await auth()
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  const denied = await requireSessionAccess(session)
  if (denied) return denied

  try {
    const hero = await getHeroResponse()
    return NextResponse.json(hero)
  } catch (err) {
    console.error("[api/hero]", err)
    return NextResponse.json({ error: "Internal server error" }, { status: 500 })
  }
}
