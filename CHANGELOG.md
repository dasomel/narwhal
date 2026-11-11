# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

English | [한국어](CHANGELOG-ko.md)

## [Unreleased]

- **Portal is pinned to `narwhal-portal:1.1.14` (was 1.1.9).** 1.1.10 adds the alerts page and
  notification channel settings (#610, #614), and keeps the partial-results notice visible after
  Trivy data is refetched (#505, #615); 1.1.11 applies the Figma split-layout login (#619, #620), which
  matches the new Keycloak login theme (#621); 1.1.12 brings the Figma app shell (grouped sidebar), dashboard and
  empty states (#624, #634); 1.1.13 redesigns the Architecture page and the onboarding platform figure (#640)
  and fixes the user list groups and session time (#641); 1.1.14 derives architecture statuses from
  workload readiness, hides one-off Jobs (#646) and shows the last login from Keycloak login events (#647). The image is Cosign-signed with the repository key (index
  digest `sha256:8b157fac…`). Mirror it to a cluster bastion by digest before the pin goes live there
  (`scripts/airgap/README.md`, "Mirror a pin bump"); the local Vagrant cluster pulls from ghcr.io directly.

## [1.4.0] - 2026-11-09

A stabilisation release after the 1.3.0 hardening. The local cluster was reworked after node-level
out-of-memory kills, host-disk growth and an etcd/NFS I/O incident (worker VMs with 8GB, a dedicated
NFS data disk, far fewer Trivy scan writes, QoS and spreading for the backup path and the monitoring
stack). Keycloak, Velero UI and the gateway admin UIs are now tied to the `cluster-admin` group, and new
alerts cover failures that went unseen (CNPG WAL archiving, mesh certificates, a stale Trivy DB mirror,
Velero backups, VM disk growth). Several changes need an operator action on an existing cluster; read
**Upgrade notes** first.

### Upgrade notes

- **Worker VM memory is 8GB (#597).** `WORKER_MEMORY` goes from 6144 to 8192 (masters stay at 6GB), so
  the host needs about 42GB for the VMs (README: 48GB+ RAM). A running VM keeps its old `memsize`, so
  an existing cluster needs a rolling restart, one worker at a time: cordon and drain, `vagrant halt
  <worker> && vagrant up <worker> --no-provision`, uncordon, then check that allocatable memory is
  about 7.3GiB before the next worker. Because `narwhal-db` now requires anti-affinity (#590), check
  where the CNPG primary runs before each drain, switch it over if it is on that worker, and never take
  down two workers at once. Full procedure and rollback: `docs/vagrant/operations.md` section 13.
- **NFS export moves to a dedicated disk on master-1 (#605, #606); existing clusters need an owner-run
  maintenance window.** Moving the export needs master-1 powered off to attach the disk, and every
  NFS-backed workload stops meanwhile. The estimate is 30 to 60 minutes for under 100GB of data and was
  not measured. Take the Velero backup, a CNPG logical dump and an etcd snapshot first, then follow
  `docs/vagrant/operations.md` section 15 (it reuses steps 3 to 7 of
  `docs/runbooks/nfs-xfs-migration.md`). **Never set `NARWHAL_NFS_DISK_GB=0` on a machine that already
  has the disk:** the Vagrantfile aborts, because booting without it would export the stale copy left
  on the root disk. A new cluster gets the disk (default 200GB) automatically.
- **Keycloak: migrate to the permanent master-realm admin (#598).** A fresh install does this in
  Phase 2. An existing cluster runs `scripts/cluster/11-1-keycloak-admin.sh` once on master-1; it
  creates `platform-admin`, stores the credentials in `iam/keycloak-admin`, checks that the new account
  can call the admin API and only then deletes `temp-admin`. It is idempotent. Until you migrate, the
  scripts fall back to `iam/keycloak-initial-admin`. Steps and recovery:
  `docs/runbooks/keycloak-permanent-admin.md`.
- **Restart ztunnel and the waypoints to get the 168h mesh certificate TTL (#601).** The new TTL is
  requested by the clients, so certificates issued before the sync keep the old 24h lifetime until
  `kubectl -n istio-system rollout restart daemonset/ztunnel` and a rollout restart of each `waypoint`
  Deployment. Verify that `istio_agent_cert_expiry_seconds` is close to 604800. Steps:
  `docs/vagrant/operations.md` section 14.
- **Apply `gitops/resources/narwhal-db.yaml` by hand (#590, #602, #605).** No Argo CD Application owns
  the CNPG `Cluster`; `14-gitops-bootstrap.sh` applies it, so a push to Gitea and a sync do not change
  the live object. Run `kubectl apply -f gitops/resources/narwhal-db.yaml` to get required hostname
  anti-affinity, the longer liveness and isolation checks, explicit memory requests and limits, and
  the PodMonitor. The CNPG instances restart in a rolling fashion. First confirm the two instances run
  on different workers, otherwise one stays Pending.
- **Delete the `trivy-server` StatefulSet before Argo CD syncs the new chart (#584).** Its cache moved
  from an `emptyDir` to a 5Gi `nfs-csi` PVC, and `volumeClaimTemplates` is immutable:
  `kubectl -n security-system delete sts trivy-server` (and clear any pending sync retry). The old
  cache held nothing worth keeping.
- **Reschedule Prometheus, Tempo and Loki once (#586).** The new preferred anti-affinity only applies
  when a pod is scheduled. Delete `tempo-0`, `loki-0` and the Prometheus pod one at a time and check
  placement with `kubectl -n monitoring get pod -o wide`. Tempo and Loki run without persistence, so
  un-flushed data is lost, as in an OOM restart.
- **Re-run `08-5-registry.sh`, `11-2-keycloak-config.sh` and `11-3-keycloak-clients.sh` on an existing
  cluster.** `08-5` creates the
  Harbor robot account and the retention and GC settings the Trivy DB refresh uses (#603). `11-3`
  generates the Velero UI JWT signing key (#555) and binds the `cluster-admin`-only login flow to the
  `velero-ui` client (#599). `11-2` sets `loginTheme` and `adminTheme` to `narwhal` on the `narwhal` and
  `master` realms, which the admin console branding needs (#572).
- **Re-push Gitea directories that were mirrored with the old script (#578).** Before this release,
  `push-to-gitea.sh <dir>` nested an existing directory (`<dir>/<basename>/...`) and
  never propagated deletions. Running it again for that path now replaces the Gitea copy exactly.

### Added

- **`csp/kakao-cloud/terraform/destroy-all.sh` tears down the whole Kakao Cloud deployment
  (D-KAKAO-TEARDOWN).** It releases the six `prevent_destroy` guards (public IPs and the NFS data
  volume), runs `tofu destroy` and restores the guards on every exit it can catch (success, a cancel,
  a failure, Ctrl-C, TERM, HUP, QUIT; only SIGKILL escapes); R292 exercises it against a stub `tofu`. The terraform README's
  "Cleanup" section now lists what a full teardown deletes, data included: every NFS-backed PVC
  with the volume, and the in-cluster Velero and CNPG backups with the worker disk that holds the
  SeaweedFS object store. It also tells a rejected VPC delete (`ACTIVE`) from a slow one
  (`PENDING_DELETE`), and the manual option B now says how to keep the NFS volume, whose own
  guard otherwise blocks a full destroy.
- **A dedicated NFS data disk on master-1 (D-NFS-VMDK, #605, #606).** etcd and the NFS-backed PVs (17 per the incident report)
  shared one VMDK, and an NFS write burst pushed etcd WAL fsync past 8 seconds (2026-11-08), which
  produced API timeouts, CNPG liveness restarts and a failover. The Vagrantfile now attaches a sparse
  200GB disk (`NARWHAL_NFS_DISK_GB`) and `01-nfs-server.sh` formats it XFS with `prjquota` and mounts it
  by UUID; `NFS_DATA_DEVICE=auto` picks a disk only when exactly one has no partition, mount or
  filesystem, and fails otherwise. On vmware_desktop the disk is attached with `nvme0:1` vmx keys and
  a `before up/reload` trigger that runs `vmware-vdiskmanager`, because `config.vm.disk` crashes
  `vagrant up master-1` on vagrant-vmware-desktop 3.0.5 (#606); VirtualBox uses `vm.disk`. Booting with
  `NARWHAL_NFS_DISK_GB=0` once the disk exists is refused (R327). The migration runbook is
  `docs/vagrant/operations.md` section 15; see Upgrade notes. Not verified: a fresh `vagrant up` or
  `reload`, the auto device pick on a new cluster, VirtualBox, and whether etcd fsync improves (both
  VMDKs sit on the same host SSD).
- **Alert and host check for VM disk growth (D-VM-DISK-GUARD, #582).** Worker-3's vmdk reached 298GiB
  on the host while the guest used 43G, because a growable linked-clone vmdk never gives freed blocks
  back and the NVMe has no discard. `NodeDiskWriteRateHigh` fires when a worker's 6h average write
  rate stays above 20GB/day for 30 minutes. `scripts/ops/check-vm-disk.sh` compares each VM's host
  vmdk directory with the guest `df` and warns at a 30G gap, a vmdk of 120G or more, or under 40G of
  host free space (`--strict` exits 1); `scripts/up.sh` runs it warn-only. The reclaim runbook is
  `docs/vagrant/operations.md` section 12 (R309). Not verified: that the alert fires on a real burst.
- **Permanent Keycloak master-realm admin (D-KC-ADMIN-573, #598).** The new critical Phase 2 step
  `11-1-keycloak-admin.sh` creates `platform-admin` with only the realm role `admin` (the same rights
  as `temp-admin`), stores it in `iam/keycloak-admin` without printing it, proves the login against
  `get realms/master`, and only then deletes `temp-admin` (it refuses if both names are equal).
  `11-2`, `11-3`, `13-2`, `test-sso.sh`, `verify-cluster.sh` and `show-credentials.sh` read
  `keycloak-admin` first and fall back to `keycloak-initial-admin`; the portal and the operator do not
  use it. Runbook: `docs/runbooks/keycloak-permanent-admin.md` (R321). Not verified: a real run of
  `11-1`, and that `temp-admin` does not return after a Keycloak pod restart.
- **The Trivy DB mirror refreshes itself, with bounded Harbor storage (D-SECDB-AUTO, #603).** The
  CronJob `trivy-db-refresh` runs every 6 hours, copies only `trivy-db:2` and `trivy-java-db:1` into the
  Harbor `trivy-db` project with a digest-pinned skopeo 1.22.2, skips an already current mirror and
  records the approver as `auto-refresh`. It uses a Harbor robot account scoped to that project (created
  by `08-5`). `08-5` also sets a retention rule that keeps the 2 newest artifacts per repository
  (untagged included) and a GC schedule. Egress-less clusters (Kakao) keep the manual
  fetch, promote and push gate. New alerts: `TrivyDbRefreshFailed` and `TrivyDbRefreshStale` (R322).
  The earlier manual path was also fixed (#550): `push-security-db.sh` now logs in over HTTPS with the
  Harbor admin credential, and `08-5` warns with the exact commands when `trivy-db` holds fewer than
  3 repositories (the Security dashboard and the Compliance infra view had shown no data). Not verified:
  a real CronJob run and the Harbor API calls.
- **Alert when `trivy-server` keeps re-downloading a stale mirrored DB (D-SECDB-STALE, #588).**
  `TrivyServerDbRedownloading` fires when its filesystem writes grow by more than 6e9 bytes in 3 hours,
  that is two or more downloads, because the live cluster exports no `trivy_*` metrics (R316). The
  refresh procedure is in `scripts/airgap/README.md`.
- **Velero metrics and backup alerts (#511).** Prometheus held no `velero_*` series. The Velero chart's
  ServiceMonitor is on, a `velero-metrics` PeerAuthentication makes only port 8085 permissive for the
  server pod, and a NetworkPolicy admits only the `monitoring` Prometheus pods to that port. Alerts
  fire on failed, partially failed and stale backups.
- **CNPG monitoring and alerts (D-CNPG-MON, #602, #604).** WAL archiving failed for about 20 hours
  without notice because `monitoring.enablePodMonitor` was off. `narwhal-db` now enables the PodMonitor
  and Prometheus selects PodMonitors in all namespaces. New alerts: a WAL archive backlog
  (`cnpg_collector_pg_wal_archive_status{value="ready"}` positive for 30 minutes, critical),
  `CNPGMetricsUnavailable` (15 minutes), `CNPGInstanceNotReady` and `IstioWaypointCertExpiringSoon`
  (certificate under 24 hours left, #601). The first WAL alert used `cnpg_pg_stat_archiver_*`, a series
  the live cluster never had, so it could not fire; #604 switched to the metric that was observed
  live (R324, R326). `CNPGClusterNotHealthy`, which always fired because of `absent()`, is fixed.
- **The portal lists OSS updates and the Trivy DB state.** Settings gets an OSS updates tab with the
  declared (a committed snapshot of `VERSIONS.md`, checked by `version-check.yml`), running and
  upstream versions, a 7-day cooling rule and a request flow that generates the upgrade issue text; the portal
  never changes the cluster (#552). The Security dashboard gets a cluster-admin-only Trivy DB card with
  tag, age and fresh, stale or missing status against the 7-day SLO (#554), which lists every mirrored
  source and shows trivy-checks as pinned (#570). The Kyverno policy table explains Enforce and Audit
  and opens a detail panel with the description, category, severity and per-rule scope (#547).
- **Live-edit portal in the local cluster (#529).** The dev Deployment is generated from the chart and
  kept by a field manager, so Argo CD no longer reverts the dev image within 30 seconds, instead of the
  hand-copied `skaffold dev` manifest that had gone stale.
- **The air-gap flow requires mirroring a pin by digest before it goes live (#468, #515).** Kakao
  nodes have no upstream egress, so a bumped pin whose image is missing from the bastion mirror hit
  `ImagePullBackOff` on the next reschedule. The `narwhal-version-upgrade` skill, `scripts/airgap/README.md`
  ("Mirror a pin bump") and the agent rules now require `skopeo copy --all --preserve-digests` and a
  node-side manifest HEAD, and `scripts/test/check-mirror-pins.py` checks the mirror read-only.
- **`check-bundle.sh` requires the exact chart version (D-BUNDLE-PIN-VERSION, #528).** A bundle that held
  only an older `falco` or `headlamp` chart passed, and a local `up.sh` failed in
  `14-gitops-bootstrap` after about 40 minutes. It now stops before any `vagrant up` and names each
  missing `<chart>-<version>.tgz`.
- **Keycloak login theme unified with the portal, branded admin console (#572).** The login theme is now a
  skin over `keycloak.v2` that copies the portal tokens and ships Pretendard (OFL licence), and the admin
  console gets the same branding. The theme source moved into the `narwhal-platform` chart
  (`gitops/charts/narwhal-platform/keycloak-theme`) and renders as two ConfigMaps with a checksum
  annotation, so any theme change rolls Keycloak; `gitops/resources/keycloak-theme` was removed. R306
  checks that the ConfigMap keys match what the Keycloak CR mounts.
- **The `dev` namespace is under GitOps (D-DEV-OWNER, #534).** The new Application `dev-namespace`
  applies `dev-namespace.yaml` (mesh opt-out, `dev-quota`, `dev-limits`, RBAC); live, `dev` had no
  label, quota or limits. R301 checks ownership and sync-wave order.

### Changed

- **Falco is enabled again on every provider, upgraded from 0.39.2 (chart 4.16.0) to 0.45.1 (chart
  9.2.1) (D-FALCO-045).** It was disabled on 2026-07-08 because 0.39.2's `modern_ebpf` probe failed
  `scap_init` on kernel 7.0. 0.45.1 carries the upstream kernel-7.0 fixes (falcosecurity/libs#2997,
  falcosecurity/libs#3021, falcosecurity/libs#3086 and falcosecurity/libs#3119); in a local kernel
  7.0.14 (linuxkit) run it opened the probe where 0.39.2 still fails with the July signature. The
  migration is breaking: the chart refuses `falco.grpc`, container metadata comes from the `container`
  plugin, and falcoctl is off, so the rules (falco-rules 5.2.0) and the plugin are the ones baked into
  the image and nothing is pulled at runtime. falcosidekick is overridden from the subchart's 2.32.0 to
  2.35.0 (D-FALCO-SIDEKICK): 2.32.0 predates falcosecurity/falcosidekick#1366 (vulnerable Go modules,
  fixed in 2.34.0) and falcosecurity/falcosidekick#1390 (multi-output race, fixed in 2.35.0), and trivy
  CRITICAL/HIGH drops from 9/80 to 3/19. It writes JSON lines to Loki (`format: json`), which is what
  the portal parses, and the portal's priority filter matches the Capitalized label falcosidekick
  writes (the old lowercase selector matched nothing). The Alertmanager output now posts to
  `/api/v2/alerts`: the chart default `/api/v1/alerts` gets 410 Gone from Alertmanager 0.27 and later
  (the cluster pins 0.33.0), so every Critical alert was dropped; against a real Alertmanager v0.33.0
  the Critical event now shows in `GET /api/v2/alerts` with `source=falco`. The Security page no
  longer hides the Runtime Events tab on Ubuntu 26.04, and `14-gitops-bootstrap.sh` no longer removes
  Falco there. Checked in a local Docker lab (real Falco, falcosidekick and Alertmanager), not on a
  cluster: the chart tarball, two images and the GitOps push must still reach a cluster, and Falco on
  the Ubuntu 26.04 / kernel 7.0 Vagrant box and on the Kubernetes CRI path has not been observed.
- **Portal is pinned to `narwhal-portal:1.1.9` (was 1.1.0; 1.1.1 to 1.1.8 were pinned in between by
  #467, #477, #481, #502, #520, #561, #580 and #596; 1.1.9 is #608).** 1.1.9 bumps `lucide-react` to 1.66.0 (#539) and
  lists the `skopeo` image in the OSS versions list (#603), with no user-facing feature change; all carry
  the 1.1.8 and earlier fixes. The image is Cosign-signed with the repository key (index digest
  `sha256:72a1f4f4…`). Mirror it to a cluster bastion by digest before the pin goes live there
  (`scripts/airgap/README.md`, "Mirror a pin bump"); the local Vagrant cluster pulls from ghcr.io directly.
- **Worker VMs have 8GB of memory and 4 vCPU, on base box 0.3.1 (D-WORKER-MEMORY, #597, #531).** Worker
  allocatable memory was about 5.3GiB and nodes hit the kernel OOM killer repeatedly on 2026-11-05 and
  06. `WORKER_MEMORY` is now 8192; the VM total grows from 36GB to 42GB on a 64GiB host (R323). Box
  `dasomel/ubuntu-26.04-xfs` moves from 0.1.0 to 0.3.1 (Ubuntu 26.04.1, kernel 7.0.0-42), resolved
  from the kube-ready-box GitHub Pages catalog with a sha256 because Vagrant Cloud stops hosting
  boxes on 2026-12-31 (D-BOX-CATALOG); 0.3.x ships arm64 `vmware_desktop` only. See Upgrade notes.
- **Trivy scans write far less to node disk (#574, #575, #584).** A daily report expiry (`scannerReportTTL` 24h) re-scanned about 114 reports,
  and every scan Job downloaded the roughly 1GB Trivy DB; worker-3 wrote 517GiB in 3 days. Now
  `scannerReportTTL` is 168h, the operator runs with `builtInTrivyServer: true` (scan Jobs query the
  server instead of pulling the DB), and the operator has real resources (requests 100m/384Mi, limit
  1Gi; chart 0.27.0 ignored the old `operator.resources` key and the operator was OOMKilled 33 times in
  22 hours). `operator.scanJobsRetryDelay` is 6h, so scans that hit the docker.io anonymous quota no
  longer retry every 30 seconds, Velero's ephemeral workloads (kopia maintenance Jobs, data-path pods)
  are skipped by label, and the server cache is a 5Gi `nfs-csi` PVC excluded from Velero. Live, 66
  minutes after the change the scan Jobs wrote nothing and `VulnerabilityReport`s grew from 94 to 114;
  24 hours have not been measured. Disk already written to a vmdk cannot be reclaimed in place.
- **The backup path survives node memory pressure (D-sw-qos, D-datapath-qos, #583).** On 2026-11-06
  worker-3 hit a global OOM during a Velero backup and killed the BestEffort `weed` filer, so S3
  returned 500 and PodVolumeBackups failed. SeaweedFS master, volume, filer and s3 now have cpu and
  memory requests and memory limits, and `velero-node-agent-config` sets `loadConcurrency.globalConfig: 1`
  and `podResources`. Schedules, volume policies and backed-up volumes are unchanged (R310). Not
  deployed against a live backup run.
- **Prometheus, Tempo and Loki are spread across workers and Loki is Burstable (D-MON-QOS-LOKI,
  D-MON-SPREAD, #586).** Prometheus was OOMKilled 16 times in about 3 hours while all three sat on
  worker-3, and Loki had no requests or limits. Loki gets requests 100m/256Mi with a 768Mi limit (its
  rules sidecar 10m/64Mi, limit 192Mi), and each of the three gets a preferred (weight 100, never
  required) hostname pod anti-affinity. Retention, storage and scrape config are unchanged (R311).
  See Upgrade notes. Not verified: real placement after the reschedule.
- **Mesh workload certificates live 168 hours (D-MESH-CERT-TTL, #601).** A sleeping host freezes the
  VMs and the renewal timers, and on 2026-11-07 six ztunnels and three waypoints ran with expired
  certificates, CNPG went not Ready and WAL archiving failed for about 20 hours. The TTL is chosen by
  the client, so `SECRET_TTL` is 168h for ztunnel and, through `meshConfig.defaultConfig.proxyMetadata`,
  for waypoints, with `MAX_WORKLOAD_CERT_TTL` 720h on istiod; GitOps and `09-istio-ambient.sh` agree.
  The cost is that a stolen workload key stays valid for 7 days instead of 1. `up.sh` warns when
  host sleep is on. Docs: `docs/vagrant/operations.md` section 14, `docs/common/troubleshooting.md`
  10.2. Not alerted: ztunnel certificate expiry (no metric, and the Loki ruler has no
  `alertmanager_url`).
- **Narwhal's CNPG cluster requires anti-affinity and tolerates probe stalls (D-CNPG-AA-REQUIRED,
  D-CNPG-RESTART-AMP, D-CNPG-RESOURCES, D-CNPG-APPDB, #590, #605).** Both `narwhal-db` instances once
  ran on worker-1, so a worker drain blocked WAL archiving. `podAntiAffinityType` is now `required`
  on `kubernetes.io/hostname`. The cost: draining one worker leaves the displaced instance only the
  third worker, and it stays Pending if that worker has no free requests or two workers are down
  (`docs/common/database.md`). Liveness waits about 120 seconds, the isolation check is 5 seconds, memory
  requests and limits are explicit (no CPU limit), and `bootstrap.initdb` is `narwhal`/`narwhal` like
  the live database; the repo still said `authentik`, which does not exist and made every default
  monitoring query fail with `database "authentik" does not exist`. `narwhal-db.yaml` and `07-cnpg.sh`
  change together (R313). The remaining default-query errors on a live cluster have not been
  re-checked.
- **`kyverno-policies` ignores only the fields Kyverno defaults (D-KYV-IGNORE-NARROW, #589).** The old
  `ignoreDifferences` hid rule bodies, `validationFailureAction`, annotations and labels because
  Kyverno was assumed to inject autogen rules into the spec; Kyverno 1.18 writes them to
  `.status.autogen`, which Argo CD does not compare. Ten live policies differed from git only in
  the Argo tracking annotation. Only `admission`, `emitWarning`, `skipBackgroundRequests`,
  `allowExistingViolations` and the verifyImages `useCache` and `signatureAlgorithm` stay ignored, and
  `add-default-securitycontext` states `Audit` explicitly (already live). `headlamp-policy` and the
  `narwhal-platform` template keep the broad lists. Eight ClusterPolicies gained the standard title,
  description, category and severity annotations (#569).
- **Harbor tolerates scan bursts (#567).** Probes and resource requests were added so Harbor
  components are not restarted or starved while Trivy scans run.
- **The portal's certificate renewal RBAC is namespace-scoped (D-CERT-RENEW-NS, #592).** `update` on
  `certificates/status` moved from the `narwhal-portal` ClusterRole to a Role and RoleBinding in
  `devtools`, `platform-system` and `storage`, the namespaces that hold Certificates (R318). A
  Certificate in any other namespace gets a 403, which the portal shows as not permitted; add a Role
  there.
- **Gitea browser login uses Gitea's own Keycloak OAuth2 source (D-GITEA-OAUTH, #496, #498).** Groups
  now map on every login (`cluster-admin` to site admin, `developer` to `narwhal/Developers`, `viewer` to
  `narwhal/Viewers`, `guest` restricted) and removed memberships are dropped; reverse-proxy
  authentication is off. `REVERSE_PROXY_TRUSTED_PROXIES` is kept for client-IP trust only
  (D-GITEA-CLIENT-IP). The old `gitea admin auth delete --name` never ran (the flag does not exist), so
  re-runs failed silently; the helper now updates the source in place.
- **Headlamp is upgraded from v0.42.0 to v0.46.0 with the built-in OIDC auto-login (#497).** It replaces
  the gateway script injection from #478.
- **Gateway admin UIs and Velero UI follow the `cluster-admin` group.** Hubble, Prometheus, Alertmanager and
  the NFS quota agent return 403 to every Keycloak user outside `cluster-admin` (before, any realm
  user, a viewer included, could create Alertmanager silences; #535). Velero UI login itself is
  limited to `cluster-admin` by a `velero-ui/login` client role and a dedicated `velero-ui-browser`
  authentication flow (D-VELERO-UI-LOGIN-563, #599); the flow is bound to that client only because a
  gate inside a copied browser flow is skipped by SSO cookie sessions. Direct visits to the OpenBao and
  Velero UI root redirect to `/sso` when there is no query string (#472). Admin
  top rights: Grafana `GrafanaAdmin` and Keycloak `realm-admin` for `cluster-admin`; the permission
  matrix now states that developer cannot list namespaces or nodes (#548).
- **Kakao external ingress goes through the master load balancer (D-INGRESS, #473).** The worker LB
  public IP stopped answering from the internet while the cluster was unchanged; the master LB
  forwards 80/443 to the same APISIX NodePorts and all 14 domains answered through it. Its target
  groups have no health monitor, so a failed master can cause intermittent failures; `INGRESS_LB=worker`
  returns to the old path. `setup-hosts-kakao.sh --apply` probes port 443 first (`SKIP_PROBE=1`
  overrides).
- **Kube-apiserver OIDC is applied on every control-plane node and survives kubeadm upgrades (#500,
  #521, #523, #495).** Only master-1 had the seven `--oidc-*` flags, so Dashboard, Headlamp and kubectl
  tokens failed whenever the Service reached master-2 or master-3 (401). `11-4b` writes them on each
  node (atomic move, bounded `readyz` wait), `11-4c` merges them into the `kubeadm-config`
  `ClusterConfiguration` so `kubeadm upgrade` keeps them, `distribute-join-kakao.sh` compares the CA
  hash before a control plane joins, and `11-3-keycloak-clients.sh` is a critical Phase 2 step.
- **Portal changes since 1.1.1 (#475, #479, #501, #514, #546, #547, #549, #556, #559, #566, #581).**
  Architecture and cluster pages authenticate with the projected SA token; the permission table shows
  role-default tools; the Security page paginates `VulnerabilityReport`s (a single response was
  10.7MB and exceeded the 8MiB limit) and retries on 429, marking partial results; the matrix's
  sticky columns are opaque; DORA lead time keeps minute precision and older history rows show a dash
  because Argo CD records a phase only for the latest operation; lead time reads Gitea through the
  new `gitea-portal-api` gateway rule (token-authenticated GET and POST on three paths only; R305)
  and shows N/A for Helm-sourced apps; the Cost service breakdown attributes every pod
  (kube-state-metrics now exports pod labels). Releases 1.1.6 to 1.1.9 were cut in this period;
  1.1.9 carries the lucide-react bump, the `skopeo` entry in the OSS list and the 8GB wording
  (#607).
- **`push-to-gitea.sh <dir>` mirrors a directory exactly and propagates deletions (D-GITOPS-PUSH-DIRS,
  #578).** On 2026-11-05 it nested an existing destination, which left Keycloak unable to mount its
  theme for 12 hours. It now removes the destination in the clone and copies, and refuses `..` and
  absolute paths. Mirror-all mode stays additive on purpose, because the Gitea repository also holds
  resources that exist only there (R308). Not exercised against a live Gitea.
- **Operator docs follow the 1.3.0 behaviour (#451, #452, #541).** The disaster-recovery procedure no
  longer promises "no data loss": it lists, per data set, the copy to take outside the cluster before
  `vagrant destroy` and what is not restored (the five volumes excluded from Velero; the in-cluster
  SeaweedFS is not an external backup). The release notices and indexes, the Velero commands in
  `operations.md` and the OS and containerd statements were corrected. The Falco arm64 TOCTOU warning
  (`libpman` cannot attach `creat` and `open`, which arm64 lacks) is documented as benign, because
  the `openat`, `openat2` and `connect` programs attach.
- **Portal browser E2E jobs time out after 15 minutes and `harden-runner` is pinned to v2.22.3
  (D-job-timeout, #465, #508).** A blocked-egress checkout had hung a job for about 25 minutes twice;
  the egress policy and allowlists are unchanged and a timeout still fails the gate.

### Fixed

- **Falco no longer floods Loki with API-server connections from the platform's own clients
  (D-FALCO-NOISE).** Twenty hours after the 0.45.1 rollout, live Loki held 47,180 events and 47,178 of them
  were the bundled NOTICE rule "Contact K8S API Server From Container", all from components whose job is to
  call the API. The rule now carries one appended exception (`customRules`, mounted on
  `/etc/falco/rules.d`) for 8 exact (namespace, image) pairs: velero-ui, narwhal-portal, keycloak-operator,
  kyverno, headlamp, the Kubernetes dashboard auth and web, and cloudnative-pg. Every other pod still fires,
  the priority floor is unchanged, and the kube-apiserver audit log still records those clients' writes and
  their reads of secrets, configmaps, serviceaccounts and RBAC objects (other reads are not audited).
  Validated with `falco -V` on the real falco:0.45.1 image and in a local lab (the 5 non-docker.io pairs
  silent, any other namespace or image still flagged; the 3 docker.io pairs are checked for syntax only,
  because the lab's Docker engine drops the `docker.io/` prefix that CRI keeps); R291 A11 pins the exception. Static change: the DaemonSet rolls on the
  next sync, and the behaviour on the Kakao cluster has not been observed.
- **Istio-cni retries ambient enrolment, and the verify script flags ztunnel-denied kubelet probes
  (D-576-DETECTION-RETRY, D-576-PROBE-DENY, #576, #577).** After node restarts ztunnel rejected the
  kubelet probes of `storage/seaweedfs-volume-0` and `storage/openbao-agent-injector` (policy
  `istio_converted_static_strict`), because the istio-cni agent can miss a pod that starts during node
  boot. The volume pod's headless DNS record dropped, kopia failed with `unexpected EOF` and Velero
  backups were at risk. `enableAmbientDetectionRetry: true` is set in the Application and in
  `09-istio-ambient.sh`; `verify-cluster.sh` reports the rejections; `troubleshooting.md` has the
  recovery step (R307). Not exercised on a real node reboot.
- **Platform `*Down` alerts can fire (D-DOWN-WORKLOAD, #525, #526).** KeycloakDown, ArgoCDDown,
  HarborDown, GiteaDown and TraefikDown joined `kube_pod_labels` series that did not exist
  (kube-state-metrics exported no pod labels), so none of them could fire, and TraefikDown watched a
  component APISIX had replaced. They now check workload availability.
- **The `loki-canary` DaemonSet is disabled (D-LOKI-CANARY-DISABLE, #587, #591).** `lokiCanary` is a
  top-level key in the chart; the nested `monitoring.lokiCanary.enabled: false` was ignored and a
  canary ran on every node (R315).
- **Phase 2 stops when OpenBao OIDC is not persisted (#544).** `auth/oidc/config` was rejected at
  first provision with an issuer mismatch (`...:9443/realms/narwhal`), the failure was swallowed by
  `&& echo ok || echo WARN`, and OpenBao SSO then failed with `could not load configuration`. The block
  now runs under `set -e`, retries 6 times 10 seconds apart and exits 1; `test-sso.sh` asks OpenBao
  for an `auth_url` and passes only on the Keycloak realm URL. The cause of the `:9443` issuer, the
  bootstrap Keycloak CR (hostname without scheme, `strict: false`), is not fixed.
- **Seamless SSO on Kakao and in the SSO checks (#478, #494, #548, #533, #593).** The bastion dnsmasq
  now restarts on failure and waits for the network (nodes could not resolve the Keycloak issuer for the
  apiserver); an empty Keycloak client-secret read now fails the script instead of writing an empty
  Secret (the cause of the empty `grafana-oidc-secret` and `gitea-oidc-secret`). Argo CD
  `policy.default` is empty so a guest is denied (it could list 46 applications), Harbor verifies the
  OIDC certificate, and the Harbor `developer` and `viewer` groups are mapped after OIDC is on (before,
  the mapping ran under `db_auth`, was rejected silently, and those users had no project access).
  `test-sso.sh` treats a Dashboard API 403 inside an HTTP 200 as denied, and `08-6-tls-routes.sh`
  restarts `harbor-core` only when the CA actually changed. A new `docs/common/sso-accounts.md`
  documents the seed accounts and troubleshooting.
- **Portal certificate renewal works (D-CERT-RENEW-ISSUING, #549).** Settings, Certificates, renew
  always failed: the portal patched `spec.renewBefore: "1s"`, which the cert-manager webhook rejects
  below 5 minutes, and could only `get` certificates. It now sets `Issuing=True` with reason
  `ManuallyTriggered` through `PUT /status`, as `cmctl renew` does, and the ClusterRole got only
  `update` on `certificates/status` (narrowed to three namespaces by #592).
- **Velero schedules skip `emptyDir` volumes (#422, #455).** On kakao-narwhal 107 of 115
  PodVolumeBackups were `emptyDir` volumes and took about 9,770 seconds of the backup window; a
  `velero-volume-policy` ConfigMap now skips them.
- **The Cost page prices Kakao (#493).** Portal 1.1.1 and later returns 503 unless eight `COST_*`
  keys exist, and `13-2-narwhal-portal-bindings.sh` set only three; it now sets all eight per provider.
  `node-collector` is tracked in the air-gap inventory.
- **Live test suites stop reporting false failures (#510, #518, #524, #530, #536).** Dead checks for
  oauth2-proxy and Traefik ForwardAuth are removed, the wildcard TLS check expands, the ambient
  namespace list matches what `09` enrols (`dev` is deliberately not ambient), and a Keycloak login
  form that cannot be parsed fails instead of passing. `test-sso.sh --section=acl` gained 16 live checks
  for the four gateway admin UIs.

### Security

- **Velero UI no longer signs sessions with a public key or hands every user manage-all (#555,
  #594, #599).** The app fell back to the upstream hard-coded JWT passphrase because
  `AUTH_SECRET_PASSPHRASE` was never set, so anyone could mint a token; `11-3` now generates a key with
  `openssl rand -base64 32` into `velero-ui-oauth` (a token forged with the old key gets 401). With
  `POLICY_FILE_PATH` set but no policy file, every logged-in user, guest included, could create and
  delete backups; the policy is now `g,cluster-admin,manage,all` and a missing file blocks pod start.
  The gateway returns 403 for `POST /api/backups/download` (no upstream policy check, and a guest got a
  real download URL; bulk download is lost) and for `DELETE /api/settings/velero/plugins/:name` (an
  upstream stub that the UI never calls). Because the app does its own OAuth, the real boundary is the
  `cluster-admin`-only login flow (#599): `/api/stats/*`, `/api/form/*`, `/api/settings/velero/*` and
  `/api/config` stay callable by anyone who can log in, upstream CORS still reflects any origin, and
  `docs/common/app-sso-permission-matrix.md` records the endpoint exposure.
- **Gateway admin UIs refuse users outside `cluster-admin` (#535, #536, #545).** A shared
  `gatewayAdminOnly` helper strips a client-supplied `X-Userinfo` before `openid-connect` and checks the
  group afterwards. R302 now renders every chart, raw manifest, plugin config and global rule, and
  catches a route in another chart without the restriction.
- **Portal dependencies moved past published advisories.** Auth.js (`next-auth` 5.0.0-beta.32,
  `@auth/core` 0.41.3) for two critical advisories on the Keycloak login path, including auth checks that
  fail open on configuration errors (#461); `zod` 4.6.7 and `undici` 7.30.1 (#471); `fast-uri` 4.2.3
  (#492); and the unused `hono` overrides are removed (#543).
- **Keycloak no longer keeps a temporary bootstrap admin (#598)** and Harbor now verifies the OIDC
  certificate (#548); see Added and Fixed.

### Known issues

- **Host disk already grown cannot be reclaimed in place (#574, #582).** A vmdk written once stays
  allocated and the linked clone cannot shrink; only recreating the worker VM returns the space
  (`docs/vagrant/operations.md` section 12). A worker such as worker-2 can still grow from Trivy scan
  writes (18 of 21GB per 24 hours of container writes were measured after #574 and #575, before #584).
- **Trivy scan Jobs for `docker.io/*` images fail with UNAUTHORIZED** on the anonymous pull quota
  (velero, busybox and dashboard images, #575), and each scan Job still writes the Java DB.
- **Unverified on a live cluster:** the Trivy refresh CronJob and Harbor retention (#603), the Keycloak
  `11-1` migration (#598), the NFS disk attach on a fresh `vagrant up` (#606), the ambient detection
  retry on a node reboot (#577), the Harbor CA restart (#593), the Dashboard forbidden-in-200 check
  (#593), the monitoring placement after the reschedule (#586), and the backup path under memory
  pressure (#583).
- **Mesh certificate expiry has no alert for ztunnel** (#601), and the 168h TTL widens the lifetime of
  a stolen workload key from 1 to 7 days.
- **Draining a worker can leave a CNPG instance Pending** if the one remaining free worker has no room
  for it, or if two workers are down (#590).
- **Headlamp and Kubernetes Dashboard keep the `platform-admin` binding;** widening it to the built-in
  `cluster-admin` waits on an owner decision (#548).

## [1.3.0] - 2026-10-22

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
    cluster, in this order (`docs/common/storage-protection-policy.md` section 8; operator runbook
    `docs/runbooks/openbao-snapshot-backup.md`): (1) re-run
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
- **OpenBao snapshot backup operator runbook (#431, #449).** `docs/runbooks/openbao-snapshot-backup.md`
  covers the activation order including fingerprint pinning, the first-run check, the routine
  freshness check, key rotation and exposure notes, a pointer to the restore procedure, and
  troubleshooting. The OpenBao snapshot backup is still not enabled on any cluster and its live
  behavior is unverified; see Known issues.
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
  live object. Observed on one cluster: at 2026-10-21 02:00:00 UTC the corrected schedule produced
  exactly one `Backup` (`narwhal-db-backup-20261021020000`, completed) and the hourly runs stopped
  (`Backup` objects 1311 to 1312); the existing `Backup` objects were not deleted.
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
  OpenSSF Scorecard flags as Dangerous-Workflow (#395, #433). The tag is fetched with git, proven to point
  at the run's `head_sha`, and only then added as a `tagged/` worktree; the trust model is unchanged. Not yet exercised by a real `portal/v*` release, and the Scorecard
  score has not been re-scanned.
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
  heredoc.
- **Air-gap bundle copy does not preserve OCI referrers** (SBOM, signature, attestation).
  `09` reports the loss but does not fail (#124).
- **The digest inventory is trust-on-first-save.** `images.digests.json` is not signed, so it
  proves the bundle matches what `02` recorded, not that `02` saw the right bytes (#124).
- **S3 authentication operations: remaining gaps (follow-ups to #412; #414 and #415 are closed).** Verified on kakao-narwhal on
  2026-10-19: the roll marker survives self-heal, the Application is Synced, the anonymous check
  passes, and all four clients pass their per-client I/O check after the S3 server rolled. The
  `08-4-storage.sh` guard was run read-only against that authenticated cluster (it passes); its
  abort paths on an anonymous cluster are covered by fake `kubectl` tests and an independent
  review's simulation, not by a live re-run of `08-4`. Since then #442 applies the client Secrets with server-side apply and removes the key from the
  `kubectl.kubernetes.io/last-applied-configuration` annotation (not re-verified on a live cluster).
  Remaining: the guard's `envFrom` check does not prove that the Loki/Tempo
  pods restarted and loaded the new keys. The same Application also lists the Secret
  `seaweedfs-db-secret` with no comparison status; that is not investigated.
- **Velero volume backups fail on volumes with owner-only files under `root_squash` (#431).** On
  the NFS server, files that other users cannot read were found in `database/` (7677 of 7686),
  `monitoring/` (138 of 1317 at the first measurement, 115 of 1296 later: the data is live),
  `devtools/` (72 of 292) and `storage/` (7 of 77). Do not rely on
  the Velero backups of volumes with owner-only files: OpenBao gets an application-level encrypted
  snapshot from a CronJob instead (#443, not enabled on any cluster yet, not a Velero backup hook),
  PostgreSQL relies on CNPG barman, and the other volumes are not protected by Velero.
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
  Harbor jobservice and SeaweedFS master pods when it is applied. Observed on one cluster (kakao-narwhal): stage 1 (#434, #436) was applied on
  2026-10-19; read-only checks on 2026-10-21 (UTC) showed the scheduled Velero backups of
  2026-10-20 and 2026-10-21 (databases, full, gitea, harbor, openbao) all Completed, and the only
  Canceled `PodVolumeBackup`s (5) belong to the 2026-10-19 runs, none to later runs (345 Completed
  in total). That is an observation, not a guarantee; a full restore of any of these volumes is
  still unverified. #431 stays open for the
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

[Unreleased]: https://github.com/dasomel/narwhal/compare/v1.4.0...HEAD
[1.4.0]: https://github.com/dasomel/narwhal/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/dasomel/narwhal/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/dasomel/narwhal/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/dasomel/narwhal/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/dasomel/narwhal/compare/v0.2.0...v1.0.0
[0.2.0]: https://github.com/dasomel/narwhal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/dasomel/narwhal/releases/tag/v0.1.0
