// Upgrade-request issue text. Deliberately English and requester-free: the issue lands in a public
// repo, so no user identity goes into it (the portal audit log records who asked). The checklist
// mirrors .agents/skills/narwhal-version-upgrade/SKILL.md and docs/common/oss-update-process.md.

import { COOLING_DAYS } from "./version"

export interface IssueInput {
  component: string
  from: string
  target: string
  repo: string
  /** Upstream release page of the target when known; falls back to the repo releases list. */
  releaseUrl: string | null
  upstreamRepo: string
  highRisk: boolean
  heldMinor: boolean
}

const MAX_URL_BODY = 6000

export function issueTitle(i: Pick<IssueInput, "component" | "from" | "target">): string {
  return `deps: upgrade ${i.component} ${i.from} -> ${i.target}`
}

export function buildIssueBody(i: IssueInput): string {
  const release = i.releaseUrl ?? `https://github.com/${i.upstreamRepo}/releases`
  const lines = [
    `## Upgrade request: ${i.component} ${i.from} -> ${i.target}`,
    "",
    "Requested from the Narwhal portal (Settings > OSS updates). The portal never changes the cluster:",
    "the upgrade goes through a PR, independent review and GitOps like any other change.",
    "",
    "| | |",
    "|---|---|",
    `| Component | ${i.component} |`,
    `| Declared (VERSIONS.md) | ${i.from} |`,
    `| Target | ${i.target} |`,
    `| Upstream release | ${release} |`,
    `| Upstream repo | https://github.com/${i.upstreamRepo} |`,
    "",
    "Links: [VERSIONS.md](https://github.com/" + i.repo + "/blob/main/VERSIONS.md) | " +
      "[narwhal-version-upgrade skill](https://github.com/" + i.repo + "/blob/main/.agents/skills/narwhal-version-upgrade/SKILL.md) | " +
      "[process](https://github.com/" + i.repo + "/blob/main/docs/common/oss-update-process.md)",
    "",
  ]
  if (i.heldMinor) {
    lines.push("> Note: this component's minor line is deliberately held in VERSIONS.md. Confirm the target is on the allowed line, or record why the hold is lifted.", "")
  }
  if (i.highRisk) {
    lines.push("> High-risk component (ordering/migration/identity/storage). Release notes review and a live rollback plan are mandatory.", "")
  }
  lines.push(
    "## Checklist",
    "",
    `- [ ] Cooling: target release is at least ${COOLING_DAYS} days old (or a written exception with owner approval)`,
    "- [ ] Release notes read: breaking chart/config/API changes, amd64 + arm64 image support, no migration/RBAC/storage redesign",
    "- [ ] All version/image/chart references updated together (manifests, scripts, `scripts/airgap/images.txt` when preloaded); no mutable tag",
    "- [ ] Mirror by digest on the bastion BEFORE the pin goes live: `skopeo copy --all --preserve-digests`; `MIRROR=http://<bastion>:5000 python3 scripts/test/check-mirror-pins.py` passes",
    "- [ ] `VERSIONS.md` synced; `bash scripts/ci/check-version-consistency.sh` and `node portal/scripts/gen-oss-versions.mjs --check` pass",
    "- [ ] Compatibility matrix in `VERSIONS.md` checked (Kubernetes / Cilium / csi-driver-nfs / Istio ranges)",
    "- [ ] Trace added under `.agents/evals/traces/` if a high-risk path is touched (`.agents/evals/risk-policy.json`: gitops/**, VERSIONS*, scripts/airgap/**, ...)",
    "- [ ] PR opened, `make lint` / `make validate` / `make test` green",
    "- [ ] Independent review PASS recorded with `scripts/ci/mark-review-pass.py` on the exact head SHA",
    "- [ ] Merged; Argo CD synced and healthy",
    "- [ ] Live verification: application-reported running version equals the target (not just chart metadata); lessons-log row if anything failed",
    "",
    "Close this issue when the live verification box is checked.",
  )
  return lines.join("\n")
}

/** Pre-filled GitHub "new issue" link. Opening it is a user navigation; the portal makes no call. */
export function newIssueUrl(repo: string, title: string, body: string): string {
  const b = body.length > MAX_URL_BODY ? body.slice(0, MAX_URL_BODY) + "\n\n(truncated: paste the copied text)" : body
  return `https://github.com/${repo}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(b)}`
}
