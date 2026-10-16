# Runbook: move the Kakao NFS export from ext4 to a dedicated XFS prjquota volume

**REQUIRES EXPLICIT OWNER APPROVAL before execution.** This is a planned-downtime change
on a shared/production cluster: every NFS-backed workload stops. Do not run it from automation
or from `01-nfs-server.sh` (which refuses to mount over a non-empty `/srv/nfs/k8s` by design).

Why: XFS project quota is only honoured when the filesystem is mounted with `prjquota` at the
initial mount, so `nfs-quota-agent` cannot enforce quotas on the ext4 root disk
(`docs/common/lessons-log.md`, 2026-10-16). Decision D-NFS-VOL.

## 0. Preconditions

- Phase 1 applied: `nfs_data_volume_size` set, two-step `tofu apply` done (README "NFS data
  volume (Kakao)"), master-1 shows `update in-place` only, volume attached and visible as
  `/dev/disk/by-id/virtio-<volume id first 20 chars>`.
- A maintenance window and the owner's written OK. Know the old data size: `sudo du -sx /srv/nfs/k8s`.
- The volume is blank (`blkid` empty) and at least as large as the data plus growth.
- Commands run on master-1 unless stated; `DEV` is the by-id device path.

## 1. Backup (while everything still runs)

```bash
# CNPG databases: logical dump of every cluster, kept off the NFS volume
kubectl -n <ns> exec <cnpg-primary> -c postgres -- pg_dumpall -U postgres | gzip > /root/<cluster>-$(date +%F).sql.gz
```

Also snapshot master-1's root volume from the Kakao console if available.

## 2. Format and mount temporarily; rsync pass 1 (online)

```bash
sudo mkfs.xfs -L narwhal-nfs "$DEV"        # ONLY if blkid shows no filesystem
sudo mkdir -p /mnt/nfs-new && sudo mount -o prjquota "$DEV" /mnt/nfs-new
sudo rsync -aHAX --numeric-ids --sparse --info=progress2 /srv/nfs/k8s/ /mnt/nfs-new/
```

Pass 1 is online and can be repeated; it only shortens the downtime copy.

## 3. Stop writers

**Pre-check: pin node-local workloads before any scale-down.** Some workloads keep data on a
node-local `hostPath`, not on NFS. A scale-down/up lets the scheduler place them on another
worker, where they start empty (2026-10-16: the SeaweedFS volume server moved from `.22`, which held
23G, to `.21` and Loki crash-looped on `unexpected EOF`). List them and record the node holding the data:

```bash
kubectl get pods -A -o json | jq -r '.items[] | select(any(.spec.volumes[]?; .hostPath != null))
  | select(.metadata.namespace | test("^(kube-system|monitoring-agents)$") | not)
  | "\(.metadata.namespace)/\(.metadata.name) node=\(.spec.nodeName) hostPath=\([.spec.volumes[].hostPath.path // empty] | join(","))"'
kubectl get nodes -l narwhal.io/seaweedfs-volume=true      # must name the node that holds /ssd/object_store
```

If the SeaweedFS label is missing (cluster provisioned before D-sw-pin), put it on the node that
holds the data BEFORE scaling anything down: `kubectl label node <node> narwhal.io/seaweedfs-volume=true`
(find it with `du -sh /ssd/object_store` on each worker). Any other hostPath workload found must be
pinned the same way (or cordon every other candidate node while it starts).

Rollout order for the SeaweedFS pin (D-sw-pin) on a cluster that already holds data: label the data
node BEFORE the new chart reaches the cluster, and never run `08-4-storage.sh` first. On Kakao, ArgoCD
syncs from the in-cluster Gitea, which only `scripts/cluster/14-gitops-bootstrap.sh` updates, so a GitHub
merge alone changes nothing there. Order: verify the data node, label it, merge, run 14, then verify
the volume pod stays on that node.

Pause ArgoCD autosync for the apps below first (otherwise it scales them back up), then scale
down in this order and wait for each to terminate. The root app `idp-apps` owns the child
Applications and re-creates their `automated` policy, so pause it too (remove `automated` on
`idp-apps` and on each child, keep the originals to restore):

1. gitea
2. harbor
3. grafana, loki, tempo
4. prometheus and alertmanager: set the `Prometheus` / `Alertmanager` CR `replicas: 0` (not the StatefulSet)
5. seaweedfs
6. openbao
7. CNPG clusters: hibernate (`cnpg.io/hibernation: "on"` annotation); the primary may be a different
   instance after un-hibernation (2026-10-16: `narwhal-db-2` became `narwhal-db-1`), which is expected
8. nfs-quota-agent (DaemonSet)

Verify no NFS client remains:

```bash
# every node
findmnt -t nfs,nfs4                                   # must print nothing for the narwhal export
# master-1
ss -Htn state established sport = :2049 | wc -l       # must be 0
```

Stop the server: `sudo systemctl stop nfs-server`.

## 4. Pass 2 and cutover

```bash
sudo rsync -aHAX --numeric-ids --sparse --delete /srv/nfs/k8s/ /mnt/nfs-new/
sudo rsync -aHAXn --numeric-ids --sparse --delete --itemize-changes /srv/nfs/k8s/ /mnt/nfs-new/   # dry-run: must print nothing
sudo umount /mnt/nfs-new
```

Mount at the real path. The old ext4 data stays underneath as the rollback copy. Do the
cutover by hand, because `01-nfs-server.sh` refuses a non-empty `/srv/nfs/k8s`:

```bash
UUID=$(sudo blkid -p -o value -s UUID "$DEV")
echo "UUID=${UUID} /srv/nfs/k8s xfs defaults,prjquota,nofail 0 2" | sudo tee -a /etc/fstab
sudo mkdir -p /etc/systemd/system/nfs-server.service.d
printf '[Unit]\nRequiresMountsFor=/srv/nfs/k8s\n' | sudo tee /etc/systemd/system/nfs-server.service.d/requires-mount.conf
sudo systemctl daemon-reload
sudo mount /srv/nfs/k8s
findmnt -no FSTYPE,OPTIONS /srv/nfs/k8s      # must be xfs with prjquota
sudo systemctl start nfs-server && sudo exportfs -v
```

Export options are unchanged.

## 5. Bring up (reverse order)

nfs-quota-agent, CNPG (remove hibernation), openbao (unseal as usual), seaweedfs,
prometheus/alertmanager (restore CR replicas), grafana/loki/tempo, harbor, gitea; re-enable
ArgoCD autosync last.

## 6. Verify

```bash
sudo xfs_quota -x -c 'state -p' /srv/nfs/k8s        # project quota accounting and enforcement ON
sudo xfs_quota -x -c 'report -p -h' /srv/nfs/k8s    # one project per quota-managed PV
scripts/verify/nfs-nonroot-write-check.sh           # non-root write from a pod
```

Quota overflow test: from a non-root pod on a PVC with a small quota, write past the limit. XFS
project quota over NFS returns `ENOSPC` (`No space left on device`), not `EDQUOT`
(`Disk quota exceeded`); expect the write to stop at the PVC size while the rest of the export stays
writable (`df` on another PVC is unaffected). Check that
nfs-quota-agent logs show applied (not failed) projects, all workloads are Ready and ArgoCD
apps are Synced/Healthy.

## 7. Rollback

1. Repeat step 3: stop every writer in the same order and verify no NFS clients remain
   (`findmnt -t nfs,nfs4` on all nodes, `ss ... sport = :2049` == 0), then `sudo systemctl stop nfs-server`.
2. Copy writes made after the cutover back to the old ext4 copy, which is still hidden under the mount.
   First confirm the XFS volume is still mounted over it:

   ```bash
   findmnt -no FSTYPE /srv/nfs/k8s    # must print: xfs
   ```

   If it prints `ext4` or nothing, the XFS volume is already unmounted and `/srv/nfs/k8s` is the ext4
   directory itself, so the reverse sync below would copy the directory onto itself. Do not run it:
   mount the XFS volume at a temporary path (`sudo mkdir -p /mnt/xfs-new && sudo mount UUID=<uuid> /mnt/xfs-new`)
   and use `/mnt/xfs-new/` as the rsync source instead, then `sudo umount /mnt/xfs-new`.

   Bind-mount the root filesystem to reach the ext4 copy:

   ```bash
   sudo mkdir -p /mnt/rootfs-old && sudo mount --bind / /mnt/rootfs-old
   sudo rsync -aHAX --numeric-ids --sparse --delete /srv/nfs/k8s/ /mnt/rootfs-old/srv/nfs/k8s/
   sudo umount /mnt/rootfs-old
   ```

   If you skip this, everything written after the cutover is LOST on rollback.
3. `sudo umount /srv/nfs/k8s`, remove the fstab line and the `requires-mount.conf` drop-in, `sudo systemctl daemon-reload`.
   The ext4 directory is exposed again.
4. `sudo systemctl start nfs-server`, then bring up as in step 5.

Delete the old ext4 data only after a verified soak period.

Record of the first execution (timeline, gates, deviations, cleanup): [`docs/kakao/nfs-xfs-migration-2026-10-16.md`](../kakao/nfs-xfs-migration-2026-10-16.md).

## Optional: root_squash alignment

Not part of the default path; needs separate explicit owner approval. The default migration
preserves the live `no_root_squash` export unchanged. This is the procedure that makes
`root_squash` safe on the live Kakao export (narwhal#224, #186). No downtime is needed before the
export switch; the SeaweedFS filer and master restart once.

Context: the live Kakao export is `no_root_squash` (installed 2026-08-26, before 6151b86b made
`root_squash` the default and 7e4e6227 fixed non-root writes). Layout there versus the
Vagrant/`01-nfs-server.sh` layout that is proven under `root_squash`:

| Path | Live Kakao (reported, re-verify with the dry run) | Target (Vagrant-proven) |
| --- | --- | --- |
| share root | `nobody:nogroup 0777` | `nobody:nogroup 0750` |
| `<share>/<namespace>` | `root:root 0755` | `nobody:nogroup 0755` (the squashed provisioner creates `<ns>/<pvc>` as nobody, so it must own `<ns>`; no `chmod 777`) |
| `<share>/<ns>/<pvc>` | owned by the app (kubelet fsGroup chown under `no_root_squash`) | `0777` from the server-side timer `narwhal-nfs-pv-permissions` |
| root-owned files inside a PV | SeaweedFS filer/master (~76 + 6 entries), `alertmanager-db`, `prometheus-db` (uid 0, gid 2000, `2775`) | owned by the workload uid:gid |

Why this layout: csi-driver-nfs creates the directories as the squashed `nobody`; the NFS CSI
driver sets no fsGroup policy (`feature.enableFSGroupPolicy=false`), so kubelet never chowns; the
timer is the only thing that makes a fresh PV directory writable to an arbitrary workload uid.

Preconditions (the align script prints each of them in its dry run):

- SeaweedFS runs as non-root: the GitOps change in this PR (`runAsUser`/`runAsGroup`/`fsGroup`
  1000 for master and filer, logs on `emptyDir`; uid verified as the `seaweed` user of
  `chrislusf/seaweedfs:4.34`). The chart overrides the image entrypoint, so without it the pods
  stay uid 0.
- `kubectl get csidriver nfs.csi.k8s.io -o jsonpath='{.spec.fsGroupPolicy}'` is not `File`. The
  chart default (`feature.enableFSGroupPolicy=true`) renders `File`, so a cluster installed before
  7e4e6227 (2026-09-28) still has it (Kakao: `File`, found 2026-10-16). `CSIDriver.spec.fsGroupPolicy`
  is immutable: `kubectl patch` and `helm upgrade` cannot change it, the object must be deleted and
  recreated. If it is `File`, delete only that object and re-run the chart exactly as
  `scripts/cluster/04-addons.sh` does (~1 s without a CSIDriver; existing PVs and mounts are
  unaffected, the controller and node pods keep running):

  ```bash
  kubectl delete csidriver nfs.csi.k8s.io
  helm upgrade --install csi-driver-nfs <chart bundle or repo chart> -n kube-system \
    --version <CSI_DRIVER_NFS_VERSION, default in scripts/cluster/04-addons.sh> \
    --set controller.replicas=1 --set feature.enableFSGroupPolicy=false
  kubectl get csidriver nfs.csi.k8s.io -o jsonpath='{.spec.fsGroupPolicy}'   # ReadWriteOnceWithFSType
  ```

  A `File` policy makes kubelet chown PV volumes through `root_squash`.
- `systemctl is-active narwhal-nfs-pv-permissions.timer` is `active` (step 5 installs it if not).

Steps, in this order:

1. Merge the GitOps PR. Kakao is unaffected until step 3, because the cluster syncs from Gitea.
2. On the NFS server (`master-1`), as root (the script refuses otherwise; once the share root is
   `0750 nobody:nogroup` an unprivileged user cannot traverse it): dry run
   `sudo bash scripts/cluster/nfs-root-squash-align.sh` and read the plan (exit 3 means a PV was
   skipped, exit 4 that a PV directory could not be read: resolve either first), then
   `sudo bash scripts/cluster/nfs-root-squash-align.sh --apply`. It chowns only
   root-owned entries to the workload identity (SeaweedFS `1000:1000` from the reviewed map, the
   rest from the bound pod's `securityContext`), makes namespace directories `nobody:nogroup`
   and the share root `nobody:nogroup 0750`. It never edits `/etc/exports`. The SeaweedFS pods are
   still uid 0 here; root bypasses permissions under `no_root_squash`, so this cannot break them.
3. Stage and roll out: run `scripts/cluster/14-gitops-bootstrap.sh`. The filer and master restart as
   uid 1000. Race: until they stop, the old root processes keep creating uid-0 files (master raft
   snapshot `0600`, filer leveldb `.ldb`/`MANIFEST`, flush on shutdown), and the new pods hit
   `EACCES` on them. So, immediately after the rollout, re-run
   `sudo bash scripts/cluster/nfs-root-squash-align.sh --apply` (the running-consumer guard passes because the
   pod uid now equals the target), then delete the master and filer pods if either is in
   CrashLoopBackOff (`kubectl -n storage delete pod seaweedfs-master-0 seaweedfs-filer-0`).
4. Verify: filer and master Ready, `kubectl -n storage exec seaweedfs-filer-0 -- id -u` and the
   master print `1000`, and S3 works (the Loki pod is the canary).
5. Switch the export. WARNING: `01-nfs-server.sh` rewrites `/etc/exports` wholesale (and restarts
   `nfs-kernel-server`, a short stall; mounts are `hard`, so clients wait and recover). Back it up
   and take the live CIDRs from it first; the script also installs the timer:

   ```bash
   sudo cp -a /etc/exports /etc/exports.pre-rootsquash
   sudo grep -n "${NFS_SHARE_PATH:-/srv/nfs/k8s}" /etc/exports   # note the two CIDRs
   HOST_NETWORK_CIDR=<first cidr> POD_NETWORK_CIDR=<second cidr> bash scripts/cluster/01-nfs-server.sh
   sudo exportfs -v   # both lines must say root_squash
   ```

   **Supported alternative where apt is unreachable** (used on Kakao 2026-10-16), or where the timer
   is already active and only the option must change: `01-nfs-server.sh` runs `apt-get update` and
   rewrites `/etc/exports`, so skip it. Install the timer from the script's own blocks, then flip
   only the export option. Run the timer block of `01-nfs-server.sh` (script, `.service` and
   `.timer` heredocs, `daemon-reload`, `enable --now`, first run) from a copy of the repo on master-1:

   ```bash
   sed -n '/^sudo tee \/usr\/local\/sbin\/narwhal-nfs-pv-permissions/,/^sudo systemctl start narwhal-nfs-pv-permissions.service/p' \
     scripts/cluster/01-nfs-server.sh | bash
   systemctl is-active narwhal-nfs-pv-permissions.timer          # must print: active
   TS=$(date -u +%Y%m%dT%H%M%SZ)
   sudo cp -a /etc/exports "/etc/exports.pre-rootsquash-${TS}"   # timestamped backup
   sudo sed -i 's/no_root_squash/root_squash/' /etc/exports
   sudo exportfs -ra
   sudo exportfs -v                                              # both lines must say root_squash
   diff "/etc/exports.pre-rootsquash-${TS}" /etc/exports         # only the option may differ
   ```

6. Verify under squash: every pod that mounts an NFS PVC is Ready, and
   `scripts/verify/nfs-nonroot-write-check.sh` passes (PASS, not WARN, for the share root). It also
   proves dynamic provisioning: it creates a throwaway namespace and PVC from the `nfs-csi` class
   and writes from a pod running as non-root uid 26; a PVC stuck Pending or a failed `touch` is a
   failure.

Rollback, in this order. Reverting the GitOps change (SeaweedFS back to root) is only safe while
the export is `no_root_squash`, so:

1. Export first: `sudo cp -a /etc/exports.pre-rootsquash /etc/exports` (or set the two lines back
   to `no_root_squash`), `sudo exportfs -ra`, confirm with `sudo exportfs -v`.
2. Only then, and only if the non-root filer/master themselves misbehave, revert the GitOps change
   and re-run `14-gitops-bootstrap.sh`. The chowns stay valid: app-owned files work under both
   modes.
