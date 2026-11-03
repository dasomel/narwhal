import { NextResponse } from "next/server"
import { requireRole, getActorId } from "@/lib/auth"
import { claimIdempotencyKey, getIdempotencyStore } from "@/lib/idempotency"
import { beginOperation, completeOperation } from "@/lib/operation-context"
import { OSS_COMPONENTS } from "@/lib/oss-updates/catalog"
import { buildIssueBody, issueTitle, newIssueUrl } from "@/lib/oss-updates/issue"
import { declaredRow, getIssueRepo } from "@/lib/oss-updates/status"
import { getUpstreamReleases } from "@/lib/oss-updates/upstream"
import { COOLING_DAYS, compareSemver, isCooling, parseSemver, sameVersion } from "@/lib/oss-updates/version"
import type { OssUpgradeRequestResponse } from "@/types/oss-updates"

export const dynamic = "force-dynamic"

// D-OSS-REQ-TTL: one request per component+target for 14 days. Cost: a request whose issue was closed
// without an upgrade cannot be re-issued from the portal for 14 days (the duplicate answer still returns
// the issue text). Escape hatch: open the issue by hand, or wait out the TTL.
const REQUEST_TTL_SECONDS = 14 * 24 * 60 * 60
const TARGET_RE = /^v?\d{1,4}\.\d{1,4}(?:\.\d{1,4})?$/

// Without a tracker token the portal cannot see GitHub, so it can only dedupe its OWN requests. It
// generates the issue text; a human files it. No credential is read here by design.
export async function POST(req: Request) {
  const gate = await requireRole("cluster-admin")
  if ("error" in gate) {
    return NextResponse.json(
      { error: gate.error === "unauthorized" ? "Unauthorized" : "Forbidden" },
      { status: gate.error === "unauthorized" ? 401 : 403 },
    )
  }
  const { session } = gate

  const body = (await req.json().catch(() => null)) as { component?: unknown; target?: unknown } | null
  const componentName = typeof body?.component === "string" ? body.component : ""
  const targetRaw = typeof body?.target === "string" ? body.target.trim() : ""

  const catalog = OSS_COMPONENTS.find((c) => c.name === componentName)
  const declared = catalog ? declaredRow(componentName) : undefined
  if (!catalog || !declared) {
    return NextResponse.json({ error: "ValidationError", message: "Unknown component", field: "component" }, { status: 400 })
  }
  if (!TARGET_RE.test(targetRaw)) {
    return NextResponse.json({ error: "ValidationError", message: "target must look like 1.2.3", field: "target" }, { status: 400 })
  }
  const target = targetRaw.replace(/^v/, "")
  const t = parseSemver(target)!
  const d = parseSemver(declared.version)!
  if (compareSemver(t, d) <= 0 || sameVersion(target, declared.version)) {
    return NextResponse.json(
      { error: "ValidationError", message: "target must be newer than the declared version", field: "target" },
      { status: 400 },
    )
  }

  // Cooling gate, only when upstream knows the release. Unknown (air-gap / lookup off) is not a
  // refusal: the checklist makes the requester verify the age.
  const releases = await getUpstreamReleases(catalog.repo)
  const release = releases?.find((r) => sameVersion(r.tag, target))
  if (release && isCooling(release.publishedAt, Date.now())) {
    return NextResponse.json(
      { error: "Cooling", message: `${componentName} ${target} is younger than ${COOLING_DAYS} days`, field: "target" },
      { status: 409 },
    )
  }

  const actorId = getActorId(session)
  const requestedAt = new Date().toISOString()
  const prior = await claimIdempotencyKey(
    getIdempotencyStore(),
    `oss-update-request:${componentName}:${target}`,
    JSON.stringify({ requestedAt, requestedBy: actorId }),
    REQUEST_TTL_SECONDS,
  )
  let record = { requestedAt, requestedBy: actorId }
  const duplicate = prior !== null
  if (duplicate) {
    try {
      record = JSON.parse(prior) as typeof record
    } catch {
      /* unreadable prior value: keep this request's own timestamp */
    }
  }

  const repo = getIssueRepo()
  const title = issueTitle({ component: componentName, from: declared.version, target })
  const issueBody = buildIssueBody({
    component: componentName,
    from: declared.version,
    target,
    repo,
    releaseUrl: release?.url || null,
    upstreamRepo: catalog.repo,
    highRisk: catalog.highRisk === true,
    heldMinor: catalog.trackMinor === true,
  })

  if (!duplicate) {
    // Audit record (live-event pipeline = the portal's governance/audit feed).
    const ctx = await beginOperation({
      request: req,
      session,
      operationType: "oss.update.request",
      source: "manual",
      resource: { kind: "OssComponent", name: componentName },
      title: `OSS upgrade requested: ${componentName} ${declared.version} -> ${target}`,
      description: `mode=copy; repo=${repo}`,
    })
    await completeOperation(ctx, `OSS upgrade request generated: ${componentName} ${declared.version} -> ${target}`, `mode=copy; repo=${repo}`)
  }

  const response: OssUpgradeRequestResponse = {
    component: componentName,
    from: declared.version,
    target,
    duplicate,
    requestedAt: record.requestedAt,
    requestedBy: record.requestedBy,
    issue: { title, body: issueBody, newIssueUrl: newIssueUrl(repo, title, issueBody) },
  }
  return NextResponse.json(response, { status: duplicate ? 200 : 201 })
}
