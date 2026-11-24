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

Every exception in `portal/src/data/compliance-acceptance-rules.json` (loaded and validated by `portal/src/lib/compliance-acceptance.ts` / `portal/src/lib/compliance-accepted.ts`) is fail-closed by exact `(scope, namespace, kind, name, checkID)` with required `id`, `reason`, `owner`, and `reviewedAt` metadata — no namespace-blanket (`SYSTEM_NAMESPACES`), substring (`UPSTREAM_RBAC_ROLE_PATTERNS`), prefix (`system:`/`kubeadm:`), or check-less role acceptance exists. Any unlisted check, unlisted workload/role, or unknown/mismatched kind remains actionable, unused rules are surfaced in `ComplianceSummary` and the Compliance UI, and report rows are marked accepted only when every failing check is present in `checks[]`, accounted for by the per-severity `summary` totals, and accepted. The allowlist currently holds **45 Config Audit rules across 45 workloads**, **47 RBAC rules**, and **0 Infra rules**.

**ReplicaSet ownership and residual risk (fail-safe fallback when ReplicaSet lookup is unavailable):** `ClusterRole narwhal-portal` (`gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`) grants read-only `get`/`list` access on `apps` `replicasets` (in addition to `deployments`, `statefulsets`, and `daemonsets`). When building Config Audit data (`portal/src/lib/compliance.ts`), the portal lists `/apis/apps/v1/replicasets` in the same request path as `configauditreports` (with no separate longer-lived ReplicaSet cache; only the combined filtered result is cached), drops a `ReplicaSet` report **only** when it is a fully drained **superseded Deployment revision** — all of: (a) `metadata.ownerReferences` names a `Deployment`, (b) `spec.replicas == 0` **and** `status.replicas == 0` **and** `status.terminatingReplicas == 0` reported explicitly (`status.replicas` does not count terminating pods; a cluster that does not report the field keeps the report), and (c) the same `Deployment` (in the same namespace) owns at least one other `ReplicaSet` in the list with a strictly higher `deployment.kubernetes.io/revision` annotation — and passes each kept `ReplicaSet`'s `metadata.ownerReferences` Deployment names into `normalizeWorkloadName` / `matchConfigAuditException` (`portal/src/lib/compliance-accepted.ts`). Standalone `ReplicaSet`s (no `Deployment` owner), a `Deployment` intentionally or HPA/KEDA scaled to `0` (whose current-revision `ReplicaSet` has no higher-revision sibling), `ReplicaSet`s with `status.replicas > 0` (pods still terminating), and any `ReplicaSet` with missing/unknown `spec.replicas`, `status.replicas`, or `deployment.kubernetes.io/revision` remain counted so live findings and scaled-to-zero workloads are never hidden. A trailing `pod-template-hash` matching the Kubernetes `SafeEncodeString` alphabet (`^[bcdfghjklmnpqrstvwxz2456789]{5,10}$`, no vowels and no `0`/`1`/`3`) collapses to `<name>` **only** when `metadata.ownerReferences` names a `Deployment` with `<name>` — a standalone `ReplicaSet` named `<allowlisted-name>-<5-to-10-safe-chars>` without a matching `Deployment` owner is **not** accepted, and non-`SafeEncodeString` suffixes are never collapsed even with an owner reference. **Residual risk:** if `/apis/apps/v1/replicasets` cannot be read (e.g. 403/timeout) or a report's `ReplicaSet` is missing from the returned list, the portal fails safe in the direction that never hides a live finding — it keeps the report, logs one warning, and leaves the findings actionable while ownership is unknown.

### Framework actionable vs. accepted controls

`getComplianceFrameworks` and `getComplianceFrameworkDetail` (`portal/src/lib/compliance.ts`) derive per-control and per-framework `actionableFailCount`, `acceptedFailCount`, and `effectivePassRate` from `ClusterComplianceReport.status.detailReport.results[].checks[]` while keeping raw `failCount` and `passRate` visible:

- **Target & check normalization (fail closed):** `parseComplianceTarget` maps `<namespace>/<kind>-<name>` to `(namespace, kind, name)` (`ClusterRole` and `Node` require an empty namespace; namespaced kinds require a non-empty namespace; unknown kinds, missing/extra slashes, or empty names stay actionable). When available in the same request path, hashed report names (such as long StatefulSet names or `system:*` ClusterRoles) are resolved from `configauditreports`, `rbacassessmentreports`, and `clusterrbacassessmentreports` by explicit kind/name labels or `ownerReferences`, falling back to fail-closed raw target parsing when unavailable. Check IDs normalize `AVD-KSV-00NN` to `KSVNNN` via `normalizeCheckId`; missing or unrecognized IDs stay actionable.
- **Reuse of existing matchers (no second allowlist):** Workload targets (`ReplicaSet`, `DaemonSet`, `StatefulSet`, `Pod`, `Deployment`, `CronJob`, `Job`) reuse `configAuditMatcher` (exact `ACCEPTED_CONFIG_AUDIT_RULES`), `fetchReplicaSetMap()` superseded-revision dropping, and Deployment `ownerReferences` verification. RBAC targets (`Role`, `ClusterRole`) reuse `rbacAuditMatcher` (exact `ACCEPTED_RBAC_RULES`). Node (`Infra`) targets reuse `infraAuditMatcher` (`ACCEPTED_INFRA_RULES`, matching the Infra tab); any unlisted `KCV` check stays actionable.
- **Control & summary rollup:** A control is marked `accepted: true` only when `failCount > 0`, `actionableFailCount === 0`, and `acceptedFailCount > 0` (mixed controls remain actionable). When `detailReport.results` is absent (e.g., `reportType=summary`) or has fewer failing controls than `status.summary.failCount`, unaccounted failures remain in `actionableFailCount`.

### Three-state control evaluation & honest reporting

Trivy `ClusterComplianceReport` emits a placeholder passing entry (`checks: [{ checkID: "", success: true }]`) for any control with zero failed checks — even when the control is manual, skipped because `kube-system` is excluded, targets `RoleBinding`/`ClusterRoleBinding` omitted from `trivy.supportedConfigAuditKinds`, targets absent worker nodes, or links lowercase `avd-ksv-*` IDs. The portal separates `passed`, `failed`, and `not_evaluated` controls without inflating pass rates or widening RBAC (`narwhal-portal` `ClusterRole` already allows reading `nodes`, `deployments`, `configmaps`, and Trivy CRDs):

- **Three-state derivation (`classifyNonFailingControl` in `portal/src/lib/compliance.ts`):**
  - When `detailReport.results` is absent (`reportType=summary`), spec controls without underlying failure evidence become `not_evaluated` (`summary_only`) and `evaluatedPassRate` stays `0`; underlying accepted failures can still contribute to `effectivePassRate`.
  - Controls present in `spec.compliance.controls` but absent from `status.detailReport.results`, with no underlying failure evidence, are materialised as `not_evaluated` (`absent_from_report`) so they stay visible in the UI and `notEvaluatedCount`.
  - When `results[].checks` is `[]` and automated spec check IDs are unavailable, the control becomes `not_evaluated` (`empty_checks`).
  - When `spec.compliance.controls[]` carries automated checks elsewhere in the benchmark and a control omits both `checks` and `commands` (or specifies `checks: []`), it becomes `not_evaluated` (`manual`). If no control carries `spec.checks` metadata (trimmed fixture), fail closed as `coverage_unknown` when check linkage is missing; a CCR pass alone cannot establish the report family or scope.
  - Non-uppercase spec check IDs (`avd-ksv-*` in PSS Baseline 6-11 and PSS Restricted 6-17) become `not_evaluated` (`unlinked_check_id`).
  - **Failure-first check-catalogue evaluation (`D-FAILURE-FIRST-JOIN`, `D-CHECK-CATALOGUE`, `D-INFRA-NO-PER-CHECK-EVIDENCE`):** Any `success: false` for a linked check ID **or any of its catalogued aliases/legacy IDs** (`KSV0012 -> KCV0012`, `KCV0135 -> KCV0035`, `KSV-0124 -> KSV124`, resolved via `resolveCanonicalCheckId` without colliding `KSV0012` into `KSV012`) in **any** report family (`ConfigAuditReport`, `ClusterConfigAuditReport`, `(Cluster)RbacAssessmentReport`, `(Cluster)InfraAssessmentReport`) makes the control `failed` (with the existing accepted/actionable/superseded split) — independently of `/api/v1/nodes` role data so a failed nodes read never hides an existing failing record, and even when the 6-hourly CCR still shows a placeholder pass or omits the control. Because `trivy-operator` `0.25.0` drops all `success: true` checks in `evaluate()` (passed Rego rules have empty `Messages`, confirmed even with `report.recordFailedChecksOnly=false`), positive success records never exist in this deployment. Instead, the portal ships a generated check catalogue (`portal/src/data/trivy-check-catalogue.generated.json`) alongside an offline integrity digest manifest (`portal/src/data/trivy-check-catalogue.generated.sha256.json`), generated from the embedded `github.com/aquasecurity/trivy-checks v1.7.1` Go module in `trivy-operator:0.25.0` / chart `0.27.0` with `advanced/optional/` excluded via `node portal/scripts/gen-trivy-check-catalogue.mjs`. **Do not hand-edit the catalogue JSON:** changing entries requires regenerating from the upstream source (network or `--from-zip`) with the pinned versions bumped consciously; `gen-trivy-check-catalogue.mjs --check` and `R391` verify the canonical SHA-256 digest and version pins offline.
  - **Narrowed positive state (Config Audit & RBAC only; Infra always not evaluated without failure):** The positive state `"passed"` — labeled in UI as **"실패 기록 없음 (스캐너는 실패만 기록)" / "No failure recorded (scanner records failures only)"**, and never "모든 검사 통과 / All checks passed" on any empty failure list (Infra, Config Audit, or RBAC) — applies **only** to `configAudit` and `rbac` families when, for EVERY check ID in its spec: (a) the ID exists in the catalogue (unknown IDs like `AVD-KSV-9999`, removed IDs like `AVD-KSV-0029`, and skipped optional IDs like `AVD-KSV-0004`/`0039`/`0040` become `not_in_catalogue`), (b) operator scope (`OPERATOR_EXCLUDE_NAMESPACES`, `OPERATOR_TARGET_NAMESPACES`, `trivy.supportedConfigAuditKinds`) includes at least one applicable kind/namespace, (c) at least one in-scope report of the right family and kind exists **in the namespaces the check applies to** (`requiredNamespaces` and `scopeNamespaces`, e.g. `KSV110` / CIS `5.7.4` requires a `Workload` `ConfigAuditReport` in `default`, not `dev` or a `default` `Service` report; else `no_report_in_scope_namespace`, `no_report_for_family`, or `excluded_namespace`), and (d) all auxiliary reads succeeded (else `coverage_unknown`). For the **`infra` family (`AVD-KCV-*`, node-collector and control-plane component checks)**, reports cannot prove a check was evaluated (collector commands that return nothing and control-plane components without their own report leave no trace). Therefore infra checks **never** receive the positive state from report existence: they are `failed` when a failing record exists (in any family/alias), otherwise `not_evaluated` with reason `infra_no_per_check_evidence` ("노드/컨트롤플레인 검사: 스캐너가 항목별 평가 증거를 남기지 않음" / "Node/control-plane check: the scanner leaves no per-check evidence"). **Consequence:** most CIS node-level and control-plane component controls will show as not evaluated by the scanner on `/compliance`; those controls are verified on-node by `scripts/test/verify-cluster.sh`. Node-type gating (`missing_node_type`) remains driven by catalogue `nodeTypes` and explicit spec/command `nodeType` data, never inferred from CIS section numbers (`4.1.5` / `KCV0073` applies to `["master", "worker"]`).
- **Cluster-scoped Config Audit & independent KISA CFG-01 (`D-CONFIG-AUDIT-CLUSTER-SCOPE`, `D-CFG01-ALLSETTLED`):** `fetchConfigAuditListWithMeta` and `getConfigAuditDetail` fetch `ClusterConfigAuditReport`s alongside namespaced `ConfigAuditReport`s (either list failing throws `ComplianceDataUnavailableError`, never an empty success), resolve hashed resource names via `ownerReferences`/annotations, render cluster-scoped rows with a visible `Cluster` / `클러스터` scope (and `_cluster` detail route), and keep `configAuditMatcher` fail-closed so cluster findings (e.g. `ClusterRoleBinding` `KSV111`/`KSV122`) appear in the Config Audit list, counters, summary totals, and KISA `CFG-01`. `checkCfg01` evaluates all four sources (`frameworks`, `configAudit`, `rbac`, `infra`) with `Promise.allSettled`: any known actionable failure returns `fail` (naming both failing and unreadable sources), while unreadable sources with zero known failures return `warn` with the failed source list (never reasonless `manual` and never cached).
- **Report family & aliases (`D-CHECK-ALIASES`, `D-FAILURE-FIRST-JOIN`):** The catalogue assigns every embedded check ID its v0.25 report family (`AVD-KCV-*` -> `infra`, `Role`/`ClusterRole` -> `rbac`, workload / `NetworkPolicy` / `RoleBinding` / `ClusterRoleBinding` / `Service` / `ConfigMap` -> `configAudit`) and upstream `aliases`/`legacyId` list (`KCV0012` -> `["KSV0012"]`, `KCV0035` -> `["KCV0135"]`, `KSV124` -> `["KSV-0124"]`, `KSV123` -> `["KSV0123"]`, `KSV1011` -> `["KSV01011"]`). Failure joins span all families (including `ClusterConfigAuditReport`) and aliases before non-failing classification.
- **Old cache compatibility:** Framework list/detail, compliance summary and KISA use `compliance:v3:*` keys. Audit rows carry exact rule metadata. Missing evaluation fields in any old object mean unknown coverage: zero verified passes, remaining non-failed controls unevaluated, no raw-pass fallback in UI/SSR/CFG-01. Existing failed controls remain visible.
- **Trimmed snapshot replay (round 5, offline, failed-only reports):** 11 controls: raw pass 7/11 (63.6%), no failure recorded 1/11 (9.0%), exception-adjusted 4/11 (36.3%), not evaluated 6 (`infra_no_per_check_evidence: 2`, `excluded_namespace: 1`, `manual: 1`, `unsupported_kind: 1`, `unlinked_check_id: 1`), actionable failed 1, accepted failed 3. CIS has 8 controls (0 no failure recorded, 5 not evaluated [`1.1.1`, `4.1.5` as `infra_no_per_check_evidence`; `1.2.1`, `3.1.1`, `5.1.1`], 1 actionable [`1.1.9`], 2 accepted [`5.2.2`, `5.7.3`]); restricted PSS has 3 (1 no failure recorded [`1`], 1 not evaluated [`13`], 1 accepted [`3`]). The underlying reports expose two dropped checks on the superseded RS, versus one recorded in the CCR. The fixture's 178-control metadata is a historical full-snapshot reference, not a new live measurement.
- **Pooled pass rates without false `100%` (`formatPassRatePct` in `portal/src/types/compliance.ts`):** Summary cards compute pooled ratios (sum of numerators / sum of denominators, e.g. `145/178` raw) with explicit `numerator/denominator` labels across summary, grid, and detail views. Every `formatPassRatePct` call (evaluated, raw, and exception-adjusted) passes `hasNotEvaluated` alongside `hasActionableFail`, flooring to 1 decimal place and capping at `99.9%` whenever any control is unevaluated or actionable (`100%` appears only when every control is evaluated and zero actionable failures remain). KISA `CFG-01` (`portal/src/lib/kisa.ts`) fails on any actionable framework control or unaccepted config-audit/RBAC/infra finding (including LOW), warns with count on any not-evaluated control (or unknown coverage), and passes only when all controls are evaluated with zero actionable failures.
- **LOW visibility & superseded-ReplicaSet reconciliation:** `totalConfigAuditFailures` continues to count unaccepted `MEDIUM+` findings in non-system namespaces, `unacceptedLowConfigAuditFailures` surfaces unaccepted non-system `LOW` findings separately from accepted hygiene `lowSeverityConfigAuditFailures`, and `droppedConfigAuditReports` / `droppedConfigAuditFailures` / `droppedFailCount` / `droppedCheckCount` keep drained superseded-ReplicaSet drops visible next to raw and active numbers.
- **Data unavailability, partial metadata & freshness (`ComplianceDataUnavailableError`):** Kubernetes HTTP/network list errors throw `ComplianceDataUnavailableError` (`503 { unavailable: true, source }` on API routes) instead of caching empty arrays or displaying `0`. `getComplianceSummary()` isolates primary failures per source (`ComplianceSummary.sources` + `dataUnavailable`), returns `503 { unavailable: true }` from `GET /api/compliance/summary` when all four primary sources fail, surfaces auxiliary read failures via `partial` / `partialSources` with a visible UI notice naming the failed sources (never caching partial results), prefers `status.updateTimestamp` on `ClusterComplianceReport`s, and displays per-source ages alongside the 6-hour (`0 */6 * * *`) scheduled rebuild cadence note.

Adding or expanding an entry requires:

1. Verifying that no safe manifest/chart knob fixes the finding without breaking functionality (`accept`) or that the upstream chart/image lacks the knob (`upstream`).
2. Recording a concise justification in `portal/src/lib/i18n.ts` (`ko`/`en` keys), `portal/src/data/compliance-acceptance-rules.json`, and the English tables below (enforced by `portal/src/lib/compliance-accepted.test.ts`).
3. Passing independent PR review before merging.

Matching is exact: `database/Pod/narwhal-db-1` and the pooler currently have no acceptance rule: the available manifests/snapshot do not prove the former UID/ownership/template-limitation reasons. All CloudNativePG instance and pooler findings stay actionable until the capability necessity is verified for each exact target/check.

### Config Audit allowlist

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |
| chaos-testing | DaemonSet/chaos-daemon | KSV001, KSV003, KSV010, KSV012, KSV017, KSV020, KSV021, KSV022, KSV023, KSV106, KSV121 | accept | Requires privileged, hostPID, /sys and containerd.sock hostPath, root UID 0, and capabilities to inject pod netns and I/O faults |
| devtools | ReplicaSet/argocd-redis | KSV020, KSV021 | accept | Uses upstream Valkey 8 Alpine image default UID/GID 999 |
| devtools | ReplicaSet/gitea | KSV020, KSV021 | accept | Uses upstream Gitea rootless image default git UID/GID 1000 |
| devtools | ReplicaSet/harbor-core | KSV011 | accept | harbor.yaml D-HARBOR-PROBES omits CPU limits to avoid probe timeouts/502 during scan bursts |
| devtools | ReplicaSet/harbor-jobservice | KSV011 | accept | harbor.yaml D-HARBOR-PROBES omits CPU limits to avoid job execution throttling |
| devtools | ReplicaSet/harbor-registry | KSV011 | accept | harbor.yaml D-HARBOR-PROBES omits CPU limits to avoid probe timeouts during large pushes |
| devtools | ReplicaSet/headlamp | KSV011 | accept | headlamp.yaml omits CPU limits to avoid 1-second probe timeouts under VM load |
| devtools | ReplicaSet/kubernetes-dashboard-api | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/kubernetes-dashboard-auth | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/kubernetes-dashboard-kong | KSV020, KSV021 | accept | Uses Kong 2.52.0 chart default UID/GID 1000 |
| devtools | ReplicaSet/kubernetes-dashboard-metrics-scraper | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/kubernetes-dashboard-web | KSV020, KSV021 | accept | Uses Kubernetes Dashboard 7.14.0 chart default UID 1001/GID 2001 |
| devtools | ReplicaSet/narwhal-portal | KSV020, KSV021 | accept | Portal image fixes non-root nextjs:nodejs UID/GID 1001 in portal/Dockerfile |
| devtools | ReplicaSet/narwhal-portal-valkey | KSV020, KSV021 | accept | Relies on upstream valkey:8-alpine default non-root UID/GID 999 |
| kube-system | CronJob/ghost-pod-reaper | KSV020, KSV021, KSV037 | accept | ghost-pod-reaper.yaml calls the Kubernetes API to clean orphaned pods and explicitly sets numeric UID/GID 1000 |
| monitoring | DaemonSet/k8s-monitoring-alloy-logs | KSV012, KSV020, KSV021, KSV023 | accept | Requires root UID 0, recommended UID/GID ownership, and root-owned /var/log (/var/log/pods) hostPath to collect node and pod logs |
| monitoring | ReplicaSet/k8s-monitoring-alloy-operator | KSV020, KSV021 | accept | Relies on upstream image fixed non-root UID/GID 4242 |
| monitoring | ReplicaSet/prometheus-stack-grafana | KSV020 | accept | Relies on upstream Grafana UID 472 and NFS persistent volume file ownership |
| monitoring | StatefulSet/loki | KSV011 | accept | Omits main CPU limit intentionally to prevent CFS throttling timeouts on ingest/queries (D-MON-QOS-LOKI) |
| monitoring | StatefulSet/prometheus-prometheus-stack-kube-prom-prometheus | KSV011 | accept | prometheus-stack.yaml omits CPU limits to avoid probe timeouts during TSDB compaction |
| nfs-quota-agent | DaemonSet/nfs-quota-agent | KSV001, KSV003, KSV012, KSV017, KSV020, KSV021, KSV023, KSV121 | accept | Requires privileged root container, capabilities, hostPath, and /dev access to set XFS/ext4 project quotas on the NFS export (allowPrivilegeEscalation: false, drop ALL, and UID > 10000 cannot be used) |
| platform-system | ReplicaSet/apisix | KSV020, KSV021 | accept | Uses upstream APISIX image fixed UID/GID 636 |
| security-system | DaemonSet/falco | KSV001, KSV003, KSV012, KSV017, KSV020, KSV021, KSV023, KSV106, KSV121 | accept | falco.yaml D-falco-rofs retains privileged modern_ebpf instrumentation after observed initialization permission errors; leastPrivileged mode remains untested |
| security-system | ReplicaSet/falco-falcosidekick | KSV020, KSV021 | accept | Keeps upstream falco 9.2.1 / falcosidekick 0.14.1 default UID/GID 1234 |
| security-system | ReplicaSet/trivy-operator | KSV020, KSV021 | accept | Keeps upstream trivy-operator:0.25.0 default UID/GID 10000 |
| storage | DaemonSet/node-agent | KSV001, KSV003, KSV012, KSV020, KSV021, KSV023, KSV105, KSV106 | accept | Requires privileged/UID 0 and /var/lib/kubelet hostPath mounts for kopia fs-backup |
| storage | CronJob/openbao-snapshot | KSV020, KSV021 | accept | openbao-backup.yaml keeps UID 100 for the snapshot writer and holder sharing the same PVC files |
| storage | ReplicaSet/openbao-snapshot-holder | KSV020, KSV021 | accept | openbao-backup.yaml keeps UID 100 for the snapshot writer and holder sharing the same PVC files |
| storage | ReplicaSet/seaweedfs-s3 | KSV011, KSV020, KSV021 | accept | Omits CPU limit to prevent S3 upload throttling (D-sw-qos) and uses seaweed UID/GID 1000 |
| storage | ReplicaSet/velero | KSV020, KSV021 | accept | Runs as numeric cnb UID 1002 / GID 1000 for /home/cnb ownership in velero:v1.18.1 (runAsNonRoot: true enabled; UID/GID <= 10000) |
| storage | ReplicaSet/velero-ui | KSV020, KSV021 | accept | Uses upstream node image UID/GID 1000 |
| storage | StatefulSet/seaweedfs-filer | KSV011, KSV020, KSV021 | accept | Omits CPU limit to prevent upload throttling (D-sw-qos) and uses seaweed UID/GID 1000 |
| storage | StatefulSet/seaweedfs-master | KSV011, KSV020, KSV021 | accept | Omits CPU limit to prevent upload throttling (D-sw-qos) and uses seaweed UID/GID 1000 |
| kube-system | DaemonSet/cilium | KSV005, KSV009, KSV017, KSV022, KSV023, KSV106, KSV119, KSV120, KSV121 | accept | Cilium programs the host eBPF datapath and network namespaces: host networking, BPF/CNI host mounts, privileged BPF mount init container and NET_ADMIN/NET_RAW/SYS_ADMIN/SYS_MODULE capabilities. |
| kube-system | DaemonSet/cilium-envoy | KSV005, KSV009, KSV022, KSV023, KSV106 | accept | Cilium Envoy integrates with the host datapath through host networking, BPF/socket mounts and NET_ADMIN/SYS_ADMIN capabilities. |
| istio-system | DaemonSet/istio-cni-node | KSV005, KSV012, KSV020, KSV021, KSV022, KSV023, KSV105, KSV106, KSV119, KSV121 | accept | Istio CNI installs host CNI binaries/configuration and enters Pod network namespaces as UID/GID 0 using NET_ADMIN/NET_RAW/SYS_PTRACE/SYS_ADMIN/DAC_OVERRIDE. |
| istio-system | DaemonSet/ztunnel | KSV005, KSV012, KSV020, KSV021, KSV022, KSV023, KSV105, KSV106, KSV119 | accept | Ztunnel enters workload network namespaces via the CNI socket under /var/run/ztunnel as UID 0/GID 1337 with NET_ADMIN/SYS_ADMIN/NET_RAW to establish ambient traffic interception. |
| kube-system | Pod/etcd-narwhal-master-1 | KSV009, KSV023, KSV024 | accept | Kubeadm control-plane static Pods use host networking/ports before CNI is available and mount host PKI, kubeconfigs or etcd state to bootstrap and persist the control plane. |
| kube-system | Pod/kube-apiserver-narwhal-master-1 | KSV009, KSV023, KSV024 | accept | Kubeadm control-plane static Pods use host networking/ports before CNI is available and mount host PKI, kubeconfigs or etcd state to bootstrap and persist the control plane. |
| kube-system | Pod/kube-controller-manager-narwhal-master-1 | KSV009, KSV023, KSV024 | accept | Kubeadm control-plane static Pods use host networking/ports before CNI is available and mount host PKI, kubeconfigs or etcd state to bootstrap and persist the control plane. |
| kube-system | Pod/kube-scheduler-narwhal-master-1 | KSV009, KSV023, KSV024 | accept | Kubeadm control-plane static Pods use host networking/ports before CNI is available and mount host PKI, kubeconfigs or etcd state to bootstrap and persist the control plane. |
| kube-system | Pod/kube-vip-narwhal-master-1 | KSV009, KSV022, KSV023, KSV106, KSV119 | accept | Kube-vip binds the API virtual IP on the host interface and sends ARP using host networking, NET_ADMIN/NET_RAW and a mounted kubeconfig for leader election. |
| kube-system | DaemonSet/csi-nfs-node | KSV005, KSV009, KSV017, KSV022, KSV023, KSV037, KSV106 | accept | CSI NFS runs the NFS mount driver with privileged SYS_ADMIN and kubelet plugin/Pod host paths to mount and register node volumes; host networking supplies node mount connectivity. |
| kube-system | ReplicaSet/csi-nfs-controller | KSV005, KSV009, KSV017, KSV022, KSV023, KSV037, KSV106 | accept | CSI NFS runs the NFS mount driver with privileged SYS_ADMIN and kubelet plugin/Pod host paths to mount and register node volumes; host networking supplies node mount connectivity. |
| monitoring | DaemonSet/prometheus-stack-prometheus-node-exporter | KSV009, KSV010, KSV023, KSV121 | accept | Node exporter measures host network/process/kernel/filesystem metrics through hostNetwork, hostPID and read-only host proc/sys/root mounts. |

### RBAC allowlist

D-P2-7: The 31 bootstrap rules below were verified against both
`kubernetes.io/bootstrapping=rbac-defaults` and
`rbac.authorization.kubernetes.io/autoupdate=true` in read-only dumps.
Trivy RBAC reports do not carry target labels/annotations, and the portal does not
fetch Role objects; runtime acceptance therefore uses exact kind/namespace/name/checks.
Human/group cluster-admin bindings remain actionable. The additional 10 upstream
RBAC and 12 workload rules accept only checks supported by dumped rules/specs and
repository pins; writable roots, missing limits, broad RBAC and other unproven
security omissions remain actionable. This is snapshot verification, not a live replay.

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |
| cluster | ClusterRole/platform-admin | KSV041, KSV048, KSV049, KSV053, KSV056 | accept | rbac-policies.yaml grants the OIDC platform administrator application, Secret and pods/exec management, excluding node and RBAC writes |
| cluster | ClusterRole/velero-server | KSV041, KSV046, KSV048, KSV049, KSV050, KSV056 | accept | Least-privilege Velero backup/restore ClusterRole generated by scripts/ops/gen-velero-rbac.py (Secrets, workloads, ConfigMaps, networking, namespaced Roles/RoleBindings; wildcard restricted to velero.io/*; ClusterRoles read-only, no pods/exec or nodes/proxy) |
| cluster | ClusterRole/ghost-pod-reaper | KSV048 | accept | Narwhal ghost-pod-reaper ClusterRole grants pods get/list/watch/delete and nodes get/list only to clean orphaned pods (no Secrets, RBAC, or exec access) |
| cluster | ClusterRole/argocd-server | KSV041, KSV046, KSV048 | accept | Narwhal-narrowed ArgoCD server ClusterRole grants read-only secrets, read-only wildcard on 11 explicit platform CRD API groups, and workload delete/patch plus jobs create for UI actions (no nodes/proxy or pods/exec) |
| devtools | Role/narwhal-portal-jobs | KSV048 | accept | Tuning and Kaniko build Jobs guarded by Kyverno admission policies |
| monitoring | Role/narwhal-portal-notification-channels | KSV113 | accept | Scoped to a single Secret name with get/update/patch verbs only |
| cluster | ClusterRole/admin | KSV041, KSV048, KSV049, KSV050, KSV053, KSV056 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/cert-manager-cainjector | KSV041, KSV114 | accept | CA injector reads certificate Secrets and patches webhook CA bundles. |
| cluster | ClusterRole/cert-manager-controller-certificates | KSV041 | accept | Certificate controller writes Secrets containing issued certificates and keys. |
| cluster | ClusterRole/cert-manager-controller-challenges | KSV041, KSV048, KSV056 | accept | ACME challenge controller reads credential Secrets and creates HTTP-01 solver Pods, Services and Ingresses. |
| cluster | ClusterRole/cert-manager-controller-clusterissuers | KSV041 | accept | ClusterIssuer controller reads and writes issuer account credential Secrets. |
| cluster | ClusterRole/cert-manager-controller-issuers | KSV041 | accept | Issuer controller reads and writes issuer account credential Secrets. |
| cluster | ClusterRole/cert-manager-controller-orders | KSV041 | accept | ACME order controller reads issuer credential Secrets to submit certificate orders. |
| cluster | ClusterRole/cluster-admin | KSV044, KSV046 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/edit | KSV041, KSV048, KSV049, KSV053, KSV056 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/istiod-clusterrole-istio-system | KSV041, KSV049, KSV114 | accept | Istiod reads gateway TLS Secrets, publishes mesh ConfigMaps and patches injection/validation webhook configurations. |
| cluster | ClusterRole/istiod-gateway-controller-istio-system | KSV048, KSV056 | accept | Istiod gateway controller reconciles gateway Deployments, autoscalers and Services. |
| cluster | ClusterRole/metallb:controller | KSV114 | accept | MetalLB controller maintains the validating webhook configuration for address pools and advertisements. |
| cluster | ClusterRole/openbao-agent-injector-clusterrole | KSV114 | accept | OpenBao injector patches its mutating webhook configuration to publish the serving CA bundle. |
| cluster | ClusterRole/system:aggregate-to-admin | KSV050 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:aggregate-to-edit | KSV041, KSV048, KSV049, KSV053, KSV056 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:cronjob-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:daemon-set-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:deployment-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:endpoint-controller | KSV056 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:endpointslice-controller | KSV056 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:endpointslicemirroring-controller | KSV056 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:generic-garbage-collector | KSV046 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:horizontal-pod-autoscaler | KSV046 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:job-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:legacy-service-account-token-cleaner | KSV041 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:namespace-controller | KSV046 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:node-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:persistent-volume-binder | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:pod-garbage-collector | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:replicaset-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:replication-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:resourcequota-controller | KSV046 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:root-ca-cert-publisher | KSV049 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:statefulset-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:controller:ttl-after-finished-controller | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:kube-controller-manager | KSV041, KSV046 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:kube-scheduler | KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| cluster | ClusterRole/system:node | KSV041, KSV048 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| kube-system | Role/system:controller:bootstrap-signer | KSV113 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| kube-system | Role/system:controller:cloud-provider | KSV049 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |
| kube-system | Role/system:controller:token-cleaner | KSV113 | accept | Kubernetes bootstrap RBAC default, reconciled by kube-apiserver; cannot be narrowed |

### RBAC bootstrap binding allowlist

> **Threat Model (`D-KISA-CLOSED-WORLD`):** The KISA tab is a compliance indicator for operators; it is designed to stay correct under misconfiguration, drift, partial API failure, and stale/incomplete objects, and it never accepts by object name or pattern. Closed-world controls return `pass` only when every observed check dimension matches the reviewed baseline and exact rule table pinned in git, whereas unpinned objects, missing metadata, mismatched `subjects`/`checks`, or unknown/drifted policies stay `warn`/`manual`/`fail` (never `pass`). It is **not** a tamper-proof detector against an adversary who already holds `cluster-admin` and forges Kubernetes object contents, metadata, or specs byte-for-byte equal to the reviewed shapes.
>
> **Known Limits of `KISA-ETCD-02` and `KISA-NET-01` (`D-KISA-ETCD02-NO-PASS`):** Both controls evaluate Kubernetes API objects (`NetworkPolicy`, `CiliumNetworkPolicy` including `spec` and `specs[]`, and cluster-wide `CiliumClusterwideNetworkPolicy` including `spec` and `specs[]`, using read-only `list` on `cilium.io/{ciliumnetworkpolicies,ciliumclusterwidenetworkpolicies}` on `ClusterRole narwhal-portal`, `R378`) against `portal/src/data/kisa-policy-baseline.json` plus `apisix-etcd` `podSelector`/template label matching. Under `D-KISA-ETCD02-NO-PASS`, `KISA-ETCD-02` currently has **no `pass` path** (same closed-world stance as `KISA-SEC-01`): it requires every `NetworkPolicy` and `CiliumNetworkPolicy` in `platform-system` and every cluster-wide `CiliumClusterwideNetworkPolicy` to match the reviewed baseline SHA-256 without selector filtering (`warn` on unreviewed/drifted policy, `fail` if baseline isolation policy is missing), reports CLI flags as observed facts only (`--config-file` is reported as `flags may be overridden by the config file; not evaluated`), and returns `warn` when baseline isolation and rollout match — the pass path will be added together with etcd TLS as a baseline digest of the reviewed workload spec. In `KISA-NET-01`, any unreviewed or drifted `CiliumClusterwideNetworkPolicy` sets covered namespaces to `0/5` (and any CCNP read failure degrades to `manual`). The portal does **not** observe Cilium host firewall / node-level `CiliumClusterwideNetworkPolicy` host policies, ignores namespaced `NetworkPolicy`/`CiliumNetworkPolicy` objects outside the evaluated namespace(s) (which cannot select pods in another namespace), and does not probe live eBPF/kernel datapath enforcement (runtime packet drops are verified on-cluster by `scripts/test/` probes, not by the portal).

`KISA-RBAC-01` evaluates bindings (`ClusterRoleBinding` and `RoleBinding`) whose `roleRef` references `ClusterRole/cluster-admin` (a namespaced `Role` that merely shares the name, such as `Role/dev/cluster-admin`, is not `KISA-RBAC-01`'s subject by design and is evaluated by `KISA-RBAC-02`). `KISA-RBAC-02` evaluates all cluster and namespaced RBAC bindings across `ClusterRole` and `Role` under `D-KISA-CLOSED-WORLD` (never exempting by `system:`/`kubeadm:` name prefixes or role name). The `"rbacBindings"` array in `portal/src/data/compliance-acceptance-rules.json` explicitly enumerates the 44 verified Kubernetes bootstrap `ClusterRoleBinding` objects (`cluster-admin`, `system:basic-user`, `system:kube-controller-manager`, `system:kube-scheduler`, `system:node`, `system:node-proxier`, `system:volume-scheduler`, and 37 `system:controller:*` bindings) and requires ALL FOUR of:

1. Both live metadata entries: `labels["kubernetes.io/bootstrapping"] = "rbac-defaults"` AND `annotations["rbac.authorization.kubernetes.io/autoupdate"] = "true"`.
2. Exact `roleRef` match (`roleRefKind`, `roleRefName`).
3. Exact `checks` membership (`checks` array non-empty; every derived KSV ID — including `KSV046` on any wildcard, `KSV047` on `nodes/proxy`, `KSV049` on write `configmaps`, and `KSV111` fallback — must be explicitly allowed in `rule.checks`, so adding an unexpected permission like `users/impersonate` or `roles/escalate` breaks acceptance even if metadata is forged).
4. Exact canonical `subjects` set equality (`kind`, `name`, `namespace`). Cross-checked against live cluster bindings and upstream Kubernetes `release-1.35` (`plugin/pkg/auth/authorizer/rbac/bootstrappolicy/testdata/*.yaml`): **44 / 44** rules match upstream `bootstrappolicy` defaults identically (0 differences). Adding an extra `ServiceAccount` or user to a bootstrap binding fails `subjectsMatchExact`, and `isBootstrapRbacRole` prevents falling back to `rbac` role-only rules when binding acceptance fails.

Bindings without all four guarantees (including `kubeadm:cluster-admins`, `kubeadm:apiserver-kubelet-client`, `kubeadm:kubelet-bootstrap`, `kubeadm:node-autoapprove-bootstrap`, `kubeadm:node-autoapprove-certificate-rotation`, or tampered bootstrap bindings) stay actionable (`RBAC-02` live after round 7: Critical 3 including `kubeadm:cluster-admins`, High 39, exact-accepted 57 including `cluster-admin`).

### Infra allowlist

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |

## Node collector & CIS 1.1.9 / 1.1.12 resolution

- **CIS 1.1.12 (`KCV0059`, etcd data directory ownership `etcd:etcd`):** `node-collector:0.3.1` executes `CMD-0012` (`stat -c %U:%G $etcd.datadirs`, resolving to `/var/lib/etcd` via `defaultdatadir`). Live collector output on `narwhal-master-1` returned `"etcdDataDirectoryOwnership":{"values":["UNKNOWN:UNKNOWN"]}` because `/var/lib/etcd` is `700 etcd:etcd` (`995:984`) on the host while the container lacked the host `etcd` entry in `/etc/passwd`/`/etc/group`. `gitops/charts/narwhal-apps/templates/trivy-operator.yaml` mounts host `/etc/passwd` and `/etc/group` read-only into `nodeCollector` (`D-NODE-COLLECTOR-PASSWD`, preserving all chart `0.27.0` defaults) and sets `podAnnotations."checksum/node-collector-mounts"` (`D-NODE-COLLECTOR-PASSWD-ROLLOUT`) so the scanner resolves real `etcd:etcd` ownership; `KCV0059` is **not** allowlisted and stays actionable until rollout + rescan. Note: `trivy-operator` v0.25.0 (chart `0.27.0`) loads `nodeCollector` ConfigMap mounts only at operator startup and its upstream Deployment omits a ConfigMap checksum annotation, so on an existing cluster without the `podAnnotations` checksum change an ArgoCD sync alone does not apply new mounts — run `kubectl -n security-system rollout restart deploy/trivy-operator` before triggering/waiting for rescan.
- **CIS 1.1.9 / 1.1.10 (`KCV0056` / `KCV0057`, CNI file permissions `<= 600` and ownership `root:root`):** `node-collector:0.3.1` executes `CMD-0009` (`stat -c %a /*/cni/*`, `nodeType: master`) and `CMD-0010` (`stat -c %U:%G /*/cni/*`, `nodeType: master`). With the chart default `mountPath: /etc/cni/net.d/`, `/*/cni/*` matched the directory `/etc/cni/net.d` itself (`700 root:root`) instead of the files inside it (`05-cilium.conflist` `600 root:root`), falsely failing `KCV0056`. Mounting host `/etc/cni/net.d/` at container path `/etc/cni` in `gitops/charts/narwhal-apps/templates/trivy-operator.yaml` (`D-NODE-COLLECTOR-CNI-MOUNT`) makes `/*/cni/*` glob the actual CNI config files (`/etc/cni/05-cilium.conflist`), so live `node-collector:0.3.1` on `narwhal-master-1` returns `"containerNetworkInterfaceFilePermissions":{"values":[600]}` and `"containerNetworkInterfaceFileOwnership":{"values":["root:root"]}` while keeping every other collector key unchanged.
  - **Deployed policy & negative proof (measured 2026-11-22):** Live `trivy-operator:0.25.0` pulls `harbor.devtools.svc.cluster.local/trivy-db/aquasecurity/trivy-checks:1` (`revision: 1.11.3`, `sha256:a55ef4122a1683407bb0f85cbfa95b5b4622376292463b908f71230ae1534b02`) into `/tmp/policy` for commands/specs (`CMD-0009`/`CMD-0010`, `k8s-cis-1.23.yaml`), while `trivy.useEmbeddedRegoPolicies: "true"` evaluates `KCV0056` from the embedded `github.com/aquasecurity/trivy-checks v1.7.1` Rego (`sp.kind == "NodeInfo"`, `sp.type == "master"`, `violation := {p | p = sp.info.containerNetworkInterfaceFilePermissions.values[_]; p > 600}`, `count(violation) > 0`; `trivy-checks:1` `1.11.3` omits `KCV0056`/`KCV0057` `.rego` files under `/tmp/policy/content/policies/`). Running four temporary `node-collector:0.3.1` Jobs in `security-system` on `narwhal-master-1` (exact 45 `k8s` commands + `node.yaml` from the live operator, `/etc/cni` backed by `emptyDir`, all Jobs deleted) and evaluating the verbatim extracted `KCV0056` Rego via `trivy config` and `opa eval` proved: (a) single `644` file → `[644]` → **FAIL**; (b) `600` + `644` files → `[600, 644]` → **FAIL**; (c) empty dir → `[]` → **PASS**; (d) `700` sub-directory only → `[700]` → **FAIL**. Additionally, `[444]` passes upstream Rego because `444 <= 600` under integer `>` (not an octal bitmask `& 0177`), and `type: "worker"` with `[644]` passes because `sp.type == "master"`.
  - **Coverage limits & empty-result guard:** `KCV0056` is **not** allowlisted (`ACCEPTED_INFRA_RULES` is empty, fail-closed), so real `> 600` regressions on **control-plane (`master`) nodes** remain visible and actionable in Trivy. Because `CMD-0009`/`KCV0056` is `master`-only (`nodeType: master`, `sp.type == "master"`), Trivy does **not** report a worker node's `644` CNI file. Furthermore, because `values: []` passes `KCV0056`, the scanner alone cannot distinguish "no CNI files visible" from "all files compliant": on a `Ready` node kubelet/containerd requires a CNI config (`05-cilium.conflist`), so `values: []` is not reachable on a functioning `Ready` node, but it *is* reachable transiently during bootstrap/join or when `/etc/cni/net.d` is empty or contains only dotfiles (e.g. `.kubernetes-cni-keep`). `scripts/test/verify-cluster.sh` (`verify_node_file_hardening`) closes both gaps wherever it is run locally on a cluster node (`IS_CLUSTER_NODE=true`, `SKIP`s remotely): it checks permissions with the octal bitmask `8#7177` (rejecting `444`, `644`, `700`, and special bits) and **FAILs** when `/etc/cni/net.d` is missing, unreadable, empty, sub-directory-only, or contains no non-hidden `*.conf`/`*.conflist`/`*.json` CNI config file; worker nodes remain covered by `harden-node-files.sh` during provisioning/join and by `verify_node_file_hardening` where it is run.

## Scan coverage

Expanded scan coverage means raw failure counts rise now; exact verified acceptance
classifies required findings without hiding actionable hardening gaps.

Because Trivy Operator's `ClusterComplianceReport` records any control with zero failed reports as an empty-placeholder `PASS` (`checks: [{checkID: "", success: true}]`), an unevaluated check is indistinguishable from a passing check unless coverage is explicitly scoped and tracked. The table below records what is evaluated by Trivy Operator, what is verified only by on-node scripts, and what is evaluated by neither (measured against `trivy-operator` `0.25.0` / chart `0.27.0`, `node-collector:0.3.1`, and `trivy-checks:1` `1.11.3` + embedded `v1.7.1`).

> **Portal interaction after rollout (D2):** Findings were formerly classified `accepted/system` by the portal's `SYSTEM_NAMESPACES` blanket rule. D-P2 replaces that behavior with exact resource/check rules; system namespaces receive no automatic acceptance. Unverified system capabilities remain actionable, and unused rules appear in the summary API and Compliance UI. Earlier rollout counts are historical observations, not the current acceptance policy.
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
patch a report's `spec.cron` to `* * * * *`. To evaluate coverage accurately without reading
cluster secrets, `/compliance` also reads `nodes`, `deployments/trivy-operator`, and
`configmaps/trivy-operator-trivy-config` under the existing `narwhal-portal` `ClusterRole`,
surfaces `503 { unavailable: true }` on lookup failure, and shows per-source timestamps with the
6-hour rebuild cadence note.

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

## Scheduled CIS benchmark test records (2026-11-24, L2)

`gitops/resources/cis-benchmark.yaml` and Application `cis-benchmark` own daily
02:17 UTC node-specific CronJobs in `security-system`. They re-test node files,
process arguments and configuration with kube-bench **v0.16.0 / cis-1.23**, rather
than interpreting an absent scanner failure as a passed test. Docker Hub is used
because the orchestrator tested the upstream Aqua multi-arch image at the exact
pinned index digest; no alternative registry image was tested. Mirror that index
before rollout. No Kyverno exception, PSA label change or portal RBAC change is
introduced: admission of the one-off trial was measured by the orchestrator;
admission of this scheduled workload remains a live trial requirement.

D1 (coverage): Phase 2 measures `kubectl get nodes -o json` before its Gitea
push and generates one node-name-bound CronJob, SA, named Role and record
ConfigMap per node. The repository carries the renderer and shared script
ConfigMap template, with no per-cluster inventory. The live list handles
`NARWHAL_TOPOLOGY=single`, default `ha` (3+3), and Kakao provisioning without
assuming hostnames. R401 tests 1-node, 3+3 and renamed-node inputs, including
idempotent CLI rendering without PyYAML (standard-library YAML emitter). Generation
writes a temporary tree and replaces the destination only on success; failed
generation retains any existing rendered file. Generation or seed failure warns and continues Phase 2;
ArgoCD owns the published result. This avoids always-on host-reading DaemonSet
pods and a fan-out CronJob's node-list/create-Jobs permissions. Cost: new or
renamed nodes need explicit regeneration; no automatic controller is introduced.

After topology changes, from a checkout with cluster access:

```bash
kubectl get nodes -o json > /tmp/cis-nodes.json
python3 scripts/ops/render-cis-benchmark.py --nodes /tmp/cis-nodes.json
scripts/gitops/push-to-gitea.sh "fix(cis): refresh node coverage" resources/cis-benchmark.yaml
```

Also publish `charts/narwhal-apps/templates/cis-benchmark.yaml` when upgrading
from the old named-ignore Application. On a guest, render with
`--output-root /home/vagrant/configs/gitops` and pass that tree as `GITOPS_DIR`
to the push helper. The existing `narwhal-master-1` hash/name
`cis-bench-974178c0082e` and its `-latest` ConfigMap are preserved. Avoid pushing
the repository's unrendered script-only template over a deployed per-node file.
The push helper explicitly treats this resource as generated: both whole-tree and
explicit-file publication re-render from live nodes in a temporary tree, refusing
the entire push on discovery/render failure. Review that live node list before publishing.

Run this coverage/freshness check after a daily run and after topology changes;
exit 1 reports each live node lacking a complete record within 26 hours and labeled
records for removed nodes (including empty/error records):

```bash
kubectl get nodes -o json > /tmp/cis-nodes.json
kubectl -n security-system get configmaps -o json > /tmp/cis-records.json
python3 scripts/ops/render-cis-benchmark.py --check-nodes /tmp/cis-nodes.json --records /tmp/cis-records.json
```

D7 (retention): the CIS Application has `automated.prune: false`. Every generated
object carries `argocd.argoproj.io/sync-options: Prune=false,Delete=false`, matching
the existing OpenBao backup PVC syntax for the pinned ArgoCD v3.4.4
(`scripts/cluster/13-argocd.sh`). Thus inventory shrinkage and Application deletion
retain node objects and records. This does not prevent an explicit Kubernetes delete.
Cost: removed-node objects remain until manual cleanup. After removing the node,
regenerate/push the live inventory, confirm no active Job for its CronJob, and
archive its latest record before the following exact cleanup (set the removed name):

```bash
NODE=removed-node-name
CJ="cis-bench-$(printf '%s' "$NODE" | sha256sum | cut -c1-12)"
kubectl -n security-system delete cronjob,serviceaccount,role,rolebinding "$CJ"
kubectl -n security-system delete configmap "${CJ}-latest"
```

For existing-cluster adoption, render from live nodes, push both the resource and
Application through `push-to-gitea.sh`, then verify the delivered Application has
prune disabled and keeps the record bytes across sync. Offline checks cannot prove
this ArgoCD behavior; follow `.review/trial-plan-L2.md` for the live trial.

Control-plane nodes target `master,etcd,controlplane,node,policies`; workers target
`node`. `nodeRoles` records the normalized scheduling role (control-plane or worker);
unlabeled non-control-plane nodes are treated as workers, not untested control planes; `kubernetesVersion` is the API server's
runtime `gitVersion`, not a claim about every node's kubelet version.

D2 (ownership/security): each `<cis-bench-hash>-latest` ConfigMap has labels
`narwhal.io/compliance-tool=kube-bench` and `narwhal.io/benchmark-node=<node>`.
The sole data key is `record.json`; schemaVersion 1 includes runId (Pod UID), tool,
toolVersion, pinned image reference, benchmarkId, node, nodeRoles,
kubernetesVersion, startedAt, finishedAt, totals and per-control results. The
existing portal SA already reads ConfigMaps; interpretation is a separate lane.
The recorder's namespaced Role only gets/updates/patches its one precreated named
ConfigMap. The Application ignores `.data` only for ConfigMaps labeled
`narwhal.io/compliance-tool=kube-bench`, with `RespectIgnoreDifferences=true` as well as
selfHeal. All other fields and the script ConfigMap remain GitOps reconciled.
Cost: runtime evidence data drift is intentional. Escape hatch: remove the ignore
rule and sync option to reset records. One latest record per node is retained;
there is no historical archive. Only recorder containers mount an explicit
short-lived API token and the memory-backed `/tmp` holding run metadata and
published records. The scanner mounts only its output `/work` volume plus
read-only hostPaths; metadata identity never comes from `/work`. R401 rejects
metadata-volume sharing and checks both recorder security contexts. The scanner has hostPID and the trial's read-only host
mounts, UID 0, read-only root filesystem, no privilege escalation, and exactly
`drop ALL` + `DAC_READ_SEARCH` (needed for etcd data directory access).

D3 (failure/freshness): record-start first publishes `status: error`, no results,
null totals/finishedAt and `reason: scan not completed`. The tokenless kube-bench
initContainer writes JSON and its exit code into memory-backed emptyDir; the
main recorder replaces the record. Only validated, nonempty, matching-benchmark
JSON and reported `kube-bench version` equal to `0.16.0` with consistent totals and scanner exit 0 becomes `status: complete`.
Version/benchmark mismatches carry explicit error reasons. Nonzero exits, bad JSON, mismatched totals and oversized output become `error`
and fail the Job. An init OOM, image pull failure after record-start, or deadline
leaves the error placeholder; a failure before record-start leaves an older
record with its **old** time. Consumers must enforce freshness and complete
status, never refresh an old pass from Job creation time. API write failure
cannot guarantee a new error record and must be monitored as Job failure.
`complete` means the scan ran, not that every control passed. A simultaneous
manual run bypasses CronJob concurrencyPolicy: operators must not overlap it
with an active scheduled/manual Job for that node.

The standalone `scripts/ops/cis-benchmark-record.jq` drops audit commands and
remediation, truncates evidence fields to 400 characters and redacts whole
fields (including string-validated section) containing token/password/secret/credential/key markers, PEM/JWT material
or URL userinfo **before** truncation. The measured fixture has repeated process
arguments with key/token-related options pointing to files and the bootstrap-token-auth boolean; no PEM, JWT or URL
credentials were found by the offline pattern check. Path evidence is hidden
conservatively too. Do not publish the raw stdout/stderr; they remain in the
Pod's memory-backed volume until deletion. Redaction cannot prove arbitrary
future output is secret-free. The offline test against the full live fixture
produced 40,667 bytes with fixed test metadata (well below the per-node 900,000
byte safety limit and the ConfigMap 1 MiB limit); runtime metadata length varies.
The committed trimmed, pre-redacted fixture is smaller and retains all 125
control IDs/statuses. Both check the measured totals 74 PASS / 5 FAIL / 46 WARN /
0 INFO and exact FAIL IDs `1.1.12, 1.2.6, 1.3.7, 1.4.2, 4.2.6`.

WARN/manual is a test record requiring human assessment, **not** a passed control.
Section 5 contributes 30 manual WARN in the one-off run. The transform preserves
kube-bench's `manual` type; its empty default type is normalized to `automated`.
This is a different evaluator from Trivy: Trivy node-collector can lack file or
process evidence, while its Rego rules and control mapping differ; failed-only
Trivy reports do not prove success, and a kube-bench PASS does not erase a Trivy
failure. Kube-bench's explicit benchmark pin is required: the orchestrator
measured `--version 1.35` selecting cis-1.12 in this build. Do not silently switch
framework versions or merge controls by similar text. This lane fixes no finding
and changes no kubelet/control-plane settings. The scheduled Jobs, recorder
image tools (bash/kubectl/jq), server-version compatibility, API writes,
selfHeal preservation and both-role live execution still need the trial in
`.review/trial-plan-L2.md`; the one-off scan alone proves none of those paths.

The recorder reuses the already inventoried `alpine/k8s:1.31.4` bash/kubectl
convention (`ghost-pod-reaper`). jq availability is inferred, not established by
an existing job or runtime inspection in this lane; trial it before rollout.
Workers omit the unused etcd directory mount. Its image is also inline digest-pinned in images.txt/TRANSIENT_IMAGES so the
bundle preserves the index rather than reserializing only an architecture.
The upstream repository `aquasecurity/kube-bench` reports the Apache-2.0 license
(GitHub license API, measured 2026-11-24). The bundle SBOM has no license source
for this image artifact, so it stays in the SBOM review inventory with that note.
The image index digest was re-measured with `crane digest` on 2026-11-24T11:51:59Z.
