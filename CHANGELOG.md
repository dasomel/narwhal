# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

English | [한국어](CHANGELOG-ko.md)

## [Unreleased]

A hardening release. The air-gapped bundle and the offline upgrade bundle now carry an
integrity chain that is checked before anything is promoted, the ArgoCD, Gitea, RBAC, OpenBao,
Keycloak, APISIX, NFS and `database` trust boundaries were tightened, and a batch of static CI
gates now keeps those boundaries from regressing. Several changes need an operator action on an
existing cluster or bundle; read **Upgrade notes** first.

### Upgrade notes

- **Re-save air-gap bundles made before the digest inventory (#337).** `02-save-images.sh` now
  writes `images.digests.json`, and `09-verify-bundle-completeness.sh` fails closed if it is
  missing, malformed or unsorted, if an image has no entry, if an `oci/` directory is not
  recorded, or if a digest or platform does not match. A bundle saved earlier fails `09` until
  `02` is re-run; `05` (load) is unchanged.
- **`09` now reads the bytes of every OCI layout (#336, #338).** It hashes manifest, index and
  config blobs, checks layer presence and size, rejects symlinked blobs, requires digest-pinned
  refs to keep their pinned digest, and fails when an image config's `os/architecture` differs
  from the recorded `collapsed_to` platform (an arm64 image in an amd64 bundle is now
  rejected).
- **Upgrade-bundle promotion fails closed on age and provenance (#335).**
  `promote-upgrade-bundle.sh` rejects a bundle whose `created_at` is older than
  `UPGRADE_BUNDLE_MAX_AGE_DAYS` (default 90; allowed range above 0 up to 3650, and 0 does not
  disable the check), is missing, malformed or in the future, or whose `provenance.author` /
  `provenance.source_repo` is empty. Raising the variable is meant to be a reviewed decision.
  `--rollback` does not run the verifier, so it applies no freshness check. The sample manifests
  in `scripts/airgap/lib/` are dated 2026-08-01 and 2026-08-25, so they pass the default ceiling
  only until 2026-10-30 and 2026-11-23. CI does not apply the ceiling.
- **Upgrade-bundle manifests are validated more strictly (#318, #321, #324, #327, #331).**
  Artifact identities must be unique and non-empty, digest width must match the algorithm,
  promotion enforces the reviewed license policy, license-only changes show up in the dry-run
  diff, and dependencies removed from a candidate bundle are reported. A manifest with a
  duplicate or empty identity, a wrong digest width or a license-policy violation is rejected at
  promotion.
- **Image-list collection is now strict (93161df, #222).** A failure to render a bundled chart
  (and so to capture its hook/Job images) aborts `01-generate-image-list.sh`; set
  `AIRGAP_ALLOW_INCOMPLETE=1` to opt out for local iteration. `up.sh` runs `check-bundle.sh`
  before any VM action and stops early when no bundle exists for the host architecture.
- **Air-gapped Trivy reads its databases from in-cluster Harbor (9377dee, #302, #320).** The
  vulnerability DB, Java DB and checks bundle are no longer pulled live from `ghcr.io`; stage
  them with `scripts/airgap/lib/fetch-security-db.sh`, `promote-security-db.sh` and
  `push-security-db.sh` (Harbor project `trivy-db`). Future or malformed freshness evidence is
  rejected.
- **ArgoCD Applications no longer run in the `default` AppProject (6ead267).** `13-argocd.sh`
  applies the new `platform` and `tenants` AppProjects (`gitops/resources/argocd-projects.yaml`)
  and fails closed if it cannot. An Application that names an unlisted namespace or repo is now
  refused at sync time. The `developer` role may `sync` only `tenants/*` in both
  `13-argocd.sh` and the `narwhal-platform` GitOps chart (`argocd-config.yaml`), so the
  self-healing Application no longer reverts the restriction (#342). `portal-reader` keeps
  `sync */*` on purpose. R246 pins both sources, including the full policy line set. Not
  verified on a live cluster. See Known issues.
- **`narwhal-gitops` is a private Gitea repository (52dbb8d).** ArgoCD reads it with its own
  `argocd` account and a `read:repository` token registered as a repository Secret; `main` is
  protected by a push whitelist after the initial push. Re-running `14-gitops-bootstrap.sh`
  reasserts `private: true` on an existing repo. A failed repo creation, clone, config copy or
  push now aborts the bootstrap instead of being swallowed (65d07b1).
- **The `developer` ClusterRole is read-only (7551c21).** Mutating verbs and `exec` /
  `portforward` / `attach` moved to the new `developer-workload-admin` role, bound only in `dev`
  (and in tenant namespaces, ed06e82). Every other namespace is read-only for direct `kubectl`
  use, and developers can no longer `kubectl patch` an ArgoCD Application; the portal uses its
  own ServiceAccount and is unaffected.
- **Portal OpenBao access is a 1-hour Kubernetes-auth token (e923d7b, 2b589b7).**
  `13-2-narwhal-portal-bindings.sh` creates the `narwhal-portal` Kubernetes-auth role (1h TTL,
  audience verified), binds `system:auth-delegator` to `storage:openbao`, and no longer persists
  the 8760h token; the legacy token is issued only with `ENABLE_LEGACY_OPENBAO_TOKEN=true`. The
  projected ServiceAccount token is mounted by the `narwhal-platform` chart
  (`narwhal-portal-k8s.yaml`), not by the script. The portal policy keeps KV-v2 metadata access
  and no longer grants `secret/data/narwhal-portal/*`. What an existing deployment must re-apply
  is unverified; the commits were not run against a live cluster here.
- **The 1-year Kubernetes API token is no longer minted by default (77d6ce3).**
  `13-2-narwhal-portal-bindings.sh` skips the 8760h `K8S_SA_TOKEN` unless
  `ENABLE_LEGACY_K8S_SA_TOKEN=true` (default `false`; setting it prints a WARN); the portal reads a 1-hour
  projected token (audience `https://kubernetes.default.svc.cluster.local`) instead. A deployment
  that still depends on the static `K8S_SA_TOKEN` must set the variable or move to the projected
  token.
- **Keycloak OIDC TLS is verified and fails closed (5352bc2, dae7237, 3950512, 6790454).**
  `insecureSkipVerify` is gone from the ArgoCD OIDC config and `rootCA` is injected into
  `argocd-cm` / `argocd-secret`. `11-3-keycloak-clients.sh` now exits 1 when neither
  `narwhal-wildcard-tls` nor `narwhal-ca-cert` yields a CA. `test-sso.sh` expects verification to
  succeed.
- **The APISIX Admin API allows only the pod CIDR and loopback (1dddbe4).** Source allowlist is
  `10.244.0.0/16` plus `127.0.0.1/32`, and a NetworkPolicy limits `:9180` ingress to the APISIX
  ingress controller and `narwhal-portal` in `devtools`
  (`gitops/resources/apisix-admin-ingress-policy.yaml`). Break-glass recovery pods must carry the
  label `app.kubernetes.io/name=apisix-ingress-controller` (`docs/common/apisix-etcd-recovery.md`,
  `docs/common/apisix-admin-api.md`). The allowed CIDR is hard-coded in
  `gitops/charts/narwhal-apps/templates/apisix.yaml`, not taken from `POD_NETWORK_CIDR`, so a
  cluster with a custom pod CIDR locks pods out of the Admin API.
- **NFS exports default to `root_squash` with a mode-750 root (6151b86, 7e4e622).**
  `no_root_squash` is removed from both export lines. `root_squash` does not rewrite existing
  on-disk ownership; follow the migration runbook in `docs/common/compliance-hardening.md` and
  check with `scripts/test/verify-nfs-root-squash.sh`. For non-root writes, `csi-driver-nfs` runs
  with `feature.enableFSGroupPolicy=false` (a live CSIDriver with the immutable `File` policy is
  deleted by a pre-upgrade guard) and Grafana's `initChownData` is disabled.
- **The `database` namespace denies ingress by default (3148c14).** Only CloudNativePG,
  its operator, Keycloak, Harbor and Gitea are allowed in; any other caller is blocked. The
  `database` and `platform-system` namespaces still carry namespace-wide `PERMISSIVE`
  `PeerAuthentication` exceptions (allowlisted by R243), so mTLS is not required there.
- **Gitea ingress is default-deny (67ea222).** `gitea-http` accepts only APISIX, the ArgoCD
  repo-server and application-controller, the portal backend and the Kaniko build job; every
  other caller is blocked.
- **The node-tuning Job is isolated (6fd186e).** It runs under a dedicated
  `narwhal-tuning-job` ServiceAccount (no RBAC grants, token unmounted) and a deny-all
  ingress and egress NetworkPolicy. CNI enforcement for its `hostNetwork` pod is unverified.
- **Kyverno `disallow-latest-tag` now enforces (24fedbb).** It moved from `Audit` to `Enforce`:
  a Pod whose image is `:latest` is rejected at admission everywhere except `kube-system`,
  `istio-system`, `platform-system`, `devtools` and `security-system`. Pin the tag before
  upgrading any workload outside those namespaces.
- **ArgoCD's cache runs Valkey (3a3aef9).** `13-argocd.sh` points `argocd-redis` at
  `docker.io/valkey/valkey:8-alpine` (Redis 8 is RSALv2/SSPL/AGPL). An existing cluster picks
  it up when `13-argocd.sh` is re-applied.
- **Portal is pinned to `narwhal-portal:1.0.19` (2a11c10, #245).** It is not Cosign-signed, and
  1.0.18 crashes on start and must not be used (`SECURITY.md`).
- **`up.sh` exits 2 when Phase 2 is incomplete (#225)**, and stops with the exact System
  Settings fix when macOS Local Network permission blocks the VM.
- **Kakao Cloud (e496631, 8f24bcf, eda06db).** The bastion has its own security group; SSH is
  limited to the required `bastion_ssh_allowed_cidrs`, which rejects `0.0.0.0/0`. SSH
  host keys are verified against a per-cluster `known_hosts` (trust on first use, a changed
  key fails closed); after rebuilding a node or the bastion, clear the old entry with
  `kakao_forget_host`. Kubelet port 10250 is open to the node subnet only.

### Added

- **Offline upgrade bundle pipeline (7becc87, #300, #301).** JSON schema, verifier, promotion
  gate with approval, dry-run diff, content and dependency verification, and a static check of
  the component upgrade-wave graph (#46); cert-manager is the first orchestration pilot
  (dd9ca0e).
- **Air-gap bundle integrity chain (#336, #337, #338).** OCI layout byte verification, a
  save-time per-image digest inventory, and config-platform verification in the `09` gate;
  `--report FILE` writes a JSON layout report.
- **Offline vulnerability DB bundle integrity check** `scripts/verify/security-db-bundle-check.sh`
  (#302).
- **Immutable-release groundwork (24fedbb, a12401c, b342bff).** `image-digests.tsv` records the
  digest each tag resolved to and `09` fails when an image has no row; kaniko and alpine/git
  are pinned to immutable tags; a scheduled workflow detects image digest drift.
- **Supply-chain CI gates.** Mutable image tags are rejected before merge (ad7804c) and mutable
  package installs across all workflows (#316); pip requirement options need hashes (#330); a
  license policy gate runs over `component-licenses.tsv` (aa9c1a4); SBOM, commit, workflow-run
  and vulnerability correlation (6dab398, #317); a CycloneDX SBOM for the offline bundle
  (c6c16dd); `NOTICE` with a per-image license map (c65f3ab); every release artifact must be
  inventoried or reviewed as `UNKNOWN` (#295); kubeconform CRD schemas are vendored and
  `-ignore-missing-schemas` is gone (7493182); third-party downloads are pinned and checksummed
  (fc31efb).
- **New static gates for the service mesh.** R242 fails CI when `AMBIENT_NAMESPACES` in
  `09-istio-ambient.sh` diverges from the namespaces the NetworkPolicy check covers (#332).
  R243 allows `PeerAuthentication` `PERMISSIVE` / `DISABLE` only from a reviewed allowlist,
  requires mesh-wide `STRICT`, and records two existing exceptions as counted known issues
  (#333, #334). R244 requires an ingress default-deny around every namespace that has a
  namespace-wide `PERMISSIVE` `PeerAuthentication`, and counts `monitoring` and `platform-system`
  as known issues (#339).
- **More static gates for security posture (#283-#293, #319, #322, #325, #326).** Webhook
  `failurePolicy` and timeout, `hostPath` mounts (including nested CronJobs), Pod Security
  Admission labels, workload runtime posture and RuntimeClass references, seccomp overrides,
  the kubelet and API-server authorization baseline, and a rejected disabled `NodeRestriction`.
- **Node and etcd verifiers.** `scripts/verify/kubelet-authz-check.sh` (#227),
  a node-side static-pod manifest verifier (#291), and
  `etcd-encryption-check.sh` with `rotate-etcd-encryption-key.sh` for safe key rotation (#233).
  The kubeadm config now pins kubelet anonymous auth off, webhook authn/authz and
  `readOnlyPort: 0` (identical to kubeadm defaults).
- **Kyverno `verify-image-signatures` (Audit, 9b19aa1) and `scripts/airgap/10-verify-image-signatures.sh`.**
  See Known issues: this policy currently enforces nothing.
- **Kyverno `restrict-tuning-job-admission` (9742afc).** Privileged and host-namespace
  pods in `devtools` are limited to the `narwhal-tuning-job` ServiceAccount.
- **Tenant namespaces by pull request (ed06e82).** `resources/tenants/<team>/<namespace>.yaml`
  is synced by a `tenants` Application; the portal's `portal-gitops` identity can push a branch
  but not merge to `main`.
- **Design documents and baselines.** CAPI-versus-provider ADR (#266), Kubernetes/OSS
  compatibility matrix (#271, #296), unified observability contract (#281), storage protection
  policy (#279), management-plane HA contract (#280), event-envelope baseline and
  multi-cluster credential export (9af2489, 7fc3a45), and the T1-T7 test taxonomy (c16dc86).

### Changed

- **Component pins (`VERSIONS.md`).** Kubernetes 1.35.5 to 1.35.7 with the
  kubeadm/kubelet/kubectl rows now checked in CI (80c0c77), nfs-quota-agent 0.2.1 to 0.5.0
  (d8821a9), Harbor pinned to the immutable tag v2.15.1, and the portal image 1.0.16 to 1.0.19
  (#245).
- **Portal RBAC.** Read-only `StorageClass` and `ResourceQuota` access for PVC preflight
  (#315); `argocd-server` no longer reaches `nodes/proxy` (#255); Velero's server role is
  least-privilege instead of `cluster-admin` (#250); the portal's Gitea token is least-privilege
  (#252, #253).
- **GitOps bootstrap fails closed (65d07b1, a6bc6bb).** The source is validated against the
  Gitea API before ArgoCD is pointed at it, and Gitea token minting is idempotent.
- **Keycloak clients are hardened (e67f234, 50aef34).** Exact per-client `webOrigins` instead of
  a wildcard, `directAccessGrantsEnabled=false` on update paths too, and
  `validate-keycloak-clients.sh` fails the bootstrap on an unexpected client.
- **SBOM correlation no longer matches unrelated vulnerability identities (#317).**

### Fixed

- **Ambient probes and HBONE.** GitOps NetworkPolicies now allow kubelet probes and port 15008
  (#240, #241).
- **Networking.** APISIX values render, the Cilium pool follows `POD_NETWORK_CIDR`, and the
  script aborts on an empty APISIX viewer key (#236, 0ca929e). The Prometheus pod is opted out
  of the ambient dataplane (dee1dca).
- **NFS PVCs mount after a reboot.** `rpc-statd` now starts at boot (#249).
- **Dashboard.** The CSRF Secret renders with `apiVersion` so the app syncs (#258).
- **Air-gap image digests** refreshed after upstream drift (#194, #259, #310), and the
  build-helper digests the portal Kaniko Job pins are shipped (#262).
- **Keycloak HTTPS readiness gate** probes `/realms/master` instead of the management port
  (#231).

### Security

- Default-deny ingress for `database`, Gitea ingress restricted to its legitimate non-APISIX
  sources, and the APISIX Admin API reachable only from the pod CIDR and loopback.
- The gateway no longer trusts a client-supplied `X-WEBAUTH-USER` (492e65a).
- Private GitOps repository with a read-only ArgoCD identity, AppProject boundaries, and a
  developer role without cluster-wide `exec` or mutation.
- Short-lived portal OpenBao identity, Keycloak OIDC TLS verification and CA trust, and
  `root_squash` NFS exports.
- Kakao bastion SSH allowlist and verified host keys (e496631, 8f24bcf).

### Known issues

- **Image signatures are not enforced.** The Kyverno `verify-image-signatures` policy is
  `Audit`-only with `required: false`, and its public key is a PLACEHOLDER
  (`MFkw...AAE/narwhal/cosign/trusted/signer/pub`). No cosign key has been issued (#35), so the
  policy currently enforces nothing. The portal image 1.0.19 is likewise not Cosign-signed.
- **Mesh-wide PERMISSIVE during bootstrap.** `09-istio-ambient.sh` applies a mesh-wide
  PERMISSIVE `PeerAuthentication`; the GitOps chart declares `STRICT` under the same name, so
  traffic is fail-open until ArgoCD reconciles (#126).
- **`monitoring-permissive`** sets PERMISSIVE for the whole `monitoring` namespace although its
  own comment recommends per-workload scope. R243 pins the count so it cannot spread; R244
  tracks `monitoring` and `platform-system` as namespaces that are PERMISSIVE without an
  ingress default-deny (#126, #339).
- **`dev` namespace label conflict.** `gitops/resources/dev-namespace.yaml` sets
  `istio.io/dataplane-mode: none`, while `09-istio-ambient.sh` lists `dev` in
  `AMBIENT_NAMESPACES` and labels it ambient (#128). Which one wins on a live cluster is
  unverified.
- **Ingress isolation is partial.** In `gitops/resources`, only `database`, Gitea, the APISIX
  Admin API and the node-tuning Job (deny-all) have ingress-isolating policies; `monitoring` and
  `platform-system` have none (policies bundled inside charts were not exhaustively checked).
- **The `portal-reader` role keeps `sync */*` by design.** The portal's service identity syncs
  on any project; narrowing it needs a separate review. R246 (#342) checks the ArgoCD policy by
  line set, parsed `argocd-rbac-cm` data and substitution expressions, but not the rendered Helm
  output, other charts, `argocd-cm`, or changes made with `kubectl` outside the bootstrap
  heredoc (see the change package's Known limits).
- **Air-gap bundle copy does not preserve OCI referrers** (SBOM, signature, attestation).
  `09` reports the loss but does not fail (#124).
- **The digest inventory is trust-on-first-save.** `images.digests.json` is not signed, so it
  proves the bundle matches what `02` recorded, not that `02` saw the right bytes (#124).
- **Not applied to a live cluster.** The commit messages for the AppProject split, the
  developer RBAC change, the private Gitea repository and the Valkey swap state that they were
  checked statically or on a scratch cluster, not on a running narwhal cluster.

## [1.2.0] - 2026-08-08

Airgap goes from "images only" to an install that genuinely completes with no route to
the internet, on both Vagrant (arm64) and Kakao Cloud (amd64).

### Added

- **A closed-network install that is actually closed.** The bundle now carries Helm charts,
  binaries (helm/cilium/hubble/yq), remote manifests and OS packages — previously only
  container images were bundled and everything else came from the public internet, hidden
  behind the egress proxy. `AIRGAP=1` switches APT to `file:///srv/airgap/apt` and drops the
  default route so the isolation is enforced rather than assumed.
- **Kakao Cloud as a first-class provider**: OpenTofu bring-up, bastion proxy/registry
  stages, `kakao.narwhal.internal` service zone served from bastion dnsmasq, per-arch
  bundles, and `set-config-kakao.sh` / `setup-hosts-kakao.sh` for access.
- **Isolation tooling**: `scripts/test/verify-isolation.sh` checks route, networkd drop-in,
  direct egress, mirror reachability and APT sources per node; `airgap-isolate-kakao.sh`
  isolates already-provisioned nodes. Documented in `docs/common/airgap-isolation-testing.md`.
- **Chaos Mesh 2.8.3 + k6 load-test suite** with Grafana dashboards and a Prometheus
  remote-write receiver; Dr. Pym rightsizing recommendations.
- **Clean-install regression suite** (`regression-check-kakao.sh`, 36 static + runtime
  checks, each mapped to a dated lessons-log row) wired into CI on every push.

### Fixed

- **The airgap mirror had never served a single pull.** Ubuntu's containerd 2.2.1 ships
  `config_path` as a colon-separated pair, which containerd does not accept — it looked for
  a directory of that literal name and ignored every `hosts.toml`. Measured both ways on one
  node: colon form → `network is unreachable`; single path → all images pull from the mirror,
  registry log 0 → 132 containerd requests.
- **Mirror coverage**: only 5 of 11 registries in the image list had a `hosts.toml`, so
  istiod, istio-cni, gitea and argocd-redis pulled upstream. The list is now derived from
  `images.txt`.
- **GitOps could not sync offline** — every ArgoCD Application named a public chart repo.
  Charts now come from the in-cluster Gitea Helm registry.
- **Isolation did not survive DHCP renewal**: `ip route del default` is undone on the next
  lease, so a networkd drop-in (`UseGateway=false`) makes it durable. On Kakao only the
  gateway is dropped — the lease also carries the metadata/NTP routes.
- **Phase 2 could report success with zero platform apps**: the Gitea and GitOps bootstrap
  steps warned and continued, leaving four services 503 with nothing behind them. Both are
  now critical.
- NFS moved to v3 with pinned NLM helper ports (the v4.1 client/server deadlock stalled
  storage for ~24h), kube-apiserver heap bounded, and resource requests/limits set across
  ArgoCD, Cilium operator, Tempo and Prometheus.

### Security

- Removed a hardcoded S3 credential and a stale public IP from the repo, and scrubbed a
  6.8 GB accidentally-committed registry blob store from history (5.79 GiB → 3.86 MiB).
- New check R35 fails the build on any file that is tracked *and* gitignored — the
  combination that let that blob store in, since `.gitignore` never applies retroactively.

### Removed

- The KubeMetal MLOps integration (MLflow/SeaweedFS/Prefect). kubemetal installs only its
  agent now, via Helm OCI charts into its own namespace, so the gitops export had no caller.
  Takes the mlflow and prefect images out of both bundles with it.

## [1.1.0] - 2026-07-13

Released without a CHANGELOG entry at the time; reconstructed here from the 83 commits in
`v1.0.0..v1.1.0` so the history is continuous.

### Added

- Kubernetes Dashboard 3.0 (chart 7.14.0, vendored — the upstream Helm index 404s) with
  zero-click Keycloak SSO.
- Dual-mode (light/dark) Keycloak login theme synced with the portal, plus logout
  auto-redirect.
- PSA audit/warn labels on all namespaces (KISA-POD-01); Trivy compliance scanners for the
  governance page.
- Policy, alert and RBAC resources managed as ArgoCD Applications.
- First `PROVIDER=kakao` groundwork: guards to skip kube-vip and Vagrant-only paths,
  MetalLB→NodePort ingress, bastion host, LB public IPs and pinned contiguous private IPs.

### Fixed

- argocd-redis opted out of the ambient mesh — root cause of the recurring EOF wedge.
- APISIX proxy buffers raised; the portal login callback 502'd on a too-big `Set-Cookie`.
- Harbor's non-deterministic chart secrets caused an endless ~5 min rollout.
- Post-logout redirect URIs registered for the SLO chain.

## [1.0.0] - 2026-07-08

First stable release — validated by consecutive zero-fix, from-scratch clean installs
(6-node HA control plane, Ubuntu 26.04 / kernel 7.0, ARM64).

### Highlights

- **IDP Portal (Next.js 16 + React 19) ships from a pinned public image** `ghcr.io/dasomel/narwhal-portal:1.0.0` (GHCR, multi-arch); the in-cluster Kaniko build is demoted to an optional developer self-service tool.
- **Harbor hardened**: internal shared secrets externalized to the `harbor-shared-secrets` K8s Secret (no plaintext in git); all component images pinned to immutable `:v2.15.1` (fixes the stale-`:latest` amd64 layer crashloop — `exec format error` — on ARM64 kernel-7.0 nodes); metrics/exporter disabled.
- **Portal ↔ cluster seam fixed end-to-end**: `monitoring` PERMISSIVE mTLS exception (metrics/logs/traces/alerts now reach the non-mesh portal), retry-hardened ArgoCD API-token and ServiceAccount-token issuance, kube-apiserver CA added to the portal trust bundle, and trivy scan-job CPU/memory tuned for the 2-core workers.
- **Falco disabled on kernel 7.0** (`modern_ebpf` `scap_init` incompatibility, documented in the mistakes log) — runtime security coverage remains via trivy-operator, kyverno, NetworkPolicy, and STRICT mTLS.
- CI (Lint & Validate / ShellCheck) is green; the READMEs (EN + KO) gain a live portal screenshot gallery.

### Changed

- **Domain migration `local.narwhal.io` → `local.narwhal.internal`** (2026-06-28): all 61 source
  files updated. Reason: `narwhal.io` is a real public domain — its wildcard DNS entry shadowed the
  internal cluster domain, causing external DNS resolution to win over the local dnsmasq. The
  `.internal` TLD is ICANN-reserved for private use and is never publicly resolvable, eliminating
  the shadow. Takes effect on next clean install; existing live clusters still run `.io`.

### Added

- OIDC RBAC test section (`test-sso.sh` 8/8: 15 checks)
- Keycloak `kubernetes` client audience mapper (validates K8s API server `aud` claim)
- `--oidc-ca-file` API server flag (validates self-signed certificate JWKS)
- Automatic cleanup of duplicate groups mapper in `microprofile-jwt` scope
- SSO web server Istio ambient mesh opt-out (`istio.io/dataplane-mode: none`)
- Strengthened per-app access control (authorization): applied across all 4 tiers (cluster-admin/developer/viewer/guest)
  - ArgoCD: explicit blocking of guest→`role:none` (added deny policy)
  - Gitea: automatic creation of `narwhal` Organization + Developers/Viewers teams, configured with `--restricted-group guest` + `--group-team-map`
  - Harbor: automatic configuration of developer→Developer and viewer→Guest group members in project `library`
  - OAuth2-Proxy: added `allowed_groups` filter (blocks guest group)
- Added `test-sso.sh` 9/9 per-app access control validation section

### Fixed

- `kcadm.sh --format csv --noquotes` returned incorrect IDs → replaced with jq-based queries (11 locations)
- Stale namespace references in `test-sso.sh` (keycloak→iam, argocd→devtools, etc.)
- Test false negatives caused by `kubectl auth can-i` warning messages
- Istio ambient mesh ztunnel SSO cookie corruption → SSO failures in ArgoCD, Grafana, Harbor, Gitea, etc.
- Gitea OAuth2 source name case mismatch (`Keycloak` → `keycloak`)

## [0.2.0] - 2026-02-24

### Added

- Consolidated namespaces by function (`platform-system`, `iam`, `devtools`, `storage`, `dev`)
- 4-user system: `admin`, `dev`, `view`, `guest`
- 4-group system (singular form): `cluster-admin`, `developer`, `viewer`, `guest`
- OIDC group-based K8s RBAC (ClusterRoleBinding + RoleBinding)
- Early creation of Keycloak HTTPRoute (required before OIDC validation)
- Keycloak HBONE NetworkPolicy (`keycloak-allow-hbone`, port 15008)
- ArgoCD ambient mesh opt-out (`istio.io/dataplane-mode: none`)
- Automatic patching of ArgoCD ClusterRoleBinding namespace (devtools)
- CoreDNS hairpin fix (resolves `*.local.narwhal.internal` to Traefik ClusterIP)
- Release and license badges in README

### Changed

- Namespace restructuring: individual OSS namespaces → consolidated function-based namespaces
  - `metallb-system`, `traefik`, `cert-manager`, `cnpg-system`, `kyverno` → `platform-system`
  - `keycloak`, `oauth2-proxy` → `iam`
  - `argocd`, `gitea`, `harbor`, `headlamp` → `devtools`
  - `seaweedfs`, `velero`, `openbao` → `storage`
- Group names plural→singular: `cluster-admins`→`cluster-admin`, `developers`→`developer`, `viewers`→`viewer`
- Username changes: `k8s-admin`→`admin`, `developer`→`dev`
- PeerAuthentication STRICT → PERMISSIVE (allows kubelet probes + external traffic)
- Keycloak hostname v1 (`hostname-url`) → v2 (`hostname.hostname` + `hostname.strict`)

### Fixed

- Missing HBONE 15008 port in Keycloak Operator NetworkPolicy (mesh-to-mesh communication failure)
- ArgoCD ClusterRoleBinding subject namespace mismatch (`argocd`→`devtools`)
- ArgoCD pods CrashLoopBackOff (ztunnel blocking kubelet probes)
- Shell quotes inserted into yq OIDC URL → API server crash
- Timing issue adding API server OIDC flags (HTTPRoute non-existent)

## [0.1.0] - 2026-02-20

### Added

- Vagrant-based Kubernetes v1.35 IDP cluster automated provisioning
- 2-Phase provisioning architecture (Phase 1: cluster infrastructure, Phase 2: platform apps)
- HA Control Plane: 3 masters + kube-vip VIP (192.168.56.100)
- CNI: Cilium v1.19 (kube-proxy replacement) + Hubble network observability
- Service Mesh: Istio v1.29 ambient mode (zero sidecars, ztunnel mTLS)
- Gateway API: Traefik v3.6 + cert-manager self-signed TLS
- Storage: NFS (Block) + SeaweedFS (S3) + nfs-quota-agent
- Database: CloudNative-PG v1.28 consolidated `narwhal-db` (shared by Keycloak, Gitea, Harbor)
- IAM/SSO: Keycloak v26.5 OIDC (integrated 6 apps: ArgoCD, Grafana, Gitea, Harbor, Headlamp, OAuth2-Proxy)
- GitOps: ArgoCD v3.3 + Gitea v1.25 (App-of-Apps pattern)
- Observability: Prometheus + Grafana + Loki + Tempo + Promtail
- Security: cert-manager (TLS), OpenBao (Secrets), Kyverno (Policy)
- Backup: Velero + CNPG barman → SeaweedFS S3
- Networking: MetalLB (LoadBalancer), dnsmasq (local DNS)
- Dashboard: Headlamp + OAuth2-Proxy
- Cluster verification scripts (`verify-cluster.sh`, `test-sso.sh`)
- Automatic deployment of SSO CA cert (Headlamp, Grafana, ArgoCD)
- DNS HA: dnsmasq on all master nodes, CoreDNS forward configuration

### Changed

- 3 CNPG clusters → consolidated `narwhal-db` (HA failover, ExternalName services)
- Individual scripts → extracted common scripts based on kube-ready-box
- Topology: single node → 3m+3w (Master NoSchedule, platform apps running on Workers)
- NFS StorageClass: hierarchical subDir + optimized mountOptions

[Unreleased]: https://github.com/dasomel/narwhal/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/dasomel/narwhal/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/dasomel/narwhal/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dasomel/narwhal/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/dasomel/narwhal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dasomel/narwhal/releases/tag/v0.1.0
