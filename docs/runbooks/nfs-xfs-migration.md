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

Pause ArgoCD autosync for the apps below first (otherwise it scales them back up), then scale
down in this order and wait for each to terminate:

1. gitea
2. harbor
3. grafana, loki, tempo
4. prometheus and alertmanager: set the `Prometheus` / `Alertmanager` CR `replicas: 0` (not the StatefulSet)
5. seaweedfs
6. openbao
7. CNPG clusters: hibernate (`cnpg.io/hibernation: "on"` annotation)
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

EDQUOT test: from a non-root pod on a PVC with a small quota, write past the limit and expect
`Disk quota exceeded` (not `No space left on device` for the whole export). Check that
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
