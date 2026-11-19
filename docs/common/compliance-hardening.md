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
`--enable-admission-plugins=NodeRestriction`. `serverTLSBootstrap` remains omitted
and false; serving certificate rotation is a separate PKI lifecycle item. Worker
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

The hardening is applied through the kubeadm init configuration. The provisioning
idempotency guard skips `kubeadm init` when `admin.conf` already exists, so an
existing cluster does not receive these settings automatically. To reconcile it,
edit the `kubelet-config` ConfigMap in `kube-system` with the hardened
KubeletConfiguration, run `kubeadm upgrade node phase kubelet-config` on each
node, then restart the kubelet on each node. Validate the live configuration with
`scripts/verify/kubelet-authz-check.sh`. This runbook documents the operator steps;
the provisioning scripts do not mutate existing clusters.

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

Every exception in `portal/src/lib/compliance-accepted.ts` is fail-closed by exact `(namespace, kind, workload, checkID)` or `(namespace, kind, roleName, checkID)` — any unlisted check, unlisted workload, or unknown/mismatched kind remains actionable, and report rows are marked accepted only when every failing check is present in `checks[]`, accounted for by the per-severity `summary` totals, and accepted. The allowlist currently holds **70 Config Audit rules across 58 workloads** and **4 RBAC rules** (plus **13** exact upstream `(namespace, kind, roleName)` triples for `chaos-mesh` (`chaos-testing/Role/*`, cluster `ClusterRole/*`) and `kubernetes-dashboard` (`devtools/Role/kubernetes-dashboard-api`, `devtools/Role/kubernetes-dashboard-web`, `ClusterRole/kubernetes-dashboard-metrics-scraper`) in `portal/src/lib/compliance.ts`).

**ReplicaSet ownership and residual risk (fail-safe fallback when ReplicaSet lookup is unavailable):** `ClusterRole narwhal-portal` (`gitops/charts/narwhal-platform/templates/narwhal-portal-k8s.yaml`) grants read-only `get`/`list` access on `apps` `replicasets` (in addition to `deployments`, `statefulsets`, and `daemonsets`). When building Config Audit data (`portal/src/lib/compliance.ts`), the portal lists `ReplicaSet` objects once, drops reports whose `ReplicaSet` has `spec.replicas == 0` (old scaled-down Deployment revisions), and passes each `ReplicaSet`'s `metadata.ownerReferences` Deployment names into `normalizeWorkloadName` / `matchConfigAuditException` (`portal/src/lib/compliance-accepted.ts`). A trailing `pod-template-hash` matching the Kubernetes `SafeEncodeString` alphabet (`^[bcdfghjklmnpqrstvwxz2456789]{5,10}$`, no vowels and no `0`/`1`/`3`) collapses to `<name>` **only** when `metadata.ownerReferences` names a `Deployment` with `<name>` — a standalone `ReplicaSet` named `<allowlisted-name>-<5-to-10-safe-chars>` without a matching `Deployment` owner is **not** accepted, and non-`SafeEncodeString` suffixes are never collapsed even with an owner reference. **Residual risk:** if `/apis/apps/v1/replicasets` cannot be read (e.g. 403/timeout) or a report's `ReplicaSet` is missing from the returned list, the portal fails safe in the direction that never hides a live finding — it keeps the report, logs one warning, and falls back to the `SafeEncodeString` suffix rule while ownership is unknown.

Adding or expanding an entry requires:

1. Verifying that no safe manifest/chart knob fixes the finding without breaking functionality (`accept`) or that the upstream chart/image lacks the knob (`upstream`).
2. Recording a concise justification in `portal/src/lib/i18n.ts` (`ko`/`en` keys), `portal/src/lib/compliance-accepted.ts`, and the English tables below (enforced by `portal/src/lib/compliance-accepted.test.ts`).
3. Passing independent PR review before merging.

### Config Audit allowlist

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |
| chaos-testing | DaemonSet/chaos-daemon | KSV001, KSV003, KSV010, KSV012, KSV017, KSV020, KSV021, KSV022, KSV023, KSV106, KSV121 | accept | Requires privileged, hostPID, /sys and containerd.sock hostPath, root UID 0, and capabilities to inject pod netns and I/O faults |
| chaos-testing | DaemonSet/chaos-daemon | KSV014 | upstream | Upstream chaos-mesh 2.8.3 DaemonSet template does not expose readOnlyRootFilesystem |
| chaos-testing | ReplicaSet/chaos-controller-manager | KSV020, KSV021 | accept | Uses upstream chaos-mesh image default non-root UID/GID |
| chaos-testing | ReplicaSet/chaos-controller-manager | KSV001, KSV003, KSV012, KSV014, KSV106 | upstream | Upstream chaos-mesh 2.8.3 controller-manager template does not expose containerSecurityContext |
| database | Pod/narwhal-db-1 | KSV036 | accept | CloudNativePG instance manager calls Kubernetes API for cluster status, leader lease, and secrets, requiring mounted service-account token |
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
| nfs-quota-agent | DaemonSet/nfs-quota-agent | KSV001, KSV012, KSV017, KSV023, KSV121 | accept | Requires privileged root container, hostPath, and /dev access to set XFS/ext4 project quotas on the NFS export (allowPrivilegeEscalation: false cannot be combined with privileged) |
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
- **CIS 1.1.9 (CNI file perms) & 1.1.12 (etcd dir ownership)**: on-disk state is already
  correct (CNI files are 600; `/var/lib/etcd` is `etcd:etcd` recursively) but the trivy
  **node-collector can't verify them** from its container context (can't map the host
  `etcd` user; CNI dir 0700 not traversable by the scanner) — a scanner limitation, not a
  real gap. KCV0056 and KCV0059 can still be reported because of this limitation. Treated as risk-accepted.
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
