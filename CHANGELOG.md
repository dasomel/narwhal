# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

English | [한국어](CHANGELOG_ko.md)

## [Unreleased]

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
