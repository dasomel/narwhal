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
- **The APISIX Admin API allows only the pod CIDR and loopback (1dddbe4, #399).** Source allowlist
  is the pod CIDR (chart default `10.244.0.0/16`) plus `127.0.0.1/32`, and a NetworkPolicy limits `:9180` ingress to the APISIX
  ingress controller and `narwhal-portal` in `devtools`
  (`gitops/resources/apisix-admin-ingress-policy.yaml`). Break-glass recovery pods must carry the
  label `app.kubernetes.io/name=apisix-ingress-controller` (`docs/common/apisix-etcd-recovery.md`,
  `docs/common/apisix-admin-api.md`). The allowed CIDR is the chart value `podNetworkCidr`
  (`gitops/charts/narwhal-apps/templates/apisix.yaml`), filled by `14-gitops-bootstrap.sh` from
  `resolve_apisix_admin_cidr` (`scripts/common/lib.sh`): `APISIX_ADMIN_CIDR`, then the Cilium
  cluster-pool CIDR, then the kubeadm `podSubnet`, then the chart default. It deliberately does not
  use `POD_NETWORK_CIDR`, and it fails closed instead of defaulting when a lookup errors, so a
  custom pod CIDR no longer needs a manual edit. Set `APISIX_ADMIN_CIDR` only to override it.
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
- **Portal is pinned to `narwhal-portal:1.1.0` (#343, eb993c24; was 1.0.19 from 2a11c10, #245).**
  1.1.0 is Cosign-signed with the repository key; 1.0.19 and earlier are not, and 1.0.18 crashes on
  start and must not be used (`SECURITY.md`). 1.1.0 requires the TLS-only Valkey listener and the
  dedicated ACL user that the same change installs (`rediss://`, `VALKEY_USERNAME`,
  `VALKEY_PASSWORD`); portal 1.0.19 has no `VALKEY_USERNAME` support and cannot run against it.
- **`up.sh` exits 2 when Phase 2 is incomplete (#225)**, and stops with the exact System
  Settings fix when macOS Local Network permission blocks the VM.
- **Kakao Cloud (e496631, 8f24bcf, eda06db).** The bastion has its own security group; SSH is
  limited to the required `bastion_ssh_allowed_cidrs`, which rejects `0.0.0.0/0`. SSH
  host keys are verified against a per-cluster `known_hosts` (trust on first use, a changed
  key fails closed); after rebuilding a node or the bastion, clear the old entry with
  `kakao_forget_host`. Kubelet port 10250 is open to the node subnet only.
- **SeaweedFS S3 needs per-client keys (#412).** The S3 server now runs with `enableAuth` and an
  identities file derived from four client Secrets (`loki`, `tempo`, `velero`, `cnpg`), each limited
  to its own bucket; `storage/seaweedfs-s3-config` is generated and must not be edited by hand. The
  embedded filer S3 is off, so `seaweedfs-filer` no longer publishes `:8333`. Loki and Tempo read
  their keys from `monitoring/loki-s3-credentials` and `monitoring/tempo-s3-credentials` via
  `envFrom`, and `narwhal-db.yaml` no longer carries `cnpg-s3-credentials`. **On an existing cluster
  follow `docs/runbooks/seaweedfs-s3-auth.md` in stages.** `08-4-storage.sh` now runs a read-only
  guard first (#414): it aborts when an existing S3 server has no `-config=` and the Loki or Tempo
  StatefulSet does not yet reference its S3 credential Secret via `envFrom` (the Secrets alone
  prove nothing: `07-cnpg.sh` creates them first); an approved immediate cutover sets
  `NARWHAL_ALLOW_S3_AUTH_CUTOVER=true` (kubectl, RBAC and parse errors are never bypassed). The
  runbook gates were corrected (#419): `backups.velero.io,schedules.velero.io`, WAL archiving
  progress plus an on-demand CNPG backup instead of `env | grep AWS_`, and the S3 server flag is
  read from `command` and `args`. The seaweedfs Application now ignores the
  `narwhal.io/s3-config-sha256` pod-template annotation (`ignoreDifferences` plus
  `RespectIgnoreDifferences=true`) so the restart marker written by `s3_sync_server_config`
  survives self-heal, and the pod template carries the static annotation `narwhal.io/s3-config-source`
  so the Application stays Synced (without it the ignored marker is the only pod-template
  annotation, Argo CD's normalized live state keeps an empty `annotations: {}` and the Application
  reads OutOfSync). Verified on kakao-narwhal on 2026-10-19: the marker survived self-heal, a second
  `s3_sync_server_config` was a no-op and the Application is Synced and Healthy. The first sync of the
  static annotation rolls the S3 server once. `07-cnpg.sh` calls the helper too (#415). `verify-backup.sh` probes the
  anonymous 403 with `python3` inside a `narwhal-db` pod instead of `wget` in a Velero pod (#419; it
  passed 6/6 on kakao-narwhal on 2026-10-19),
  and generated key values are single-quoted so numeric keys stay strings (#415). `verify-backup.sh` now names
  Velero and CNPG resources with their API group (`schedules.velero.io`, `backups.postgresql.cnpg.io`, ...);
  the bare `schedule` resolved to Chaos Mesh and reported "No Velero schedules found" (R278).
  `ensure_s3_identities` is idempotent: a second run keeps the keys.
- **Label the SeaweedFS data node before running `08-4-storage.sh` (#409, cd62c691).** The volume
  server keeps data in node-local hostPath `/ssd/object_store` and is pinned by
  `nodeSelector narwhal.io/seaweedfs-volume=true` (chart and `08-4-storage.sh`). On an existing
  cluster, label the node that already holds the data by hand first. If no node carries the label,
  `08-4` labels the first worker by name, and a volume server that lands on a node without the
  data starts empty (a reschedule during the 2026-10-16 NFS migration did exactly that and
  crash-looped Loki). `08-4` aborts when more than one node has the label. SeaweedFS master and filer now run as uid/gid 1000 (#410).
- **The APISIX admin key is generated (#400).** The live Kakao cluster had been running the public
  upstream admin key. Bootstrap now generates it once (`resolve_apisix_keys`), rejects the upstream
  defaults by sha256, and gives the ingress controller an existing Secret. An existing cluster must
  run `scripts/cluster/rotate-apisix-admin-key.sh` (it verifies 401 for the old key and 200 for the
  new one). A new install needs no action.
- **Optional dedicated XFS volume for the NFS export on Kakao Cloud (#407, #408, #409, #411).**
  `nfs_data_volume_size` (default 0) creates a `prevent_destroy` volume attached to master-1;
  `01-nfs-server.sh` formats it XFS only when it is blank and mounts it with `prjquota`. Moving an
  existing export is the approval-gated `docs/runbooks/nfs-xfs-migration.md`. Its OpenTofu part is
  four `-target`-gated steps (`csp/kakao-cloud/terraform/README.md`), because a full plan after the
  attach replaces four load-balancer members. The same runbook orders the `root_squash` switch (ownership alignment with the dry-run-by-default
  `scripts/cluster/nfs-root-squash-align.sh`, which needs root; the CSIDriver `fsGroupPolicy` is
  immutable). Both ran once on the live Kakao cluster on 2026-10-16
  (`docs/kakao/nfs-xfs-migration-2026-10-16.md`); there is no clean-room repeat.
- **nfs-quota-agent chart/image skew is refused (#404, #406).** `05-nfs-quota-agent.sh` compares
  the chart's flags with the image before `helm` (`scripts/common/lib-nqa.sh`), waits on the
  DaemonSet and exits 1 on `CrashLoopBackOff`. A live cluster had run a v0.3.0 image under a chart
  that passes `--state-dir` (new in v0.4.0) for about 14,000 restarts.
- **Velero volume backup needs two extra permissions (#421).** The generated `velero-server`
  RBAC adds `get` on `nodes` (cluster scope) and a namespaced Role `velero-node-agent-events` for
  `events` get/list/watch in `storage`; `list`/`watch` on nodes and cluster-wide events stay denied.
  Without them a `PodVolumeBackup` fails with `cannot get resource "nodes"` and then stays
  `Prepared`. Argo CD reverts `kubectl apply`: push `resources/velero-server-rbac.yaml` with
  `push-to-gitea.sh`, then hard-refresh the `velero-server-rbac` Application (the script does not
  refresh it). Before #412 the kopia repositories could not initialise against the anonymous S3, and
  on kakao-narwhal no `PodVolumeBackup` had ever existed. Verified on kakao-narwhal on 2026-10-18/19: a volume backup
  and restore, and the permissions. The permanent RBAC was applied on kakao-narwhal on 2026-10-19. That day's
  `velero-daily-databases` run (no volumes) completed. **Volume backups do not work for volumes
  whose files only their owner or group can read when the export uses the default `root_squash`
  (#431).** The Velero data mover runs as root, `root_squash` maps root to `nobody`, kopia gets
  `permission denied` and cancels the volume. Reproduced on kakao-narwhal on 2026-10-19 for the
  OpenBao `data` volume (`vault.db` mode 0660, owner 100:1000) twice with a targeted backup, and for
  the PostgreSQL `pgdata` volume (mode 0700) in the `velero-daily-full` schedule run. emptyDir volumes and
  the OpenBao `audit` volume (world-readable) backed up in the same runs, and a test PVC was
  backed up and restored earlier (#418). In the
  first targeted run the backup, with one Canceled `PodVolumeBackup`, stayed `InProgress` for more
  than 20 minutes and the next backup waited behind it; a delete request was refused while it was
  in progress and restarting the Velero server pod released it. The second targeted run was
  released after about two minutes, so how long such a backup stays `InProgress` is not known.
- **Kakao Cloud networking (#396, #265, #403).** All five `kakaocloud_public_ip` resources are
  `prevent_destroy`; use `-replace` instead of a targeted destroy. The kubelet (10250) security
  group rule is limited to the node subnet. The `08-1` install now gives the APISIX ingress
  controller a 256Mi request and 512Mi limit (it was OOMKilled at 256Mi during its full sync). The
  GitOps chart's `ingressController` block now matches (256Mi request / 512Mi limit, #424);
  the controller on kakao-narwhal is a separate Helm release and runs 512Mi.
- **Existing clusters: CNPG backup schedule, OpenBao snapshot backup, Tempo and Velero restarts
  (#441, #443, #439, #440).** Merging changes nothing on a running cluster, and each step below
  changes a shared cluster, so get authorization first.
  - *CNPG `ScheduledBackup` (#441).* The live `narwhal-db-backup` in `database` is created by
    `07-cnpg.sh` and is not updated by a merge. Re-run that part of `07-cnpg.sh`, or run
    `kubectl patch scheduledbackup -n database narwhal-db-backup --type=merge -p '{"spec":{"schedule":"0 0 2 * * *"}}'`.
    The old 5-field schedule ran hourly, so `Backup` objects have accumulated. Do not bulk-delete
    them: check the cluster's `retentionPolicy` first and let it prune.
  - *OpenBao snapshot backup (#443); not enabled on any cluster yet.* Each step changes a shared
    cluster, in this order (`docs/common/storage-protection-policy.md` section 8): (1) re-run
    `scripts/cluster/13-2-narwhal-portal-bindings.sh`, which creates the policy and
    Kubernetes-auth role `openbao-backup`; (2) on the operator workstation run
    `scripts/ops/openbao-backup-keygen.sh <private-key-path>`, which generates the RSA key pair
    locally (the private key is passphrase-protected by default and written only to that path)
    and applies only the certificate to the cluster as ConfigMap
    `storage/openbao-backup-recipient`; **keep the private key off the cluster and off Git: if it
    is lost, every snapshot is unrecoverable**; (3) pin the SHA-256 fingerprint the script prints
    by setting `RECIPIENT_FPR` in `gitops/resources/openbao-backup.yaml` through a reviewed Git
    change before deploying (unset, the job only logs a WARNING); (4) deploy with
    `scripts/gitops/push-to-gitea.sh`; (5) check the first run with a manual Job
    (`kubectl create job --from=cronjob/openbao-snapshot ...`) and confirm that the next
    `daily-openbao` `PodVolumeBackup` picks up the snapshot volume. Until the ConfigMap exists the
    CronJob fails closed and writes no snapshot.
  - *Rollouts.* The Tempo change (#439) restarts Tempo and, because `persistence` is off, drops
    its un-flushed WAL. The Velero `node-agent` change (#440) adds the `--node-agent-configmap`
    argument, which restarts the node-agent DaemonSet.

### Added

- **Daily encrypted OpenBao raft snapshot for Velero (#431, #443).** A CronJob at 04:30 UTC
  authenticates with a Kubernetes-auth role limited to reading `sys/storage/raft/snapshot`,
  encrypts the snapshot with `openssl cms` (RSA-OAEP) to an operator-held recipient certificate
  (the private key stays offline), and writes it to a PVC. The plaintext exists only in a memory
  `emptyDir`. A holder Deployment mounts the PVC read-only so Velero fs-backup picks the file up;
  the job pod itself is excluded from Velero. The job fails closed, and `RECIPIENT_FPR` can pin
  the certificate fingerprint. `scripts/ops/openbao-backup-keygen.sh` generates the key pair locally on the operator workstation.
  Verified by static checks and by running the extracted script locally only; see Known issues.
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
- **Agent execution evidence chain, reference implementation (#413, ADR-0002).**
  `scripts/evidence/` is a signed, hash-chained log around grant consumption: an `intent` record
  before the grant is spent and a `result` record after, countersigned checkpoints that detect
  tail truncation and rollback, separate entry and checkpoint keys, and spend-once derived from
  the chain. Python standard library plus `openssl`; 66 attack tests run as R273. It is **not wired
  into any policy enforcement point yet**; `docs/adr/0002-agent-execution-evidence-chain.md` lists
  the residual risks (signing oracle, advisory locking, checkpoint interval). Follow-ups in #417
  are fixed (DER private key rejected, key-pair check, 0600 checkpoint file with rename-swap check,
  documented torn-line recovery).

### Changed

- **Component pins (`VERSIONS.md`).** Kubernetes 1.35.5 to 1.35.7 with the
  kubeadm/kubelet/kubectl rows now checked in CI (80c0c77), nfs-quota-agent 0.2.1 to 0.5.0
  (d8821a9), Harbor pinned to the immutable tag v2.15.1, and the portal image 1.0.16 to 1.0.19
  (#245), then to 1.1.0 (#343).
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
- **CI and release gates.** CI package installs run without lifecycle scripts (#346), the portal
  sign job receives the image digest (#394), the portal `THIRD-PARTY-NOTICES.md` is
  markdownlint-clean (#386), `mark-review-pass` rejects non-ASCII PR numbers (#313), and
  `setup-python` in the research-evidence workflow is pinned to a SHA (#314).
- **Dependency bumps.** `ruby/setup-ruby` 1.327.0 to 1.329.0 (#349), `@base-ui/react` 1.8.0 to
  1.9.0 (#354), and `shadcn` 4.19.0 to 4.21.4 in `portal/` (#353); the portal notices generator
  now converts tabs to spaces in legal text blocks.

### Fixed

- **S3 key-bearing Secrets are applied with server-side apply (#415, #442).** The field manager is
  `narwhal-s3-identities` and the values are written as base64 `data:`, so the key no longer lands
  in the `kubectl.kubernetes.io/last-applied-configuration` annotation. Five Secrets are covered
  (the four client Secrets and `storage/seaweedfs-s3-config`, `s3_apply_secret` in
  `scripts/common/lib.sh`). The old annotation is removed, and a failure of that cleanup or of the
  encoding aborts instead of continuing. **An existing cluster keeps the old annotation, with the
  plaintext keys, until `ensure_s3_identities` runs again (`07-cnpg.sh` / `08-4-storage.sh`).**
  Only a fake `kubectl` has exercised this; ownership transfer from the old client-side owner and
  key rotation on a real API server are unverified.
- **The CNPG `ScheduledBackup` ran hourly (#431, #441).** The schedule was the 5-field
  `0 2 * * *`, but CNPG uses 6 fields with seconds first, so it fired every hour at minute 2 and
  accumulated `Backup` objects. It is now `0 0 2 * * *` (daily at 02:00). See Upgrade notes for the
  live object.
- **Tempo memory ballast disabled and `GOMEMLIMIT=1280MiB` set (#420, #439).** This is a
  hypothesis for the OOMKills, not a confirmed fix; see Known issues.
- **Velero data-movement pods are labelled `app.kubernetes.io/name=velero-data-mover` (#422, #440).**
  The label comes from `podLabels` in the node-agent ConfigMap `velero-node-agent-config` and is
  applied to the pods node-agent creates for volume backups, not to the node-agent DaemonSet pods.
  The `pods/log` and event rejection are documented.

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

- `portal-release.yml` no longer uses a second `actions/checkout` with a `workflow_run` ref, which
  OpenSSF Scorecard flags as Dangerous-Workflow (#395). The tag is fetched with git, proven to point
  at the run's `head_sha`, and only then added as a `tagged/` worktree; the trust model is unchanged.
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
  `Audit`-only with `required: false`. It now carries the repository Cosign public key
  (`scripts/airgap/keys/cosign.pub`, #389; R74/R75c fail if they drift) and skips the Rekor lookup
  for air-gapped verification (#391), so it reports unsigned images but blocks nothing (#35). Portal
  1.1.0 is signed; 1.0.19 and earlier are not.
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
- **S3 authentication operations: what is still open (#414, #415).** Verified on kakao-narwhal on
  2026-10-19: the roll marker survives self-heal, the Application is Synced, the anonymous check
  passes, and all four clients pass their per-client I/O check after the S3 server rolled. The
  `08-4-storage.sh` guard was run read-only against that authenticated cluster (it passes); its
  abort paths on an anonymous cluster are covered by fake `kubectl` tests and an independent
  review's simulation, not by a live re-run of `08-4`. Since then #442 applies the client Secrets with server-side apply and removes the key from the
  `kubectl.kubernetes.io/last-applied-configuration` annotation (not re-verified on a live cluster).
  Still open: the guard's `envFrom` check does not prove that the Loki/Tempo
  pods restarted and loaded the new keys. The same Application also lists the Secret
  `seaweedfs-db-secret` with no comparison status; that is not investigated.
- **Velero volume backups fail on volumes with owner-only files under `root_squash` (#431).** On
  the NFS server, files that other users cannot read were found in `database/` (7677 of 7686),
  `monitoring/` (138 of 1317 at the first measurement, 115 of 1296 later: the data is live),
  `devtools/` (72 of 292) and `storage/` (7 of 77). Until the owner
  decides between running the data mover with a matching identity, application-level backups (for
  OpenBao `bao operator raft snapshot` through a backup hook) and a backup taken on the NFS server,
  do not rely on the Velero backups of OpenBao, PostgreSQL or other volumes with owner-only files.
  CNPG's own barman backups to S3 are separate; an on-demand backup completed and WAL archiving
  advanced on 2026-10-18. Containment (stage 1): the five volumes whose `PodVolumeBackup` was
  Canceled in the scheduled runs (OpenBao `data`, CNPG `pgdata`, Gitea `data`, Harbor jobservice
  `job-logs`, SeaweedFS master `data-storage`) now carry the pod annotation `backup.velero.io/backup-volumes-excludes`, set through
  the OpenBao, Harbor and SeaweedFS (`master.podAnnotations`) charts, the CNPG `Cluster` `inheritedMetadata` and the Gitea chart, so a
  scheduled backup should finish instead of staying `InProgress` and queueing the later schedules.
  This only stops the hang: **Velero still does not protect those volumes.** OpenBao relies on the
  new application-level snapshot (#443, below; not enabled on any cluster yet), PostgreSQL relies on CNPG barman, and Gitea `data`, Harbor
  `job-logs` and the SeaweedFS master `data-storage` (raft metadata) are unprotected; the master's
  `seaweedfs-master-log-volume` stays backed up. The annotation change rolls the OpenBao, CNPG, Gitea,
  Harbor jobservice and SeaweedFS master pods when it is applied. Not verified on a live cluster. #431 stays open for the
  durable fix. **Stage 2 for OpenBao (#443):** the code adds a daily encrypted raft snapshot on a PVC that
  Velero fs-backup can read (see Added). **It is not enabled on any cluster**: it needs the
  operator key, the `13-2` re-run and a deploy (Upgrade notes), and only static checks and local
  OpenSSL runs were done. Limits: **Gitea `data`, the SeaweedFS master
  `data-storage` and the Harbor jobservice `job-logs` volumes are still not protected by Velero
  fs-backup under `root_squash`.** The `openbao-init` Secret (Shamir unseal key and root token) is
  still inside the Velero backup, so the snapshot is protected by the offline key but the cluster's
  unseal secret is not. Restoring an OpenBao snapshot has not been drilled, there is no freshness
  alert for the snapshot yet. Not verified live: real snapshot creation, the Kubernetes-auth login,
  `openssl cms` inside the cluster, the NFS write by uid 100, the effect of the Velero exclusion
  label on the job pod, completion of the holder `PodVolumeBackup`, and a restore drill.
- **Velero schedules also back up emptyDir volumes, and failures are hard to diagnose (#422).** The
  server account had no `pods/log` or `events create`, so the data mover pod's log was unreadable
  without a temporary grant (that is how #431 was found). `pods/log` `get` in namespace `storage`
  is now granted by the generated `velero-pod-logs` Role (not verified on a live cluster);
  `events create` is still missing because it needs a write verb and the rejected namespace is
  not established. The data-movement pods that node-agent creates now carry the label
  `app.kubernetes.io/name=velero-data-mover` (#440; applied live on 2026-10-20, but whether the
  Kyverno events drop is not yet verified), but Velero v1.18 cannot label its repository-maintenance Jobs, so their Kyverno audit
  events remain. The `events create` rejection is only documented, not fixed.
- **Scorecard `Dangerous-Workflow` finding on `portal-release.yml` (#395).** The workflow checks
  out `workflow_run.head_sha`. It already runs only for `portal/v*` tag pushes, executes the
  trusted default-branch checkout and proves the tag points to that commit, so this looks like a
  heuristic match; the owner still has to accept it as an exception (with a review date) or
  restructure the workflow.
- **Tempo is chronically OOMKilled at its 1536Mi limit (#420).** Hundreds of restarts a week on the
  live cluster, unrelated to the S3 change. #439 sets the memory ballast to 0 and `GOMEMLIMIT` to
  1280MiB as a hypothesis; whether the restarts stop is not yet observed, and a rollout drops the
  un-flushed WAL (`persistence` is off).
- **Not applied to a live cluster.** The commit messages for the AppProject split, the
  developer RBAC change, the private Gitea repository and the Valkey swap state that they were
  checked statically or on a scratch cluster, not on a running narwhal cluster.
  Verified on the live kakao-narwhal cluster since: S3 authentication, Velero backup and restore
  (2026-10-18), the Velero RBAC permissions (2026-10-19), the S3 operations fixes (2026-10-19), and the NFS to XFS and `root_squash`
  switch (2026-10-16).

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
