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

## Optional: root_squash alignment during the same downtime

Not part of the default path; needs separate explicit owner approval at migration time. The
default path preserves the live `no_root_squash` export unchanged.

Context: the live Kakao export is `no_root_squash` (installed 2026-08-26, before 6151b86b made
`root_squash` the default and 7e4e6227 fixed non-root writes); the share root is
`drwxrwxrwx nobody:nogroup`. Switching to `root_squash` breaks anything that writes as uid 0:

- SeaweedFS filer/master have no securityContext (uid 0, dirs `0:0 755`): they break.
- `alertmanager-db` is uid 0; about 72 uid-0 entries at depth <= 3 (`filerldb2/*`,
  `m9333/snapshot`, `alertmanager-db`).
- Gitea init containers may run as root: verify.
- CNPG, Harbor, Prometheus, Loki, Tempo, Grafana and OpenBao use their own uid/fsGroup and setgid
  dirs: fine.

Steps:

1. Separate GitOps PR first: give SeaweedFS `runAsUser`/`fsGroup`, or keep it off the NFS export.
2. After rsync pass 2, on the NEW volume, `chown` the uid-0 entries to each pod's uid (list them
   with `find /srv/nfs/k8s -maxdepth 3 -uid 0`).
3. Switch the export to `root_squash` using the export logic in `01-nfs-server.sh` (its export
   options are intentionally not changed by the PR that added this runbook), then `sudo exportfs -ra`.
4. Run `scripts/verify/nfs-nonroot-write-check.sh` and confirm every workload is Ready.
5. Rollback of this step only: revert the export line to `no_root_squash` and `sudo exportfs -ra`.
