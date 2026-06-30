# Reboot Auto-Recovery

> How the narwhal local cluster heals itself after a host/VM reboot — and what
> to do if it doesn't.

Narwhal is a **local** Vagrant/VMware cluster, so the VMs get powered off and on
often (host reboot, `vagrant halt`/`up`, laptop sleep). A cold boot leaves the
cluster in a wedged state that does **not** self-heal out of the box. Two
systemd units, installed during provisioning and fired on **every boot**, fix
this automatically with no manual intervention.

## The problem (why a cold boot wedges the cluster)

After an unclean shutdown + cold boot, three failure modes pile up:

1. **containerd stale-container wedge** — containerd keeps metadata for
   containers that have no running task (`{running 0}`), so the kubelet hits
   `CreateContainerError` and can't recreate static pods (kube-apiserver,
   kube-scheduler) or DaemonSet pods (metallb, …). Symptom:
   `CreateContainerError`, `crictl ps` failing.
2. **CNI ghosts** — Cilium / Istio-CNI / ztunnel pods from before the reboot
   stay `Unknown`; new pods can't get a network sandbox and hang in
   `ContainerCreating`.
3. **Platform CrashLoops** — APISIX, metallb, gitea, tempo, etc. crash because
   their dependencies (CNI, DB, Harbor) aren't ready yet, and never recover on
   their own.

Observed impact before the fix: after a host reboot the cluster sat with
~40+ non-running pods and **ingress dead** (no APISIX/metallb) until containerd
was manually restarted on every node and the ghost pods were deleted.

> Note: the Phase-2 `cilium_health_gate` (06-phase2-start.sh) only runs during
> provisioning, so it does **not** help a plain `vagrant up`/reboot resume —
> hence the dedicated boot-time units below.

## The solution

Two oneshot systemd units (`scripts/common/06-boot-heal-install.sh` installs +
enables them during provisioning; they then fire on every boot):

### `narwhal-boot-heal.service` (all nodes)

`scripts/common/boot-heal.sh`, ordered `After=containerd.service kubelet.service`.

- **Guard:** exits immediately if `/etc/kubernetes/kubelet.conf` is absent
  (node not yet joined — never interferes with first provisioning).
- Sleeps 45s to let containerd/kubelet settle.
- **Wedge detection** (any one → recover):
  1. `kubelet` not active, or
  2. `crictl ps` fails (containerd wedged), or
  3. one or more containers stuck in a non-`Running`/non-`Exited` state
     (`Unknown`/`Created` — the stale-container hallmark).
- **Recovery:** `systemctl restart containerd` → `kubelet`. No-op on a healthy
  node.

### `narwhal-cluster-heal.service` (master-1 only)

`scripts/cluster/cluster-heal.sh`, ordered `After=network-online.target`.

- Waits for the API server (up to 300s) and ≥5/6 nodes Ready.
- Deletes not-Ready/`Unknown` Cilium + operator and Istio (cni/ztunnel/istiod)
  pods so the DaemonSets/Deployments recreate them cleanly.
- Force-deletes all remaining `Unknown` ghost pods cluster-wide (controllers
  recreate them).
- Force-deletes `CrashLoopBackOff`/`CreateContainerError` pods in
  platform-system/devtools/monitoring/storage (metallb, apisix, …) to give them
  a fresh start.
- Polls up to 450s for convergence (≤3 non-running pods, excl. transient Trivy
  scan jobs). Best-effort: always exits 0.

## Validation (2026-07-01)

`vagrant reload` of all 6 VMs, **no manual intervention**:

- `boot-heal` on master-1 logged `Wedge detected: crictl ps failed — containerd
  may be wedged` and restarted containerd+kubelet.
- `cluster-heal` drove non-running pods 46 → 41 → 19 → 10 → **0**.
- Final: ingress `portal.local.narwhal.internal` → **HTTP 307**; 6/6 nodes
  Ready; Cilium/Istio/Keycloak/Harbor/portal all Running.

## Operating notes

- **Manual trigger** (if ever needed):
  `sudo systemctl start narwhal-boot-heal` on a node, or
  `sudo systemctl start narwhal-cluster-heal` on master-1.
- **Logs:** `journalctl -u narwhal-boot-heal` / `journalctl -u narwhal-cluster-heal`.
- **Existing clusters** (provisioned before these units existed) need them
  installed once: `sudo bash /home/vagrant/scripts/common/06-boot-heal-install.sh`
  on each node, then they fire on every subsequent boot.

## Known cosmetic artifacts (not failures)

- **Mirror pods stuck `0/1` with `<invalid>` age** right after boot: VM clock
  skew makes the kubelet's status-update loop briefly stall on static-pod mirror
  objects. `/readyz` and `etcdctl endpoint health` are 200/healthy — it's a
  display artifact that clears once the clock settles.
- **`velero` CrashLoopBackOff**: unrelated to reboot — Velero needs its backup
  storage location seeded (SeaweedFS S3); see the Velero docs, not this flow.

## Related

- DNS: node `systemd-resolved` uses the 3 master dnsmasq nodes as `DNS=` and
  public resolvers as `FallbackDNS=` (avoids the glibc 3-nameserver overflow).
- `scripts/up.sh` Phase-2 retry loop handles the *provisioning-time* VMware SSH
  race; the units here handle the *runtime reboot* case.
