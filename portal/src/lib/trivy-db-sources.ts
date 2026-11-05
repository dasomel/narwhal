// The security data sources mirrored into Harbor project `trivy-db` (scripts/airgap/security-db.txt).
// One list drives the API, the status card and its tests. Not "server-only": the card imports the
// copyable commands from here.
//
// D-SECDB-SOURCES-SEAM: names, repositories and tags must equal security-db.txt and the Harbor paths
// trivy-operator.yaml pulls; scripts/test/lib/check-security-db-provisioning.py (R304) reads this file
// and fails on any drift, so adding a source means editing security-db.txt and this list together.
//
// sloDays null = the upstream tag is a versioned line, not a rolling feed. trivy-checks:1 is the
// newest bundle major Trivy 0.60.0 supports and upstream froze it (created 2025-10-09; development
// moved to :2 for newer Trivy), so an age check would read "stale" forever and no refresh can fix it.
// Escape hatch: give it a number to judge it by age again.

export const TRIVY_DB_SLO_DAYS = 7

export type TrivyDbConsumer = "vulnScan" | "javaScan" | "configAudit"

export interface TrivyDbSource {
  /** Name in security-db.txt (OCI layout directory name). */
  name: string
  /** Harbor repository under the trivy-db project. */
  repository: string
  tag: string
  consumer: TrivyDbConsumer
  sloDays: number | null
}

export const TRIVY_DB_SOURCES: readonly TrivyDbSource[] = [
  { name: "trivy-db", repository: "aquasecurity/trivy-db", tag: "2", consumer: "vulnScan", sloDays: TRIVY_DB_SLO_DAYS },
  { name: "trivy-java-db", repository: "aquasecurity/trivy-java-db", tag: "1", consumer: "javaScan", sloDays: TRIVY_DB_SLO_DAYS },
  { name: "trivy-checks", repository: "aquasecurity/trivy-checks", tag: "1", consumer: "configAudit", sloDays: null },
]

// The three steps scripts/cluster/08-5-registry.sh prints when the trivy-db project is empty.
// Shown as text to copy; the portal never executes them.
export const TRIVY_DB_FIX_STEPS = [
  { key: "fetch", command: "scripts/airgap/lib/fetch-security-db.sh --bundle <dir>" },
  { key: "promote", command: 'scripts/airgap/lib/promote-security-db.sh --bundle <dir> --approved-by "<name>"' },
  { key: "push", command: "scripts/airgap/lib/push-security-db.sh --bundle <dir> --ca-cert <narwhal ca.crt> --kube-context <ctx>" },
] as const
