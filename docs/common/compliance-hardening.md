# Compliance Hardening & Risk-Accepted Exceptions

Status of the portal `/compliance` page (trivy-operator ClusterComplianceReport:
CIS 1.23, NSA 1.0, PSS baseline/restricted). Records what was hardened, and which
failing controls are **accepted risk** (fixing them would break the platform or
undo a deliberate design choice) vs. **deferred** (safe but not yet done).

## Applied hardening (2026-07-08)

| Area | Change | Controls fixed |
|------|--------|----------------|
| Control-plane profiling | `--profiling=false` on apiserver / controller-manager / scheduler (kubeadm `extraArgs` in `scripts/cluster/02-init-cluster.sh` + live static-pod patch on all 3 masters) | CIS 1.2.18, 1.4.1 (verified PASS) |
| Portal / Valkey workloads | `securityContext`: `runAsNonRoot`, drop ALL caps, `allowPrivilegeEscalation:false`, `seccompProfile:RuntimeDefault` (`gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`) | Removed all critical/high config-audit findings for these two workloads (PSS 5.2.x / 5.7.2 contributions) |
| Secret encryption at rest | `--encryption-provider-config` (aescbc) on all 3 apiservers; all 105 existing Secrets rewritten/encrypted; key generated once, shared across masters. Source: `02-init-cluster.sh` (generate+write on master-1) + `02-join-control-plane.sh` (fetch same key). | CIS 1.2.30 (verified PASS; etcd raw shows `k8s:enc:aescbc:v1:key1:`) |
| API audit logging | `--audit-policy-file` + `--audit-log-path/maxage=30/maxbackup=10/maxsize=100` on all 3 apiservers; balanced policy (secrets/rbac at Metadata, health/reads dropped). | CIS 1.2.19/1.2.20/1.2.21/1.2.22 (verified PASS; audit.log writing) |
| Node file permissions | `chmod 600` kubelet service unit + config.yaml + k8s CA + static-pod manifests on all nodes (`scripts/cluster/harden-node-files.sh`). Wired into provisioning path (`02-init-cluster.sh`, `02-join-control-plane.sh`, `02-join-worker.sh`, `03-cni-install.sh`); clean install validation pending. | CIS 4.1.1, 4.1.7, 4.1.9 (verified PASS) |

Result: CIS 72% → 84%+, NSA 44% → 59%+. Control-plane flag controls (profiling, audit, encryption) all PASS.

### Round-3 workload KSV014 fixes (2026-11-18)

Both `storage/velero` server and `iam/keycloak-operator` `KSV014` findings were resolved (neither is accepted or unfixed):

| Workload | Change | Verification and pending scope |
| --- | --- | --- |
| `storage/velero` server (`velero.yaml`, `08-4-storage.sh`) | `readOnlyRootFilesystem: true` with `velero-home` `emptyDir` at `/home/cnb` (`D-velero-rofs`; chart already mounts `tmpdir` at `/tmp`, so no second `/tmp` mount). `node-agent` untouched. | Full `velero-daily-full` backup completed `1774/1774` with no read-only errors (orchestrator, measured 2026-11-18); R362/R366/R370 + offline `helm template` pass. The next two hourly kopia repository maintenance Jobs completed under the read-only root, and their pod spec inherited `readOnlyRootFilesystem: true` with the `/home/cnb` and `/tmp` mounts; a ConfigMap restore from that backup into a scratch namespace completed 4/4 (orchestrator, measured 2026-11-18). No volume-data restore and no clean install were run. |
| `iam/keycloak-operator` (`11-keycloak.sh`) | Strategic patch sets `readOnlyRootFilesystem: true`, `tmp` `emptyDir` volume, and `/tmp` mount (idempotent on re-run and when `volumes` already exists). Keycloak StatefulSet untouched (stock image re-augments at start). | Operator started in `3.6s` (`prod` profile) with no read-only errors and Keycloak CR stayed `Ready=True HasErrors=False` (orchestrator, measured 2026-11-18); R348/R359/R370 stub-`kubectl` tests pass. **Pending:** no clean install was run. |

### Round-4 workload hardening fixes (2026-11-19)

Three remaining `KSV014`/`KSV104` findings were resolved across `security-system/falco`, `storage/velero` (`node-agent`), and `nfs-quota-agent`:

| Workload | Change | Verification and pending scope |
| --- | --- | --- |
| `security-system/falco` (`falco.yaml`, GitOps-only) | `containerSecurityContext` sets both `privileged: true` and `readOnlyRootFilesystem: true` (`D-falco-rofs`; chart `falco-9.2.1` mounts `/root/.falco`, and setting `containerSecurityContext` replaces the chart's computed context). | Without `privileged: true`, Falco failed `scap_init` (`errno: 1`) and crash-looped ~3m (6 restarts); with `privileged: true` repeated, pod reached Ready with 0 restarts, opened the modern BPF probe, and emitted 22 rule events in 2 minutes after a test pod (orchestrator, measured 2026-11-19). Offline `helm template` and R358/R371 pass; no clean install was run. |
| `storage/velero` `node-agent` (`velero.yaml`, `08-4-storage.sh`) | `nodeAgent.containerSecurityContext.readOnlyRootFilesystem: true` with `emptyDir` mounts at `/root` (`node-agent-home`) and `/tmp` (`tmp`) (`D-node-agent-rofs`; `runAsNonRoot: false` preserved). | Full backup from the `velero-daily-full` template Completed `1771/1771` with its 2 PodVolumeBackups Completed, 0 `read-only file system` lines, and 0 restarts (orchestrator, measured 2026-11-19). Offline `helm template` and R362/R366/R370/R371 pass; no volume-data restore and no clean install were run. |
| `nfs-quota-agent/nfs-quota-agent` (`05-nfs-quota-agent.sh`, Helm-only) | `HELM_ARGS` passes `podSecurityContext.seccompProfile.type=RuntimeDefault`, `securityContext.privileged=true`, and `securityContext.readOnlyRootFilesystem=true` directly via chart values (`D-nqa-hardening`; state and audit logs already live on hostPath volumes). | Pod Running with 0 restarts, `"Quota applied successfully"` for every PV, history and cleanup loops started, and no read-only errors (orchestrator, measured 2026-11-19). Offline `helm template`, `nqa_check_skew`, and R371 pass; no clean install was run. |

### Round-5 workload hardening fixes (2026-11-19)

`platform-system/apisix` `KSV014` (`readOnlyRootFilesystem`) was resolved in source across both Phase 2 bootstrap (`scripts/cluster/08-1-networking.sh`) and GitOps (`gitops/charts/narwhal-apps/templates/apisix.yaml`):

| Workload | Change | Verification and pending scope |
| --- | --- | --- |
| `platform-system/apisix` (`08-1-networking.sh`, `apisix.yaml`) | `securityContext.readOnlyRootFilesystem: true` with `init-conf` (`apache/apisix:3.15.0-ubuntu`, matching `securityContext` and `10m/16Mi` req / `100m/64Mi` lim) running `cp -R /usr/local/apisix/conf/. /conf-rw/` (`cp -R` avoids `cp -a` timestamp `EPERM` on `/conf-rw/.`) plus `emptyDir` mounts at `/usr/local/apisix/conf` (listed before `/usr/local/apisix/conf/cert/narwhal`), `/usr/local/apisix/logs`, `/tmp`, and `/usr/local/apisix/{client_body,proxy,fastcgi,uwsgi,scgi}_temp` (`D-apisix-rofs`). | Throwaway pods built from the live APISIX Deployment spec became Ready and served portal/gitea/grafana routes over HTTP 9080 and TLS 9443 (30x from upstreams) (orchestrator, measured 2026-11-19). Offline `helm template` (`apisix-2.13.0.tgz`) and R212/R356/R375 pass. No clean install was run. **Rollout note:** upgrading restarts the APISIX data plane; since PR #736 `apisix-etcd` is persistent so routes survive, and the rollout runbook must still run `scripts/cluster/lib/recover-apisix-resource-sync.sh platform-system --divergence` afterwards. Live on the local single-node cluster (orchestrator, measured 2026-11-19): the serving APISIX Deployment rolled out with the read-only root, all six ingress hosts answered before and after, and the divergence check reported routes 26/26, upstreams 14/14, ssls 1/1. The `alloy-logs` collector now runs with no added capabilities (`capabilities.add: []`, D-alloy-caps; the key must stay present or the chart merges its 14 defaults back): logs from ten namespaces kept arriving in Loki, a new pod's lines were collected, no permission errors. |

## Kubelet API authentication and authorization (Narwhal#113)

Before this change, `scripts/cluster/02-init-cluster.sh` omitted all these
security fields. kubeadm's generated KubeletConfiguration baseline sets anonymous
auth disabled, webhook authentication enabled, authorization `Webhook`, and
`readOnlyPort:0`; `serverTLSBootstrap` also defaulted to false, so serving
certificate rotation was not enabled. kubeadm supplies `Node,RBAC` authorization
and enables NodeRestriction by default; no repository flag overrode either. The
source settings were therefore safe by defaults but implicit. The official
[KubeletConfiguration defaults](https://v1-35.docs.kubernetes.io/docs/reference/config-api/kubelet-config.v1beta1/)
and [kubeadm configuration](https://kubernetes.io/docs/reference/config-api/kubeadm-config.v1beta4/)
document those baselines.

The configuration now explicitly sets `authentication.anonymous.enabled:false`,
webhook authentication and authorization, and `readOnlyPort:0`. It also explicitly
sets apiserver `--authorization-mode=Node,RBAC` and
`--enable-admission-plugins=NodeRestriction`. `protectKernelDefaults` (CIS 4.2.6),
`serverTLSBootstrap` (CIS 4.2.12), and `tlsCipherSuites` (CIS 4.2.13) remain omitted
as open, non-compliant kubelet gaps (see `## Scan coverage` below for rollout
prerequisites). Worker
and control-plane join commands are generated by kubeadm and consume the
cluster-published kubelet and control-plane configuration.

`nodes/proxy` is privileged and non-read-only: it can reach kubelet operations such
as pod logs and exec. The verifier's explicit allowlist includes `system:masters`
(cluster administrators), `system:nodes` (node identities subject to Node
authorization), and these kubeadm/Kubernetes identities:

- `kubeadm:cluster-admins` is kubeadm's administrative group.
- `kube-apiserver-kubelet-client` is the approved apiserver-to-kubelet client.
- `system:kube-controller-manager` is the controller manager's built-in identity.
- `kube-system:generic-garbage-collector`, `kube-system:namespace-controller`,
  and `kube-system:resourcequota-controller` are built-in controllers whose
  bootstrap roles reach `nodes/proxy` only through wildcard resource rules.

The verifier labels wildcard-derived access separately from an explicit
`nodes/proxy` rule. It does not pre-allow the monitoring service account: the checked-in
Prometheus configuration enables kubelet scraping, but repository evidence does not
establish that it needs the apiserver proxy specifically. The verifier reports the
actual role and subjects and fails closed if monitoring has `nodes/proxy`; add its
exact service account only after the live scrape path proves that dependency.
The live `storage:velero-server` service account was bound to `cluster-admin`, which
grants `nodes/proxy` through a wildcard rule. This was a real finding tracked by
Narwhal#226 and was intentionally not allowlisted. **Resolved (v1.3.0, #250/#421):**
the binding `velero-server-least-privilege` in `gitops/resources/velero-server-rbac.yaml`
now grants the generated least-privilege `velero-server` ClusterRole (no `nodes/proxy`)
instead of `cluster-admin`. The finding text above is kept as the historical record of the run.

Exposure finding (historical, at the time of this slice; **resolved by #265**): Kakao's
security group allowed TCP 10250 from the VPC CIDR. It is now limited to the node subnet
(`remote_ip_prefix = var.subnet_cidr` in
`csp/kakao-cloud/terraform/modules/security/main.tf`). Original text: the rule allowed
TCP 10250 from the VPC CIDR (`csp/kakao-cloud/terraform/modules/security/main.tf`),
so any source in that VPC can reach kubelet if routing permits. Vagrant nodes use
the host-only `192.168.56.0/24` network and this repository contains no explicit
host firewall rule for 10250; host reachability should be confirmed at runtime.
MetalLB's `192.168.56.200-220` range is a service VIP range and does not itself
expose a node's 10250 listener. No matching Cilium host policy or nftables rule
was found in the repository. The live kubeadm 1.35 six-node run confirmed anonymous
auth disabled, authorization mode `Webhook`, and TCP 10255 closed on every node. The
verifier initially reported 30 PASS / 7 FAIL: six false positives because configz
omitted default `readOnlyPort:0`, and one allowlist finding containing the approved
built-ins plus the real Velero finding. The updated verifier treats an omitted
`readOnlyPort` as its kubelet default of zero and probes TCP 10255 directly.

### Reconcile an existing cluster

The hardening is applied through the kubeadm init configuration and node file hardening
script. The provisioning idempotency guard skips `kubeadm init` when `admin.conf` already
exists, so an existing cluster does not receive kubeadm config changes automatically. To
reconcile an existing cluster:

1. Run `sudo bash scripts/cluster/harden-node-files.sh` on each node (tightens
   `/etc/kubernetes/pki/**/*.crt` and `*.key`, CNI config files, kubelet unit/drop-ins,
   and `/var/lib/kubelet/config.yaml` to `0600 root:root`, sets `/var/lib/etcd` to `etcd:etcd`,
   and installs `narwhal-harden-node-files.timer` so future `kubeadm certs renew` runs are
   re-hardened). After any manual `kubeadm certs renew` or `kubeadm upgrade`, run
   `sudo /usr/local/sbin/narwhal-harden-node-files.sh` immediately rather than waiting for
   the daily timer.
2. Validate the live configuration with `scripts/verify/kubelet-authz-check.sh` and
   `scripts/test/verify-cluster.sh --section nodes` (CIS 4.2.6/4.2.12/4.2.13 are reported
   as `WARN`-level known gaps; see `## Scan coverage` for their rollout prerequisites
   before touching `kubelet-config`). This runbook documents the operator steps; the
   provisioning scripts do not mutate existing clusters.

> **Gotcha (learned):** kubelet watches EVERY file in the static-pod dir
> (`/etc/kubernetes/manifests/`), not just `*.yaml`. A backup/temp file left there
> that defines the same pod (e.g. `kube-apiserver.yaml.bak`) SHADOWS the real
> manifest — the apiserver silently runs the stale spec. Always write manifest
> backups/temp files OUTSIDE that directory.
>
> **Live rollout note:** applied master-by-master via the manifest edit; the initial
> secret-encryption is only consistent once ALL apiservers share the key, after which
> `kubectl get secrets -A -o json | kubectl replace -f -` rewrites every Secret encrypted.
> **The source changes above are NOT yet clean-install validated** — verify enc/audit come
> up on a from-scratch `vagrant up` (all 3 masters) before relying on the provisioning path.

## etcd encryption verification and key rotation (Narwhal#109)

The current profile protects Kubernetes `secrets` with local `aescbc`; `identity` is the
last provider and exists only to decrypt legacy unencrypted values. The first provider for
each protected resource handles new writes. Expanding scope to ConfigMaps or CRDs requires
an explicit resource inventory and rollout review.

On every control-plane node, `/etc/kubernetes/enc/encryption-config.yaml` is the
EncryptionConfiguration and contains the base64 key material. Treat it as both config and
key material: it must be `root:root` mode `0600`. Master-1 generates it once and stages the
same file to joining masters before kubeadm join. Run
`sudo /home/vagrant/scripts/verify/etcd-encryption-check.sh <master-1-ip> <master-2-ip>
<master-3-ip>` on a control-plane node to check provider order, permissions, active
apiserver wiring, raw etcd ciphertext, HA config hashes, and the count of legacy plaintext
Secret values. It accepts the legacy layout where the key exists only in the protected config.
The verifier uses `kubectl exec` with `/etc/kubernetes/admin.conf` to run etcdctl inside the
etcd static pod; Python decodes the JSON/base64 values on the node.

Run `sudo scripts/ops/rotate-etcd-encryption-key.sh <master-1-ip> <master-2-ip>
<master-3-ip>` from master-1. Review `--dry-run` first. On legacy clusters the rotation reads
the key from the embedded config; its first config mutation creates the separate mode-0600
key file, making that mutation the documented legacy-layout migration step. The resumable state file records
the completed phase and new key name, never key bytes. Rotation adds a second key, rolls each apiserver
sequentially, promotes the new key, rolls again, rewrites Secrets namespace by namespace,
and requires zero unencrypted etcd values before removing the old key. Both keys remain
available to decrypt during this dual-key decrypt window. If verification fails, leave the
old key and resume after resolving the cause.

Local `aescbc` is the offline default and keeps key custody on the control-plane hosts, so a
host compromise also exposes the key. `secretbox` is another local provider if explicitly
selected in the cluster profile. KMS v2 is a documented future option for external key
custody, not an implicit dependency: any KMS profile must specify fail-closed behavior when
KMS is unavailable and a tested offline fallback/restore procedure. The offline fallback
remains a locally managed provider with separately controlled key backups; never silently
fall back to `identity` for new writes.

## NFS export least-privilege migration (narwhal#186, 2026-09-17)

`scripts/cluster/01-nfs-server.sh` changed the NFS share root from mode `0777`
(world-writable) + `no_root_squash` on both exports to mode `750` + `root_squash`
(R151/R151b in `scripts/test/regression-check-kakao.sh`). This is a **fresh-install**
default; it does not run against an already-provisioned share. This section is the
operator runbook for bringing an existing cluster's on-disk NFS state in line.

### What actually changed on disk

- **Share root** (`${NFS_SHARE_PATH}`, default `/srv/nfs/k8s`): owner was and remains
  `nobody:nogroup`; only the mode changed, `0777` → `750`. `chmod` is idempotent and
  affects only the root directory itself, not its contents recursively.
- **Exports** (`/etc/exports`): `no_root_squash` → `root_squash` on both the host and
  pod CIDR lines. `root_squash` is an NFS **server-side mapping rule applied per RPC
  request at mount/access time** — it changes how a future request's root UID (0) is
  remapped to the anonymous UID/GID (`nobody:nogroup`, 65534:65534 by default). It does
  **not** touch any byte or inode already on disk. Existing files keep whatever
  owner/mode they already had until something writes to them again.

### Is existing data affected?

**Only indirectly, and only for files/directories that were created *as real root* under
the old `no_root_squash` export.** Under the old config, any root-capable client on the
host or pod CIDR could write to the share as UID 0, and the server honored that — so
some on-disk paths may actually be owned by `root:root` (or another UID a squash would
normally remap) instead of `nobody:nogroup`. The `nfs.csi.k8s.io` provisioner
(`scripts/cluster/05-nfs-quota-agent.sh`) itself mostly creates PV subdirectories that
end up owned by the mounting pod's identity, not necessarily `nobody`.

The risk after switching to `root_squash`: a future write from a client acting as root
now arrives on the server as `nobody:nogroup` (65534:65534), not `root:root`. If a
directory in the tree is owned `root:root` with no group/other write bit (a plausible
leftover from a `no_root_squash` write), that squashed `nobody` identity can no longer
write into it — a previously-working path silently starts failing with `EACCES`/
`Permission denied` after the upgrade.

**If every path in the tree was already owned `nobody:nogroup` (the common case, since
the CSI provisioner and quota agent both operate as `nobody` in practice), existing data
is unaffected — `root_squash` changes nothing for a client that was never sending real
root UID 0 in the first place.** The migration is conditional: audit first, fix only if
the audit finds a mismatch.

### Verify an existing cluster's on-disk state

Run on the NFS server node (`master-1` by default), as a user who can `sudo`:

```bash
NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"

# 1. Confirm the share root itself matches the new default.
stat -c '%U:%G %a %n' "${NFS_SHARE_PATH}"
# expected: nobody:nogroup 750 /srv/nfs/k8s

# 2. Find anything NOT owned nobody:nogroup under the share — these are the
#    candidates that may have been written as real root under no_root_squash.
sudo find "${NFS_SHARE_PATH}" \( ! -user nobody -o ! -group nogroup \) -print

# 3. Specifically flag anything owned by real root (uid 0) — the highest-risk case,
#    since that is exactly the identity no_root_squash used to preserve.
sudo find "${NFS_SHARE_PATH}" -uid 0 -print

# 4. Confirm the live export table matches /etc/exports (root_squash on both lines).
sudo exportfs -v
```

An empty result from step 3 means no root-owned data exists (step 2 also lists legitimate files owned by a workload's own non-root uid, which need no change) — the
`root_squash` export change is safe to apply as-is.

### Fix an existing cluster's on-disk state (only if step 2/3 above found matches)

> **Update (v1.3.0, #224/#408/#431):** the supported way to align the on-disk layout is now
> `scripts/cluster/nfs-root-squash-align.sh` (dry run by default, `--apply` to change; run as root on
> the NFS server), sequenced with the export switch in `docs/runbooks/nfs-xfs-migration.md`. It chowns
> only root-owned entries inside each PV directory to that PV's workload identity. The blanket
> `chown -R nobody:nogroup` below is the older manual fallback. **Do not run it blanket over the
> share:** it re-owns files of non-root workloads to nobody and locks those pods out of their own
> data; prefer the script, and if you must use the manual command scope it to the specific
> root-owned paths the audit flagged. Note that under `root_squash` the Velero data mover (root) cannot
> read owner-only volumes, so five of them are excluded from Velero fs-backup (#431); see
> `docs/common/storage-protection-policy.md`.

```bash
NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"

# Re-own every mismatched path to the identity root_squash now maps requests to.
# Review the step-2 output first — this is a recursive chown, scope it to the
# specific subpaths the audit flagged rather than blindly running it over the
# whole share if any subtree legitimately expects a different owner.
sudo find "${NFS_SHARE_PATH}" \( -uid 0 -o -gid 0 \) -print0 \
  | sudo xargs -0 chown nobody:nogroup

# Re-apply exports if /etc/exports was hand-edited out of band.
sudo exportfs -ra
```

### Confirm success

```bash
# Success criterion: no root-owned entry (uid 0 or gid 0) is left inside the PV directories,
# except PVs the script reported as skipped/UNRESOLVED (exit code 3). Files owned by a
# workload's own non-root uid (for example 1000:1000 for SeaweedFS) are CORRECT and must NOT be
# changed to nobody:nogroup; only the share root and the namespace directories are nobody:nogroup.
sudo find "${NFS_SHARE_PATH}" -uid 0 -print
sudo find "${NFS_SHARE_PATH}" -gid 0 -print
stat -c '%U:%G %a %n' "${NFS_SHARE_PATH}"   # expected: nobody:nogroup 750

# Confirm dynamic provisioning still works: create a test PVC using the nfs.csi.k8s.io
# storage class and verify it binds and is writable from a pod, then delete it.

# Static regression checks still pass (see scripts/test/regression-check-kakao.sh):
./scripts/test/regression-check-kakao.sh --static
```

### Known gap: no live-cluster negative test yet

The acceptance criteria for narwhal#186 also call for a **root-client / cross-tenant
negative test** — proof that an unprivileged or foreign client cannot create/modify data
outside its intended area post-migration. That test needs a live cluster/host to mount
the NFS export and attempt privileged writes against it; no cluster has been available in
any agent session that has touched this issue so far, so it has **not** been executed,
and this migration guidance does not claim it has been. A ready-to-run script for that
test is at `scripts/test/verify-nfs-root-squash.sh` (see its header comment for exactly
what it checks and how to run it against a live server) — a future session with cluster
access should run it and record the result here.

## Risk-accepted exceptions (do NOT "fix")

These failing controls are expected for this IDP platform. Changing them breaks
functionality or reverses an intentional trade-off.

1. **`--bind-address` = 0.0.0.0 on controller-manager / scheduler** (CIS 1.3.7, 1.4.2 —
   1.4.2 is CRITICAL). Deliberately set so Prometheus (off the control-plane node) can
   scrape `/metrics`. On the private `192.168.56.0/24` lab network this is not an
   exposure. Reverting to 127.0.0.1 blinds monitoring. See `02-init-cluster.sh`.
2. **Privileged / hostPath / hostNetwork / NET_RAW / root workloads** (CIS 5.2.2/5.2.3/
   5.2.5/5.2.6/5.2.7/5.2.8/5.2.10/5.2.12/5.2.13, 5.7.2/5.7.3; NSA 1.0/1.1/1.2/1.4/1.5/
   1.7/1.9/1.10; PSS baseline/restricted 3/4/5). The large counts are dominated by
   **platform components that require these privileges to function**: Cilium (NET_ADMIN,
   privileged, hostNetwork), Istio ambient (ztunnel/istio-cni), trivy node-collector
   (hostPath to read node files), CSI/NFS, kube-system system pods. These cannot be
   hardened without breaking CNI / mesh / storage / scanning.
3. **`kube-system` used by platform** (NSA 1.12) — the platform legitimately runs there.
4. **Broad RBAC / secret access / wildcards** (CIS 5.1.2, 5.1.3, 5.1.6) — required by
   operators (ArgoCD, Keycloak Operator, cert-manager, etc.).

### Adding or updating allowlist entries

Every exception in `portal/src/lib/compliance-accepted.ts` is fail-closed by exact `(namespace, kind, workload, checkID)` or `(namespace, kind, roleName, checkID)` (or exact node check ID for Infra) — any unlisted check, unlisted workload, or unknown/mismatched kind remains actionable, and report rows are marked accepted only when every failing check is present in `checks[]`, accounted for by the per-severity `summary` totals, and accepted. The allowlist currently holds **73 Config Audit rules across 61 workloads**, **4 RBAC rules**, and **0 Infra rules** (plus **13** exact upstream `(namespace, kind, roleName)` triples for `chaos-mesh` (`chaos-testing/Role/*`, cluster `ClusterRole/*`) and `kubernetes-dashboard` (`devtools/Role/kubernetes-dashboard-api`, `devtools/Role/kubernetes-dashboard-web`, `ClusterRole/kubernetes-dashboard-metrics-scraper`) in `portal/src/lib/compliance.ts`).

**ReplicaSet ownership and residual risk (fail-safe fallback when ReplicaSet lookup is unavailable):** `ClusterRole narwhal-portal` (`gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`) grants read-only `get`/`list` access on `apps` `replicasets` (in addition to `deployments`, `statefulsets`, and `daemonsets`). When building Config Audit data (`portal/src/lib/compliance.ts`), the portal lists `/apis/apps/v1/replicasets` in the same request path as `configauditreports` (with no separate longer-lived ReplicaSet cache; only the combined filtered result is cached), drops a `ReplicaSet` report **only** when it is a fully drained **superseded Deployment revision** — all of: (a) `metadata.ownerReferences` names a `Deployment`, (b) `spec.replicas == 0` **and** `status.replicas == 0` **and** `status.terminatingReplicas == 0` reported explicitly (`status.replicas` does not count terminating pods; a cluster that does not report the field keeps the report), and (c) the same `Deployment` (in the same namespace) owns at least one other `ReplicaSet` in the list with a strictly higher `deployment.kubernetes.io/revision` annotation — and passes each kept `ReplicaSet`'s `metadata.ownerReferences` Deployment names into `normalizeWorkloadName` / `matchConfigAuditException` (`portal/src/lib/compliance-accepted.ts`). Standalone `ReplicaSet`s (no `Deployment` owner), a `Deployment` intentionally or HPA/KEDA scaled to `0` (whose current-revision `ReplicaSet` has no higher-revision sibling), `ReplicaSet`s with `status.replicas > 0` (pods still terminating), and any `ReplicaSet` with missing/unknown `spec.replicas`, `status.replicas`, or `deployment.kubernetes.io/revision` remain counted so live findings and scaled-to-zero workloads are never hidden. A trailing `pod-template-hash` matching the Kubernetes `SafeEncodeString` alphabet (`^[bcdfghjklmnpqrstvwxz2456789]{5,10}$`, no vowels and no `0`/`1`/`3`) collapses to `<name>` **only** when `metadata.ownerReferences` names a `Deployment` with `<name>` — a standalone `ReplicaSet` named `<allowlisted-name>-<5-to-10-safe-chars>` without a matching `Deployment` owner is **not** accepted, and non-`SafeEncodeString` suffixes are never collapsed even with an owner reference. **Residual risk:** if `/apis/apps/v1/replicasets` cannot be read (e.g. 403/timeout) or a report's `ReplicaSet` is missing from the returned list, the portal fails safe in the direction that never hides a live finding — it keeps the report, logs one warning, and falls back to the `SafeEncodeString` suffix rule while ownership is unknown.

### Framework actionable vs. accepted controls

`getComplianceFrameworks` and `getComplianceFrameworkDetail` (`portal/src/lib/compliance.ts`) derive per-control and per-framework `actionableFailCount`, `acceptedFailCount`, and `effectivePassRate` from `ClusterComplianceReport.status.detailReport.results[].checks[]` while keeping raw `failCount` and `passRate` visible:

- **Target & check normalization (fail closed):** `parseComplianceTarget` maps `<namespace>/<kind>-<name>` to `(namespace, kind, name)` (`ClusterRole` and `Node` require an empty namespace; namespaced kinds require a non-empty namespace; unknown kinds, missing/extra slashes, or empty names stay actionable). When available in the same request path, hashed report names (such as long StatefulSet names or `system:*` ClusterRoles) are resolved from `configauditreports`, `rbacassessmentreports`, and `clusterrbacassessmentreports` by explicit kind/name labels or `ownerReferences`, falling back to fail-closed raw target parsing when unavailable. Check IDs normalize `AVD-KSV-00NN` to `KSVNNN` via `normalizeCheckId`; missing or unrecognized IDs stay actionable.
- **Reuse of existing matchers (no second allowlist):** Workload targets (`ReplicaSet`, `DaemonSet`, `StatefulSet`, `Pod`, `Deployment`, `CronJob`, `Job`) reuse `configAuditMatcher` (`SYSTEM_NAMESPACES` + `ACCEPTED_CONFIG_AUDIT_RULES`), `fetchReplicaSetMap()` superseded-revision dropping, and Deployment `ownerReferences` verification. RBAC targets (`Role`, `ClusterRole`) reuse `rbacAuditMatcher` (`isAcceptedRbacRole` + `ACCEPTED_RBAC_RULES`). Node (`Infra`) targets reuse `infraAuditMatcher` (`ACCEPTED_INFRA_RULES`, matching the Infra tab); any unlisted `KCV` check stays actionable.
- **Control & summary rollup:** A control is marked `accepted: true` only when `failCount > 0`, `actionableFailCount === 0`, and `acceptedFailCount > 0` (mixed controls remain actionable). When `detailReport.results` is absent (e.g., `reportType=summary`) or has fewer failing controls than `status.summary.failCount`, unaccounted failures remain in `actionableFailCount`.

Adding or expanding an entry requires:

1. Verifying that no safe manifest/chart knob fixes the finding without breaking functionality (`accept`) or that the upstream chart/image lacks the knob (`upstream`).
2. Recording a concise justification in `portal/src/lib/i18n.ts` (`ko`/`en` keys), `portal/src/lib/compliance-accepted.ts`, and the English tables below (enforced by `portal/src/lib/compliance-accepted.test.ts`).
3. Passing independent PR review before merging.

Matching is exact: `database/Pod/narwhal-db-1` covers only that pod name (no prefix or wildcard rule). Other CloudNativePG instance pods (`narwhal-db-2` on the declared `instances: 2` HA setup, or `narwhal-db-N` after an instance replacement) stay actionable until they are listed explicitly after the same verification.

### Config Audit allowlist

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |
| chaos-testing | DaemonSet/chaos-daemon | KSV001, KSV003, KSV010, KSV012, KSV017, KSV020, KSV021, KSV022, KSV023, KSV106, KSV121 | accept | Requires privileged, hostPID, /sys and containerd.sock hostPath, root UID 0, and capabilities to inject pod netns and I/O faults |
| chaos-testing | DaemonSet/chaos-daemon | KSV014 | upstream | Upstream chaos-mesh 2.8.3 DaemonSet template does not expose readOnlyRootFilesystem |
| chaos-testing | ReplicaSet/chaos-controller-manager | KSV020, KSV021 | accept | Uses upstream chaos-mesh image default non-root UID/GID |
| chaos-testing | ReplicaSet/chaos-controller-manager | KSV001, KSV003, KSV012, KSV014, KSV106 | upstream | Upstream chaos-mesh 2.8.3 controller-manager template does not expose containerSecurityContext |
| database | Pod/narwhal-db-1 | KSV020, KSV021, KSV036 | accept | CloudNativePG instance manager calls Kubernetes API for cluster status, leader lease, and secrets (SA token), and postgresql:18 / 0700 pgdata PVC require postgres UID/GID 26 |
| database | ReplicaSet/narwhal-db-pooler-rw | KSV020, KSV021 | upstream | CloudNativePG v1.29.1 operator unconditionally overwrites the Pooler pod securityContext with UID 998/GID 996; no supported setting changes it |
| devtools | ReplicaSet/argocd-applicationset-controller | KSV020, KSV021 | accept | Uses upstream ArgoCD image non-root UID/GID 999 |
| devtools | ReplicaSet/argocd-dex-server | KSV020, KSV021 | accept | Uses upstream ArgoCD dex-server and copyutil non-root UID/GID 999 |
| devtools | ReplicaSet/argocd-notifications-controller | KSV020, KSV021 | accept | Uses upstream ArgoCD image non-root UID/GID 999 |
| devtools | ReplicaSet/argocd-redis | KSV020, KSV021 | accept | Uses upstream Valkey 8 Alpine image default UID/GID 999 |
| devtools | ReplicaSet/argocd-repo-server | KSV020, KSV021 | accept | Uses upstream ArgoCD repo-server and copyutil non-root UID/GID 999 |
| devtools | ReplicaSet/argocd-server | KSV020, KSV021 | accept | Uses upstream ArgoCD image non-root UID/GID 999 |
| devtools | ReplicaSet/gitea | KSV020, KSV021 | accept | Uses upstream Gitea rootless image default git UID/GID 1000 |
| devtools | ReplicaSet/gitea | KSV014 | upstream | Gitea rootless requires writable paths for SSH host keys, LevelDB queues, and git hooks; readOnlyRootFilesystem unsupported |
| devtools | ReplicaSet/harbor-core | KSV011, KSV020, KSV021 | accept | Uses Harbor Photon OS UID/GID 10000; omits CPU limit to prevent probe timeouts/502 during scan job bursts (D-HARBOR-PROBES) |
| devtools | ReplicaSet/harbor-core | KSV014 | upstream | Harbor Photon OS image and chart 1.19.1 template do not support readOnlyRootFilesystem |
| devtools | ReplicaSet/harbor-jobservice | KSV011, KSV020, KSV021 | accept | Uses Harbor Photon OS UID/GID 10000; omits CPU limit to prevent throttling during job execution (D-HARBOR-PROBES) |
| devtools | ReplicaSet/harbor-jobservice | KSV014 | upstream | Harbor Photon OS image and chart 1.19.1 template do not support readOnlyRootFilesystem |
| devtools | ReplicaSet/harbor-nginx | KSV020, KSV021 | accept | Uses Harbor Photon OS UID/GID 10000 |
| devtools | ReplicaSet/harbor-nginx | KSV014 | upstream | Nginx temporary writes and Harbor chart 1.19.1 template do not support readOnlyRootFilesystem |
| devtools | ReplicaSet/harbor-portal | KSV020, KSV021 | accept | Uses Harbor Photon OS UID/GID 10000 |
| devtools | ReplicaSet/harbor-portal | KSV014 | upstream | Nginx-based web UI and Harbor chart 1.19.1 template do not support readOnlyRootFilesystem |
| devtools | ReplicaSet/harbor-registry | KSV011, KSV020, KSV021 | accept | Uses Harbor Photon OS UID/GID 10000; omits CPU limit to prevent probe timeouts on large image pushes (D-HARBOR-PROBES) |
| devtools | ReplicaSet/harbor-registry | KSV014 | upstream | Harbor registry/registryctl chart 1.19.1 template does not support readOnlyRootFilesystem |
| devtools | ReplicaSet/headlamp | KSV011, KSV020, KSV021 | accept | Uses upstream Headlamp hardcoded UID 100/GID 101; omits CPU limit to avoid 1s probe timeouts under VM load |
| devtools | ReplicaSet/kubernetes-dashboard-api | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/kubernetes-dashboard-auth | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/kubernetes-dashboard-kong | KSV020, KSV021 | accept | Uses Kong 2.52.0 chart default UID/GID 1000 |
| devtools | ReplicaSet/kubernetes-dashboard-metrics-scraper | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/kubernetes-dashboard-web | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/narwhal-portal | KSV020, KSV021 | accept | Portal image fixes non-root nextjs:nodejs UID/GID 1001 in portal/Dockerfile |
| devtools | ReplicaSet/narwhal-portal-valkey | KSV020, KSV021 | accept | Relies on upstream valkey:8-alpine default non-root UID/GID 999 |
| devtools | ReplicaSet/waypoint | KSV020, KSV021 | accept | Istio ambient waypoint proxy requires fixed non-root UID/GID 1337 |
| devtools | StatefulSet/argocd-application-controller | KSV020, KSV021 | accept | Uses upstream ArgoCD image non-root UID/GID 999 |
| devtools | StatefulSet/harbor-redis | KSV020, KSV021 | accept | Uses Redis image default UID/GID 999 |
| devtools | StatefulSet/harbor-redis | KSV014 | upstream | Harbor chart 1.19.1 redis StatefulSet template does not expose readOnlyRootFilesystem |
| iam | ReplicaSet/keycloak-operator | KSV020, KSV021 | accept | Uses upstream keycloak-operator:26.5.7 UID 1000 |
| iam | StatefulSet/keycloak | KSV020, KSV021 | accept | Uses upstream keycloak:26.5.7 UID 1000 |
| iam | StatefulSet/keycloak | KSV014 | upstream | Stock keycloak:26.5.7 image started with start-optimized=false re-augments Quarkus into /opt/keycloak/lib/quarkus on every start (docs/common/lessons-log.md D-COMPLIANCE-R2) |
| monitoring | DaemonSet/k8s-monitoring-alloy-logs | KSV012, KSV020, KSV021, KSV023 | accept | Requires root UID 0, recommended UID/GID ownership, and root-owned /var/log (/var/log/pods) hostPath to collect node and pod logs |
| monitoring | DaemonSet/k8s-monitoring-alloy-logs | KSV118 | upstream | Upstream chart schema does not expose DaemonSet pod-level securityContext |
| monitoring | DaemonSet/prometheus-stack-prometheus-node-exporter | KSV009, KSV010, KSV023, KSV121 | accept | Requires hostNetwork, hostPID, and root/proc/sys hostPath mounts to collect host metrics |
| monitoring | ReplicaSet/k8s-monitoring-alloy-operator | KSV020, KSV021 | accept | Relies on upstream image fixed non-root UID/GID 4242 |
| monitoring | ReplicaSet/prometheus-stack-grafana | KSV020, KSV021 | accept | Relies on upstream Grafana UID 472 and NFS persistent volume file ownership |
| monitoring | ReplicaSet/waypoint | KSV020, KSV021 | accept | Istio ambient waypoint proxy requires fixed non-root UID/GID 1337 |
| monitoring | StatefulSet/alertmanager-prometheus-stack-kube-prom-alertmanager | KSV020, KSV021 | accept | Relies on upstream Alertmanager UID 1000 and config-reloader UID 65534 |
| monitoring | StatefulSet/loki | KSV011 | accept | Omits main CPU limit intentionally to prevent CFS throttling timeouts on ingest/queries (D-MON-QOS-LOKI) |
| monitoring | StatefulSet/prometheus-prometheus-stack-kube-prom-prometheus | KSV011, KSV020, KSV021 | accept | Relies on Prometheus UID 1000/reloader UID 65534; omits CPU limit to avoid TSDB compaction probe timeouts (D-MON-QOS) |
| nfs-quota-agent | DaemonSet/nfs-quota-agent | KSV001, KSV003, KSV012, KSV017, KSV020, KSV021, KSV023, KSV121 | accept | Requires privileged root container, capabilities, hostPath, and /dev access to set XFS/ext4 project quotas on the NFS export (allowPrivilegeEscalation: false, drop ALL, and UID > 10000 cannot be used) |
| platform-system | DaemonSet/metallb-speaker | KSV009, KSV012, KSV020, KSV021, KSV022, KSV106, KSV119 | accept | Requires hostNetwork, NET_RAW/NET_ADMIN, and UID/GID 0 raw socket access for L2 ARP/NDP announcements |
| platform-system | ReplicaSet/apisix | KSV020, KSV021 | accept | Uses upstream APISIX image fixed UID/GID 636 |
| platform-system | ReplicaSet/apisix-ingress-controller | KSV020, KSV021 | accept | Uses upstream image UID/GID 65532 |
| platform-system | ReplicaSet/apisix-ingress-controller | KSV011, KSV015, KSV016, KSV018 | upstream | Upstream chart 0.14.1 template does not expose resources fields for initContainers |
| platform-system | ReplicaSet/cert-manager | KSV020, KSV021 | accept | Uses upstream cert-manager controller UID 1000/GID 65532 |
| platform-system | ReplicaSet/cert-manager-cainjector | KSV020, KSV021 | accept | Uses upstream cert-manager cainjector UID 1000/GID 65532 |
| platform-system | ReplicaSet/cert-manager-webhook | KSV020, KSV021 | accept | Uses upstream cert-manager webhook UID 1000/GID 65532 |
| platform-system | ReplicaSet/metallb-controller | KSV021 | accept | Uses upstream MetalLB controller nobody GID 65534 |
| platform-system | ReplicaSet/waypoint | KSV020, KSV021 | accept | Istio ambient waypoint proxy requires fixed non-root UID/GID 1337 |
| security-system | DaemonSet/falco | KSV001, KSV003, KSV012, KSV017, KSV020, KSV021, KSV023, KSV030, KSV104, KSV106, KSV121 | accept | Requires privileged, root, hostPath (/boot,/etc,/proc,/usr,containerd.sock), and disabled seccomp for modern_ebpf instrumentation |
| security-system | ReplicaSet/falco-falcosidekick | KSV020, KSV021 | accept | Keeps upstream falco 9.2.1 / falcosidekick 0.14.1 default UID/GID 1234 |
| security-system | ReplicaSet/trivy-operator | KSV020, KSV021 | accept | Keeps upstream trivy-operator:0.25.0 default UID/GID 10000 |
| security-system | StatefulSet/trivy-server | KSV021 | accept | Upstream chart sets fsGroup 65534 with runAsUser 65534 and omits runAsGroup |
| storage | DaemonSet/node-agent | KSV001, KSV003, KSV012, KSV020, KSV021, KSV023, KSV105, KSV106 | accept | Requires privileged/UID 0 and /var/lib/kubelet hostPath mounts for kopia fs-backup |
| storage | ReplicaSet/openbao-agent-injector | KSV020, KSV021 | accept | Uses upstream OpenBao image/chart default UID/GID 100/1000 |
| storage | CronJob/openbao-snapshot | KSV020, KSV021 | accept | Existing backup files on the PVC are owned by UID 100; changing the UID requires an ownership migration of the backup volume |
| storage | ReplicaSet/openbao-snapshot-holder | KSV020, KSV021 | accept | Existing backup files on the PVC are owned by UID 100; changing the UID requires an ownership migration of the backup volume |
| storage | ReplicaSet/seaweedfs-s3 | KSV011, KSV020, KSV021 | accept | Omits CPU limit to prevent S3 upload throttling (D-sw-qos) and uses seaweed UID/GID 1000 |
| storage | ReplicaSet/velero | KSV012, KSV020, KSV021 | accept | Upstream velero:v1.18.1 non-numeric USER cnb (UID <= 10000, GID 65532) fails CreateContainerConfigError with runAsNonRoot: true |
| storage | ReplicaSet/velero-ui | KSV020, KSV021 | accept | Uses upstream node image UID/GID 1000 |
| storage | StatefulSet/openbao | KSV020, KSV021 | accept | Uses upstream OpenBao UID/GID 100/1000 matching raft PVC ownership |
| storage | StatefulSet/seaweedfs-filer | KSV011, KSV020, KSV021 | accept | Omits CPU limit to prevent upload throttling (D-sw-qos) and uses seaweed UID/GID 1000 |
| storage | StatefulSet/seaweedfs-master | KSV011, KSV020, KSV021 | accept | Omits CPU limit to prevent upload throttling (D-sw-qos) and uses seaweed UID/GID 1000 |
| storage | StatefulSet/seaweedfs-volume | KSV011, KSV012, KSV020, KSV021, KSV023 | accept | Requires root and hostPath for existing root-owned /ssd/object_store data; omits CPU limit (D-sw-qos) with UID/GID <= 10000 |

### RBAC allowlist

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |
| cluster | ClusterRole/platform-admin | KSV041, KSV048, KSV049, KSV053, KSV056 | accept | Administrative privileges required by OIDC platform-admin role |
| cluster | ClusterRole/developer-workload-admin | KSV048, KSV049, KSV053, KSV056 | accept | Developer workload management privileges scoped solely via dev RoleBinding |
| devtools | Role/narwhal-portal-jobs | KSV048 | accept | Tuning and Kaniko build Jobs guarded by Kyverno admission policies |
| monitoring | Role/narwhal-portal-notification-channels | KSV113 | accept | Scoped to a single Secret name with get/update/patch verbs only |

### Infra allowlist

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |

## Node collector & CIS 1.1.9 / 1.1.12 resolution

- **CIS 1.1.12 (`KCV0059`, etcd data directory ownership `etcd:etcd`):** `node-collector:0.3.1` executes `CMD-0012` (`stat -c %U:%G $etcd.datadirs`, resolving to `/var/lib/etcd` via `defaultdatadir`). Live collector output on `narwhal-master-1` returned `"etcdDataDirectoryOwnership":{"values":["UNKNOWN:UNKNOWN"]}` because `/var/lib/etcd` is `700 etcd:etcd` (`995:984`) on the host while the container lacked the host `etcd` entry in `/etc/passwd`/`/etc/group`. `gitops/charts/narwhal-apps/templates/trivy-operator.yaml` mounts host `/etc/passwd` and `/etc/group` read-only into `nodeCollector` (`D-NODE-COLLECTOR-PASSWD`, preserving all chart `0.27.0` defaults) and sets `podAnnotations."checksum/node-collector-mounts"` (`D-NODE-COLLECTOR-PASSWD-ROLLOUT`) so the scanner resolves real `etcd:etcd` ownership; `KCV0059` is **not** allowlisted and stays actionable until rollout + rescan. Note: `trivy-operator` v0.25.0 (chart `0.27.0`) loads `nodeCollector` ConfigMap mounts only at operator startup and its upstream Deployment omits a ConfigMap checksum annotation, so on an existing cluster without the `podAnnotations` checksum change an ArgoCD sync alone does not apply new mounts — run `kubectl -n security-system rollout restart deploy/trivy-operator` before triggering/waiting for rescan.
- **CIS 1.1.9 / 1.1.10 (`KCV0056` / `KCV0057`, CNI file permissions `<= 600` and ownership `root:root`):** `node-collector:0.3.1` executes `CMD-0009` (`stat -c %a /*/cni/*`, `nodeType: master`) and `CMD-0010` (`stat -c %U:%G /*/cni/*`, `nodeType: master`). With the chart default `mountPath: /etc/cni/net.d/`, `/*/cni/*` matched the directory `/etc/cni/net.d` itself (`700 root:root`) instead of the files inside it (`05-cilium.conflist` `600 root:root`), falsely failing `KCV0056`. Mounting host `/etc/cni/net.d/` at container path `/etc/cni` in `gitops/charts/narwhal-apps/templates/trivy-operator.yaml` (`D-NODE-COLLECTOR-CNI-MOUNT`) makes `/*/cni/*` glob the actual CNI config files (`/etc/cni/05-cilium.conflist`), so live `node-collector:0.3.1` on `narwhal-master-1` returns `"containerNetworkInterfaceFilePermissions":{"values":[600]}` and `"containerNetworkInterfaceFileOwnership":{"values":["root:root"]}` while keeping every other collector key unchanged.
  - **Deployed policy & negative proof (measured 2026-11-22):** Live `trivy-operator:0.25.0` pulls `harbor.devtools.svc.cluster.local/trivy-db/aquasecurity/trivy-checks:1` (`revision: 1.11.3`, `sha256:a55ef4122a1683407bb0f85cbfa95b5b4622376292463b908f71230ae1534b02`) into `/tmp/policy` for commands/specs (`CMD-0009`/`CMD-0010`, `k8s-cis-1.23.yaml`), while `trivy.useEmbeddedRegoPolicies: "true"` evaluates `KCV0056` from the embedded `github.com/aquasecurity/trivy-checks v1.7.1` Rego (`sp.kind == "NodeInfo"`, `sp.type == "master"`, `violation := {p | p = sp.info.containerNetworkInterfaceFilePermissions.values[_]; p > 600}`, `count(violation) > 0`; `trivy-checks:1` `1.11.3` omits `KCV0056`/`KCV0057` `.rego` files under `/tmp/policy/content/policies/`). Running four temporary `node-collector:0.3.1` Jobs in `security-system` on `narwhal-master-1` (exact 45 `k8s` commands + `node.yaml` from the live operator, `/etc/cni` backed by `emptyDir`, all Jobs deleted) and evaluating the verbatim extracted `KCV0056` Rego via `trivy config` and `opa eval` proved: (a) single `644` file → `[644]` → **FAIL**; (b) `600` + `644` files → `[600, 644]` → **FAIL**; (c) empty dir → `[]` → **PASS**; (d) `700` sub-directory only → `[700]` → **FAIL**. Additionally, `[444]` passes upstream Rego because `444 <= 600` under integer `>` (not an octal bitmask `& 0177`), and `type: "worker"` with `[644]` passes because `sp.type == "master"`.
  - **Coverage limits & empty-result guard:** `KCV0056` is **not** allowlisted (`ACCEPTED_INFRA_RULES` is empty, fail-closed), so real `> 600` regressions on **control-plane (`master`) nodes** remain visible and actionable in Trivy. Because `CMD-0009`/`KCV0056` is `master`-only (`nodeType: master`, `sp.type == "master"`), Trivy does **not** report a worker node's `644` CNI file. Furthermore, because `values: []` passes `KCV0056`, the scanner alone cannot distinguish "no CNI files visible" from "all files compliant": on a `Ready` node kubelet/containerd requires a CNI config (`05-cilium.conflist`), so `values: []` is not reachable on a functioning `Ready` node, but it *is* reachable transiently during bootstrap/join or when `/etc/cni/net.d` is empty or contains only dotfiles (e.g. `.kubernetes-cni-keep`). `scripts/test/verify-cluster.sh` (`verify_node_file_hardening`) closes both gaps wherever it is run locally on a cluster node (`IS_CLUSTER_NODE=true`, `SKIP`s remotely): it checks permissions with the octal bitmask `8#7177` (rejecting `444`, `644`, `700`, and special bits) and **FAILs** when `/etc/cni/net.d` is missing, unreadable, empty, sub-directory-only, or contains no non-hidden `*.conf`/`*.conflist`/`*.json` CNI config file; worker nodes remain covered by `harden-node-files.sh` during provisioning/join and by `verify_node_file_hardening` where it is run.

## Scan coverage

Because Trivy Operator's `ClusterComplianceReport` records any control with zero failed reports as an empty-placeholder `PASS` (`checks: [{checkID: "", success: true}]`), an unevaluated check is indistinguishable from a passing check unless coverage is explicitly scoped and tracked. The table below records what is evaluated by Trivy Operator, what is verified only by on-node scripts, and what is evaluated by neither (measured against `trivy-operator` `0.25.0` / chart `0.27.0`, `node-collector:0.3.1`, and `trivy-checks:1` `1.11.3` + embedded `v1.7.1`).

> **Portal interaction after rollout (D2):** New `kube-system` / `istio-system` findings are classified `accepted/system` by the portal's `SYSTEM_NAMESPACES` blanket rule (`portal/src/lib/compliance.ts`) until the portal lane replaces that rule with exact entries; raw failure counts rise now while `actionable` counts for those namespaces may remain `0` until the portal lane lands. New config-audit findings from the live trial: `kube-system` High 54 / Medium 102 / Low 142 (Critical 0) across 40 reports; `istio-system` High 6 / Medium 11 / Low 18 (Critical 0). Both namespaces' findings are currently accepted under `SYSTEM_NAMESPACES`.
>
> **Live trial measured scan impact (orchestrator, single-node cluster):**
>
> - **Report counts:** ConfigAuditReports rose 188 → 281 within one minute (`kube-system` 40 reports, kinds now include RoleBinding 62; ClusterConfigAuditReports reached 121); VulnerabilityReports rose 108 → 110.
> - **Observed scans & runtime suppression:** Exactly two new vulnerability scans appeared in the first six minutes: `kube-system/pod-kube-vip` (image `kube-vip/kube-vip`) and `istio-system/daemonset-istio-cni-node`. `kube-system` has 18 pods; no other system workload was vulnerability-scanned (running scan Jobs at t+1..6 min: 0). In this ClientServer deployment `excludeImages` suppressed the listed images at runtime as observed, while the reviewer's reading of the v0.25.0 source indicated the filter is Standalone-only; both facts are recorded, and behavior must be re-measured on an operator upgrade.
> - **Decision on `kube-vip` and `istio install-cni`:** kube-vip and istio install-cni images belong to the same control-plane/mesh class as the existing system entries, do not affect hashed mounts, and adding exact repo entries (`kube-vip/kube-vip:*`, `docker.io/kube-vip/kube-vip:*`, `istio/install-cni:*`, `docker.io/istio/install-cni:*`) suppresses the two residual scan Jobs without widening wildcards.
> - **Host load range:** `node_load1` during the six minutes was 0.56–1.66 (baseline ~0.5–0.9); node remained Ready throughout and all ArgoCD apps stayed Synced/Healthy.

| Area / Control | Trivy Scanner | On-Node Verifier (`verify-cluster.sh` / `kubelet-authz-check.sh`) | Notes & Residual Limits |
| --- | --- | --- | --- |
| Control-plane static pods (`kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `etcd`) — CIS `1.2.x`/`1.3.x`/`1.4.x`/`2.x`, `KISA-CP-01`/`KISA-ETCD-01` | **Evaluated** (`ConfigAuditReport` + `InfraAssessmentReport` in `kube-system`; `D-SCAN-CONTROL-PLANE`) | Partial (`etcd-encryption-check.sh`, `kubelet-authz-check.sh`, `static-pod-manifest-check.sh`) | `excludeNamespaces` dropped `kube-system` and `istio-system` (keeping `kube-public,kube-node-lease,trivy-system`). System-image CVE scan Jobs are suppressed via `trivyOperator.excludeImages` (`D-SCAN-SYSTEM-EXCLUDE-IMAGES`, exact live repositories with `:*` tag glob only — no `/*` org wildcards; `registry.k8s.io/etcd` stays scanned because `platform-system/apisix-etcd` shares the repo, and `docker.io/alpine/k8s` stays scanned while `ghost-pod-reaper` child Jobs are skipped via `skipResourceByLabels: narwhal.dasomel.io/skip-ephemeral-job-scan`). Live trial measured ConfigAuditReports 188->281, VulnerabilityReports 108->110 (only kube-vip and istio-cni-node appeared, as unlisted images), and node_load1 0.56-1.66 (baseline ~0.5-0.9); excludeImages suppressed listed images at runtime in ClientServer mode (re-measure on operator upgrade), and exact kube-vip and istio install-cni entries were added to eliminate the residual jobs. |
| Ambient mesh (`istio-system`: `istiod`, `istio-cni-node`, `ztunnel`) | **Evaluated** (`ConfigAuditReport` + `RbacAssessmentReport`) | Partial (`ambient-netpol-check.sh`) | Exact system repos (`install-cni`, `pilot`, `ztunnel`) excluded from vuln scan Jobs via `:*` tag glob; app `waypoint` (`registry.istio.io/release/proxyv2`) keeps vuln scanning. |
| `RoleBinding` — CIS `5.1.1` (`AVD-KSV-0111`), `KISA-RBAC-01` | **Evaluated** (`ConfigAuditReport` + `ClusterComplianceReport`; `D-SCAN-BINDINGS`) | `kubelet-authz-check.sh` (`nodes/proxy` bindings) | `trivy.supportedConfigAuditKinds` adds `RoleBinding,ClusterRoleBinding`. `RoleBinding` writes `ConfigAuditReport`, which `trivy-operator` v0.25.0 aggregates into CIS `5.1.1` / KISA. |
| `ClusterRoleBinding` — CIS `5.1.1` (`AVD-KSV-0111`) | **Partial** (`ClusterConfigAuditReport` CRD only) | `kubelet-authz-check.sh` (`nodes/proxy` bindings) | `trivy-operator` v0.25.0 writes `ClusterRoleBinding` to `ClusterConfigAuditReport`, but `pkg/compliance/io.go` (`misconfigReportToTrivyResults`) does **not** read `ClusterConfigAuditReportList`, so `ClusterRoleBinding` KSV111 findings do not roll into `k8s-cis-1.23` or `k8s-kisa-1.0` until upstream aggregates it (inspect via `kubectl get clusterconfigauditreports`). |
| PSS Baseline (`1`–`11`) & Restricted (`1`–`17`) | **Evaluated** (all controls, uppercase `AVD-KSV-*`; `D-PSS-SPEC-CASE`) | N/A | Chart `0.27.0` shipped lowercase `avd-ksv-*` on baseline `6`–`11` and restricted `6`–`17`, causing silent false PASS. `trivy-operator.yaml` limits `compliance.specs` to `k8s-cis-1.23` + `k8s-nsa-1.0` and `Application/pss-compliance` (`gitops/resources/pss-compliance.yaml`) owns both uppercase specs (pinned by `R388` offline drift check against chart `0.27.0` upstream controls). |
| KISA controls (`k8s-kisa-1.0`) | **Evaluated** (7 mapped controls; `D-KISA-CONTROL-ACCURACY`) | N/A | `KISA-ETCD-01` uses `KCV0029/0030/0042/0043` (replacing mis-mapped `KCV0004`); `KISA-RBAC-01` adds `KSV111`; `KISA-NET-01` accurately states NetworkPolicy selector check (`KSV038`), not cluster-wide default-deny presence (omitted KISA controls remain documented in `kisa-compliance.yaml`). |
| Node files — CIS `1.1.9`/`1.1.10` (CNI), `1.1.12` (etcd dir) | **Master only** (`node-collector` `KCV0056`/`0057`/`0059`; empty `[]` and `[444]` pass Rego) | **Evaluated** (`verify_node_file_hardening`, all nodes where run, octal `8#7177`/`8#7077`, fail-closed on empty/dotfile/missing) | Worker nodes are not evaluated by `CMD-0009`/`KCV0056`; covered by `harden-node-files.sh` + `verify_node_file_hardening`. |
| Node PKI — CIS `1.1.19` (`KCV0066`), `1.1.20` (`KCV0068`), `1.1.21` (`KCV0067`), `4.1.7`/`4.1.8` (CA) | **Not evaluated** (`CMD-0019/0020/0021` return `[]`: `$kubelet.cafile` is a file + `CMD-0020` has upstream `awk'...'` typo; `CMD-0028/0029` `ps` flags absent on kubeadm, `node-collector` only populates `ca.crt` via `defaultcafile`) | **Evaluated** (`verify_node_file_hardening` checks `/etc/kubernetes/pki` `root:root`, `ca.crt` required, all `*.crt`/`*.key` `<= 600` via `8#7177`) | `harden-node-files.sh` (`D-NODE-PKI-PERMS`, `D-NODE-HARDEN-EXIT`) tightens all `*.crt`/`*.key` to `0600 root:root`, installs `narwhal-harden-node-files.timer` (`D-NODE-PKI-RENEW`) to re-harden after `kubeadm certs renew`, and exits non-zero if any `chmod`/`chown`/timer step fails (provisioning callers keep `\|\| echo "WARN: ..."` so a healthy install is not aborted; systemd unit records real failures). `02-join-control-plane.sh` fetches `oidc-ca.crt` via `sudo cat` and installs `0600`; `11-4b-apiserver-oidc-node.sh` installs `0600`. |
| Kubelet service unit & drop-ins — CIS `4.1.1`/`4.1.2` | **Partial** (`node.yaml` checks first match in `svc`, misses `/usr/lib/systemd/system/kubelet.service.d/10-kubeadm.conf` and `/etc/systemd/system/kubelet.service.d/*`) | **Evaluated** (`verify_node_file_hardening` checks unit + both `kubelet.service.d/*` dirs `<= 600 root:root`) | `harden-node-files.sh` hardens unit and both drop-in directories to `0600`. |
| Kubelet config permissions & read-only port — CIS `4.1.9`/`4.1.10`, `4.2.4` (`readOnlyPort`) | **Partial** (`config.yaml` perms checked by `node-collector`; `readOnlyPort: 0` omitted from `/configz`) | **Evaluated** (`verify_node_file_hardening` checks `config.yaml <= 600 root:root`; `kubelet-authz-check.sh` probes `readOnlyPort` / TCP 10255) | `02-init-cluster.sh` sets `readOnlyPort: 0`; `harden-node-files.sh` tightens `config.yaml` to `0600`. |
| Kubelet runtime hardening — CIS `4.2.6` (`protectKernelDefaults`), `4.2.12` (`serverTLSBootstrap`), `4.2.13` (`tlsCipherSuites`) | **Not evaluated** (`kubelet_mapping.yaml` reads `/configz`; `trivy.useEmbeddedRegoPolicies=true` `v1.7.1` silently passes empty `[]` when fields are unset) | **WARN (known gap)** (`verify_node_file_hardening` reports `WARN` — neither `PASS` nor `FAIL` — with pointer to this section) | **Open, non-compliant (`D-KUBELET-DEFERRED-GAPS`).** Omitted from `02-init-cluster.sh` / `01-prerequisites.sh` because kubelet refuses to start if `protectKernelDefaults: true` runs on a node lacking the 6 kernel sysctls (including Kakao nodes with an older `base` completion marker that skip `01-prerequisites.sh`). **Rollout prerequisites:** (1) CIS `4.2.6`: persist and verify all 6 sysctls (`vm.overcommit_memory=1`, `vm.panic_on_oom=0`, `kernel.panic=10`, `kernel.panic_on_oops=1`, `kernel.keys.root_maxkeys=1000000`, `kernel.keys.root_maxbytes=25000000`) on **every** node before kubelet restart and test reboot survival; (2) CIS `4.2.13`: test all kubelet TLS consumers (`kube-apiserver`, Prometheus kubelet scrape, `metrics-server`) across a live kubelet restart; (3) CIS `4.2.12`: deploy an automated kubelet-serving CSR approver (e.g., `kubelet-csr-approver`) before enabling `serverTLSBootstrap` so joining/restarted nodes do not stall on `Pending` CSRs. |
| Kubelet `eventRecordQPS` (CIS `4.2.9`), kube-proxy kubeconfig (CIS `4.1.3`/`4.1.4`) | **Not evaluated** (`eventRecordQPS` broken by upstream `kubelet_mapping.yaml` typo `eventRecordQPS",`; kube-proxy absent under Cilium) | **Not evaluated** | `eventRecordQPS` is an upstream collector bug; kube-proxy is N/A (replaced by Cilium). |

## Deferred (safe, not yet done)

Worthwhile but out of the "quick safe win" scope; each needs care and control-plane
restarts on all 3 masters:

- **Persist node file perms across reinstall**: `harden-node-files.sh` is wired into
  the provisioning path non-fatally and idempotently: `02-init-cluster.sh` runs it after
  `kubeadm init` (before CNI exists), `03-cni-install.sh` runs it on `master-1` after
  Cilium and node readiness, and `02-join-control-plane.sh` / `02-join-worker.sh` wait
  up to `CNI_WAIT_TIMEOUT` (default 180s, polling every 5s) for a regular file in
  `/etc/cni/net.d` (including on already-joined re-runs) before running it. On timeout,
  join scripts log a `WARN` that CNI file permissions (CIS 1.1.9) remain unhardened until
  re-run and still harden the remaining node files. This wiring has NOT been validated by
  a clean install on a live cluster yet.
- **`--anonymous-auth=false`**, `EventRateLimit`, `--kubelet-certificate-authority` —
  apiserver hardening (test probes first).
- Portal/Valkey `readOnlyRootFilesystem` (CIS 5.7.3 / KSV014) — Next.js writes cache/tmp
  at runtime; needs writable `emptyDir` mounts before enabling.

## How the page is fed

trivy-operator scanners must be enabled (`gitops/charts/narwhal-apps/templates/trivy-operator.yaml`):
`configAuditScannerEnabled`, `rbacAssessmentScannerEnabled`, `infraAssessmentScannerEnabled`,
`clusterComplianceEnabled` = true, and `compliance.reportType: all` (summary yields no
per-control detail). Reports regenerate on the `0 */6 * * *` cron; to force one, briefly
patch a report's `spec.cron` to `* * * * *`.

## Issue #109 live verifier findings

The lead's first live run on master-1 of the six-node kubeadm 1.35 cluster built from main returned 6 PASS / 3 FAIL. Provider order, config permissions, manifest/apiserver wiring, and peer hashes passed. The failures exposed two compatibility gaps: legacy clusters embed the key in the protected config without a separate key file, and kubeadm has etcdctl only in the distroless etcd static pod. The verifier now recognizes the embedded-key layout and executes etcdctl in that pod with explicit admin.conf, decoding JSON/base64 values on the node. The lead will rerun the live check after these fixes.

## NodeRestriction disable precedence (#113)

The offline kubelet/API-server baseline guard inspects both
`enable-admission-plugins` and `disable-admission-plugins` in list/map kubeadm
arguments and template fallback text. Explicitly disabling `NodeRestriction`
fails the declared baseline even if it also appears enabled or is assumed by
default. Additional configuration sources declaring disable flags require review.

Verify actual API-server command arguments and node admission behavior on the
cluster separately; passing repository source checks is not runtime evidence.
