# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

English | [한국어](CHANGELOG-ko.md)

## [Unreleased]

### Changed

- **Update Config Audit acceptances for Lane E1 stateless UID/GID remediation (R428):** add exact `upstream` rules for `istio-system/ReplicaSet/istiod` (`KSV020`/`KSV021`, chart `1.30.1` exposes no UID/GID fields and `pilot:1.30.1` runs as `1337:1337`) and `istio-system/DaemonSet/ztunnel` (`KSV030`/`KSV104`, chart `1.30.1` exposes no `seccompProfile` key and hardcodes container context), plus exact `KSV105` acceptances for `kube-system/DaemonSet/csi-nfs-node` and `kube-system/ReplicaSet/csi-nfs-controller` (`csi-nfs-postrender` sets pod-level `runAsGroup: 10100` alongside explicit `runAsUser: 0` because containerd rejects `runAsGroup` without `runAsUser`). Config Audit allowlist now holds 60 rules across 55 workloads.

## [1.1.35] - 2026-11-28

### Changed

- **KCV0012 acceptance reason updated for EventRateLimit (CIS 1.2.10):** the AlwaysPullImages reason no longer says NodeRestriction is the only enabled admission plugin; it now names NodeRestriction and EventRateLimit and cites Harbor's own restart and the RKE2/GKE CIS guidance for isolated clusters. (#848)
- Added exact Config Audit exceptions for the measured kube-bench CronJob and observed Chaos Mesh ReplicaSet, and documented why the pinned CNPG 1.29.1 database Pod retains its API token mount (R423). Other generated workload identities remain actionable. (#849)
- **Add exact ClusterRole acceptances for Lane D (CIS 5.1.2/5.1.3):** six exact roles plus KSV046 on istiod, with bilingual technical reasons; the RBAC table summary now reflects 52 rules. (#849)
- **Update Config Audit acceptances for Cilium 1.19.4 hardening and CoreDNS kubeadm addon (R425):** remove `KSV017` and add `KSV001`/`KSV012`/`KSV020`/`KSV021` for `kube-system/DaemonSet/cilium` (chart `1.19.4` templates omit those fields and UID/GID 0 is required for host cgroup/sysctl/bpffs/CNI operations); add `KSV105` for `kube-system/DaemonSet/cilium-envoy` alongside `runAsGroup: 10100` (containerd requires explicit `runAsUser: 0`); add exact rules for `kube-system/ReplicaSet/cilium-operator` (`KSV009`/`KSV024`, `cluster-pool` IPAM hostNetwork + linked hostPort 9234), `kube-system/ReplicaSet/hubble-ui` (`KSV020`/`KSV021`, chart `1001:1001`), and `kube-system/ReplicaSet/coredns` (`KSV012`/`KSV020`/`KSV021`/`KSV022`/`KSV030`/`KSV104`, kubeadm-reconciled addon and port 53 `NET_BIND_SERVICE`). (#850)
- **Clarify Infra Audit evidence:** report rows with zero checks now say “Not evaluated”; failure-free reports retain “No failure recorded” with a caveat about unset and inapplicable checks and links to kube-bench and `verify-cluster.sh` evidence. (#851)
- **Control-plane exact acceptance (Lane C):** retain only `NET_BIND_SERVICE` on kube-apiserver because its v1.35.7 image binary has measured `cap_net_bind_service=ep`. Exact etcd acceptance covers KSV001/KSV003/KSV004 after the 2026-11-28 hardening patch prevented startup; root cause remains under investigation. (#852)
- **Control-plane static Pod Config Audit acceptances updated (`D-CP-STATIC-POD-SC`, `D-CP-ETCD-DEFER`, R426):** exact rules for `etcd-narwhal-master-1`, `kube-apiserver-narwhal-master-1`, `kube-controller-manager-narwhal-master-1`, and `kube-scheduler-narwhal-master-1` accept `KSV012`, `KSV020`, and `KSV021` (kubeadm 1.35.7 runs all four as UID/GID 0; non-root requires the alpha `RootlessControlPlane` gate, `runAsGroup` alone fails containerd without `runAsUser`, and explicit `runAsUser: 0` triggers `KSV105`). `KSV001`, `KSV003`, and `KSV004` are fixed by kubeadm strategic patches on the three components; the apiserver patch retains `NET_BIND_SERVICE` because the v1.35.7 binary has measured `cap_net_bind_service=ep`; live Trivy reports KSV022, accepted in a separate exact rule because dropping the file capability breaks `exec` with EPERM and live CapEff is `0x400`. The exact etcd rule accepts these three checks after its hardening patch prevented startup in the 2026-11-28 trial; root cause remains under investigation. (#852)

## [1.1.34] - 2026-11-27

### Changed

- **SeaweedFS Config Audit acceptance narrowed for UID 10100 (`D-OWN-POD`, R419):** master, filer and s3 drop `KSV020`/`KSV021` (they now run as platform UID/GID 10100) and keep only `KSV011` (no CPU limit, `D-sw-qos`); the volume server, which used to run as root, gets its own exact rule for `KSV011` and `KSV023` (the `/ssd/object_store` hostPath kept by owner decision). (#845)

## [1.1.33] - 2026-11-27

### Changed

- **Narrow CSI NFS Config Audit acceptances (R417):** controller KSV012/KSV020 now cite the measured root-owned 0755 shared CSI socket, and KSV001 is accepted for both workloads because only the privileged SYS_ADMIN nfs container still fails it after the sidecar fix. KSV021 and KSV125 remain actionable. (#837)
- **Narrow Falco Config Audit acceptance for `leastPrivileged: true` (`D-FALCO-LEAST-PRIV`, R421):** `security-system/DaemonSet/falco` removes `KSV001`, `KSV003`, and `KSV017` (now resolved by `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, and removing `privileged: true`) and accepts `KSV002` (chart `falco-9.2.1` renders `container.apparmor.security.beta.kubernetes.io/falco: unconfined` when `driver.modernEbpf.leastPrivileged: true` so containerd AppArmor does not block `/host/proc` `ptrace` reads for container and Kubernetes metadata) and `KSV022` (`capabilities.add: [BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE]`) alongside the retained `KSV012`, `KSV020`, `KSV021`, `KSV023`, `KSV106`, and `KSV121` checks, keeping the original `D-falco-rofs` caveat text and appending the `D-FALCO-LEAST-PRIV` rationale in `src/lib/i18n.ts`. (#841)
- **ArgoCD RBAC acceptance updated for Stage 1 privilege split (`D-OWN-ARGO-STAGED`):** removed `ClusterRole/argocd-server` (`KSV041`, `KSV046`, `KSV048` fixed by `D-ARGO-SERVER-RO`) and added exact `KSV044`/`KSV046` acceptance for `ClusterRole/argocd-application-controller` (reconciles ClusterRoles/Bindings for 14 charts and platform roles; bounded role requires `escalate`/KSV050 or identical leaf rights; revisit with design option C). (#842)

## [1.1.32] - 2026-11-26

### Changed

- **Velero node-agent Config Audit acceptance narrowed:** `storage/node-agent` no longer has KSV003/KSV106 accepted; it now drops all capabilities (#831). The accepted reason records the measured backup and restore.
- **OSS versions list includes kubelet-csr-approver 1.2.16** (#825).

## [1.1.31] - 2026-11-26

### Changed

- **Narrow Batch C node-agent Config Audit acceptances:** Falco KSV004/KSV030/KSV104, Cilium KSV003/KSV004/KSV025, Envoy KSV025, and Envoy, MetalLB speaker, kube-vip and CSI NFS node KSV021 stay actionable when reported. KSV001 reasons for Istio CNI, ztunnel and Cilium Envoy record the measured SYS_ADMIN and the chart behavior. KSV105 is accepted for the exact kube-vip Pod and MetalLB speaker DaemonSet with measured UID 0 reasons. Falco’s accepted privileged eBPF mode retains the caveat about the untested leastPrivileged follow-up. The rule changes take effect with the next portal release.

## [1.1.30] - 2026-11-26

### Changed

- Compliance records scoped risk acceptances for the API server on `narwhal-master-1` (CIS 1.2.1 and 1.2.12) and for the low UID/GID checks on the `narwhal-db-1` Pod and the Deployment-owned `narwhal-db-pooler-rw` ReplicaSet.

## [1.1.29] - 2026-11-25

### Changed

- The Trivy check catalogue (`scripts/gen-trivy-check-catalogue.mjs`, `src/data/trivy-check-catalogue.generated.sha256.json`) is regenerated for trivy-operator 0.36.1 (embedded trivy-checks `v1.12.2-0.20261012053351-ac85645895ed`); the generator reads the new `id` + `aliases` metadata format (#807).
- The accepted-finding reason for `ReplicaSet/trivy-operator` now names `trivy-operator:0.36.1`; the operator still runs as UID/GID 10000 (measured on the live pod) (#807).

### Security

- Resolved OSV advisories with package overrides: `minimatch` `^11.0.1`, `glob` `^14.0.2`, `seroval` `~1.6.11` (#803).

## [1.1.28] - 2026-11-25

### Changed

- **Compliance per-resource verdict source (`D-COMPLIANCE-VERDICT-SOURCE`, `src/lib/compliance.ts`, `src/lib/trivy-check-catalogue.ts`, `src/types/compliance.ts`, `src/components/compliance/failure-counters.tsx`, `src/components/compliance/frameworks-grid.tsx`, `src/app/(dashboard)/compliance/frameworks/[id]/page.tsx`):**
  - Switched framework control verdicts from `ClusterComplianceReport.status.detailReport.results[].checks` to paginated (`limit=200` with `metadata.continue` and single `410 Gone` restart) reads of per-resource Trivy reports (`configauditreports`, `clusterconfigauditreports`, `rbacassessmentreports`, `clusterrbacassessmentreports`, `infraassessmentreports`, `clusterinfraassessmentreports`, already permitted by `ClusterRole/narwhal-portal` without RBAC changes), joined via `ClusterComplianceReport.spec.compliance.controls[].checks[].id` and normalized with `resolveCanonicalCheckId` (including `AVD-KSV-0001`/`KSV001`/`KSV-0001`/`KCV0033` without colliding `KSV0012` and `KSV012`).
  - A control is `failed` if any mapped check has `success: false` on any scanned resource (preserving exact acceptance rules and `kube-bench` failure-first merge/`sourceDisagreement`), `tested-passed` (`testedByReport: true`, counted in optional `reportTestedPassCount` and deduplicated `testedPassCount`) only when at least one mapped check has an explicit `success: true` record on a resource inside the control's scope (node type, namespace, kinds) and none failed; a mapped check missing from the trivy-checks catalogue keeps the control not evaluated (`D-VERDICT-UNKNOWN-CHECK`), and otherwise retains existing honest `no failure recorded` / `not_evaluated` states without inferring a pass from absence. Ignoring `status.detailReport` checks and inflated `status.summary.failCount` prevents `trivy-operator` `0.36.1` (`reportRecordFailedChecksOnly: "false"`) from misclassifying passed checks as failures while keeping `0.25.0` live output identical (verified read-only against `narwhal-portal` ServiceAccount on 2026-11-25 across all 5 frameworks).

## [1.1.27] - 2026-11-25

### Changed

- Added exact node-scoped acceptance rules for CIS benchmark findings 1.1.12 (kubeadm static-pod root etcd), 1.3.7, and 1.4.2 (off-node secure metrics) on node `narwhal-master-1` and matching Trivy infra findings KCV0039 (`kube-controller-manager`) and KCV0041 (`kube-scheduler`), with reasons verified on the live cluster; no live rollout or new passing-test claim (`src/data/compliance-acceptance-rules.json`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/lib/i18n.ts`). Matching is exact on `(benchmarkId, controlId, node)` for benchmark findings and on `(kind, namespace, name, check)` for infra findings. The portal reports observed test records and baseline comparisons and never claims to prove or guarantee a control.
- CIS 1.3.6 / KCV0038 stays actionable without an acceptance rule: observed kubelets lack `serverTLSBootstrap` / CSRs and serve self-signed certificates; `kube-bench` `PASS` checks only the controller-manager gate. Controls 1.2.1, 1.2.6, 1.2.10, 1.2.12, 1.3.6, and 4.2.6 remain actionable by owner decisions (the controller-manager flag for CIS 1.3.1 is a cluster change, not a portal change).

## [1.1.26] - 2026-11-24

### Changed

- **Compliance > CIS `kube-bench` test records integration (`src/lib/kube-bench.ts`, `src/lib/compliance.ts`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/data/compliance-acceptance-rules.json`, `src/types/compliance.ts`, `src/lib/cache-keys.ts`, `src/lib/i18n.ts`, `src/components/compliance/failure-counters.tsx`, `src/components/compliance/frameworks-grid.tsx`, `src/app/(dashboard)/compliance/frameworks/[id]/page.tsx`):**
  - Integrated per-node `kube-bench` ConfigMap records (written by the scheduled CIS benchmark of #792 in `security-system`, label `narwhal.io/compliance-tool=kube-bench`, key `record.json`, read with existing `configmaps` list/get RBAC without portal RBAC changes) into `k8s-cis-1.23` evaluation as test evidence under fail-closed validation (`schemaVersion=1`, `tool=kube-bench`, `benchmarkId=cis-1.23`, `status=complete`, non-empty unique `controlId`s, `totals` consistency, non-future `finishedAt`, `BENCHMARK_MAX_AGE_HOURS = 26`, existing cluster `node` check via `/api/v1/nodes`, `512 KiB` JSON byte cap, and truncation/403 non-caching) and section-scoped cluster node coverage (1.x/2.x/3.x across all control-plane nodes; 4.x across all nodes; 5.x cluster-once). These rules describe the benchmark evidence itself, not the final control state: a record older than 26 h, with error status, malformed, duplicated or missing gives no benchmark verdict (reason stated), and `WARN`/`type: "manual"` (`manual`) and `INFO` (`benchmark_info`) results give none either. The final state then follows the failure-first merge with Trivy: a Trivy failure keeps the control failed, a control Trivy reports without failure stays "no failure recorded", and only a control with no evidence from either source is shown as not evaluated.
  - Enforced failure-first cross-source merging with Trivy: any Trivy failure OR any node `kube-bench` `FAIL` keeps the control `failed` (with `sourceDisagreement` naming both verdicts when Trivy and `kube-bench` disagree, e.g. CIS `1.3.6`), and validation/coverage gaps surface dedicated ko/en `benchmark_*` reasons. Live measurement as the portal ServiceAccount on 2026-11-24 for cis-1.23 (116 controls) yielded: tested passed (benchmark) 70, no failure recorded 4, failed 25 (actionable 24, accepted 1), not evaluated 17 (before: 0 / 4 / 23 / 89). The portal reports observed test records and baseline comparisons and never claims to prove or guarantee a control.
  - Extended `compliance-acceptance-rules.json` with an empty `benchmark` array and exact `(benchmarkId, controlId, node?)` loader/matcher (`matchBenchmarkException`) so benchmark findings can be accepted only by exact rules (no acceptance rules added in this release). Stopped rendering the scanner's raw pass count and pass rate in summary cards, grid, and framework detail UI (which had shown 95/116 for CIS and 122/178 overall; evidence-based figures are 74/116 and 86/178), updating the UI to count `tested (benchmark)` separately from `no failure recorded` and show tool/version/node count/timestamp plus FAIL `actualValue`/`expectedResult` excerpts. Bumped `complianceFrameworksList`, `complianceFrameworksDetail`, and `complianceSummary` cache keys to `v4`; regression check `R402` (23 mutations).

## [1.1.25] - 2026-11-24

### Changed

- **Compliance > KISA live controls under closed-world baseline (`src/lib/kisa.ts`, `src/lib/kisa-checks.ts`, `src/lib/kisa-checks-ops.ts`, `src/lib/kisa-controls.ts`, `src/lib/kisa-policy-canonical.mjs`, `src/data/kisa-policy-baseline.json`, `scripts/gen-kisa-policy-baseline.mjs`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/data/compliance-acceptance-rules.json`, `src/lib/oss-updates/version.ts`, `src/lib/k8s-client.ts`, `src/lib/trivy.ts`, `src/types/security.ts`, `src/types/kisa.ts`, `src/lib/cache-keys.ts`, `src/lib/i18n.ts`, `src/components/compliance/kisa-checklist.tsx`):**
  - Replaced the six remaining hard-coded statuses (SEC-01, ETCD-02, IMG-01, TLS-01, OBS-01, IMG-02) with live Kubernetes API evaluations (IMG-01 becomes `manual` with a stated reason, because node files are not visible through the API) and put all 16 KISA controls under a closed-world rule (`D-KISA-CLOSED-WORLD`, `R397`, `R399`). Live measurement with admin rights on 2026-11-24 yielded pass 3 / fail 4 / warn 8 / manual 1 (1.1.24 showed pass 6 / fail 6 / warn 4, with 10 controls live and 6 static).
  - In this model, `pass` means the observed state equals an enumerated reviewed shape (baseline: 10 Kyverno policies, 33 NetworkPolicy/CiliumNetworkPolicy manifests, 1 cluster-wide policy in `src/data/kisa-policy-baseline.json` schemaVersion 2). The portal never claims to prove or guarantee a control, and never claims tamper detection against an adversary holding cluster-admin who forges objects byte-for-byte equal to reviewed shapes. Three controls (`KISA-SEC-01`, `KISA-ETCD-02`, and `KISA-IMG-01`) have no pass path by design.
  - Portal RBAC (`ClusterRole/narwhal-portal`) gained minimal read-only permissions pinned by regression tests (`R378`, `R397`): `security.istio.io/peerauthentications` [list], `batch/cronjobs` [get, list], `wgpolicyk8s.io/policyreports` [list], and `cilium.io/{ciliumnetworkpolicies,ciliumclusterwidenetworkpolicies}` [list]; no Secret read access was granted.
  - `KISA-IMG-02` replaces custom Kyverno rule/CEL evaluation with canonical spec SHA-256 comparison against `kisa-policy-baseline.json` (stripping 9 documented Kyverno 1.18 defaults after value checks), `Ready=True`, override rejection (`specHasOverrides`), and `enforceEligible`; `KISA-ADM-01` validates all `admissionRelevant: true` policies against the baseline and surfaces unreviewed policies.
  - `KISA-NET-01` (`checkNet01`) and `KISA-ETCD-02` (`checkEtcd02`) verify `CiliumClusterwideNetworkPolicy` parity against baseline `clusterwidePolicies` (`warn` on unreviewed or drifted CCNPs, `0/5` covered namespaces in `KISA-NET-01` whenever any CCNP is unreviewed/drifted, `manual` on 403 or API failure). `KISA-ETCD-02` requires every `NetworkPolicy` and `CiliumNetworkPolicy` in `platform-system` to equal the baseline digest (`warn` on unreviewed policy in namespace), verifies exact `matchesLabelSelector` coverage of running `platform-system/apisix-etcd` pod template labels (`null`/`undefined` selects nothing, `{}` selects everything; `warn` on mismatch, `manual` on missing rollout fields), and reports observed CLI facts only without a `pass` path (`--config-file` reported as `flags may be overridden by the config file; not evaluated`; pass path deferred until etcd TLS is backed by a reviewed workload spec digest).
  - `KISA-OBS-01` (`checkObs01`) enforces an explicit `ALERTMANAGER_INTEGRATION_VALIDATORS` table matching all 15 `AlertmanagerConfig` `v1alpha1` CRD integration types and required destination fields (rejecting empty `webhookConfigs: [{}]`, `slackConfigs` missing `apiURL`, or unlisted types with `warn`), requiring label-matched Falco/falcosidekick pods with `Ready=True`. `KISA-CP-01` and `KISA-ETCD-01` isolate checks strictly to `kube-apiserver` with `Ready=True` pod condition required.
  - `KISA-RBAC-01` and `KISA-RBAC-02` evaluate RBAC bindings under `D-KISA-CLOSED-WORLD` without exempting by name prefixes: `portal/src/data/compliance-acceptance-rules.json` (`rbacBindings`, `R395`) explicitly enumerates 44 verified Kubernetes bootstrap `ClusterRoleBinding` objects matching upstream Kubernetes release-1.35 defaults with zero differences, requiring bootstrap metadata, exact `roleRef`, non-empty `checks`, and exact canonical `subjects` set equality, while clarifying that namespaced `Role/dev/cluster-admin` is evaluated by `KISA-RBAC-02`.
  - `KISA-TLS-01` (`checkTls01`) removes name-priority selection and oldest-wins precedence: requires exactly one selector-less `PeerAuthentication` in `istio-system` with `mtls.mode === "STRICT"` and no `portLevelMtls` (0 mesh-wide => `warn`, 2+ mesh-wide => `warn`, non-STRICT => `fail`), reports `UNSET (inherits)` on selector-less policies, and confirms `STRICT` inheritance only for a sole selector-less `UNSET` policy in a non-root namespace under a single `STRICT` mesh root without `portLevelMtls`.
  - Hardened remaining controls with full `ko`/`en` `detailLocalized` and `reasonKey` across all 16 controls (`not accepted by an exact rule`), handling PSA `audit`/`warn`/`privileged`, missing Certificate `notAfter`, failed/truncated Trivy summaries, `rootPa` `portLevelMtls`, and zero-control false-pass prevention.
- **Dependencies**: Bumped `next` and `eslint-config-next` to 16.4.6 (#708), `@opentelemetry/auto-instrumentations-node` to 0.83.1, `@opentelemetry/exporter-trace-otlp-http` and `@opentelemetry/sdk-node` to 0.301.2, `@opentelemetry/resources` to 3.1.1 (#709), `@playwright/test` to 1.65.1, `@vitejs/plugin-react` to 6.3.0, `vite` to 8.3.8 (#710), `zustand` to 5.1.0 (#711), and `@grpc/grpc-js` to 1.14.7 (#712).
- **Settings > OSS Updates (deployment-side, #778)**: no portal code change; the GitOps chart now grants the portal `get` on the single CRD `gateways.gateway.networking.k8s.io`, so the Gateway API CRDs row shows a version instead of "unknown: no-permission", and sets `OSS_UPDATES_UPSTREAM=true` (default and vagrant providers; `false` for the air-gapped kakao provider), which enables the latest-upstream-release lookup.

## [1.1.24] - 2026-11-23

### Fixed

- **OSS Updates truthful running detection, rollout/suffix/CVE/sibling isolation, and explicit unknown reasons (`src/lib/oss-updates/catalog.ts`, `src/lib/oss-updates/status.ts`, `src/lib/oss-updates/version.ts`, `src/lib/k8s-client.ts`, `src/lib/trivy.ts`, `src/types/oss-updates.ts`, `src/components/settings/oss-updates-table.tsx`, `src/lib/i18n.ts`):**
  - Replaced Trivy-only image discovery with authoritative Kubernetes reads (`getOssClusterInventory()`: `/version`, bounded `deployments`/`daemonsets`/`statefulsets`/`replicasets`, cluster-wide `/api/v1/pods` reading runtime `status.containerStatuses[].image`, `state.running`, `imageID` digests, `metadata.ownerReferences`, `metadata.deletionTimestamp`, and static-pod `kubernetes.io/config.mirror` + `spec.nodeName`, plus a probe on `gateways.gateway.networking.k8s.io` CRD) using existing portal RBAC only. Pods count only when owned by the defining workload (or mirror-annotated on a node for static pods); truncated bounded lists propagate as `list-truncated` (`unknown`); Terminating/Pending/Succeeded/Failed/non-running pods do not count as running; same-tag pods with differing `imageID` digests surface as `rollout-mixed` drift with short digests; chart versions alone and Trivy tags never produce `in-sync`.
  - Replaced substring `imageMatch` (`r.image.includes(...)`) with exact repository+tag/digest attribution (`summarizeCvesForArtifacts`) so CVEs match only reports for the actually running image (returning `No scan (no report for this running image)` when only older tags have reports), and gave each companion/sibling row its own CVE count and visible failure/digest-only reason (`No tag (digest-pinned)` with shortened digest).
  - Enforced per-component tag suffix allow-lists (`allowedTagSuffixes`: `-rootless` for Gitea, `-ubuntu` for APISIX, `-distroless` for Istio; all unlisted suffixes such as `-rc1`, `-preview1`, `-custom`, and non-semver tags stay verbatim as `tag-unparseable`, never `in-sync`) and surfaced ko/en reasons for every unknown/no-scan state (`R394`).

### Changed

- **Exact Compliance acceptance (`src/data/compliance-acceptance-rules.json`, `src/lib/compliance-acceptance.ts`, `src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/components/compliance/failure-counters.tsx`, `src/lib/i18n.ts`):** replaced namespace/built-in/substring blanket acceptance with reviewed scope, namespace, kind, resource and explicit check IDs (Config Audit rules 73->45, RBAC 4->47 including 31 Kubernetes bootstrap roles); removed unverifiable exceptions, require ReplicaSet Deployment ownership, versioned affected caches, and expose unused rule targets/counts in the summary API and English/Korean UI (`R395`).

- **Honest three-state Compliance status & data-unavailability reporting (`src/lib/compliance.ts`, `src/lib/kisa.ts`, `src/types/compliance.ts`, `src/components/compliance/*`, `src/app/api/compliance/*`, `src/app/(dashboard)/compliance/*`, `src/lib/i18n.ts`, `src/data/trivy-check-catalogue.generated.json`):**
  - Evaluated failed-only compliance controls against a generated, pinned check catalogue of 158 checks (`src/data/trivy-check-catalogue.generated.json`), replacing binary pass assumptions on Trivy's `{ checkID: "", success: true }` placeholders with three-state control evaluation (`passed`, `failed`, `not_evaluated`) in which `passed` means only that no failure is recorded for the control's catalogued check IDs in an in-scope report of the matching kind (the scanner records failures only; infra checks never show `passed`) (`no_evidence_in_report` on clean `checks: []` reports or unknown IDs, `coverage_unknown` on failed auxiliary reads, `absent_from_report` for spec controls omitted from `detailReport.results`, plus `excluded_namespace`, `unsupported_kind`, `missing_node_type` from spec/collector `nodeType` only, `unlinked_check_id`, `manual`, `scanner_disabled`, `summary_only`, `empty_checks`) (`R387`, `R389`, `R391`, `R392`, `R393`).
  - Pooled aggregate framework pass rates (sum of numerators / sum of denominators with explicit `num/den` labels), prevented `100%` leaks across every `formatPassRatePct` call site (including raw rates), and updated KISA `CFG-01` to fail on actionable controls, warn on unevaluated controls, and pass only when every control is evaluated with zero actionable failures.
  - Surfaced `unacceptedLowConfigAuditFailures` separately from actionable MEDIUM+ (`totalConfigAuditFailures`) and accepted hygiene LOW (`lowSeverityConfigAuditFailures`), plus `droppedConfigAuditReports` / `droppedConfigAuditFailures` / `droppedFailCount` / `droppedCheckCount` for superseded ReplicaSets.
  - Stopped masking Kubernetes list failures as empty `[]` / `0` or caching failed/partial responses: API routes (including `/api/compliance/summary` when all primary sources fail) return `503 { unavailable: true }`, `getComplianceSummary` tracks per-source availability, `partial` / `partialSources` UI notices, and `status.updateTimestamp` freshness alongside the 6-hour rebuild cadence note.
- **Remove `KCV0056` Infra acceptance (`src/lib/compliance-accepted.ts`, `src/lib/i18n.ts`):**
  - Emptied `ACCEPTED_INFRA_RULES` (removing the `KCV0056` `upstream` rule and `compliance.acceptedReason.infraCniDirGlob` ko/en i18n keys) now that `gitops/charts/narwhal-apps/templates/trivy-operator.yaml` mounts host `/etc/cni/net.d/` at container `/etc/cni` (`D-NODE-COLLECTOR-CNI-MOUNT`) so `node-collector` `CMD-0009` (`stat -c %a /*/cni/*`) inspects CNI files rather than the `700` directory. The fail-closed Infra/Frameworks accepted/actionable split machinery stays intact so failing `KCV0056` reports are actionable again and future data-only rules remain supported.

## [1.1.23] - 2026-11-20

### Changed

- **Infra Audit & Frameworks accepted/actionable split for `KCV0056` (`src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/types/compliance.ts`, `src/components/compliance/infra-audit-list.tsx`, `src/components/compliance/failure-counters.tsx`, `src/app/(dashboard)/compliance/infra-audit/[node]/page.tsx`, `src/lib/i18n.ts`):**
  - Extended `normalizeCheckId` for `KCV` IDs (`AVD-KCV-0056 -> KCV0056`) and added `ACCEPTED_INFRA_RULES` / `matchInfraException` fail-closed for `Node` checks, allowlisting `KCV0056` (`upstream`: `node-collector:0.3.1` / `trivy-checks` `CMD-0009` runs `stat -c %a /*/cni/*`, which matches `/etc/cni/net.d` directory mode `700` instead of files inside it `600`, and `trivy-operator` `0.25` has no command override; backed by `harden-node-files.sh` + `verify-cluster.sh`). Unlisted `KCV` IDs (including `KCV0059`, resolved by mounting host `/etc/passwd` + `/etc/group` read-only in `trivy-operator.yaml`) and reports without matching `checks[]` stay actionable.
  - Added `actionableSummary`, `acceptedSummary`, `accepted`, and `acceptedScope` on `InfraAuditRow` plus `acceptedInfraFailures` on `ComplianceSummary`, reused `infraAuditMatcher` for `Node` targets in Compliance Frameworks, and surfaced accepted badges/reasons while preserving raw severity counts in the Infra tab, detail page, and summary counter.

## [1.1.22] - 2026-11-19

### Changed

- **Compliance allowlist for remaining low-severity framework findings (`src/lib/compliance-accepted.ts`, `src/lib/i18n.ts`, `docs/common/compliance-hardening.md`):**
  - Expanded `database/Pod/narwhal-db-1` (`KSV020`, `KSV021` alongside `KSV036`: `postgresql:18` `/etc/passwd` UID 26 and `0700` `pgdata` PVC ownership; exact pod-name match, so other CNPG instance pods stay actionable until listed), added `database/ReplicaSet/narwhal-db-pooler-rw` as `upstream` (`KSV020`, `KSV021`: CloudNativePG v1.29.1 overwrites the Pooler pod securityContext with UID 998/GID 996, no supported setting), added `storage/CronJob/openbao-snapshot` and `storage/ReplicaSet/openbao-snapshot-holder` (`KSV020`, `KSV021`: existing backup files on the PVC are owned by UID 100, changing it needs an ownership migration), and expanded `nfs-quota-agent/DaemonSet/nfs-quota-agent` (`KSV003`, `KSV020`, `KSV021` alongside existing privileged XFS/ext4 quota exceptions). Updated `compliance-accepted.test.ts` and `compliance.test.ts` (`ccr-live-trimmed.json`). `openbao-auto-unseal` was fixed to `runAsUser`/`runAsGroup` `10100` in its GitOps manifest instead of being allowlisted.
- **Compliance Frameworks: separate accepted exceptions from actionable failures (`src/lib/compliance.ts`, `src/components/compliance/frameworks-grid.tsx`, `src/components/compliance/failure-counters.tsx`, `src/app/(dashboard)/compliance/frameworks/[id]/page.tsx`, `docs/common/compliance-hardening.md`):**
  - Evaluated every failing check in `ClusterComplianceReport.status.detailReport.results[].checks[]` using `parseComplianceTarget` (`<namespace>/<kind>-<name>`) and `normalizeCheckId` (`AVD-KSV-00NN -> KSVNNN`) and reused the existing Config Audit (`configAuditMatcher`, `fetchReplicaSetMap()` superseded/Deployment-ownership filtering), RBAC (`rbacAuditMatcher`), and Infra (actionable) matchers fail-closed, resolving hashed Trivy report targets from `configauditreports` / `(cluster)rbacassessmentreports` when available and keeping any unparseable, missing-ID, or unlisted finding actionable.
  - Added `actionableFailCount`, `acceptedFailCount`, and `effectivePassRate` on frameworks and controls alongside raw `failCount` and `passRate` (failing closed when `detailReport.results` is absent), and updated the Frameworks grid, expanded preview, detail page, and Compliance summary counter to headline actionable counts with accepted badges, collapsed accepted-control groups, and links to `docs/common/compliance-hardening.md#risk-accepted-exceptions` (`ko`/`en` i18n).

## [1.1.21] - 2026-11-19

Patch release: Compliance no longer counts Trivy reports left behind by superseded Deployment revisions (narwhal#727).

### Operator notes / behaviour changes

- **Requires the matching platform change:** the portal ClusterRole needs read-only `get`/`list` on `apps/replicasets`
  (added in the same change set to `narwhal-portal-k8s.yaml`). Without it the ReplicaSet list fails and every report
  is kept, which is the previous behaviour.
- A report is dropped only for a fully drained old revision of a Deployment that has a newer revision. Deployments
  scaled to zero, standalone ReplicaSets and revisions with terminating pods are still counted.

### Fixed

- **Exclude ConfigAuditReports of superseded Deployment ReplicaSet revisions and verify Deployment ownership (`src/lib/compliance.ts`, `src/lib/compliance-accepted.ts`, narwhal#727):**
  - Fetched `/apis/apps/v1/replicasets` in the same request path as `configauditreports` (using `complianceK8sFetch` and `K8S_POLICY`, with no separate ReplicaSet cache so stale 0-replica state cannot outlive the report cache) when building Compliance Config Audit list/detail views, and excluded a `ReplicaSet` report only when it is a fully drained superseded Deployment revision (`Deployment` `ownerReferences`, `spec.replicas == 0` and `status.replicas == 0`, and another ReplicaSet owned by the same Deployment in the same namespace with a strictly higher `deployment.kubernetes.io/revision`). Standalone ReplicaSets, scaled-to-zero Deployments (current revision), terminating revisions (`status.replicas > 0`), and unknown metadata remain counted.
  - Failed safe so live findings are never hidden: if `/apis/apps/v1/replicasets` cannot be read (e.g. 403/timeout) or a report's `ReplicaSet` is missing from the list, kept all affected reports and logged one warning.
  - Passed each kept `ReplicaSet`'s `metadata.ownerReferences` Deployment names into `normalizeWorkloadName` / `matchConfigAuditException` so a `SafeEncodeString` hash suffix collapses to `<name>` only when `ownerReferences` names a `Deployment` with `<name>` (standalone ReplicaSets like `harbor-core-<hash-like>` with no Deployment owner are no longer accepted; unknown ownership keeps the alphabet fallback without broadening acceptance).

## [1.1.20] - 2026-11-19

Security patch release: permission reductions now take effect in the portal without waiting for the next sign-in, and
an open tab no longer keeps data fetched under the old scope (narwhal#734, narwhal#688).

### Operator notes / behaviour changes

- Keycloak group changes apply at the next access-token refresh. If the refresh fails (Keycloak unreachable, session
  revoked), the user is treated as guest and sent to sign in again: during a Keycloak outage longer than one token
  lifetime, signed-in users including admins lose access until Keycloak is back.
- When the server-side scope of a session changes, an open tab reloads (or shows a "scope changed" page if it reloaded
  in the last 30 s). Live event and service-flow streams reconnect at least once per token lifetime.
- No configuration is required.

### Fixed

- **Re-derive Keycloak `groups` and `teams` claims on token refresh, fail closed across server gates, and bound SSE stream lifetime (`src/lib/auth.ts`, `src/lib/scope.ts`, `src/app/api/events/stream/route.ts`, `src/components/providers.tsx`, narwhal#734):**
  - On `refreshKeycloakToken` success in `config.callbacks.jwt`, decoded the refreshed `id_token` (or `access_token` when `id_token` is absent) via `decodeJwtClaims` (`D-734-REFRESH-JWT-TRUST`, server-side TLS trust against `KEYCLOAK_ISSUER` without a new JWKS dependency), enforced `validateIssuerAudience`, `isUnexpiredExp`, and `alg !== "none"`, and reused `applyGroupClaims` (`sanitizeGroups`, `classifyGroupClaim`, `sanitizeTeamGroups`) so Keycloak group changes apply within one access-token lifetime.
  - Failed closed (`groups: ["guest"]`, `teams: []`, `groupClaimStatus: "no_groups"`, `token.error = "RefreshTokenError"`) via `failClosedRefresh` without keeping stale `groups`/`teams` or untrusted tokens on both claim-validation errors and token-endpoint failures (e.g. HTTP 400 `invalid_grant` or network error), rejected any session carrying `session.error` at the shared server gates in `src/lib/scope.ts` (`resolveEffectiveAccess`/`hasSessionAccess`/`requireEffectiveScope`/`getEffectiveScope`), and triggered a one-shot client re-sign-in (`signIn` guarded by a ref) via `SessionErrorHandler` in `src/components/providers.tsx` when `session.error === "RefreshTokenError"`.
  - Forwarded `session.expiresAt` from `auth.ts` and bounded `/api/events/stream` to the access token's `expiresAt` (falling back to 5 minutes when unknown) so `EventSource` reconnects and re-authorizes with refreshed claims; added Vitest regression coverage in `src/lib/auth.test.ts`, `src/lib/scope.access.test.ts`, `src/app/api/events/stream/route.test.ts`, and `src/components/providers.test.tsx`. Cross-tab sync remains out of scope for follow-up.

- **Evict stale browser query cache and open detail views on server scope change or revocation (`src/lib/scope.ts`, `src/lib/scope-guard.ts`, `src/components/providers.tsx`, `src/app/api/**`, narwhal#688):**
  - Attached a 32-hex-char `AUTH_SECRET`-keyed HMAC of `scope.fingerprint` (`signScopeFingerprint` in `withScopeHeader`) in the `X-Narwhal-Scope` response header across tenant-scoped API routes and `/api/events/stream` so team/namespace names cannot be dictionary-attacked.
  - Routed React Query `queryFn`s through `apiFetch` / `apiFetchJson` bound to the shared `QueryClient` in `Providers` so a changed `X-Narwhal-Scope` header, `401`, or `403 ACCESS_NOT_GRANTED` synchronously clears the cache, blocks all concurrent/subsequent responses via a module-level `navigating` flag (`ScopeChangeError`), and performs a full document navigation (`window.location.reload()` on fingerprint change, `/access-denied` on `403 ACCESS_NOT_GRANTED`, `/login` on `401`), with a 30 s `sessionStorage` reload limit; when the limit is hit or `sessionStorage` is unavailable the tab navigates to `/access-denied?reason=scope-changed`, which shows a manual Reload link and, unlike the plain page, does not redirect a user who still has access back to `/`, so it cannot loop (replacing the earlier surgical in-memory epoch/cancel/reset eviction, which proved prone to concurrent-response races).
  - Added real-`QueryClient` regression tests (`src/lib/scope-guard.test.ts`) plus `viewer` session and HMAC `X-Narwhal-Scope` assertions in `src/lib/scope.access.test.ts`, `src/app/api/events/route.test.ts`, `src/app/api/events/stream/route.test.ts`, and `src/app/api/alerts/route.test.ts`.
  - Deferred `auth.ts` claim refresh on token renewal, SSE stream (`/api/events/stream`) mid-connection scope re-validation, and cross-tab sync to `narwhal#734`.

## [1.1.19] - 2026-11-18

Patch release that adds reviewed compliance exceptions backed by concrete evidence and closes a fail-open path in
the RBAC exception matcher (narwhal#717, narwhal#722).

### Operator notes / behaviour changes

- More findings that cannot be fixed are shown as accepted with a reason (CloudNativePG instance pod token mount,
  the privileged NFS quota agent, the stock Keycloak image). Findings that can still be fixed stay actionable.
- Authored RBAC exceptions now match only a `Role` in its namespace or a `ClusterRole`; a report of unknown kind is
  never accepted. No configuration is required.

### Added

- **Reviewed Config Audit allowlist additions (`src/lib/compliance-accepted.ts`, `src/lib/i18n.ts`, `docs/common/compliance-hardening.md`):**
  - Expanded `ACCEPTED_CONFIG_AUDIT_RULES` to 70 rules across 58 workloads (adding `Pod` kind support for exact `database/Pod/narwhal-db-1` `KSV036`, `nfs-quota-agent/DaemonSet/nfs-quota-agent` `KSV001`/`KSV012`/`KSV017`/`KSV023`/`KSV121` while keeping `KSV014`/`KSV104` actionable, and `iam/StatefulSet/keycloak` `KSV014` as `upstream` per `D-COMPLIANCE-R2`; fixable `KSV014` findings for `keycloak-operator`, `velero`, and `node-agent` remain actionable).

### Fixed

- **Fail closed on RBAC report kind in `matchRbacException` (`src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, narwhal#717):**
  - Indexed `ACCEPTED_RBAC_RULES` by `(namespace, kind, roleName)` and required `kind === "Role"` (non-empty namespace) or `kind === "ClusterRole"` (empty namespace), failing closed on `Unknown`, omitted, or mismatched kinds such as `devtools/Unknown/narwhal-portal-jobs`.

## [1.1.18] - 2026-11-18

Patch release that separates reviewed, non-actionable compliance findings from actionable ones on the Compliance page
and shows the result of a certificate renewal request (narwhal#713, narwhal#716).

### Operator notes / behaviour changes

- Compliance counters now show actionable findings; reviewed exceptions are still listed, labelled as accepted with a
  reason. The exception list is fixed in code and documented in `docs/common/compliance-hardening.md`; anything not
  listed stays actionable. No configuration is required.

### Added

- **Reviewed Config Audit & RBAC compliance allowlist (`src/lib/compliance-accepted.ts`, `src/lib/compliance.ts`, `src/components/compliance/*`, `docs/common/compliance-hardening.md`):**
  - Added typed per-workload/per-check `ACCEPTED_CONFIG_AUDIT_RULES` (67 entries across 56 workloads, with ReplicaSet `SafeEncodeString` pod-template-hash stripping `^[bcdfghjklmnpqrstvwxz2456789]{5,10}$` and `AVD-KSV-*`/`KSV*` ID normalization) and exact role/check `ACCEPTED_RBAC_RULES` (4 platform-authored roles: `platform-admin`, `developer-workload-admin`, `narwhal-portal-jobs`, `narwhal-portal-notification-channels`), plus `UPSTREAM_EXACT_RBAC_ROLES` (13 exact chart role names for `chaos-mesh` and `kubernetes-dashboard` instead of substring matching).
  - Split failing checks into `actionableSummary` vs `acceptedSummary` per row (failing closed on unlisted check IDs, unlisted workloads, or summary-vs-checks mismatches, e.g. `KSV042` on `platform-admin` or summary Critical 1 / High 1 with only one accepted High in `checks[]`), keeping non-system `LOW` checks in `lowSeverityConfigAuditFailures` without double-counting and preserving all findings in API responses.
  - Updated Compliance counters, Config Audit/RBAC tables, expanded check panels, and detail pages to surface accepted counts, per-check accepted badges, and `ko`/`en` i18n accepted reasons (`compliance.acceptedReason.*`); synced the allowlist with `docs/common/compliance-hardening.md` via a markdown-table parity test (`src/lib/compliance-accepted.test.ts`).

### Fixed

- **TLS certificates renewal feedback and issued metadata (`src/components/settings/certs-table.tsx`, `src/lib/k8s-client.ts`):**
  - Added `revision` (`status.revision`) and `issuing` (`Issuing=True` condition) to the `Certificate` projection and `GET /api/settings/certs` response while keeping existing fields and the `cluster-admin` gate.
  - Added an `Issued` (`발급일`) column to `CertsTable` showing locale-formatted `notBefore` alongside `rev <N>`, gave `POST /api/settings/certs/renew` a 15s `AbortSignal` timeout (aborted on unmount as well), and replaced the static renewal-sent badge with bounded row-keyed polling (every 2s for 30s while `Issuing…`, then every 10s up to 5m total while `Still pending — check again later`, keeping the baseline for later manual/refocus refetches and continuing polling across transient GET failures) transitioning across `Issuing…`, `Renewed at <time>`, `Still pending — check again later`, and `Renewal failed` without losing row state across refetches or leaking timers on unmount.

## [1.1.17] - 2026-11-17

Patch release that denies portal access to users without a team mapping or labelled namespace and scopes tenant data
to each caller, while reserving cluster-wide signals for cluster admins (narwhal#659 follow-up, #686, #678, #687).

### Operator notes / behaviour changes

- Accounts with no team mapping and no `narwhal.io/team`-labelled namespace, including guests, can no longer access the
  portal. They land on `/access-denied`, and data APIs return `403 ACCESS_NOT_GRANTED` (narwhal#659 follow-up, #686).
  Add a team mapping or label a namespace before upgrading.
- Developers and viewers no longer see fleet-wide data (narwhal#678, #687). Alerts, incidents, traces, the service graph,
  and cluster overview are limited to their own namespaces. Namespace-less (node/cluster) alerts, workload distribution,
  and the live flow stream are cluster-admin only; traces without a namespace resource attribute are hidden from non-admins.
- No new configuration is required. Cluster-admin behaviour is unchanged.

### Changed

- Non-admin users whose effective visibility scope is empty (no matching `teamMappings` entry and no
  `narwhal.io/team`-labelled namespace) are now redirected after sign-in to a dedicated `/access-denied` screen
  without navigation or tenant data fetches, and portal data/action APIs respond with HTTP `403`
  (`code: "ACCESS_NOT_GRANTED"`) while session, sign-out, and health endpoints remain reachable (`D-PORTAL-UNMAPPED-DENY`,
  follow-up to narwhal#659).
- **Tenant scoping & admin-only enforcement across portal read/stream APIs (narwhal#678):**
  - `GET /api/alerts`, `GET /api/hero`, `GET /api/status`, and `GET /api/events` (plus `/api/my-apps`) now filter
    alerts through the shared `alertVisible` policy (`D-PORTAL-TENANT-SCOPE-678`): developers and viewers see only
    alerts matching their effective namespaces, while namespace-less (node/cluster-level) alerts are restricted to
    `cluster-admin`.
  - `GET /api/hero` filters ArgoCD applications, alerts, and node-pressure incidents (admin-only) and recomputes
    incident counters and mascot state from the caller's filtered set; `GET /api/status` retains platform/core
    component health while scoping Alertmanager `incidents`.
  - `GET /api/traces` scopes queries and post-filters results by each span's own OpenTelemetry namespace resource
    attribute (`k8s.namespace.name` / `service.namespace`) rather than `service.name`, projects non-admin rows strictly
    from the caller's matched in-scope span(s) (`serviceName`, `operationName`, `startTime`, `duration`, and in-scope
    `spanCount` — never foreign root-span metadata, retaining only `traceID` for traces the caller participated in),
    re-filters and re-projects cached span records against the caller's current scope on every read, denies traces with
    unknown/missing namespace to non-admins, and fails closed (`403`) on `?service=` queries when ownership evidence is
    incomplete (`snapshot.complete: false` or provider failure).
  - Scope cache fingerprints (`scopeFingerprint`) now include `all`, sorted resolved namespaces, sorted `argocdProjects`,
    and role class (`admin` / `non-admin`), and app/project-filtered cached routes (`/api/events`, `/api/my-apps`,
    `/api/governance/scorecard`) validate/re-filter cached records against the caller's current scope on read.
  - `GET /api/cluster` returns cluster aggregates (`summary`) and in-scope `namespaces` only for non-admins, redacting
    per-node (`nodes: []`) and control-plane (`controlPlane: []`) lists (`ClusterInfraView` hides those panels when
    redacted).
  - `GET /api/service-graph` and `GET /api/service-graph/[svc]` key Istio error-rate and p95 latency aggregation and
    L7/L4 deduplication by `(sourceNamespace, source, destinationNamespace, destination)`, namespace-qualify ArgoCD app
    mapping and status fallbacks (no cross-tenant name/status bleed), deny Hubble/namespace-less edges to non-admins,
    re-filter cached graph nodes/edges against current scope on read, and return `403` for out-of-scope, ambiguous, or
    incomplete-ownership `[svc]`.
  - `GET /api/catalog/[name]` and `GET /api/governance/scorecard` now enforce the shared `alertVisible` predicate on
    related alerts (hiding cross-namespace `labels.app` matches and namespace-less node alerts from non-admins).
  - **Signals without namespace (denied to non-admins):** OpenTelemetry traces lacking `k8s.namespace.name` /
    `service.namespace`, Hubble-derived service-graph edges without namespace labels, and namespace-less Alertmanager
    alerts remain visible to `cluster-admin` only and are denied (fail closed) to `developer` and `viewer`.
  - `GET /api/governance/distribution` and `GET /api/service-graph/stream` (SSE) are now `cluster-admin` only (`403`
    for `developer` and `viewer`); `DistributionView` shows an admin-required message and `ServiceMapView` hides the
    live stream toggle for non-admins.

## [1.1.16] - 2026-11-16

Patch release that fails closed for unmapped non-admin teams and updates dependencies, including upstream security fixes
(narwhal#659, narwhal#673).

### Fixed

- Non-admin users (`developer`, `viewer`) without a matching `teamMappings` entry in `config/role-filter.json`
  previously fell back to `roleDefaults` (`["*"]`), exposing every namespace across tenants and bypassing
  `narwhal.io/team` label-only isolation. Visibility scope now fails closed (`[]`) when no team mapping or
  `narwhal.io/team` namespace label matches, while preserving label-only namespace ownership (`narwhal#659`).

- **Operator note:** After upgrading, developers and viewers whose team has neither a `teamMappings` entry nor a
  `narwhal.io/team`-labelled namespace see an empty portal. Add a team mapping or label a namespace first.

### Security

- Updated `next` and `eslint-config-next` from 16.4.3 to 16.4.5, including upstream security fixes. Also updated
  `@opentelemetry/sdk-node` and `@opentelemetry/exporter-trace-otlp-http` to 0.301.0,
  `@opentelemetry/auto-instrumentations-node` to 0.83.0, `@opentelemetry/resources` to 3.1.0,
  `@opentelemetry/semantic-conventions` to 1.45.0, `@tanstack/react-query` to 5.107.1 and `lucide-react` to 1.70.0.
  Dev tooling: `@playwright/test` 1.65.0, `tailwindcss` 4.3.5, `vite` 8.3.6 and `@vitejs/plugin-react` 6.2.1
  (narwhal#673).

## [1.1.15] - 2026-11-14

Patch release that preserves ambiguous namespace-less service telemetry when a same-name Job cannot be
distinguished from a live service (narwhal#653).

### Fixed

- The Services graph could hide namespace-less telemetry for a live service when a Job in another namespace
  had the same name. It now retains the telemetry when workload evidence is incomplete or a same-name live
  controller or pod exists (narwhal#653).

## [1.1.14] - 2026-11-11

Patch release that fixes wrong "unknown" statuses and one-off Job noise on the Architecture page, and shows the
real last login in the Settings user list.

### Fixed

- Architecture page and onboarding platform figure showed "unknown" for most services, including IDP Portal,
  Keycloak and ArgoCD. Status now comes from Deployment/StatefulSet/DaemonSet readiness (then live pod readiness),
  with ArgoCD application health as the fallback (narwhal#646, narwhal#649).
- Services tab listed one-off workloads (backup and maintenance Job pods) as services. They are hidden, and the
  number hidden is shown on the tab (narwhal#646, narwhal#649).
- Settings user list lost the last login after logout. It now shows the newest Keycloak LOGIN event (column
  "Last Login"), falling back to the newest active session start, which is marked as session-sourced. This needs
  realm user events, which the provisioning script now enables with 30-day retention (narwhal#647, narwhal#648).

## [1.1.13] - 2026-11-11

Patch release that applies the dashboard design language to the Architecture page and the onboarding platform
figure, and fixes the Settings user list showing no groups and no last login.

### Changed

- Architecture page: infra and service views use the dashboard cards, meters and tokens. Services are grouped by
  observed namespace; the connection graph, filters, live overlay and selection detail are kept (narwhal#640,
  narwhal#642).
- Onboarding: the platform architecture figure is redrawn as five labelled layers of icon, role and status cards,
  with a stacked layer list on narrow screens. Missing status data shows as unknown (narwhal#640, narwhal#642).

### Fixed

- Settings user list showed `—` for every user's groups and "Never" for last login. Groups now come from Keycloak
  group memberships, and the time column shows the newest active session start (relabelled "Latest Session Start";
  the realm keeps no login history). The enriched list is cached for 60 seconds (narwhal#641, narwhal#643).

## [1.1.12] - 2026-11-10

Patch release applying the Figma app shell, dashboard and empty-state designs, and recording the 1.1.11 Portal
pin in the OSS versions snapshot.

### Changed

- The top navigation becomes a grouped left sidebar (Overview, Delivery, Governance, Get started) with the mascot
  wordmark, an active accent bar and a user card; a 56px top bar holds the breadcrumb, ⌘K search, health pill,
  language and theme. Below 1024px the sidebar opens as a drawer. Role gating is unchanged (narwhal#624, narwhal#634).
- The dashboard uses KPI cards with an icon chip and meter, a segmented time range, area charts, ArgoCD status
  pills with a stacked sync bar and alert rows with a severity stripe; cards without data show "No data" instead
  of zero-based labels (narwhal#624, narwhal#634).
- Shared empty and error states show the mascot in a dashed frame with a description and retry (narwhal#624, narwhal#634).
- Update the Portal version in the OSS versions snapshot to 1.1.11 to match the GitOps pin (narwhal#623).

## [1.1.11] - 2026-11-10

Patch release applying the Figma login design and recording the 1.1.10 Portal pin in the OSS versions snapshot.

### Changed

- `/login` uses the Figma split layout: a brand panel (mascot, headline, tagline, contour waves) beside a
  left-aligned 400px column for the redirect spinner or the mock role picker; below 900px only a compact
  wordmark remains (narwhal#619, narwhal#620).
- Update the Portal version in the OSS versions snapshot to 1.1.10 to match the GitOps pin (narwhal#618).

## [1.1.10] - 2026-11-10

Patch release adding alerts and notification channel settings, preserving partial Trivy results on refetch,
and recording the 1.1.9 Portal pin in the OSS versions snapshot.

### Added

- Alerts page with severity filtering, suppression state, degraded upstream handling and navigation (narwhal#610).
- Cluster-admin notification settings for Slack/Mattermost and SMTP, write-only credentials, channel toggles and a five-minute test alert. Existing GitOps-owned resources are updated only; stored values are never returned (narwhal#610).

### Fixed

- Keep the partial-results notice visible after Trivy data is refetched (narwhal#615).

### Changed

- Update the OSS versions snapshot to Portal 1.1.9 for the 1.1.9 GitOps pin (narwhal#608).

## [1.1.9] - 2026-11-09

Patch release with no user-facing feature change beyond a dependency bump and one new entry in the OSS
versions list. `lucide-react` is bumped to 1.66.0, and the OSS versions page lists the `skopeo`
(trivy-db-refresh) image used by the Trivy DB mirror refresh job (#603). The other changes under `portal/`
since `portal/v1.1.8` are the OSS snapshot regenerated by the 1.1.8 pin (#596), the third-party notices
regenerated for the `lucide-react` bump (#539), and a documentation wording update in
`portal/docs/local-dev.md` for the 8GB worker VM memory (#597).

### Changed

- Bump `lucide-react` from 1.58.0 to 1.66.0 (narwhal#539).
- OSS version snapshot (`src/data/oss-versions.generated.json`) gained the `skopeo (trivy-db-refresh)` 1.22.2
  entry in the Registry section (narwhal#603).
- OSS version snapshot regenerated for the 1.1.8 pin: IDP Portal 1.1.7 -> 1.1.8 and Next.js 16.4.2 -> 16.4.3
  (narwhal#596).
- `THIRD-PARTY-NOTICES.md` regenerated for `lucide-react` 1.66.0 (narwhal#539).

## [1.1.8] - 2026-11-06

Patch release: DORA recent-deployment rows without a recorded Argo CD phase show a dash instead of an
"Unknown" badge, and `next` is bumped to 16.4.3. The only other changes under `portal/` since
`portal/v1.1.7` are the OSS version snapshot regenerated for the 1.1.7 pin (#580) and a comment update in
`portal/src/lib/k8s-client.ts` describing the namespaced cert-renew RBAC that now lives in gitops (#592).

### Fixed

- **Governance: DORA history rows labelled "Unknown" (narwhal#581)**: Argo CD records
  `status.operationState.phase` only for the latest operation, and older `status.history` entries have no
  status field, so the recent-deployments table showed an "Unknown" badge that read like a real status.
  Those rows now render "—" with a tooltip (KO/EN) citing the Argo CD field constraint; Succeeded and Failed
  keep their badges.

### Changed

- Bump `next` from 16.4.2 to 16.4.3 (next-react group, narwhal#486).

## [1.1.7] - 2026-11-05

Patch release: DORA lead time preserves minute precision, the Trivy DB status card lists every mirrored
security data source with trivy-checks shown as pinned, the Pretendard font OFL license ships in the
portal container image, and `@opentelemetry/semantic-conventions` is bumped to 1.44.0. The only other change
under `portal/` since `portal/v1.1.6` is the OSS version snapshot regenerated for the 1.1.6 pin (#561).

### Added

- **Security: Trivy DB card lists every mirrored source (narwhal#570)**: the API, the status card and the
  copyable airgap update commands are now driven by a single source list
  (`portal/src/lib/trivy-db-sources.ts`) covering consumer, tag and per-source SLO. Sources without an age
  SLO (such as `trivy-checks:1`, which upstream froze on 2025-10-09) render as "pinned" rather than "stale".

### Fixed

- **Governance: DORA lead time rounded to 0 hours (narwhal#566)**: on 1.1.6, git-sourced apps displayed a
  lead time of "0 h" because the API rounded push-to-sync durations to 0.1 hours. The API now exposes
  `leadTimeMinutes` alongside the backward-compatible `leadTimeHours`, and the summary card and per-app table
  format durations with minute precision (`<1 min`, `N min`, `N h M min`). A real 0 is formatted
  differently from null (which still denotes "N/A" for Helm-sourced apps).

### Changed

- **Packaging: Pretendard OFL license shipped in portal image (narwhal#572)**: the container image now
  copies `src/app/fonts/OFL.txt` to `/app/OFL-Pretendard.txt` alongside the bundled Pretendard font files to
  fulfill SIL Open Font License 1.1 redistribution terms, and font provenance is recorded in `NOTICE`.
- Bump `@opentelemetry/semantic-conventions` from 1.43.0 to 1.44.0 (narwhal#538).

## [1.1.6] - 2026-11-05

Minor-feature patch release: the Settings and Security pages gain the OSS updates tab, the Trivy DB
card and the Kyverno policy explanations, and several fixes reach the cluster that 1.1.5 still lacks.
Everything since `portal/v1.1.5` is covered here.

### Added

- **Settings: OSS updates tab (narwhal#552)**: shows each component's declared version (a committed
  snapshot of `VERSIONS.md`), the running version (Argo CD `targetRevision` plus the image tags and CVEs
  Trivy already reports) and the upstream latest. Versions younger than 7 days are marked as cooling and
  cannot be requested (409). The request button opens an upgrade issue carrying the
  `narwhal-version-upgrade` checklist; the portal never changes the cluster. The upstream lookup is
  public GitHub releases, off unless `OSS_UPDATES_UPSTREAM=true`, cached for 6 hours (10 minutes after
  a failure), and shows "unknown" when off or failing.
- **Security: Trivy DB status card (narwhal#554)**: cluster-admin only and read-only. Shows trivy-db,
  trivy-java-db and trivy-checks in the Harbor `trivy-db` project with tag, age and fresh / stale /
  missing status against the 7-day SLO, plus scanner health (report count, newest report, recent
  `BackoffLimitExceeded` events). A stale or missing DB shows the copyable airgap update commands. Harbor
  is read anonymously; the optional `HARBOR_URL` defaults to
  `http://harbor-core.devtools.svc.cluster.local`. The new `GET /api/security/trivy-db` backs it.
- **Settings: Kyverno policy explanations and detail panel (narwhal#547)**: a legend explains Enforce
  (blocked at admission) and Audit (allowed, recorded in a PolicyReport). The action now comes from each
  rule's `validate.failureAction` (Kyverno 1.13+) and shows `Mixed` when rules disagree. Clicking a policy
  name opens a panel with description, category, severity, scope, action, background setting and
  per-rule details. The policy API gains backward-compatible fields.
- **Local dev cluster**: `make dev-cluster` / `pnpm dev:cluster` runs a live-edit portal build in the
  local cluster, generated from the chart (narwhal#529). Development tooling only.

### Fixed

- **Cost: the service breakdown was empty (narwhal#559)**: it grouped pods only by
  `app.kubernetes.io/instance` from `kube_pod_labels`, which kube-state-metrics did not export, so every
  workload was excluded. Pods are now attributed by instance, name, app, k8s-app, owner workload or
  pod-name stem (fallback rows are marked as estimated), anything left lands in a visible
  "(unclassified)" row, and row totals equal the cluster total. Needs the kube-state-metrics
  label allowlist from the same PR.
- **Certificates: renew always failed (narwhal#549)**: the portal patched `spec.renewBefore: "1s"`,
  which the cert-manager webhook rejects (minimum 5 minutes), and its ServiceAccount could only `get`
  certificates. Renewal now works as `cmctl renew` does: set the `Issuing` condition to `True`
  (`ManuallyTriggered`) through the `/status` subresource, retrying once on a 409; the spec is never
  written. The `narwhal-portal` ClusterRole gains only `update` on `certificates/status`, which needs
  the GitOps chart change to be live. Errors are logged with status and path, and Kubernetes
  403/404/409/422 map to their own responses instead of a bare 500.
- **Governance: DORA lead time always showed "—" (narwhal#556)**: Gitea API calls through the gateway
  were redirected to the Keycloak login, so commit timestamps never parsed. A narrow APISIX rule,
  `gitea-portal-api`, now lets only GET/POST on the `narwhal-gitops` commit, contents and pulls paths
  with a `token <40 hex>` Authorization header reach Gitea. The same redirect also broke the namespace
  request PR flow. Apps sourced from a Helm chart version now show "N/A (Helm chart)" instead of "—".
  A redirect or HTML answer is logged once as a likely SSO redirect (narwhal#557 corrects its hint to
  check the `gitea-portal-api` rule and `GITEA_URL`).
- **Security: partial Trivy results (narwhal#505, narwhal#514)**: a mid-walk 429 or expired continue
  token no longer ends the vulnerability report walk silently. Pages retry up to 3 times honouring
  `Retry-After`; if later pages still fail the page shows a partial-data notice, the APIs set
  `X-Partial-Data`, and partial results are never cached.
- **Settings: permission matrix sticky columns see-through (narwhal#546)**: the group-name, header and
  Save columns are opaque while the matrix scrolls horizontally.
- **Security: Trivy DB display path (narwhal#550)**: the Trivy DB registry shown in the portal now
  matches `trivy-operator.yaml` instead of a nonexistent `/library/trivy-db` path.
- **Hardcoded Korean (narwhal#512, narwhal#513, narwhal#527)**: ten untranslated UI strings in the DORA
  metrics, resource drawer and KISA checklist now use the `ko`/`en` message keys.

### Changed

- OpenTelemetry moves to SDK 3.0: `auto-instrumentations-node` 0.82.0, `sdk-node` and
  `exporter-trace-otlp-http` 0.300.0, with `@opentelemetry/resources` 3.0.0 (narwhal#487). `shadcn`
  4.23.0 (narwhal#540); dev tooling `@tanstack/react-query-devtools` 5.105.0, `eslint-config-next`
  16.4.3, `vite` 8.3.5 (narwhal#517). The unused `hono` and `@hono/node-server` overrides are removed
  (narwhal#543).

## [1.1.5] - 2026-10-31

Patch release: fix the deferred `body-parser` advisory (narwhal#470) and ship the Portal
reader fixes merged in narwhal#503. Everything since `portal/v1.1.4` is covered here.

### Fixed

- **Runtime Events: the priority filter matched nothing (narwhal#503)**: falcosidekick writes the Loki
  `priority` label capitalised (`Critical`), but the reader selected the lowercase value, so the
  priority filter and the critical-alert query matched no stream. The selector now accepts both
  spellings, and the JSON `null` fields Falco emits for processes outside Kubernetes are treated as
  absent.
- **Runtime Events tab hidden on Ubuntu 26.04 (narwhal#503)**: the tab was replaced by an
  "unavailable on Ubuntu 26.04" notice whenever a node ran 26.04. Falco 0.45.1 is enabled on every
  provider, so the tab is always shown and the node-OS probe and the
  `security.runtimeEvents.unavailable2604` text are removed. The KISA control notes no longer say
  Falco is disabled; they make no claim about a live cluster.

### Security

- `body-parser` 2.3.0 to 2.4.0 fixes GHSA-27r3-jxmh-p828 (narwhal#470, narwhal#505).
  Express 5.3.0 already permits it through `^2.3.0`; no override was added. Its required
  dependency `qs` moves from 6.16.0 to 6.16.1 to satisfy `^6.16.1`. Both versions passed the
  7-day lockfile cooling gate.
- `systeminformation` 5.33.15 to 5.33.16 fixes GHSA-3f7j-7jwc-fxfj (high, via
  `@opentelemetry/instrumentation-host-metrics`, whose `^5.31.6` range allows it). 5.33.16 was
  published 2026-10-19 and has passed the cooling gate; the newer 5.33.17-5.33.19 have not and
  are not taken. No other package versions changed.

## [1.1.4] - 2026-10-28

Patch release: the Security page shows vulnerability data again on clusters with many scanned
images, and `fast-uri` is upgraded past its published advisories (narwhal#470). Everything since
`portal/v1.1.3` is covered here.

### Fixed

- **Security page showed no vulnerabilities (narwhal#501)**: the Trivy reader listed every
  VulnerabilityReport in one unbounded request. On Kakao, 109 reports came to 10,761,256 bytes, over
  the client's 8 MiB response cap, and the page fell back to empty results. The summary, the
  workload table and the image view now page through the list 20 reports at a time (up to 50
  pages); a truncated list logs a warning and keeps what was fetched.

### Security

- `fast-uri` 4.2.2 to 4.2.3 (GHSA-w74x-v6f9-j3xg, GHSA-wjr9-j795-jxcr, GHSA-xhq7-xmqj-769h,
  GHSA-333x-w4r6-6crp), the transitive copy under `ajv` 8.20.0 (narwhal#492). The existing
  `>=3.1.2` override already allowed it and nothing else in the lockfile moved; `pnpm audit --prod`
  no longer reports `fast-uri`.

### Known issues

- `body-parser` (GHSA-27r3-jxmh-p828, fixed in 2.4.0) is not upgraded yet: its fix clears the 7-day
  lockfile cooling gate after 2026-10-29 11:39Z. It ships in the next patch (narwhal#470).

## [1.1.3] - 2026-10-27

Patch release: the permission management table shows the tools each role actually reaches.
Everything since `portal/v1.1.2` is covered here.

### Fixed

- **Settings > Permission management looked empty (narwhal#479)**: the table showed only each
  group's `allowed_tools` attribute, while `/api/tools` grants the role defaults from `tools.ts`
  plus that attribute. On a realm where no group carries the attribute, every cell was a faint
  "off" icon. Role defaults are now shown checked and locked (they cannot be removed here);
  attribute grants stay editable.

### Known issues

- `fast-uri` and `body-parser` are not upgraded yet (narwhal#470). The `fast-uri` fix cleared the
  7-day lockfile cooling gate on 2026-10-26 22:51Z but is left out on purpose: 1.1.3 ships only the
  permission management fix, and the lockfile upgrade gets its own reviewed change. `body-parser`
  (GHSA-27r3-jxmh-p828) clears the gate after 2026-10-29 11:39Z. Both ship in a later patch.

## [1.1.2] - 2026-10-27

Patch release: fixes the empty cluster-infrastructure view on production clusters and upgrades
`zod` and `undici` past published advisories (narwhal#470). Everything since `portal/v1.1.1` is
covered here.

### Fixed

- **Architecture > Cluster infrastructure showed 0 nodes, pods and namespaces (narwhal#475)**:
  `/api/cluster` and `/api/domain/clusters` read the in-cluster token only from the `K8S_SA_TOKEN`
  env, which production does not set (it mounts a projected token file), so they called the API
  server with an empty bearer, got 403 as `system:anonymous`, and rendered the failures as empty
  lists. The in-cluster `K8S_API_SERVER` + `K8S_SA_TOKEN` pair now reads the projected token like every
  other Kubernetes route; any other pairing still reads only its own env, so this pod's token is
  never sent to another API server.

### Security

- `zod` 4.6.7 (GHSA-mrhx-c7xc-m3w5) and `undici` 7.30.1 (GHSA-xjj6-f33q-929c,
  GHSA-4qv8-2p9j-2685, GHSA-f72v-6w45-q472), by raising `shadcn` to ^4.22.0 (narwhal#471).

### Changed

- `@opentelemetry/auto-instrumentations-node` 0.81.0 to 0.81.2 (narwhal#469).

### Known issues

- `body-parser` (GHSA-27r3-jxmh-p828, fixed in 2.4.0) and `fast-uri` (GHSA-w74x-v6f9-j3xg,
  GHSA-wjr9-j795-jxcr, GHSA-xhq7-xmqj-769h, GHSA-333x-w4r6-6crp, fixed in 4.2.3) are not upgraded
  yet: their fixes are younger than the 7-day lockfile cooling gate (fast-uri clears it after
  2026-10-26 22:51Z, body-parser after 2026-10-29 11:39Z). They ship in a later patch
  (narwhal#470).

## [1.1.1] - 2026-10-23

Security patch release: upgrades Auth.js and several transitive packages past published advisories
(narwhal#459), and picks up the Next.js 16.4 and React 19.3.1 updates. Everything since
`portal/v1.1.0` is covered here.

### Upgrade notes

- **An OAuth login in flight during the rollout fails once and must be retried (narwhal#461)**:
  `@auth/core` 0.41.3 binds the state, nonce and PKCE check cookies to the provider that created
  them and rejects a callback whose cookie was issued for another provider. Cookies created before
  the upgrade do not carry that binding, so a sign-in started on the old pod and completed on the
  new one fails; signing in again works. Existing sessions are unaffected.

### Security

- **Auth.js upgraded past critical advisories (narwhal#461, fixes narwhal#459)**: `next-auth`
  5.0.0-beta.30 to 5.0.0-beta.32 and `@auth/core` ^0.34.3 to ^0.41.3 (GHSA-8fpg-xm3f-6cx3,
  GHSA-7rqj-j65f-68wh, GHSA-xmf8-cvqr-rfgj). The new regression test
  `src/lib/auth.fail-closed.test.ts` runs the real next-auth: with `AUTH_SECRET` missing, beta.30
  returned an error object instead of `null` (so `if (!session)` gates did not trip) and
  `/api/alerts` answered 200; beta.32 returns `null` and the route answers 401.
- **Transitive packages updated in the lockfile (narwhal#461)**: `form-data` 4.0.6
  (GHSA-hmw2-7cc7-3qxx), `ws` 8.22.0 (GHSA-96hv-2xvq-fx4p) and `protobufjs` 8.8.1
  (GHSA-wcpc-wj8m-hjx6).
- **Next.js 16.4.2, React and React DOM 19.3.1 (narwhal#457)**: picks up the Next.js 16.4.1
  security fixes. The image optimizer is disabled in this app.

### Changed

- `lucide-react` 1.7.0 to 1.58.0 (narwhal#463); the `CheckCircle2` icon has a redrawn check mark.
- `shadcn` 4.19.0 to 4.21.4 (narwhal#353) and `@base-ui/react` 1.8.0 to 1.9.0 (narwhal#354), both
  runtime `dependencies`.
- Development tooling only, not part of the image (narwhal#454): Playwright, Vitest, Vite,
  `@vitejs/plugin-react`, `@types/node`, `eslint-config-next` and TanStack Query devtools.
- `SECURITY.md` and `SECURITY-ko.md` list 1.1.x as the supported series (narwhal#451).

### Known issues

- `braces` (GHSA-vfj7-8cjw-p6xm) has no patched version. It is reached only through the shadcn CLI
  (`shadcn > fast-glob > micromatch`) and is left as is (see `docs/lessons-log.md`).

## [1.1.0] - 2026-10-15

The portal now lives in the `dasomel/narwhal` monorepo under `portal/`; release tags are `portal/vX.Y.Z`
(this is the first release cut from there, and versions up to 1.0.17 keep their old `dasomel/narwhal-portal`
links).

Hardens the portal's production posture (authenticated Valkey TLS, explicit OpenBao auth mode,
call-time dependency configuration checks, namespace-scoped reads) and makes provider failures visible
instead of silently healthy: write routes now distinguish rejected, credential and
outcome-unknown results, and platform status no longer reports green when ArgoCD or Alertmanager
cannot be read. Tags 1.0.18 and 1.0.19 were pushed but never published as GitHub Releases (the
cosign signing step failed), so this section covers everything since 1.0.17.

### Upgrade notes
- **Production now requires authenticated Valkey TLS (#205, 63603d6, after 1.0.19)**: in
  production `getValkey()` / `getLiveValkey()` (`src/lib/valkey.ts`) throw unless all of
  `VALKEY_URL=rediss://…` (TLS scheme), `VALKEY_TLS=true`, `VALKEY_USERNAME` (a dedicated ACL
  user, `portal` in the docs) and `VALKEY_PASSWORD` are set. `VALKEY_INSECURE_PRODUCTION` is no
  longer supported: if it is defined at all (even `false`) the client refuses to initialize.
  See `docs/security-clean-install.md` section 6. The failure is lazy (first Valkey use, not
  process start). With `VALKEY_URL` or `VALKEY_PASSWORD` set, `/api/health/ready` then returns
  503 (`Critical cache dependency unavailable`), so a rollout never becomes Ready (unless
  `AUTH_MOCK=true`). If neither variable is set, the probe skips Valkey and returns 200 and the
  cache is silently disabled: the cache helpers catch the secure-config error (`cacheGet` and
  `cacheGetWithMeta` return null; `cacheSet` and `cacheDel` are no-ops). Code that calls `getValkey()` /
  `getLiveValkey()` directly sees the throw; live-event persistence and idempotency claims catch
  it and degrade to process-local in-memory paths: idempotency claims are not shared across
  replicas (duplicate ingests are no longer deduplicated between replicas), live events are
  neither persisted nor published to other replicas, and both are lost on restart. **The narwhal
  GitOps chart at the time of writing still injects `VALKEY_INSECURE_PRODUCTION=true` and a
  plaintext `redis://` URL against a Valkey without TLS or ACL, so this portal version cannot be
  deployed with it until narwhal provides Valkey TLS + ACL and matching bindings.** Images up to
  1.0.19 predate this change.
- **OpenBao static-token mode must be explicit in production (#237, 5319ada)**: in production,
  if `OPENBAO_TOKEN` is the credential in use, `OPENBAO_AUTH_METHOD` must be exactly `token`;
  otherwise the first OpenBao request throws `Static credentials require explicit
  OPENBAO_AUTH_METHOD=token in production`. The check is lazy (first OpenBao token request, not
  startup) and only reached when the auth method resolves to `token`, i.e. `OPENBAO_AUTH_METHOD`
  is unset and no Kubernetes service-account JWT is mounted. Deployments on Kubernetes auth
  (`OPENBAO_AUTH_METHOD=kubernetes`, or a mounted JWT with the variable unset) are unaffected. In
  production any other non-empty `OPENBAO_AUTH_METHOD` value (for example a typo) now throws
  `OPENBAO_AUTH_METHOD must be kubernetes or token`.
- **OpenBao address must be HTTPS on every request (#236, 513a71a)**: `OPENBAO_ADDR` is now
  validated before each outbound request and the Kubernetes login, not once per process, and a
  non-HTTPS address in production is rejected every time.
- **Dependency URLs no longer fall back to localhost in production (since 1.0.17)**:
  `ARGOCD_URL`, `ALERTMANAGER_URL`, `PROMETHEUS_URL`, `TEMPO_URL`, `LOKI_URL`,
  `APISIX_ADMIN_URL`, `OPENBAO_ADDR`, `GITEA_URL` and `KEYCLOAK_INTERNAL_URL` no longer fall back
  to localhost or in-cluster defaults (752273f, 451bec8). `getDependencyUrl()`
  (`src/lib/config.ts`) throws `Missing required production configuration: <NAME>` on first use,
  not at startup, and `/api/health/ready` does not flag missing values (`validateRuntimeConfig` lists
  them only as `missingOptional`; it does flag a non-https `GITEA_URL`). `SLO_CHAIN_START` is now also required in production for federated
  logout, which throws without it (451bec8). `KEYCLOAK_ISSUER` and `GITEA_URL` must be `https://`
  (#110); `APISIX_API_KEY_READONLY` no longer falls back to the admin `APISIX_API_KEY` (#112).
- **Kubernetes token source (since 1.0.17)**: the portal reads a projected, short-lived
  service-account token (`K8S_SA_TOKEN_FILE` to override the path) instead of `K8S_SA_TOKEN`,
  which is dev-only; setting `K8S_TOKEN_AUDIENCE` enables the client-side `aud` check and an
  unset value logs a warning in production (eeb5ef6, 1a27584, 1e35aac).
- **`/api/events/ingest` is stricter for existing event producers (3344e78)**: a request
  without both `idempotency_key` and `source_event_id` returns 400 (the v1.0.17 route had no such
  requirement); a request whose `X-Producer-Id` (or `X-Ingest-Producer`) header differs from the
  body `source` returns 403; and bodies over 64 KiB (65,536 bytes, `MAX_INGEST_BODY_SIZE_BYTES`) return
  413.
- **The live event stream is default-deny (3c4c36a)**: a namespace-less event is shown to
  non-admin users only if it declares `visibility: "cluster"`; otherwise it is hidden. The
  title/description namespace-regex fallback is removed. Producers must set `resource.namespace`
  or `visibility: "cluster"`. Viewers and guests also still lose `node` events and events whose
  description contains "secret".
- **`/api/secrets` fails visibly and returns no key names (0c715f5)**: a metadata read failure
  now returns 502 `{error, degraded: true}` instead of `[]`, and the response no longer carries
  secret key names. The inventory now reads only KV v2 `secret/metadata/<path>` and no longer
  reads `secret/data/<path>`, so the portal's OpenBao policy needs metadata list and read but not
  data read for this route.
- **`/api/cluster` rejects guests (de1edc7)**: it now requires `cluster-admin`, `developer` or
  `viewer` and returns 403 for other roles; it previously only required a session.
- **Kubeconfig download needs `K8S_API_SERVER` in production (506c279)**: downloaded kubeconfigs
  use `getExternalK8sApiServer()`, which throws without `K8S_API_SERVER` in production.
  `KUBERNETES_SERVICE_HOST` alone is not enough, because it is the unreachable in-cluster address.
- **Namespace visibility follows the `narwhal.io/team` label (b094fb7)**: the label is the
  source of ownership; the namespace patterns in `config/role-filter.json` still resolve as a
  migration path for unlabelled namespaces. `POST /api/namespaces` no longer creates a namespace:
  it commits a tenant manifest to the GitOps repo and opens a pull request (058426e).
- **API consumers**: `/api/governance/audit` is a deprecated alias of the new
  `/api/governance/events` (#161); `/api/health/status` now requires `cluster-admin`
  (a286598); federated logout is POST-only (ab69c4f); catalog rollback, silence create/delete and
  the sync routes return new status codes and fixed error texts (see Changed).
- **Release pipeline**: tag builds sign the image with cosign and fail without the
  `COSIGN_PRIVATE_KEY` / `COSIGN_PASSWORD` repository secrets (3026d23,
  `docs/cosign-signing-setup.md`); the GitHub Release is only created after a successful
  publish (#120).

### Added
- **Aggregate dependency health for every user**: `GET /api/health/summary` returns only
  `{state, observedAt}` to any authenticated role and drives a navigation indicator (#155); the
  detailed, admin-only `/api/health/dependencies` exposes a unified dependency contract with
  informer owner state and live-pipeline counters (#148, #158, #162).
- **`/api/governance/events`**: returns `{items, truncated, evidenceKind, freshness}`; the old
  audit-named route keeps its bare array and adds `X-Truncated`, `Deprecation` and `Link`
  headers (#161).
- **Namespace requests by pull request**: namespaces are requested as a GitOps PR carrying a
  `narwhal.io/team`-labelled Namespace, a RoleBinding and a ResourceQuota (058426e).
- **Canonical event envelope and live-stream resilience**: idempotent `/api/events/ingest` with
  per-producer credentials, rate limiting (429 + `Retry-After`) and payload bounds (3344e78),
  deterministic `Last-Event-ID` replay (#154), and a single-owner Kubernetes informer through a
  Valkey lease (#158).
- **Preflight routes**: `/api/dr/restore-preflight` (#215), `/api/storage/pvc-expansion-preflight`
  (#221, #222, #223) and `/api/settings/role-mappings/preflight` for orphaned namespace patterns
  (2d44836); the DR and PVC verdicts derive from server-observed facts, never from caller input.
- **Cluster registry foundation**: `config/clusters.json`, a `cluster_id` scope dimension,
  admin CRUD at `/api/settings/clusters` (681f845) and a `/api/domain/clusters` pilot (e36a68d).
- **Shared outbound HTTP client**: timeout, bounded retry and correlation for every provider
  client, including the Kubernetes apiserver (#143, #149, #151, #152).
- **Operator UX**: a shared loading/error/empty/partial/stale `DataState` with retry and
  freshness (#156, #159), cache capture time (#157), vulnerability-DB freshness (584b797), portal
  version and commit in the navigation (#150), and a "More" menu for overflowing navigation
  items (#167).
- **User role groups on create**: `POST /api/settings/users` accepts an optional `groups` array
  limited to the portal role allowlist (#132).
- **Supply chain**: cosign image signing (3026d23), OSS attribution and a Dependency SBOM
  (2e3ffc5), and a 7-day lockfile cooling gate for new dependencies (69a4be5).

### Changed
- **Catalog rollback classifies the outcome (#245, b0c304e)**: a 4xx from ArgoCD returns that
  status with `outcome: "rejected"`; a missing or rejected ArgoCD token returns 503; a 403
  returns 403; a timeout, network error, 5xx, 408 or unreadable 2xx returns 502 with
  `outcome: "unknown"` and "verify the application status before retrying". The ArgoCD POST is
  sent once and never auto-retried. A 2xx must carry the requested Application.
- **Silence create and delete classify the outcome (#247, 486ac21)**: delete of an absent
  silence returns 404; other 4xx return that status with `outcome: "rejected"`; 401 returns 503;
  403 returns 403; timeout, network, 408, 5xx or a malformed 2xx return 502 with
  `outcome: "unknown"`. A created `silenceID` is accepted only if it matches
  `[A-Za-z0-9_-]{1,128}` and is used as returned (never trimmed).
- **Silence DELETE ownership lookup (#244, 3b4d320)**: only an explicit Alertmanager 404 reads as
  "not found"; 401 returns 503, 403 returns 403, and other failures or a malformed lookup
  response return 502 without sending the DELETE.
- **ArgoCD write precondition (#243, 7f7dc1c)**: a provider failure while checking the
  application no longer reports `Application not found`; credential errors now reach the 503
  mapping.
- **Fixed error texts on sync routes (#246, 1b9984d)**: `argocd/sync` and `catalog/[name]/sync`
  return `ArgoCD sync failed` (502) instead of the raw upstream error, and the
  `operation.failed` event description is a fixed text; rollback events follow the same rule
  (#245).
- **`/api/status` reports `degraded` when ArgoCD or Alertmanager cannot be read (#242,
  2d38cbb)**: the failed provider appears as an `unknown` component (summary suffix
  `status incomplete: … unreachable`) and the result is not cached. Environments where ArgoCD or
  Alertmanager are unconfigured or unreachable, such as local development, now show `degraded`
  instead of `healthy`.
- **Dependency health fails closed**: an aggregate over zero dependency statuses is
  `unavailable`, not `ok` (#240), and a cached snapshot is trusted only after shape validation
  (#241).
- **Kubernetes list contract (#239, 70bc7ce)**: a 200 response without an `items` array is an
  error, and `limit` / `maxPages` must be positive safe integers or the call is rejected before
  any request. Cluster-wide inventory lists are bounded and report `truncated` (4c146b7).
- **Outbound HTTP bodies and cancellation**: decoded response bodies over 8 MiB fail with a
  `response-too-large` error unless the caller raises the limit (#225); retry backoff and native
  `RequestInit` cancellation now follow the caller's abort (#228, #238).
- **Live events**: recovery after Kubernetes watch expiry applies a bounded paginated snapshot
  (#226), and the informer waits for each event to be accepted so submissions stay in flight one
  at a time (#229).
- **ArgoCD sync and rollback verify convergence**: a 2xx only means accepted; the routes re-read
  the application and return `pending: true` until `operationState` converges (#121).
- **Required CI checks**: `Portal critical regression gate` is an always-reported required check
  (#227), and a SHA-bound `independent-review` commit status gates merges (#213).
- **Dependencies**: Next.js 16.3.5 with React 19.3.0 (#170), `@base-ui/react` 1.8.0 (#212),
  `@grpc/grpc-js` 1.14.5 (#211), and grouped GitHub Actions bumps for `upload-artifact`,
  `download-artifact`, `setup-node` and `harden-runner` (#230, #233, #232, #231).
- **Kaniko build evidence (#169, 5df84c5)**: `kaniko-build.sh` writes `build-evidence/<tag>.json`
  after a successful build and fails on a malformed image digest.
- **Typed Kubernetes credential errors (#176, d34cc54)**: `K8sCredentialError` is raised when a
  401 survives the token refresh, or on a 403.

### Fixed
- **Read-scope gaps closed**: `/api/cost` (2ba8fc5) and its detail and trend routes (#123),
  `/api/governance/resources` (#147), `/api/k8s/events` (#145), `/api/catalog`, `/api/events`,
  `/api/k8s/pods` and `/api/k8s/resource` (1e48371) now filter by the caller's visible
  namespaces before any cache or upstream read; cache keys carry a scope fingerprint.
- **Silence create is scoped**: non-admin callers need an exact-match `namespace` matcher inside
  their scope, catch-all regex matchers are rejected for every role, and an optional
  `Idempotency-Key` is honored (5ce9516).
- **ArgoCD writes check the destination namespace**, not only the project (d00eff1).
- **Unknown is not healthy**: scorecards distinguish `unavailable` evidence from `fail` (#118),
  DORA reports unknown deployment outcomes and a `null` change-failure rate instead of a false
  zero (#117), cost fails closed on unconfigured pricing (e2ad866), and partial Keycloak group
  fetches are no longer presented as complete (#113).
- **Expired list continue token**: a bounded Kubernetes list restarts once on 410 (#141).
- **Kubeconfig server**: downloaded kubeconfigs use the external API server address, not the
  in-cluster one (506c279).
- **Image builds**: the Kaniko path pushes a SHA-named source tag with a digest file instead
  of `latest` (a874ac7), and the standalone image traces `@swc/helpers` and is smoke-booted in CI
  (#164).
- **Outage recovery for live events (#183, 1b8f5ca)**: a numeric pre-outage `Last-Event-ID`
  cursor now replays from the process-local ring, idempotency claims made during a Valkey outage
  survive Valkey recovery within the same process, and a 410 relist ingests the page it already
  fetched (only the most recent event, not a full backlog).

### Security
- **Authenticated Valkey TLS in production** (#205; see Upgrade notes).
- **Logout hardening**: federated logout is POST-only with CSRF protection (ab69c4f), fails
  closed on unverifiable requests (a6fc4ec), and the redirect-host allowlist is anchored so
  `maliciouslocalhost` no longer passes (25381e1).
- **Credential handling**: ArgoCD and Gitea tokens are read per call and fail closed on 401
  (#134), APISIX has no silent admin fallback (#112), and Keycloak admin token acquisition is
  standardized (ea96a9a).
- **Error text no longer leaks upstream hosts or bodies** from sync and rollback responses and
  from the `operation.failed` event stream (#245, #246).
- **Privileged node tuning**: hardened Job execution boundary (3a209f7), a preflight dry-run
  (#105) and exact invocation approval at runtime (#90).
- **Supply chain**: build images pinned by digest and no Gitea password in URLs (#109), CI egress
  blocked on install-time jobs (#106), and `kubelogin` pinned with checksum verification (#104).

### Known issues
- **UI rollback returns 400**: the catalog UI (`service-actions.tsx`) sends `{historyId}` but
  `POST /api/catalog/[name]/rollback` requires `{id}`. This predates 1.0.17.
- **No idempotency for rollback**: the route never claims an `Idempotency-Key`, so retrying with
  the same key after an `outcome: "unknown"` response re-sends the ArgoCD POST.
- **Silence DELETE ownership** compares only `createdBy`, not team scope, and there is a
  lookup-to-delete race window (Alertmanager allows changing `createdBy` on the same id).
- **`ArgoForbiddenError` text** includes the ArgoCD project or namespace in the 403 body.
- **Groups and users PATCH** (`/api/settings/groups`, `/api/settings/users/[pk]`) return a bare
  `{ok: true}` without reading the change back.
- **Cluster-wide views for viewers**: `/api/governance/distribution` (any authenticated user) and
  `/api/service-graph` (viewer and above) apply no scope filter.
- **Cluster registry is a foundation only**: `/api/settings/clusters` persists to
  `config/clusters.json`, and when that file is not writable the change stays in memory and is
  lost on restart; the Kubernetes and ArgoCD clients do not yet resolve a cluster per request.
- **Kaniko build path is unverified (a874ac7)**: the Job placeholder `__HARBOR_DESTINATION__`
  became `__HARBOR_DESTINATION_PREFIX__` (tag appended from `HARBOR_TAG`), and the commit states
  it was not verified with a real in-cluster build and push (unverified).
- **Release notes link**: `release.yml` links `CHANGELOG_ko.md`, but the file is
  `CHANGELOG-ko.md`.

## [1.0.17] - 2026-08-09

Fixes platform tool health checks, node role detection, compliance reporting metrics, and node infrastructure audit queries, while introducing an automated release notes workflow for version tags.

### Added
- **Automated GitHub Release workflow**: Added `.github/workflows/release.yml` to publish release notes from `CHANGELOG.md` on `v*` tag pushes, failing the job if the version section is missing.

### Fixed
- **Configurable platform tool base domain**: Added `CLUSTER_BASE_DOMAIN` (and `NEXT_PUBLIC_CLUSTER_BASE_DOMAIN`) environment variable support (defaulting to `local.narwhal.internal`) to resolve tile URLs on custom cluster domains without in-pod NXDOMAIN health check failures.
- **Node role derivation via Prometheus and K8s labels**: Derived node roles using the `kube_node_role` metric joined on node labels in Prometheus queries, and recognized both `control-plane` and legacy `master` label/taint keys in K8s API responses.
- **Compliance framework pass rate percentage scaling**: Scaled average framework pass rate by multiplying by 100 before rounding, preventing 0-1 pass rate ratios from rendering as 1%.
- **Cluster-scoped node infrastructure audit report querying**: Queried and merged cluster-scoped `clusterinfraassessmentreports` alongside namespaced `infraassessmentreports` so trivy-operator node security audit findings are properly reflected.

## [1.0.16] - 2026-07-25

Introduces a dependency-aware Platform Status page for real-time component health tracking
and optimizes container release workflows with native multi-architecture builds.

### Added
- **Dependency-aware Platform Status page**: Added the `/status` view displaying real-time
  health indicators across cluster components and underlying infrastructure dependencies.
- **Design system token contract**: Introduced `DESIGN.md` establishing design token guidelines
  and CI lint rules for frontend styling consistency.

### Changed
- **Each architecture builds on a native runner** — the 1.0.16 image never published, because
  one job built both platforms and the arm64 leg ran `next build` under QEMU: amd64 finished in
  ~90s while arm64 was still compiling five minutes later, and an earlier run reached 2h38m
  before being cancelled. Now one job per arch (`ubuntu-24.04-arm`, free for public repos)
  pushes by digest and a final job merges them into one multi-arch manifest. No QEMU anywhere.

## [1.0.15] - 2026-07-13

*Version jumped from 1.0.4 to 1.0.15 to align package versioning with the cluster GitOps image pin.*

Improves authentication stability with Keycloak OIDC session keep-alives, single-flight token
refreshing, and chunked session cookie handling, while expanding platform tools and refining
security compliance reporting.

### Added
- **Kubernetes Dashboard and NFS Quota tool tiles**: Integrated zero-click gateway SSO dashboard
  tiles for Kubernetes Dashboard 3.0 and NFS Quota management.
- **Keycloak SSO session keep-alive**: Implemented background token refresh using single-flight
  request execution to maintain active OIDC sessions without user interruption.
- **Parent-domain theme synchronization**: Shared the `narwhal-theme` cookie across the parent
  domain to keep dark/light theme preferences synchronized with Keycloak login screens.
- **Hygiene severity tier in compliance**: Added a `LOW` severity tier to the config-audit
  summary to track minor security hygiene findings separately from actionable threats.

### Fixed
- **Login redirect loop from chunked session cookies**: Updated NextAuth session cookie handling
  with a shared predicate (`isSessionCookie`) to correctly reconstruct split session cookies
  exceeding browser header size limits.
- **Compliance headline noise reduction**: Filtered built-in/upstream RBAC roles and
  system-namespace findings out of actionable headline metrics on the compliance dashboard.

## [1.0.4] - 2026-07-10

Converts core KISA security compliance controls into live Kubernetes resource audits and enhances
real-time event stream classification.

### Added
- **Live KISA security compliance checks**: Converted static compliance rules for `KISA-CP-01`,
  `KISA-ETCD-01`, `KISA-POD-01`, and `KISA-NET-01` into live cluster inspection checks with
  builtin-aware evaluation for `KISA-RBAC-01` / `KISA-RBAC-02`.

### Changed
- **Direct Gitea repository link**: Updated the Gitea platform tool tile link to navigate
  directly to the `narwhal-gitops` repository.
- **Valkey TLS bypass configuration**: Added support for `VALKEY_INSECURE_PRODUCTION` env var to
  bypass strict TLS verification in non-production environments.

### Fixed
- **Live event stream classification**: Categorized streamed K8s events to enable UI category
  filtering and surfaced warning events under critical views.

## [1.0.3] - 2026-07-09

Separates live-streaming pub/sub operations onto a dedicated Valkey connection pool.

### Fixed
- **Dedicated Valkey client for live streaming**: Created a separate Valkey client instance
  for Server-Sent Events (SSE) pub/sub channels and pipelines to prevent subscriber blocking on
  general cache operations.

## [1.0.2] - 2026-07-09

Establishes a live Kubernetes Events informer backend to feed real-time event streams into the
dashboard.

### Added
- **Kubernetes Events informer** — `/live` had no event source at all: `live-k8s-informer.ts`
  was a TODO stub that was never invoked, and nothing posted to `/api/events/ingest`, so the
  page could only ever be empty. It now runs a cluster-wide core/v1 Events watch
  (`resourceVersion` + backoff reconnect, all `Warning` events plus a curated set of `Normal`
  reasons), maps each to a `LiveEvent` and calls `pushEvent()`, which publishes to the Valkey
  pub/sub channel. Started once from `instrumentation.ts` on the Node.js runtime.

### Changed
- **Release CI build triggers**: Restricted GHCR container build workflows to tag pushes (`v*`)
  to prevent redundant untagged image builds on intermediate commits.

## [1.0.1] - 2026-07-09

Stabilizes real-time SSE stream connections, refines compliance report parsing, and cleans up
alert scorecard metrics.

### Added
- **Action-only toggle for node inspection**: Added a filter toggle to hide healthy CNI and
  control-plane info panels in node detail views, focusing on actionable items.

### Fixed
- **SSE reconnect storm**: Kept Server-Sent Events streams open during pub/sub subscriber
  failures to prevent client reconnect loops.
- **Trivy compliance report parsing**: Updated report parsing logic to read `detailReport.results`
  and accurately evaluate per-control pass/fail statuses.
- **Governance scorecard noise**: Excluded Prometheus `Watchdog` and `InfoInhibitor` synthetic
  meta-alerts from governance scorecard metric counts.

## [1.0.0] - 2026-07-07

First public release of the Narwhal IDP Portal, a Kubernetes Internal Developer Platform
management dashboard built with Next.js 16 and React 19.

### Added
- **Core Dashboard & Service Map**:
  - Live dashboard featuring real-time cluster event timelines, activity feeds, and resource summaries.
  - Interactive service dependency graph integrating Hubble relay L4/L7 eBPF network flows,
    live traffic rates, namespace filtering, node detail drawers, and visual legends.
  - Node inspection views with interactive action-needed accordion filters, system audit panels,
    and capacity metrics.
- **Application Catalog & Workloads**:
  - Application catalog and pod management views supporting streaming container logs scoped by
    `app.kubernetes.io/instance`.
  - Role-default application visibility scope (`my-apps`) with team override capabilities.
- **Security Compliance & Governance**:
  - KISA security control framework checklist (`/compliance`) with sortable tables and Trivy
    vulnerability report integration.
  - Governance dashboard featuring workload distribution analysis, pods missing resource requests,
    RBAC risk analysis, and DORA metrics.
  - On-premises actual cost-basis calculation methodology.
- **Single Sign-On & Access Control**:
  - Keycloak OIDC authentication with automatic login proxy redirection and relative `callbackUrl`
    handling.
  - RP-initiated federated logout (SLO chain) routing through the APISIX gateway (`/apisix/logout`)
    while clearing secure cookies (`__Secure-`, `__Host-`).
  - 4-tier Role-Based Access Control supporting `cluster-admin`, `developer`, `viewer`, and `guest`
    roles across navigation and platform tools.
- **Platform Tool Integration**:
  - Platform tools grid with deep links and zero-click SSO bootstrapping for ArgoCD, Grafana,
    Harbor, Gitea, OpenBao, Velero UI, and Headlamp.
- **Internationalization & Design System**:
  - Dual-language i18n support (English and Korean) with cookie-based locale switching and
    self-hosted Pretendard font for Korean typography.
  - Modern responsive UI built with Next.js 16, React 19, and Tailwind CSS.
- **Container Build & Local Development**:
  - Containerization setup supporting multi-arch Docker builds published to GHCR
    (`ghcr.io/dasomel/narwhal-portal:1.0.0`).
  - In-cluster Kaniko build Job manifest (`deploy/kaniko-build-job.yaml`) for offline clean
    installs.
  - Skaffold development profile configured for live Hot Module Replacement (HMR) and container
    file syncing.

[Unreleased]: https://github.com/dasomel/narwhal/compare/portal/v1.1.35...HEAD
[1.1.35]: https://github.com/dasomel/narwhal/compare/portal/v1.1.34...portal/v1.1.35
[1.1.34]: https://github.com/dasomel/narwhal/compare/portal/v1.1.33...portal/v1.1.34
[1.1.33]: https://github.com/dasomel/narwhal/compare/portal/v1.1.32...portal/v1.1.33
[1.1.32]: https://github.com/dasomel/narwhal/compare/portal/v1.1.31...portal/v1.1.32
[1.1.31]: https://github.com/dasomel/narwhal/compare/portal/v1.1.30...portal/v1.1.31
[1.1.30]: https://github.com/dasomel/narwhal/compare/portal/v1.1.29...portal/v1.1.30
[1.1.29]: https://github.com/dasomel/narwhal/compare/portal/v1.1.28...portal/v1.1.29
[1.1.28]: https://github.com/dasomel/narwhal/compare/portal/v1.1.27...portal/v1.1.28
[1.1.27]: https://github.com/dasomel/narwhal/compare/portal/v1.1.26...portal/v1.1.27
[1.1.26]: https://github.com/dasomel/narwhal/compare/portal/v1.1.25...portal/v1.1.26
[1.1.25]: https://github.com/dasomel/narwhal/compare/portal/v1.1.24...portal/v1.1.25
[1.1.24]: https://github.com/dasomel/narwhal/compare/portal/v1.1.23...portal/v1.1.24
[1.1.23]: https://github.com/dasomel/narwhal/compare/portal/v1.1.22...portal/v1.1.23
[1.1.22]: https://github.com/dasomel/narwhal/compare/portal/v1.1.21...portal/v1.1.22
[1.1.21]: https://github.com/dasomel/narwhal/compare/portal/v1.1.20...portal/v1.1.21
[1.1.20]: https://github.com/dasomel/narwhal/compare/portal/v1.1.19...portal/v1.1.20
[1.1.19]: https://github.com/dasomel/narwhal/compare/portal/v1.1.18...portal/v1.1.19
[1.1.18]: https://github.com/dasomel/narwhal/compare/portal/v1.1.17...portal/v1.1.18
[1.1.17]: https://github.com/dasomel/narwhal/compare/portal/v1.1.16...portal/v1.1.17
[1.1.16]: https://github.com/dasomel/narwhal/compare/portal/v1.1.15...portal/v1.1.16
[1.1.15]: https://github.com/dasomel/narwhal/compare/portal/v1.1.14...portal/v1.1.15
[1.1.14]: https://github.com/dasomel/narwhal/compare/portal/v1.1.13...portal/v1.1.14
[1.1.13]: https://github.com/dasomel/narwhal/compare/portal/v1.1.12...portal/v1.1.13
[1.1.12]: https://github.com/dasomel/narwhal/compare/portal/v1.1.11...portal/v1.1.12
[1.1.11]: https://github.com/dasomel/narwhal/compare/portal/v1.1.10...portal/v1.1.11
[1.1.10]: https://github.com/dasomel/narwhal/compare/portal/v1.1.9...portal/v1.1.10
[1.1.9]: https://github.com/dasomel/narwhal/compare/portal/v1.1.8...portal/v1.1.9
[1.1.8]: https://github.com/dasomel/narwhal/compare/portal/v1.1.7...portal/v1.1.8
[1.1.7]: https://github.com/dasomel/narwhal/compare/portal/v1.1.6...portal/v1.1.7
[1.1.6]: https://github.com/dasomel/narwhal/compare/portal/v1.1.5...portal/v1.1.6
[1.1.5]: https://github.com/dasomel/narwhal/compare/portal/v1.1.4...portal/v1.1.5
[1.1.4]: https://github.com/dasomel/narwhal/compare/portal/v1.1.3...portal/v1.1.4
[1.1.3]: https://github.com/dasomel/narwhal/compare/portal/v1.1.2...portal/v1.1.3
[1.1.2]: https://github.com/dasomel/narwhal/compare/portal/v1.1.1...portal/v1.1.2
[1.1.1]: https://github.com/dasomel/narwhal/compare/portal/v1.1.0...portal/v1.1.1
[1.1.0]: https://github.com/dasomel/narwhal/releases/tag/portal/v1.1.0
[1.0.17]: https://github.com/dasomel/narwhal-portal/compare/v1.0.16...v1.0.17
[1.0.16]: https://github.com/dasomel/narwhal-portal/compare/v1.0.15...v1.0.16
[1.0.15]: https://github.com/dasomel/narwhal-portal/compare/v1.0.4...v1.0.15
[1.0.4]: https://github.com/dasomel/narwhal-portal/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/dasomel/narwhal-portal/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/dasomel/narwhal-portal/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/dasomel/narwhal-portal/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/dasomel/narwhal-portal/releases/tag/v1.0.0
