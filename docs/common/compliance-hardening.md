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
| Node file permissions | `chmod 600` kubelet service unit + config.yaml + k8s CA + static-pod manifests on all nodes (`scripts/cluster/harden-node-files.sh`). | CIS 4.1.1, 4.1.7, 4.1.9 (verified PASS) |

Result: CIS 72% → 84%+, NSA 44% → 59%+. Control-plane flag controls (profiling, audit, encryption) all PASS.

> **Gotcha (learned):** kubelet watches EVERY file in the static-pod dir
> (`/etc/kubernetes/manifests/`), not just `*.yaml`. A backup/temp file left there
> that defines the same pod (e.g. `kube-apiserver.yaml.bak`) SHADOWS the real
> manifest — the apiserver silently runs the stale spec. Always write manifest
> backups/temp files OUTSIDE that directory.

> **Live rollout note:** applied master-by-master via the manifest edit; the initial
> secret-encryption is only consistent once ALL apiservers share the key, after which
> `kubectl get secrets -A -o json | kubectl replace -f -` rewrites every Secret encrypted.
> **The source changes above are NOT yet clean-install validated** — verify enc/audit come
> up on a from-scratch `vagrant up` (all 3 masters) before relying on the provisioning path.

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

An empty result from steps 2 and 3 means existing data needs no changes — the
`root_squash` export change is safe to apply as-is.

### Fix an existing cluster's on-disk state (only if step 2/3 above found matches)

```bash
NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"

# Re-own every mismatched path to the identity root_squash now maps requests to.
# Review the step-2 output first — this is a recursive chown, scope it to the
# specific subpaths the audit flagged rather than blindly running it over the
# whole share if any subtree legitimately expects a different owner.
sudo find "${NFS_SHARE_PATH}" \( ! -user nobody -o ! -group nogroup \) -print0 \
  | sudo xargs -0 chown nobody:nogroup

# Re-apply exports if /etc/exports was hand-edited out of band.
sudo exportfs -ra
```

### Confirm success

```bash
# Re-run the audit — both should now be empty.
sudo find "${NFS_SHARE_PATH}" \( ! -user nobody -o ! -group nogroup \) -print
sudo find "${NFS_SHARE_PATH}" -uid 0 -print

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

## Deferred (safe, not yet done)

Worthwhile but out of the "quick safe win" scope; each needs care and control-plane
restarts on all 3 masters:

- **Persist node file perms across reinstall**: `harden-node-files.sh` is applied live
  but kubeadm/kubelet recreate these files at default perms on a fresh install. Wire the
  script into the provisioning path (post-join, per node) after clean-install validation,
  or run it manually post-install.
- **CIS 1.1.9 (CNI file perms) & 1.1.12 (etcd dir ownership)**: on-disk state is already
  correct (CNI files are 600; `/var/lib/etcd` is `etcd:etcd` recursively) but the trivy
  **node-collector can't verify them** from its container context (can't map the host
  `etcd` user; CNI dir 0700 not traversable by the scanner) — a scanner limitation, not a
  real gap. Treated as risk-accepted.
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
