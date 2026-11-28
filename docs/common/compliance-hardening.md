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
| `security-system/falco` (`falco.yaml`, GitOps-only) | `containerSecurityContext` sets `readOnlyRootFilesystem: true`; on 2026-11-26 (`D-FALCO-LEAST-PRIV`, R371/R421) `driver.modernEbpf.leastPrivileged: true`, `podSecurityContext.seccompProfile.type: RuntimeDefault`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, and `capabilities.add: [BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE]` replaced the earlier `privileged: true` (`D-falco-rofs`; chart `falco-9.2.1` mounts `/root/.falco`, and setting `containerSecurityContext` replaces the chart's computed context). | Without `privileged: true` or the 4 capabilities, Falco failed `scap_init` (`errno: 1`) and crash-looped ~3m (6 restarts); with `privileged: true` repeated (2026-11-19), pod reached Ready with 0 restarts, opened the modern BPF probe, and emitted 22 rule events in 2 minutes after a test pod (orchestrator, measured 2026-11-19). Offline `helm template` (`falco-9.2.1.tgz`), `trivy config`, and R358/R371/R421 pass for the least-privileged context; verified live on the single-node cluster 2026-11-27: `CapEff 000000c001080000`, `NoNewPrivs 1`, `Seccomp 2`, no scap_init errors, events keep `k8s.ns.name`/pod/image, `D-FALCO-NOISE` unchanged. |
| `storage/velero` `node-agent` (`velero.yaml`, `08-4-storage.sh`) | `nodeAgent.containerSecurityContext` is `readOnlyRootFilesystem: true` plus `capabilities.drop: [ALL]`; the ignored `nodeAgent.privileged` value is removed. `/root` (`node-agent-home`) and `/tmp` (`tmp`) remain writable, and `runAsNonRoot: false` is preserved (`D-NODE-AGENT-DROP-ALL`). | Measured live on the single-node cluster (2026-11-26, Velero 1.18/chart 12.0.3/kopia): the node-agent container ran with CapEff `0000000000000000`; the kopia data copy runs in a separate per-volume data-path Pod (UID 0, `privileged: false`, chart-default capabilities, not changed by this setting); fs-backup of uid/gid 1234, mode 0600 file in a mode 0700 directory Completed, and restore into another namespace Completed with identical content, uid/gid, and modes. Offline render and R371 pin exact context in GitOps/bootstrap. Cost: a future uploader needing capabilities can fail backups; add only capabilities justified by a new backup/restore trial. |
| `nfs-quota-agent/nfs-quota-agent` (`05-nfs-quota-agent.sh`, Helm-only) | `HELM_ARGS` passes `podSecurityContext.seccompProfile.type=RuntimeDefault`, `securityContext.privileged=true`, and `securityContext.readOnlyRootFilesystem=true` directly via chart values (`D-nqa-hardening`; state and audit logs already live on hostPath volumes). | Pod Running with 0 restarts, `"Quota applied successfully"` for every PV, history and cleanup loops started, and no read-only errors (orchestrator, measured 2026-11-19). Offline `helm template`, `nqa_check_skew`, and R371 pass; no clean install was run. |

### Round-5 workload hardening fixes (2026-11-19)

`platform-system/apisix` `KSV014` (`readOnlyRootFilesystem`) was resolved in source across both Phase 2 bootstrap (`scripts/cluster/08-1-networking.sh`) and GitOps (`gitops/charts/narwhal-apps/templates/apisix.yaml`):

| Workload | Change | Verification and pending scope |
| --- | --- | --- |
| `platform-system/apisix` (`08-1-networking.sh`, `apisix.yaml`) | `securityContext.readOnlyRootFilesystem: true` with `init-conf` (`apache/apisix:3.15.0-ubuntu`, matching `securityContext` and `10m/16Mi` req / `100m/64Mi` lim) running `cp -R /usr/local/apisix/conf/. /conf-rw/` (`cp -R` avoids `cp -a` timestamp `EPERM` on `/conf-rw/.`) plus `emptyDir` mounts at `/usr/local/apisix/conf` (listed before `/usr/local/apisix/conf/cert/narwhal`), `/usr/local/apisix/logs`, `/tmp`, and `/usr/local/apisix/{client_body,proxy,fastcgi,uwsgi,scgi}_temp` (`D-apisix-rofs`). | Throwaway pods built from the live APISIX Deployment spec became Ready and served portal/gitea/grafana routes over HTTP 9080 and TLS 9443 (30x from upstreams) (orchestrator, measured 2026-11-19). Offline `helm template` (`apisix-2.13.0.tgz`) and R212/R356/R375 pass. No clean install was run. **Rollout note:** upgrading restarts the APISIX data plane; since PR #736 `apisix-etcd` is persistent so routes survive, and the rollout runbook must still run `scripts/cluster/lib/recover-apisix-resource-sync.sh platform-system --divergence` afterwards. Live on the local single-node cluster (orchestrator, measured 2026-11-19): the serving APISIX Deployment rolled out with the read-only root, all six ingress hosts answered before and after, and the divergence check reported routes 26/26, upstreams 14/14, ssls 1/1. The `alloy-logs` collector now runs with no added capabilities (`capabilities.add: []`, D-alloy-caps; the key must stay present or the chart merges its 14 defaults back): logs from ten namespaces kept arriving in Loki, a new pod's lines were collected, no permission errors. |

### CSI NFS chart hardening (2026-11-26)

The bootstrap-only `csi-driver-nfs` 4.13.2 release is post-rendered by the `csi-nfs-postrender` Helm 4 plugin in `scripts/cluster/lib/helm-plugins/csi-nfs-postrender/`; its script fails closed if a named workload or sidecar is absent. Helm 4 requires `--post-renderer` to name an installed `postrenderer/v1` plugin. The bootstrap installs it idempotently from `/home/vagrant/scripts/cluster/lib/helm-plugins/csi-nfs-postrender` before upgrade, replacing an installation from a different source directory. The renderer changes only sidecar privilege escalation and the specified sidecar root filesystems. CPU limits are 100m (10x the chart's existing 10m CPU requests) for every container; memory limits and requests are unchanged. This bounds CPU while leaving scheduling requests intact; orchestrator live throttle measurement is still required. Existing clusters roll out by running the same `helm plugin install` step and `helm upgrade --install` block in `scripts/cluster/04-addons.sh` on `master-1` with repository scripts available at `/home/vagrant/scripts`; then verify both workloads become Ready and inspect Trivy plus CPU throttling. No GitOps Application owns this release. KSV125 remains actionable because registry trust requires a separate decision. R417 exercises the Helm plugin render using isolated Helm directories and rejects plugin metadata, wiring, context, and value mutations.

### Pod-security Batch C: node agents (2026-11-26)

The portal reasons use the 2026-11-26 process and host observations. Pre-merge trials exercised kube-vip on `narwhal-master-1` and MetalLB speaker on the live cluster. Istio CNI was verified by chart render only.

| Workload | FIXED (value, source) | ACCEPTED (exact checks, measured fact) | LEFT actionable (why) |
| --- | --- | --- | --- |
| `istio-system/istio-cni-node` | KSV030, KSV104: `cni.seccompProfile.type: RuntimeDefault` (`istio-cni.yaml`, `09-istio-ambient.sh`, R411) | KSV001: CapEff `0000000000283002` includes SYS_ADMIN. The chart exposes no `allowPrivilegeEscalation` value. | Live mesh enrollment and node-restart recovery checks pending. |
| `istio-system/ztunnel` | none (chart 1.30.1 has no seccomp key and hardcodes the container context) | KSV001: CapEff `0000000000203000` includes SYS_ADMIN. The chart hardcodes `allowPrivilegeEscalation: true`. | KSV030, KSV104: need a chart patch or post-render. Live Seccomp was 0. |
| `kube-system/Pod/kube-vip-narwhal-master-1` | KSV001, KSV003, KSV004, KSV030, KSV104: drop ALL, add only NET_ADMIN/NET_RAW, set `allowPrivilegeEscalation: false` and pod seccomp RuntimeDefault. KSV021: `runAsGroup: 10100`. Both static Pod generators are covered by R411. | KSV012, KSV020, KSV105: a non-root process gets no effective NET_ADMIN or NET_RAW without ambient capabilities. The trial measured UID 0 with only NET_ADMIN and NET_RAW effective. | KSV105 remains in the scan and is accepted for this exact Pod. |
| `platform-system/metallb-speaker` | KSV021: `speaker.securityContext.runAsGroup: 10100` with explicit `runAsUser: 0` in GitOps and bootstrap. Chart 0.16.1 renders both at Pod level. | KSV009, KSV012, KSV020, KSV022: hostNetwork true, default L2Advertisement, UID 0, CapEff `0000000000002000` (NET_RAW only), NoNewPrivs 1. KSV105: the speaker needs UID 0 for NET_RAW and ARP, and containerd failed to create the sandbox when the Pod set only the group. | KSV105 remains in the scan and is accepted for this exact DaemonSet. |
| `kube-system/cilium` | none | KSV024 (hostPorts 9879/4244 equal containerPort under hostNetwork) | KSV003, KSV004: flagged on the privileged `mount-bpf-fs` init container only. See the bpffs follow-up. KSV025: see follow-up. KSV001, KSV012, KSV020, KSV021: no proven need for the hardcoded `config` and `install-cni-binaries` init contexts. KSV030, KSV104: the chart sets `Unconfined` and RuntimeDefault needs a trial. |
| `kube-system/cilium-envoy` | none | KSV001: CapEff `0000000000201000` includes SYS_ADMIN. The chart exposes no `allowPrivilegeEscalation` value. KSV012, KSV020: UID 0 holds the measured capabilities. KSV024: hostPort 9964 equals containerPort. | KSV021, KSV025: see follow-up. KSV030, KSV104: `envoy.podSecurityContext.seccompProfile` renders and its syscall requirements need a trial. |
| `kube-system/csi-nfs-node` | `allowPrivilegeEscalation: false` for sidecars; read-only rootfs for registrar; CPU limits 100m for all three containers (R417) | KSV012, KSV020: existing measured node identity/capability constraints. KSV001: after the sidecar fix only the `nfs` plugin fails it; it requires privileged mode and SYS_ADMIN to mount volumes. | KSV021: UID 0 containers keep access to the root-owned socket through owner permissions, so a non-root GID is not ruled out; needs a post-render GID trial. KSV125: registry trust is a separate decision. |
| `chaos-testing/chaos-daemon`, `nfs-quota-agent/nfs-quota-agent` | none | KSV004: privileged, CapEff `000001ffffffffff` | none |
| `storage/node-agent` | KSV003, KSV004, KSV106: `capabilities.drop: [ALL]` in GitOps and bootstrap (`D-NODE-AGENT-DROP-ALL`, R371); a kopia fs-backup and restore of a uid 1234 0600/0700 emptyDir completed (2026-11-26); the node-agent container ran with CapEff `0000000000000000`; the kopia data copy runs in a separate per-volume data-path Pod (UID 0, `privileged: false`, chart-default capabilities, not changed by this setting). NFS-backed PVC backups under this setting are not yet verified | KSV001, KSV012, KSV020, KSV021, KSV023, KSV105: UID 0 and `/var/lib/kubelet` hostPath remain | none |
| `security-system/falco` | KSV001, KSV003, KSV004, KSV017, KSV030, KSV104: `driver.modernEbpf.leastPrivileged: true`, `podSecurityContext.seccompProfile.type: RuntimeDefault`, `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: [ALL]`, `capabilities.add: [BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE]` (`falco.yaml`, `D-FALCO-LEAST-PRIV`, R371/R421, verified by offline `helm template` and `trivy config` against chart 9.2.1 and live on the single-node cluster 2026-11-27 (`CapEff 000000c001080000`, events keep k8s metadata)) | KSV002, KSV012, KSV020, KSV021, KSV022, KSV023, KSV106, KSV121: chart 9.2.1 `pod-template.tpl` renders `container.apparmor.security.beta.kubernetes.io/falco: unconfined` when `leastPrivileged: true` so containerd AppArmor does not block `/host/proc` `ptrace` reads for container and Kubernetes metadata (KSV002) and requires the four kernel tracing capabilities (KSV022, KSV106), UID/GID 0 (KSV012, KSV020, KSV021), and hostPath mounts (KSV023, KSV121) | none (live trial 2026-11-27 passed; see the Batch C row above) |

| Actionable follow-up | Candidate and verification |
| --- | --- |
| `kube-system/cilium` KSV003, KSV004 | Candidate fix is `bpf.autoMount.enabled=false` with bpffs mounted by provisioning (chart 1.19.4). This needs the Cilium upgrade path. Verify the host mount before agent startup and confirm datapath recovery. |
| `kube-system/cilium` and `kube-system/cilium-envoy` KSV025 | candidate fix: `securityContext.seLinuxOptions=null` and `envoy.securityContext.seLinuxOptions=null` (rendered by the reviewer). Cilium is installed by `scripts/cluster/03-cni-install.sh` which skips existing installs, so it needs an upgrade path. |
| `kube-system/cilium-envoy` KSV021 | candidate fix: `envoy.podSecurityContext.runAsGroup` above 10000 (rendered by the reviewer). Apply through the same Cilium upgrade path and verify the datapath. |
| `kube-system/csi-nfs-node` KSV021 | Chart 4.13.2 ignores `node.securityContext`. Measure the liveness socket permissions and test a chart change or post-render with node volume registration, mount and probe checks. |

#### Trials on 2026-11-26

- **kube-vip:** On `narwhal-master-1`, the process ran with UID 0, GID 10100, CapEff `0x3000` (NET_ADMIN and NET_RAW only), NoNewPrivs 1, and Seccomp 2. The VIP address was present, its `/readyz` returned HTTP 200, and the `plndr-cp-lock` Lease renewed.
- **MetalLB speaker:** The first trial set only `runAsGroup: 10100`. Containerd failed to create the sandbox with `user group "10100" is specified without user`. After recovery, a second trial explicitly set `runAsUser: 0` alongside `runAsGroup: 10100`. The speaker was Ready, the LoadBalancer IP was retained, and `test-sso` recorded 144 passes and 0 failures.
- **Istio CNI:** Chart 1.30.1 rendered RuntimeDefault for the unprivileged `install-cni` container. Live verification still needs an ArgoCD sync of `devtools/istio-cni`, a mesh enrollment check, and a node-restart recovery check.

#### Apply kube-vip to existing nodes

Apply on one control-plane node at a time. On a multi-node control plane, start with a node that does not currently hold the Lease.

1. Back up the existing manifest outside kubelet's watched directory. Preserve this original if you retry.

   ```bash
   sudo install -d -m 0700 /var/lib/narwhal/kube-vip-trial
   sudo cp -an /etc/kubernetes/manifests/kube-vip.yaml /var/lib/narwhal/kube-vip-trial/kube-vip.yaml.orig
   sudo cp -a /etc/kubernetes/manifests/kube-vip.yaml /etc/kubernetes/.kube-vip.yaml.new
   ```

2. Edit `/etc/kubernetes/.kube-vip.yaml.new`. Match the bootstrap manifests in `02-init-cluster.sh` and `02-join-control-plane.sh`. Under `spec.securityContext` set `seccompProfile.type: RuntimeDefault`. Under the `kube-vip` container's `securityContext` set `allowPrivilegeEscalation: false`, `runAsUser: 0`, `runAsGroup: 10100`, `capabilities.drop: [ALL]`, and `capabilities.add: [NET_ADMIN, NET_RAW]`. Review the diff, then run `sudo mv /etc/kubernetes/.kube-vip.yaml.new /etc/kubernetes/manifests/kube-vip.yaml`.
3. Wait for kubelet to recreate the container. Use `sudo crictl ps --name kube-vip` and `sudo crictl inspect <container-id>` to find the new PID. In `/proc/<pid>/status`, verify UID 0, GID 10100, CapEff `0000000000003000`, NoNewPrivs 1, and Seccomp 2. With `VIP` set to the API virtual IP, run `sudo kubectl --kubeconfig /etc/kubernetes/admin.conf --server="https://${VIP}:6443" -v=6 get --raw /readyz` and verify HTTP 200. Run `sudo kubectl --kubeconfig /etc/kubernetes/admin.conf -n kube-system get lease plndr-cp-lock -o custom-columns=HOLDER:.spec.holderIdentity,RENEWED:.spec.renewTime -w` and verify that `spec.renewTime` advances before proceeding to another node.
4. If a check fails, copy the backup back through the staging file, then repeat the VIP and Lease checks.

   ```bash
   sudo cp -a /var/lib/narwhal/kube-vip-trial/kube-vip.yaml.orig /etc/kubernetes/.kube-vip.yaml.new
   sudo mv /etc/kubernetes/.kube-vip.yaml.new /etc/kubernetes/manifests/kube-vip.yaml
   ```

### Cilium family & CoreDNS hardening (2026-11-28, `D-CILIUM-BPFFS-SELINUX`, `D-CILIUM-HARDENING`, R425)

- **`scripts/common/01-prerequisites.sh` + `scripts/cluster/03-cni-install.sh` (`cilium 1.19.4`):**
  - `01-prerequisites.sh` verifies `findmnt -rn -t bpf -T /sys/fs/bpf` (systemd mounts `/sys/fs/bpf` itself as an API file system and refuses a `sys-fs-bpf.mount` unit for it, measured on systemd 259). `03-cni-install.sh` verifies host `bpffs` fail-closed before setting `bpf.autoMount.enabled=false`, removing the privileged `mount-bpf-fs` initContainer (`KSV003`, `KSV004`, `KSV017`). Because upstream `templates/cilium-envoy/daemonset.yaml` gates `bpf-maps` on `bpf.autoMount.enabled`, `03-cni-install.sh` also passes `envoy.extraHostPathMounts[0]` (`/sys/fs/bpf`, `HostToContainer`, `DirectoryOrCreate`).
  - `DaemonSet/cilium` sets `podSecurityContext.seccompProfile.type=RuntimeDefault` (`KSV030`, `KSV104`) and `securityContext.seLinuxOptions=null` (`KSV025`). `KSV001`, `KSV012`, `KSV020`, `KSV021` are accepted because chart `1.19.4` templates omit `allowPrivilegeEscalation`/`runAsNonRoot`/`runAsUser`/`runAsGroup` on `cilium-agent` and all 5 initContainers and UID/GID 0 is required for cgroup/sysctl `nsenter`, `bpffs`, `/etc/cni/net.d`, and retained capabilities.
  - `DaemonSet/cilium-envoy` sets `envoy.podSecurityContext.seccompProfile.type=RuntimeDefault` (`KSV030`, `KSV104`), `envoy.podSecurityContext.runAsUser=0` + `runAsGroup=10100` (`KSV021` fixed; explicit `runAsUser: 0` triggers `KSV105` which is accepted because containerd rejects `runAsGroup` without `runAsUser`), and `envoy.securityContext.seLinuxOptions=null` (`KSV025`).
  - `Deployment/cilium-operator` sets `operator.podSecurityContext.runAsNonRoot=true`, `runAsUser=10100`, `runAsGroup=10100` (`KSV012`, `KSV020`, `KSV021` fixed); `KSV009` (`hostNetwork: true`) and `KSV024` (`hostPort: 9234`) are accepted for `cluster-pool` IPAM PodCIDR allocation before `cilium-agent` assigns Pod IPs.
  - `Deployment/hubble-ui` sets `hubble.ui.securityContext.runAsNonRoot=true` (`KSV012`), `seccompProfile.type=RuntimeDefault` (`KSV030`, `KSV104`), and `capabilities.drop: [ALL]` on `frontend` and `backend` (`KSV003`, `KSV004`); `KSV020`/`KSV021` remain accepted for chart `1.19.4`'s `1001:1001` (`nginx-unprivileged` + backend).
  - Existing clusters converge via `scripts/ops/reconcile-cilium-hardening.sh --dry-run` / `--apply` (`D-CILIUM-RECONCILE-BASE`: reads live user values via `helm get values cilium -n kube-system -o yaml`, deep-merges only the hardening overlay with `yq` v4 so existing settings such as `routingMode` and `tunnelProtocol` survive without `--reuse-values`, prints a unified diff plus `helm upgrade --dry-run=server` in dry-run, waits for all four workload rollouts on `--apply`, and prints the exact `helm rollback` command on failure without auto-rolling back; requires live orchestrator trial for `RuntimeDefault` seccomp on `cilium`/`cilium-envoy` and `10100:10100` on `cilium-operator`).
- **`kube-system/ReplicaSet/coredns` (kubeadm `1.35.7` addon):** accepted (`KSV012`, `KSV020`, `KSV021`, `KSV022`, `KSV030`, `KSV104`). `kubeadm` (`02-init-cluster.sh`) creates `Deployment/coredns` and overwrites it on `kubeadm upgrade`; `v1beta4` `ClusterConfiguration.dns` exposes only `imageMeta` and `disabled`, and the repo has no `kubeadm upgrade` wrapper (`10-dnsmasq.sh` manages only `ConfigMap/coredns`). `KSV022` (`NET_BIND_SERVICE`) is required for port 53 while `coredns:v1.13.1` runs as `USER 65532:65532` with `setcap cap_net_bind_service=+p`.

The revised acceptances take effect with a portal release.

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
`--enable-admission-plugins=NodeRestriction,EventRateLimit` (EventRateLimit since 2026-11-27, CIS 1.2.10). `protectKernelDefaults` (CIS 4.2.6)
and `tlsCipherSuites` (CIS 4.2.13) remain open. CIS 4.2.12 uses
`serverTLSBootstrap` in the post-CNI kubelet configuration. The first kubelet
uses its fallback certificate for initial startup. Bootstrap verifies the signed
serving certificate before releasing worker and control-plane join commands,
which consume the cluster-published kubelet and control-plane configuration.

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
# workload's own non-root uid (for example SeaweedFS platform UID/GID 10100:10100) are CORRECT and must NOT be
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

### ApplicationSet controller Secret and ConfigMap reads (CIS 5.1.2/KSV041)

`13-argocd.sh` filters the cluster-wide core Secret/ConfigMap `get/list/watch` rule
from the bundled `argocd-applicationset-controller` ClusterRole before server-side apply.
Bootstrap reruns submit the shortened RBAC rules list and replace the existing list.
The same-named Role applied in `devtools` still grants all three verbs there.
GitOps manages Argo CD settings and the server ClusterRole, and bootstrap owns
the ApplicationSet controller ClusterRole.

To trial only the ClusterRole on an existing cluster, stage the updated script on
master-1 and run:

```bash
sudo -u vagrant env KUBECONFIG=/home/vagrant/.kube/config-local bash -o pipefail -c '
  bash /home/vagrant/scripts/cluster/13-argocd.sh --render-install /home/vagrant/manifests/argocd-install.yaml |
    yq -e "$1" |
    kubectl apply --server-side --force-conflicts -f -
' _ 'select(.kind == "ClusterRole" and .metadata.name == "argocd-applicationset-controller")'
```

Compare the live ClusterRole with the baseline and check the controller service
account with `kubectl auth can-i` for each verb on Secrets and ConfigMaps. Expect
allow in `devtools` and deny in another namespace and across all namespaces.
Check controller readiness and logs, reconcile a local Cluster generator and
any SCM/PR generators in use, confirm ordinary Application sync, and check
KSV041 on the current ClusterRole UID after Trivy rescans. R409 provides offline
render evidence. Pre-merge live trial (2026-11-26): the rendered ClusterRole applied server-side; the controller stayed 1/1 Running with 0 restarts and no forbidden logs; `auth can-i list secrets -A` no, `-n devtools` yes. Re-run R409 with `--manifest` against the staged `argocd-install.yaml` on every `ARGOCD_VERSION` bump, because the filter keeps the rule unchanged if upstream changes its shape.

### ArgoCD privilege split — Stage 1 (`D-OWN-ARGO-STAGED`, 2026-11-27)

Stage 1 resolves the `argocd-server` ClusterRole findings (`KSV041`, `KSV046`, `KSV048`) and bounds the `AppProject/platform` cluster-scoped resource boundary while keeping the `argocd-application-controller` ClusterRole explicitly accepted until Stage 2 (design option C) decouples privileged ClusterRole reconciliation:

- **`ClusterRole/argocd-server` (`D-ARGO-SERVER-RO`, R420):** `gitops/charts/narwhal-apps/templates/argocd-server-rbac.yaml` (single source applied directly by `scripts/cluster/13-argocd.sh` and reconciled by `idp-apps`) removes cluster-wide `secrets` (`KSV041`), `apps/*` `delete`/`patch`, `pods` `delete`, and `batch/jobs` `create` (`KSV048`), and replaces all `resources: ["*"]` CRD-group rules (`KSV046`) with explicit plural resource names derived from repository live-API exports (`velero-server-rbac.yaml`, `narwhal-portal-k8s.yaml`, `chaos-mesh-rbac.yaml`, and `kong` CRDs; every name was checked against live `kubectl api-resources` on 2026-11-27; one nonexistent name was removed). Read-only non-secret Kubernetes discovery, `pods/log` `get`, and `argoproj.io` (`applications`, `applications/finalizers`, `applicationsets`, `appprojects` read; `applications`, `applications/finalizers`, `applicationsets` `delete`/`patch`) remain intact. Portal API callers (`portal/src/lib/argocd.ts`, `scripts/cluster/13-2-narwhal-portal-bindings.sh`, `scripts/test/test-sso.sh`) use only `/api/v1/applications` GET, `/sync`, `/rollback`, `/api/v1/session`, `/api/v1/account`, and `/api/v1/version` (no workload delete/patch or Job creation). The `ClusterRole/argocd-server` acceptance rule is removed so any regression stays actionable.
- **`AppProject/platform` (`D-ARGO-PROJECT-WHITELIST`, R420):** `gitops/resources/argocd-projects.yaml` replaces `clusterResourceWhitelist: */*` with the exact 12 cluster-scoped kinds reconciled by platform Applications (`Namespace`, `MutatingWebhookConfiguration`, `ValidatingWebhookConfiguration`, `CustomResourceDefinition`, `ClusterComplianceReport`, `ClusterIssuer`, `CiliumClusterwideNetworkPolicy`, `ClusterPolicy`, `IngressClass`, `ClusterRole`, `ClusterRoleBinding`, `PriorityClass`). `StorageClass` and `CSIDriver` are omitted because `nfs-csi` and `nfs.csi.k8s.io` are bootstrap-owned (`scripts/cluster/04-addons.sh`). `AppProject/tenants` keeps `clusterResourceWhitelist: []`.
- **`ClusterRole/argocd-application-controller` (`D-OWN-ARGO-STAGED`):** exact RBAC acceptance for `KSV044` and `KSV046` is recorded in `portal/src/data/compliance-acceptance-rules.json` (and the table below) because the controller reconciles `ClusterRole` and `ClusterRoleBinding` objects across 14 Helm charts and platform roles, and Kubernetes RBAC forbids creating a role with permissions the creator lacks without `escalate` (`KSV050`). Revisit with design option C (bootstrap-decoupled RBAC + bounded controller + per-namespace Roles). Verified live on the single-node cluster 2026-11-27: argocd-server lost cluster-wide Secret reads and workload writes (kubectl auth can-i), all 56 Applications stayed Synced/Healthy with no `not permitted in project`, test-sso 144/0. Trivy rescan pending.

## Risk-accepted exceptions (do NOT "fix")

### Lane D: operator Secret and wildcard RBAC findings (2026-11-28)

D-RBAC-LANE-D: six exact role/check acceptances below record measured live rules with documented platform functions. Two actionable roles (Cilium operator and Alloy operator) remain findings. The two Keycloak controller findings were fixed in source by removing their Secret rules from the ClusterRoles and granting Secret operations only in `iam` (D-KC-RBAC-NARROW, R427); live reconciliation and Trivy confirmation remain pending. R424 pins the six exact targets and check sets. The technical reasons are role rules and repo resource placement, not a name or namespace pattern.

### Keycloak Operator Secret RBAC (2026-11-28)

`keycloak-k8s-resources` v26.5.7 `kubernetes.yml` sets both controller namespace selectors to `JOSDK_WATCH_CURRENT`. The main controller already has a namespaced `keycloak-operator-role` containing Secret operations. `scripts/cluster/keycloak-operator-rbac.yaml`, applied after the upstream manifest by `11-keycloak.sh`, preserves both ClusterRoles' non-Secret rules, omits their Secret rules, and adds an `iam` Role/RoleBinding for the RealmImport controller's Secret operations. The Operator RBAC is bootstrap-owned; no GitOps Application owns these resources. D-KC-RBAC-NARROW cost: reconciliation outside `iam` is unsupported, matching the pinned watch scope. Escape hatch: restore upstream Secret ClusterRole rules only if a measured need requires watching additional namespaces. R427 rejects mutations that re-add cluster-wide Secret rules. Live Keycloak and RealmImport reconciliation, Secret creation, operator logs, and Trivy KSV041 confirmation are pending.

These failing controls are expected for this IDP platform. Changing them breaks
functionality or reverses an intentional trade-off.

1. **`--bind-address` = 0.0.0.0 on controller-manager / scheduler** (CIS 1.3.7, 1.4.2 —
   1.4.2 is CRITICAL). Deliberately set so Prometheus (off the control-plane node) can
   scrape `/metrics`. Secure ports 10257/10259 use kubeadm authentication/authorization
   kubeconfigs (recorded scheduler command in `cis-record-live-r2.json`).
   Binding all interfaces still exposes the listener; this acceptance does not claim
   node firewall isolation. Reverting to 127.0.0.1 blinds monitoring.
   Owner: `scripts/cluster/02-init-cluster.sh` controllerManager/scheduler extraArgs.
2. **Privileged / hostPath / hostNetwork / NET_RAW / root workloads** (CIS 5.2.2/5.2.3/
   5.2.5/5.2.6/5.2.7/5.2.8/5.2.10/5.2.12/5.2.13, 5.7.2/5.7.3; NSA 1.0/1.1/1.2/1.4/1.5/
   1.7/1.9/1.10; PSS baseline/restricted 3/4/5). The large counts are dominated by
   **platform components that require these privileges to function**: Cilium (NET_ADMIN,
   privileged, hostNetwork), Istio ambient (ztunnel/istio-cni), trivy node-collector
   (hostPath to read node files), CSI/NFS, kube-system system pods. These cannot be
   hardened without breaking CNI / mesh / storage / scanning.
3. **`kube-system` used by platform** (NSA 1.12) — the platform legitimately runs there.
4. **Broad RBAC / secret access / wildcards** (CIS 5.1.2, 5.1.3, 5.1.6) — some
   permissions remain required by operators (ArgoCD, cert-manager, etc.); Keycloak
   Operator Secret access is now limited to `iam` as documented above.

### Monitoring sidecar and kube-state-metrics Secret reads (CIS 5.1.2/KSV041, R418)

`loki-clusterrole`, `prometheus-stack-grafana-clusterrole` and
`prometheus-stack-kube-state-metrics` no longer grant cluster-wide Secret reads.
No Secret in the repository carries the `loki_rule`, `grafana_dashboard` or
`grafana_datasource` sidecar labels, and nothing queries `kube_secret_*` series.
The Loki rules sidecar and the Grafana dashboard and datasource sidecars watch
ConfigMaps only (`D-LOKI-RBAC`, `D-GRAFANA-RBAC`). Both charts hardcode
`configmaps` plus `secrets` in their ClusterRole while a sidecar is enabled, so
the release replaces it with a same-named ConfigMap-only ClusterRole
(`rbac.useExistingRole` / `rbac.useExistingClusterRole` plus `extraObjects`).
kube-state-metrics keeps its default collectors except `secrets` (`D-KSM-RBAC`).
GitOps (`loki.yaml`, `prometheus-stack.yaml`) and bootstrap (`08-2-monitoring.sh`)
render the same rules; R418 pins them. A Secret-backed rule, dashboard or
datasource is no longer picked up; use a ConfigMap.

Live on the single-node cluster (2026-11-26): both Applications Synced/Healthy,
the three ClusterRoles have no Secret rule, the Loki, Grafana and
kube-state-metrics pods run without restarts or `forbidden` log lines,
`up{job="kube-state-metrics"}` is 1, and all 31 labelled dashboard ConfigMaps
load in Grafana.

### Adding or updating allowlist entries

Every exception in `portal/src/data/compliance-acceptance-rules.json` (loaded and validated by `portal/src/lib/compliance-acceptance.ts` / `portal/src/lib/compliance-accepted.ts`) is fail-closed by exact `(scope, namespace, kind, name, checkID)` with required `id`, `reason`, `owner`, and `reviewedAt` metadata — no namespace-blanket (`SYSTEM_NAMESPACES`), substring (`UPSTREAM_RBAC_ROLE_PATTERNS`), prefix (`system:`/`kubeadm:`), or check-less role acceptance exists. Any unlisted check, unlisted workload/role, or unknown/mismatched kind remains actionable, unused rules are surfaced in `ComplianceSummary` and the Compliance UI, and report rows are marked accepted only when every failing check is present in `checks[]`, accounted for by the per-severity `summary` totals, and accepted. The allowlist currently holds **56 Config Audit rules across 54 workloads**, **52 RBAC rules**, and **4 Infra rules**.

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
  - **Narrowed positive state (Config Audit & RBAC only; Infra always not evaluated without failure; CIS `kube-bench` integration):** The positive state `"passed"` — labeled in UI as **"실패 기록 없음 (스캐너는 실패만 기록)" / "No failure recorded (scanner records failures only)"**, and never "모든 검사 통과 / All checks passed" on any empty failure list (Infra, Config Audit, or RBAC) — applies **only** to `configAudit` and `rbac` families when, for EVERY check ID in its spec: (a) the ID exists in the catalogue (unknown IDs like `AVD-KSV-9999`, removed IDs like `AVD-KSV-0029`, and skipped optional IDs like `AVD-KSV-0004`/`0039`/`0040` become `not_in_catalogue`), (b) operator scope (`OPERATOR_EXCLUDE_NAMESPACES`, `OPERATOR_TARGET_NAMESPACES`, `trivy.supportedConfigAuditKinds`) includes at least one applicable kind/namespace, (c) at least one in-scope report of the right family and kind exists **in the namespaces the check applies to** (`requiredNamespaces` and `scopeNamespaces`, e.g. `KSV110` / CIS `5.7.4` requires a `Workload` `ConfigAuditReport` in `default`, not `dev` or a `default` `Service` report; else `no_report_in_scope_namespace`, `no_report_for_family`, or `excluded_namespace`), and (d) all auxiliary reads succeeded (else `coverage_unknown`). For the **`infra` family (`AVD-KCV-*`, node-collector and control-plane component checks)**, Trivy reports cannot prove a check was evaluated (collector commands that return nothing and control-plane components without their own report leave no trace). Therefore Trivy infra checks **never** receive the positive state from report existence: they are `failed` when a failing record exists (in any family/alias), otherwise `not_evaluated` with reason `infra_no_per_check_evidence` ("노드/컨트롤플레인 검사: 스캐너가 항목별 평가 증거를 남기지 않음" / "Node/control-plane check: the scanner leaves no per-check evidence"). **Consequence:** most CIS node-level and control-plane component controls show as not evaluated by the Trivy scanner on `/compliance` when no benchmark test record exists; on-node verification is performed by `scripts/test/verify-cluster.sh` and the daily `kube-bench` CronJob (`security-system` ConfigMaps labelled `narwhal.io/compliance-tool=kube-bench`, key `record.json`). When fresh (<= 26h), complete, schema-validated `kube-bench` records cover the expected cluster nodes (all control-plane nodes for sections 1–3, all nodes for section 4, cluster-once for section 5), `k8s-cis-1.23` merges them under failure-first cross-source precedence (`PASS` only when Trivy records no failure and every expected node passes; `WARN`/manual and `INFO` remain `not_evaluated`; UI counts `tested (benchmark)` separately from `no failure recorded` and names both verdicts when tools disagree). Node-type gating (`missing_node_type`) remains driven by catalogue `nodeTypes` and explicit spec/command `nodeType` data, never inferred from CIS section numbers (`4.1.5` / `KCV0073` applies to `["master", "worker"]`).
- **Cluster-scoped Config Audit & independent KISA CFG-01 (`D-CONFIG-AUDIT-CLUSTER-SCOPE`, `D-CFG01-ALLSETTLED`):** `fetchConfigAuditListWithMeta` and `getConfigAuditDetail` fetch `ClusterConfigAuditReport`s alongside namespaced `ConfigAuditReport`s (either list failing throws `ComplianceDataUnavailableError`, never an empty success), resolve hashed resource names via `ownerReferences`/annotations, render cluster-scoped rows with a visible `Cluster` / `클러스터` scope (and `_cluster` detail route), and keep `configAuditMatcher` fail-closed so cluster findings (e.g. `ClusterRoleBinding` `KSV111`/`KSV122`) appear in the Config Audit list, counters, summary totals, and KISA `CFG-01`. `checkCfg01` evaluates all four sources (`frameworks`, `configAudit`, `rbac`, `infra`) with `Promise.allSettled`: any known actionable failure returns `fail` (naming both failing and unreadable sources), while unreadable sources with zero known failures return `warn` with the failed source list (never reasonless `manual` and never cached).
- **Report family & aliases (`D-CHECK-ALIASES`, `D-FAILURE-FIRST-JOIN`):** The catalogue assigns every embedded check ID its v0.25 report family (`AVD-KCV-*` -> `infra`, `Role`/`ClusterRole` -> `rbac`, workload / `NetworkPolicy` / `RoleBinding` / `ClusterRoleBinding` / `Service` / `ConfigMap` -> `configAudit`) and upstream `aliases`/`legacyId` list (`KCV0012` -> `["KSV0012"]`, `KCV0035` -> `["KCV0135"]`, `KSV124` -> `["KSV-0124"]`, `KSV123` -> `["KSV0123"]`, `KSV1011` -> `["KSV01011"]`). Failure joins span all families (including `ClusterConfigAuditReport`) and aliases before non-failing classification.
- **Old cache compatibility:** Framework list/detail and compliance summary use `compliance:v4:*` keys (KISA uses `compliance:v4:kisa:list`, Config Audit/RBAC use `compliance:v3:*`). Audit rows carry exact rule metadata. Missing evaluation fields in any old object mean unknown coverage: zero verified passes, remaining non-failed controls unevaluated, no raw-pass fallback in UI/SSR/CFG-01. Existing failed controls remain visible.
- **Trimmed snapshot replay (round 5, offline, failed-only reports):** 11 controls: raw pass 7/11 (63.6%), no failure recorded 1/11 (9.0%), exception-adjusted 4/11 (36.3%), not evaluated 6 (`infra_no_per_check_evidence: 2`, `excluded_namespace: 1`, `manual: 1`, `unsupported_kind: 1`, `unlinked_check_id: 1`), actionable failed 1, accepted failed 3. CIS has 8 controls (0 no failure recorded, 5 not evaluated [`1.1.1`, `4.1.5` as `infra_no_per_check_evidence`; `1.2.1`, `3.1.1`, `5.1.1`], 1 actionable [`1.1.9`], 2 accepted [`5.2.2`, `5.7.3`]); restricted PSS has 3 (1 no failure recorded [`1`], 1 not evaluated [`13`], 1 accepted [`3`]). The underlying reports expose two dropped checks on the superseded RS, versus one recorded in the CCR. The fixture's 178-control metadata is a historical full-snapshot reference, not a new live measurement.
- **Pooled pass rates without false `100%` (`formatPassRatePct` in `portal/src/types/compliance.ts`):** Summary cards compute pooled ratios (sum of numerators / sum of denominators, e.g. `145/178` raw) with explicit `numerator/denominator` labels across summary, grid, and detail views. Every `formatPassRatePct` call (evaluated, raw, and exception-adjusted) passes `hasNotEvaluated` alongside `hasActionableFail`, flooring to 1 decimal place and capping at `99.9%` whenever any control is unevaluated or actionable (`100%` appears only when every control is evaluated and zero actionable failures remain). KISA `CFG-01` (`portal/src/lib/kisa.ts`) fails on any actionable framework control or unaccepted config-audit/RBAC/infra finding (including LOW), warns with count on any not-evaluated control (or unknown coverage), and passes only when all controls are evaluated with zero actionable failures.
- **LOW visibility & superseded-ReplicaSet reconciliation:** `totalConfigAuditFailures` continues to count unaccepted `MEDIUM+` findings in non-system namespaces, `unacceptedLowConfigAuditFailures` surfaces unaccepted non-system `LOW` findings separately from accepted hygiene `lowSeverityConfigAuditFailures`, and `droppedConfigAuditReports` / `droppedConfigAuditFailures` / `droppedFailCount` / `droppedCheckCount` keep drained superseded-ReplicaSet drops visible next to raw and active numbers.
- **Data unavailability, partial metadata & freshness (`ComplianceDataUnavailableError`):** Kubernetes HTTP/network list errors throw `ComplianceDataUnavailableError` (`503 { unavailable: true, source }` on API routes) instead of caching empty arrays or displaying `0`. `getComplianceSummary()` isolates primary failures per source (`ComplianceSummary.sources` + `dataUnavailable`), returns `503 { unavailable: true }` from `GET /api/compliance/summary` when all four primary sources fail, surfaces auxiliary read failures via `partial` / `partialSources` with a visible UI notice naming the failed sources (never caching partial results), prefers `status.updateTimestamp` on `ClusterComplianceReport`s, and displays per-source ages alongside the 6-hour (`0 */6 * * *`) scheduled rebuild cadence note.

Adding or expanding an entry requires:

1. Verifying that no safe manifest/chart knob fixes the finding without breaking functionality (`accept`) or that the upstream chart/image lacks the knob (`upstream`).
2. Recording a concise justification in `portal/src/lib/i18n.ts` (`ko`/`en` keys), `portal/src/data/compliance-acceptance-rules.json`, and the English tables below (enforced by `portal/src/lib/compliance-accepted.test.ts`).
3. Passing independent PR review before merging.

Matching is exact: `database/Pod/narwhal-db-1` and the Deployment-owned `database/ReplicaSet/narwhal-db-pooler-rw` are accepted for `KSV020` and `KSV021` under D-OWN-POD, which supersedes D-P2-2 for these targets. D-LANE-B-R2-CNPG: KSV036 is a separate exact rule because it has a distinct technical cause and version boundary; keeping KSV020/KSV021 at the 2026-11-26 review preserves their pinned UID/data-ownership semantics. Cost: one extra exact rule. Escape hatch: remove the KSV036 rule when the pinned CNPG version supports a safe token-mount replacement.

The observed pooler ReplicaSet `narwhal-db-pooler-rw-5b44f8765f` resolves to the exact Deployment name through the ownerReferences check above. The rule can carry over to subsequent revisions of that Deployment. Review or remove it if the operator changes its UID/GID behavior.

### Config Audit allowlist

| namespace | workload | checks | class | reason |
| --- | --- | --- | --- | --- |
| chaos-testing | DaemonSet/chaos-daemon | KSV001, KSV003, KSV004, KSV010, KSV012, KSV017, KSV020, KSV021, KSV022, KSV023, KSV106, KSV121 | accept | Requires privileged, hostPID, /sys and containerd.sock hostPath, root UID 0, and capabilities to inject pod netns and I/O faults Measured live 2026-11-26: privileged container with CapEff 000001ffffffffff (all capabilities), so a capabilities.drop list changes nothing (KSV004). |
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
| nfs-quota-agent | DaemonSet/nfs-quota-agent | KSV001, KSV003, KSV004, KSV012, KSV017, KSV020, KSV021, KSV023, KSV121 | accept | Requires privileged root container, capabilities, hostPath, and /dev access to set XFS/ext4 project quotas on the NFS export (allowPrivilegeEscalation: false, drop ALL, and UID > 10000 cannot be used) Measured live 2026-11-26: privileged container with CapEff 000001ffffffffff (all capabilities), so a capabilities.drop list changes nothing (KSV004). |
| platform-system | ReplicaSet/apisix | KSV020, KSV021 | accept | Uses upstream APISIX image fixed UID/GID 636 |
| security-system | DaemonSet/falco | KSV002, KSV012, KSV020, KSV021, KSV022, KSV023, KSV106, KSV121 | accept | Falco ran privileged modern_ebpf until 2026-11-26 because of the initialization permission errors recorded in falco.yaml D-falco-rofs. The chart’s driver.modernEbpf.leastPrivileged path was verified live on 2026-11-27 and is now used. Updated 2026-11-26 (D-FALCO-LEAST-PRIV): falco.yaml now sets driver.modernEbpf.leastPrivileged true, pod-level RuntimeDefault seccomp, allowPrivilegeEscalation false, readOnlyRootFilesystem true, and capabilities drop ALL with add [BPF, SYS_RESOURCE, PERFMON, SYS_PTRACE] (resolving KSV001, KSV003, KSV004, KSV017, KSV030, KSV104); chart 9.2.1 pod-template.tpl renders container.apparmor.security.beta.kubernetes.io/falco: unconfined so containerd AppArmor does not block /host/proc ptrace reads for container and Kubernetes metadata (KSV002) and requires the four kernel tracing capabilities (KSV022, KSV106) alongside UID/GID 0 (KSV012, KSV020, KSV021) and hostPath mounts (KSV023, KSV121). |
| security-system | ReplicaSet/falco-falcosidekick | KSV020, KSV021 | accept | Keeps upstream falco 9.2.1 / falcosidekick 0.14.1 default UID/GID 1234 |
| security-system | ReplicaSet/trivy-operator | KSV020, KSV021 | accept | Keeps upstream trivy-operator:0.36.1 default UID/GID 10000 |
| storage | DaemonSet/node-agent | KSV001, KSV012, KSV020, KSV021, KSV023, KSV105 | accept | Requires UID 0 and /var/lib/kubelet hostPath mounts for kopia fs-backup; measured backup and restore completed with all capabilities dropped |
| storage | CronJob/openbao-snapshot | KSV020, KSV021 | accept | openbao-backup.yaml keeps UID 100 for the snapshot writer and holder sharing the same PVC files |
| storage | ReplicaSet/openbao-snapshot-holder | KSV020, KSV021 | accept | openbao-backup.yaml keeps UID 100 for the snapshot writer and holder sharing the same PVC files |
| storage | ReplicaSet/seaweedfs-s3 | KSV011 | accept | Omits CPU limit to prevent S3 upload throttling (D-sw-qos) |
| storage | StatefulSet/seaweedfs-filer | KSV011 | accept | Omits CPU limit to prevent upload throttling (D-sw-qos) |
| storage | StatefulSet/seaweedfs-master | KSV011 | accept | Omits CPU limit to prevent upload throttling (D-sw-qos) |
| storage | StatefulSet/seaweedfs-volume | KSV011, KSV023 | accept | Runs SeaweedFS volume as 10100:10100 under D-OWN-POD and retains hostPath /ssd/object_store for the node-local data path of the S3-compatible Velero backup store (KSV023); omits CPU limit to prevent upload throttling (D-sw-qos, KSV011) |
| storage | ReplicaSet/velero | KSV020, KSV021 | accept | Runs as numeric cnb UID 1002 / GID 1000 for /home/cnb ownership in velero:v1.18.1 (runAsNonRoot: true enabled; UID/GID <= 10000) |
| storage | ReplicaSet/velero-ui | KSV020, KSV021 | accept | Uses upstream node image UID/GID 1000 |
| kube-system | DaemonSet/cilium | KSV001, KSV005, KSV009, KSV012, KSV020, KSV021, KSV022, KSV023, KSV024, KSV106, KSV119, KSV120, KSV121 | accept | Cilium configures the host eBPF datapath and network namespaces using host networking, BPF/CNI mounts and network/kernel capabilities. The hostPorts 9879 and 4244 equal their containerPorts with hostNetwork enabled (KSV024). Chart 1.19.4 templates omit allowPrivilegeEscalation, runAsNonRoot, runAsUser, and runAsGroup on cilium-agent and its 5 initContainers and values.schema.json exposes no UID/GID keys, while cgroup/sysctl nsenter, mode 0700 bpffs, root-owned /etc/cni/net.d writes, and retained root capabilities require UID/GID 0 (KSV001, KSV012, KSV020, KSV021). |
| kube-system | DaemonSet/cilium-envoy | KSV001, KSV005, KSV009, KSV012, KSV020, KSV022, KSV023, KSV024, KSV105, KSV106 | accept | Cilium Envoy uses host networking and BPF/socket mounts to integrate with the host datapath. On 2026-11-26 UID 0 held CapEff 0000000000201000 (NET_ADMIN, SYS_ADMIN) (KSV012, KSV020). The hostPort 9964 equals its containerPort with hostNetwork enabled (KSV024). The chart exposes no allowPrivilegeEscalation value, and the scanner flags the unset field (KSV001). Setting runAsGroup 10100 without runAsUser fails containerd sandbox creation, so explicit runAsUser 0 is retained for NET_ADMIN/SYS_ADMIN and /sys/fs/bpf access (KSV105). |
| kube-system | ReplicaSet/cilium-operator | KSV009, KSV024 | accept | Because 03-cni-install.sh uses cluster-pool IPAM, cilium-operator must allocate per-node PodCIDRs before cilium-agent can assign Pod IPs, so disabling hostNetwork deadlocks bootstrap (KSV009). Chart cilium-operator/deployment.yaml automatically sets hostPort 9234 equal to containerPort 9234 whenever operator.hostNetwork is true (KSV024). |
| kube-system | ReplicaSet/hubble-ui | KSV020, KSV021 | accept | Cilium chart 1.19.4 fixes runAsUser 1001, runAsGroup 1001, and fsGroup 1001 for the nginx-unprivileged hubble-ui frontend and backend images (KSV020, KSV021), while runAsNonRoot true, allowPrivilegeEscalation false, capabilities drop ALL, and RuntimeDefault seccomp are enforced. |
| kube-system | ReplicaSet/coredns | KSV012, KSV020, KSV021, KSV022, KSV030, KSV104 | accept | Kubeadm (02-init-cluster.sh) owns Deployment/coredns from its built-in addon template and overwrites it on kubeadm upgrade; kubeadm v1beta4 ClusterConfiguration.dns exposes only imageMeta and disabled with no upgrade-safe securityContext or seccompProfile fields and no repo upgrade wrapper exists (KSV012, KSV020, KSV021, KSV030, KSV104). Binding DNS port 53 (<1024) requires NET_BIND_SERVICE (KSV022), while registry.k8s.io/coredns/coredns:v1.13.1 builds with setcap cap_net_bind_service=+p and USER 65532:65532. |
| istio-system | DaemonSet/istio-cni-node | KSV001, KSV005, KSV012, KSV020, KSV021, KSV022, KSV023, KSV105, KSV106, KSV119, KSV121 | accept | Istio CNI installs host CNI binaries and configuration and enters Pod network namespaces. CapEff 0000000000283002 measured on 2026-11-26 includes SYS_ADMIN. The chart exposes no allowPrivilegeEscalation value, and the scanner flags the unset field (KSV001). |
| istio-system | DaemonSet/ztunnel | KSV001, KSV005, KSV012, KSV020, KSV021, KSV022, KSV023, KSV105, KSV106, KSV119 | accept | Ztunnel enters workload network namespaces through the /var/run/ztunnel CNI socket to intercept ambient traffic. CapEff 0000000000203000 measured on 2026-11-26 includes SYS_ADMIN. The chart adds SYS_ADMIN and hardcodes allowPrivilegeEscalation: true (KSV001). |
| kube-system | Pod/etcd-narwhal-master-1 | KSV001, KSV003, KSV004, KSV009, KSV012, KSV020, KSV021, KSV023, KSV024 | accept | etcd hardening patch prevented etcd from starting in the 2026-11-28 live trial; root cause under investigation. KSV001/KSV003/KSV004 remain accepted only for etcd-narwhal-master-1 while the cause is investigated. kubeadm 1.35.7 runs this static Pod as UID/GID 0; moving it off root requires alpha RootlessControlPlane (KSV012/KSV020/KSV021) |
| kube-system | Pod/kube-apiserver-narwhal-master-1 | KSV009, KSV012, KSV020, KSV021, KSV023, KSV024 | accept | Kubeadm control-plane static Pods use host networking before CNI and mount host PKI, kubeconfigs or etcd state. Kubeadm 1.35.7 runs all four as UID/GID 0; moving them off root requires alpha RootlessControlPlane (runAsGroup alone fails in containerd and explicit runAsUser 0 triggers KSV105). The three control-plane components have privilege escalation disabled and capabilities dropped (KSV012, KSV020, KSV021). |
| kube-system | Pod/kube-apiserver-narwhal-master-1 | KSV022 | accept | Measured 2026-11-28: getcap reports cap_net_bind_service=ep on the kube-apiserver binary; dropping that file capability breaks container exec with EPERM. After dropping all capabilities, only NET_BIND_SERVICE is added back; live CapEff measured 0x400. |
| kube-system | Pod/kube-controller-manager-narwhal-master-1 | KSV009, KSV012, KSV020, KSV021, KSV023, KSV024 | accept | Kubeadm control-plane static Pods use host networking before CNI and mount host PKI, kubeconfigs or etcd state. Kubeadm 1.35.7 runs all four as UID/GID 0; moving them off root requires alpha RootlessControlPlane (runAsGroup alone fails in containerd and explicit runAsUser 0 triggers KSV105). The three control-plane components have privilege escalation disabled and capabilities dropped (KSV012, KSV020, KSV021). |
| kube-system | Pod/kube-scheduler-narwhal-master-1 | KSV009, KSV012, KSV020, KSV021, KSV023, KSV024 | accept | Kubeadm control-plane static Pods use host networking before CNI and mount host PKI, kubeconfigs or etcd state. Kubeadm 1.35.7 runs all four as UID/GID 0; moving them off root requires alpha RootlessControlPlane (runAsGroup alone fails in containerd and explicit runAsUser 0 triggers KSV105). The three control-plane components have privilege escalation disabled and capabilities dropped (KSV012, KSV020, KSV021). |
| kube-system | Pod/kube-vip-narwhal-master-1 | KSV009, KSV012, KSV020, KSV022, KSV023, KSV105, KSV106, KSV119 | accept | Kube-vip binds the API virtual IP on the host interface and sends ARP using host networking, NET_ADMIN/NET_RAW and a mounted kubeconfig for leader election. Measured live 2026-11-26: UID 0 with CapEff 0000000000003000 (NET_ADMIN and NET_RAW only), NoNewPrivs 1, and Seccomp 2. A non-root process gets no effective NET_ADMIN or NET_RAW without ambient capabilities (KSV012, KSV020, KSV105). |
| kube-system | DaemonSet/csi-nfs-node | KSV001, KSV005, KSV009, KSV012, KSV017, KSV020, KSV022, KSV023, KSV037, KSV106 | accept | The CSI NFS node plugin uses a privileged NFS driver with SYS_ADMIN, kubelet plugin/Pod host paths and host networking to mount and register node volumes. On 2026-11-26 the registrar used root-owned plugins_registry (0750) to register its socket and dropped all capabilities, so it needs UID 0 (KSV012, KSV020). Only the privileged nfs container still fails KSV001. |
| kube-system | ReplicaSet/csi-nfs-controller | KSV001, KSV005, KSV009, KSV012, KSV017, KSV020, KSV022, KSV023, KSV037, KSV106 | accept | CSI NFS runs the NFS mount driver with privileged SYS_ADMIN and kubelet plugin/Pod host paths to mount and register node volumes; host networking supplies node mount connectivity. On 2026-11-26 the root NFS plugin created the shared CSI socket as root:root 0755, so non-root sidecars cannot connect (KSV012, KSV020); only the privileged nfs container still fails KSV001. |
| monitoring | DaemonSet/prometheus-stack-prometheus-node-exporter | KSV009, KSV010, KSV023, KSV121 | accept | Node exporter measures host network/process/kernel/filesystem metrics through hostNetwork, hostPID and read-only host proc/sys/root mounts. |
| database | Pod/narwhal-db-1 | KSV020, KSV021 | accept | CloudNativePG 1.29.1 runs the PostgreSQL instance as UID/GID 26; the live data directory is owned by 26:26, so changing IDs requires data migration (KSV020/KSV021). |
| database | Pod/narwhal-db-1 | KSV036 | upstream | The CNPG 1.29.1 instance manager calls the Kubernetes API using the Pod service-account token (official CNPG 1.29 security docs). The Cluster automountServiceAccountToken option and equivalent projected token were added in 1.31; this pinned version has no supported setting to disable the automatic mount while preserving those calls (KSV036). |
| database | ReplicaSet/narwhal-db-pooler-rw | KSV020, KSV021 | accept | The CloudNativePG operator sets UID 998 and GID 996 on the running Pooler pod even though the Pooler template leaves securityContext unset. |
| platform-system | DaemonSet/metallb-speaker | KSV009, KSV012, KSV020, KSV022, KSV105 | accept | MetalLB speaker answers layer 2 ARP/NDP on the host interface and runs memberlist (7946), so it needs host networking and NET_RAW. Measured live 2026-11-26: hostNetwork true, the default L2Advertisement in use, process UID 0, CapEff 0000000000002000 (NET_RAW only), NoNewPrivs 1; a non-root process would not hold NET_RAW effectively (KSV009, KSV012, KSV020, KSV022). The speaker needs image UID 0 for NET_RAW and ARP. A live trial with pod-level runAsGroup alone failed to create the containerd sandbox (user group 10100 is specified without user), so the Pod explicitly sets runAsUser 0 (KSV105). |
| security-system | CronJob/cis-bench-974178c0082e | KSV010, KSV012, KSV020, KSV021, KSV022, KSV023 | accept | Exact node CronJob cis-bench-974178c0082e (R423). render-cis-benchmark.py runs kube-bench against host CIS targets with hostPID and read-only mounts of kubelet, systemd, Kubernetes, CNI and (control-plane only) etcd paths, as root with all capabilities dropped except DAC_READ_SEARCH, RuntimeDefault seccomp and no privilege escalation. Non-root runs (UID 1000 or 10100, with or without DAC_READ_SEARCH, and runAsGroup 10100) made the scan fail in the 2026-11-28 live trials, so KSV010/KSV012/KSV020/KSV021/KSV022/KSV023 are accepted only for this exact CronJob. |
| chaos-testing | ReplicaSet/chaos-controller-manager | KSV001, KSV003, KSV004 | accept | Accept only the ReplicaSets of Deployment chaos-controller-manager (R423). The chaos-mesh 2.8.3 controller-manager template renders controllerManager.securityContext at Pod scope and exposes no container security-context values. Container allowPrivilegeEscalation and capabilities.drop/privileged findings therefore cannot be fixed through this chart input. |

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
| cluster | ClusterRole/argocd-application-controller | KSV044, KSV046 | accept | Reconciles ClusterRoles and ClusterRoleBindings for 14 Helm charts and platform roles; Kubernetes RBAC forbids creating a role with permissions the creator lacks without escalate, so a bounded role would require escalate (KSV050) or the same leaf rights; staged decision D-OWN-ARGO-STAGED, revisit with design option C |
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
| cluster | ClusterRole/istiod-clusterrole-istio-system | KSV041, KSV046, KSV049, KSV114 | accept | Istiod reads gateway TLS Secrets, publishes mesh ConfigMaps and patches injection/validation webhook configurations. |
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
| cluster | ClusterRole/trivy-operator | KSV041 | accept | Trivy Operator watches workloads across the cluster to produce ConfigAudit, vulnerability and secret reports; restricting discovery would omit namespaces from compliance scanning. |
| cluster | ClusterRole/apisix-ingress-controller-clusterrole | KSV041 | accept | APISIX ingress controller resolves ApisixRoute and Gateway TLS Secret references across namespaces. The measured ClusterRole grants Secret get/list/watch for that reconciliation. |
| cluster | ClusterRole/cnpg-cloudnative-pg | KSV041 | accept | CloudNativePG runs in platform-system and reconciles PostgreSQL Cluster resources in database. The measured cluster-wide Secret read supports cross-namespace reconciliation. |
| cluster | ClusterRole/nfs-external-provisioner-role | KSV041 | accept | The NFS CSI provisioner reads PVC-referenced Secret credentials from workload namespaces when mounting dynamically provisioned NFS volumes. The measured rule is get-only. |
| cluster | ClusterRole/prometheus-stack-kube-prom-operator | KSV041, KSV045 | accept | The kube-prometheus-stack operator reconciles Prometheus resources and referenced TLS/auth Secrets across monitored namespaces. The measured Secret rule uses wildcard verbs, and its separate wildcard resource rule triggers KSV045. |

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
| kube-system | Pod/kube-controller-manager-narwhal-master-1 | KCV0039 | accept | bind-address=0.0.0.0 permits off-node Prometheus scraping; secure ports 10257/10259 use kubeadm authentication/authorization kubeconfigs. No node firewall isolation is claimed. |
| kube-system | Pod/kube-scheduler-narwhal-master-1 | KCV0041 | accept | bind-address=0.0.0.0 permits off-node Prometheus scraping; secure ports 10257/10259 use kubeadm authentication/authorization kubeconfigs. No node firewall isolation is claimed. |
| kube-system | Pod/kube-apiserver-narwhal-master-1 | KCV0001 | accept | API server health probes use unauthenticated HTTPS /livez and /readyz. kubeadm join also uses anonymous access to kube-public cluster-info through the kubeadm:bootstrap-signer-clusterinfo RoleBinding. |
| kube-system | Pod/kube-apiserver-narwhal-master-1 | KCV0012 | accept | The explicitly enabled admission plugins are NodeRestriction and EventRateLimit. The air-gapped cluster uses its in-cluster registry and 111 of 120 observed containers use IfNotPresent. AlwaysPullImages would make their starts depend on registry availability, including Harbor's own restart (RKE2 and GKE CIS guidance treat it as unsuitable for isolated clusters). |

## Node collector & CIS 1.1.9 / 1.1.12 resolution

Historical collector investigation below is not the current ownership conclusion.
On 2026-11-24 the owner reported kubeadm root etcd and no host etcd user;
the new kube-bench result is UNKNOWN:UNKNOWN (not an observed UID). The exact
benchmark acceptance and 0700 evidence are recorded in Control-plane CIS follow-up.
KCV0059 remains unaccepted: no current Trivy failure was supplied for that check.

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
| Kubelet runtime hardening — CIS `4.2.6` (`protectKernelDefaults`), `4.2.12` (`serverTLSBootstrap`), `4.2.13` (`tlsCipherSuites`) | **Not evaluated** (`kubelet_mapping.yaml` reads `/configz`; `trivy.useEmbeddedRegoPolicies=true` `v1.7.1` silently passes empty `[]` when fields are unset) | **WARN (known gap)** (`verify_node_file_hardening` reports `WARN` — neither `PASS` nor `FAIL` — with pointer to this section) | **4.2.13 remains open. 4.2.6 and 4.2.12 rollouts are in progress.** The open controls follow `D-KUBELET-DEFERRED-GAPS`. Kubelet refuses to start if `protectKernelDefaults: true` runs on a node lacking the 6 kernel sysctls (including Kakao nodes with an older `base` completion marker that skip `01-prerequisites.sh`). **Rollout status & prerequisites:** (1) CIS `4.2.6` (`D-KUBELET-KERNEL-DEFAULTS`): `01-prerequisites.sh`, `02-init-cluster.sh`, `02-join-control-plane.sh`, and `02-join-worker.sh` now persist and verify all 6 sysctls (`/etc/sysctl.d/95-kubelet-kernel-defaults.conf`: `vm.overcommit_memory=1`, `vm.panic_on_oom=0`, `kernel.panic=10`, `kernel.panic_on_oops=1`, `kernel.keys.root_maxkeys=1000000`, `kernel.keys.root_maxbytes=25000000`) via `sysctl --system` BEFORE kubeadm starts kubelet with `protectKernelDefaults: true`; existing clusters use `scripts/ops/apply-kubelet-kernel-defaults.sh` (persists/verifies sysctls on every node first, then restarts kubelet serially, then patches `kubelet-config` CM) and still require a reboot-survival live check; (2) CIS `4.2.13`: test all kubelet TLS consumers (`kube-apiserver`, Prometheus kubelet scrape, `metrics-server`) across a live kubelet restart; (3) CIS `4.2.12`: follow the kubelet serving TLS rollout below and verify the CA-signed certificate from every node. |
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
- **`--anonymous-auth=false`** — kept accepted (KCV0001). Trivy matches the literal flag only, so path-limited anonymous access through `--authentication-config` still fails the check, and on 1.35 that file cannot be combined with the `--oidc-*` flags the cluster uses. Revisit if OIDC moves to `AuthenticationConfiguration.jwt`.
- Portal/Valkey `readOnlyRootFilesystem` (CIS 5.7.3 / KSV014) — Next.js writes cache/tmp
  at runtime; needs writable `emptyDir` mounts before enabling.

## How the page is fed

trivy-operator scanners must be enabled (`gitops/charts/narwhal-apps/templates/trivy-operator.yaml`):
`configAuditScannerEnabled`, `rbacAssessmentScannerEnabled`, `infraAssessmentScannerEnabled`,
`clusterComplianceEnabled` = true, `trivyOperator.reportRecordFailedChecksOnly: "false"` (string
`"false"`, because the chart wraps the key in Helm `with` and omits boolean `false`), and
`compliance.reportType: summary` (`D-CCR-SUMMARY`, also set on `pss-compliance.yaml` and
`kisa-compliance.yaml`). Portal 1.1.28+ reads `spec.compliance.controls` from the CCRs for
control-to-check mapping and derives per-control pass/fail verdicts from the 6 per-resource report
collections (`ConfigAuditReport`, `ClusterConfigAuditReport`, `(Cluster)RbacAssessmentReport`,
`(Cluster)InfraAssessmentReport`), avoiding the upstream `FromDetailReport` `Success: false` bug
and keeping `ClusterComplianceReport` objects safely below the 1.5 MiB etcd limit. Reports
regenerate on the `0 */6 * * *` cron; to force one, briefly patch a report's `spec.cron` to
`* * * * *`. To evaluate coverage accurately without reading cluster secrets, `/compliance` also
reads `nodes`, `deployments/trivy-operator`, and `configmaps/trivy-operator-trivy-config` under the
existing `narwhal-portal` `ClusterRole`, surfaces `503 { unavailable: true }` on lookup failure, and
shows per-source timestamps with the 6-hour rebuild cadence note.

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

## Control-plane CIS follow-up (2026-11-24, offline author lane)

D1: bootstrap explicitly renders `- --terminated-pod-gc-threshold=12500` in
kube-controller-manager. This retains the upstream default, avoiding an unmeasured
retention reduction to 1000. Join scripts consume the published ClusterConfiguration;
they do not render a second controllerManager argument list. Existing clusters need
`scripts/ops/apply-control-plane-flags.sh`; no live rollout was done in this lane.

### Reviewed exact exceptions

The original 2026-11-24 control-plane rules in `portal/src/data/compliance-acceptance-rules.json` are restricted to
`narwhal-master-1`. New nodes remain actionable. Trivy control-plane reports identify the exact Pod
resource name (`kube-controller-manager-narwhal-master-1` or
`kube-scheduler-narwhal-master-1`); the legacy infra matcher argument `node`
contains this resource identity for Pod reports, not the host node name.

| Control / source | True reason and evidence | Owner |
| --- | --- | --- |
| 1.1.12 / kube-bench | kubeadm static-pod etcd runs as root; no host etcd user exists (owner-provided live facts). `cis-record-live-r2.json` reports 1.1.12 `UNKNOWN:UNKNOWN`, not proof of root ownership; 1.1.11 PASS actualValue is `permissions=700`. Changing ownership alone can deny etcd access. | `02-init-cluster.sh` etcd.local; kubeadm owns the generated static pod |
| 1.3.7 / kube-bench + Trivy KCV0039 | `controllerManager.extraArgs` intentionally binds 0.0.0.0 for off-node Prometheus. Secure-port authentication/authorization is retained; no firewall isolation is asserted. See Risk-accepted exceptions item 1. | `scripts/cluster/02-init-cluster.sh` |
| 1.4.2 / kube-bench + Trivy KCV0041 | `scheduler.extraArgs` intentionally binds 0.0.0.0 for off-node Prometheus. The measured scheduler command includes authentication/authorization kubeconfigs. See Risk-accepted exceptions item 1. | `scripts/cluster/02-init-cluster.sh` |
| 1.2.1 / Trivy KCV0001 | D-OWN-CP accepts only `kube-system/Pod/kube-apiserver-narwhal-master-1`. Observed HTTPS `/livez` and `/readyz` probes are unauthenticated, and the `kubeadm:bootstrap-signer-clusterinfo` RoleBinding in `kube-public` binds `system:anonymous` for kubeadm join discovery. | `scripts/cluster/02-init-cluster.sh` |
| 1.2.12 / Trivy KCV0012 | D-OWN-CP accepts only the same API server Pod. Admission flags explicitly enable NodeRestriction and EventRateLimit (CIS 1.2.10, 2026-11-27). Of 120 observed containers, 111 use `IfNotPresent`. Requiring image pulls on startup would add a dependency on the in-cluster registry in air-gapped deployments, including Harbor's own restart; CIS 1.2.11 is a Manual control and the RKE2 and GKE CIS guides treat it as unsuitable for isolated clusters. | `scripts/cluster/02-init-cluster.sh` |
| 5.7.3 / Trivy KSV020, KSV021 | D-OWN-POD accepts only `database/Pod/narwhal-db-1` (26:26, including PostgreSQL data ownership) and the Deployment-owned pooler ReplicaSet (998:996, set by the operator with no securityContext in the Pooler template). | `gitops/resources/narwhal-db.yaml`, `scripts/cluster/07-cnpg.sh` |

The D-OWN-CP and D-OWN-POD rules were reviewed on 2026-11-26. The supplied kube-bench triage records both 1.2.1 and 1.2.12 as WARN, so the decision adds only Trivy rules for these controls. Revisit the exact rules if authenticated health and discovery or a registry outage rehearsal makes the respective hardening feasible.

### Needs owner decision / staged rollout

These controls remain actionable. The single-node trial verified kubelet serving TLS stages 1 and 2.

| Control | Concrete risk | Prerequisite / owner decision |
| --- | --- | --- |
| 1.2.6 kubelet-certificate-authority / AVD-KCV-0006 | Enforcing CA validation against a self-signed kubelet serving certificate breaks API server access to that kubelet. | Stage 3 verifies every registered kubelet from each control plane before enabling the CA flag. Verified on the single-node cluster on 2026-11-26: the flag is in the manifest and kubeadm-config, `logs`/`exec`/`port-forward`/node proxy work, and Trivy now passes AVD-KCV-0006. HA is not yet verified. |
| 1.3.6 RotateKubeletServerCertificate / AVD-KCV-0038 | On the single-node cluster (2026-11-26), Trivy flagged the absent gate argument. The v1.35.7 controller-manager help reported the beta gate defaults to true, and kube-bench already passed. | Stage 3 makes the existing default explicit. On the single-node cluster (2026-11-26) Trivy now passes AVD-KCV-0038. Initial issuance was verified on one node in stages 1–2. Scheduled rotation remains open. |
| 1.2.10 EventRateLimit / AVD-KCV-0010 | Event writes above configured budgets receive HTTP 429. | Fixed for fresh installs and joins: `EventRateLimit` with root-owned 0600 config, server 500/1000 and namespace 50/100 (cache 2000). Existing-cluster rollout: `scripts/ops/apply-apiserver-admission.sh --kubeconfig FILE --node NAME=USER@HOST` (dry-run); review then pass `--apply`. |
| 4.2.6 protect-kernel-defaults | Kubelet refuses startup (`Invalid kernel flag`) if any of the 6 required kernel sysctls differs, including after reboot if a later `sysctl.d` file overrides it or on Kakao nodes whose `base` sentinel skipped `01-prerequisites.sh`. | Fixed for fresh installs and joins (`D-KUBELET-KERNEL-DEFAULTS`): `01-prerequisites.sh`, `02-init-cluster.sh`, `02-join-control-plane.sh`, and `02-join-worker.sh` install `/etc/sysctl.d/95-kubelet-kernel-defaults.conf`, run `sysctl -p` + `sysctl --system`, and verify all 6 live `/proc/sys` values before kubelet starts with `protectKernelDefaults: true`. Existing clusters: `scripts/ops/apply-kubelet-kernel-defaults.sh --kubeconfig FILE --node NAME=USER@HOST` (dry-run), then `--apply` (persists/verifies sysctls on all nodes before touching kubelet, patches `kube-system/kubelet-config` last); live rollout + reboot-survival check pending. |

### Control-plane static Pod securityContext & kubelet protectKernelDefaults (2026-11-28, Lane C)

- **Control-plane static Pods (`D-CP-STATIC-POD-SC`, `D-CP-ETCD-DEFER`, R426):** kubeadm strategic patches in `scripts/ops/lib/kubeadm-patches/{kube-apiserver,kube-controller-manager,kube-scheduler}+strategic.yaml` set container `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, and pod-level `seccompProfile: {type: RuntimeDefault}` (resolving `KSV001`, `KSV003`, `KSV004` on those three static Pods). `02-init-cluster.sh` installs them to `/etc/kubernetes/patches` (`0700`/`0600 root:root`) and wires `InitConfiguration.patches.directory: /etc/kubernetes/patches`; `02-join-control-plane.sh` installs the same three files and passes `--patches=/etc/kubernetes/patches` to `kubeadm join`. All three binaries listen on unprivileged ports (>1024: 6443, 10257, 10259) and read host PKI/kubeconfig files as UID 0. The apiserver patch adds only `NET_BIND_SERVICE` (`D-CP-APISERVER-FILECAP`): measured `getcap` on the v1.35.7 image binary reports `cap_net_bind_service=ep`, and dropping it caused `exec ... operation not permitted`; remove the add if upstream removes the file capability.
  - **`etcd` deferred (`D-CP-ETCD-DEFER`):** in the 2026-11-28 live trial, rolling `etcd` timed out before becoming Ready and the prior script restored all manifests without capturing container logs; an offline `ctr` reproduction on a copy of `/var/lib/etcd` (`etcd:etcd` `0700`) started `etcd` under `cap-drop ALL` both with and without `DAC_OVERRIDE`, so the live failure root cause is not established. `etcd` is therefore left unmodified by kubeadm patches and by `apply-control-plane-security-context.sh`, `/var/lib/etcd` stays `etcd:etcd` `0700` via `scripts/cluster/harden-node-files.sh` (CIS 1.1.12 / `KCV0059`), and `etcd-narwhal-master-1` accepts exactly `KSV001`, `KSV003`, and `KSV004` with the measured reason: “etcd hardening patch prevented etcd from starting in the 2026-11-28 live trial; root cause under investigation.”
  - **Root runtime acceptance (`KSV012`, `KSV020`, `KSV021`):** exact rules for all four `narwhal-master-1` static Pods accept `KSV012`, `KSV020`, and `KSV021` because kubeadm 1.35.7 runs all four as UID/GID 0 and moving them off root requires the alpha `RootlessControlPlane` feature gate (pod-level `runAsGroup` alone fails in containerd while explicit `runAsUser: 0` triggers `KSV105`). `KSV022` is accepted only by a separate exact kube-apiserver rule: the binary's measured `cap_net_bind_service=ep` file capability is required for container `exec` (EPERM when dropped), with only `NET_BIND_SERVICE` retained and live CapEff `0x400`; it remains unaccepted on the other three Pods. `KSV105` and `KSV106` stay unaccepted on all four Pods; `KSV001`, `KSV003`, and `KSV004` remain actionable on scheduler/controller-manager/apiserver, while the exact etcd rule accepts those three during root-cause investigation.
  - **Existing-cluster rollout:** dry-run first (`bash scripts/ops/apply-control-plane-security-context.sh --kubeconfig FILE --node NAME=USER@HOST` or `--local NAME`, optionally scoped with repeatable `--component kube-scheduler|kube-controller-manager|kube-apiserver`), then run with `--apply` (backs up outside `/etc/kubernetes/manifests/` to `/var/lib/narwhal/control-plane-security-context/run.*/`, stages in `/etc/kubernetes/.narwhal-tmp` with `st_dev` check, installs `/etc/kubernetes/patches/` for selected components, rolls `kube-scheduler` -> `kube-controller-manager` -> `kube-apiserver` by default). Health verification runs node-side without depending on the API server (`crictl` container state/ID rotation + `runtimeSpec`/CRI `securityContext` check plus local HTTPS health endpoints `https://127.0.0.1:6443/readyz`, `:10257/healthz`, `:10259/healthz` with `-k`; API mirror-pod check is reported non-blocking). Before any automatic restore, the script captures `crictl logs --tail 300` and `crictl inspect` for the current and previous failing container under `<backup-dir>/diagnostics/<component>/` and prints that path. `etcd` and `/var/lib/etcd` (`etcd:etcd` `0700`) are never touched.
- **Kubelet `protectKernelDefaults=true` (`D-KUBELET-KERNEL-DEFAULTS`, CIS 4.2.6, R426):** **ordering risk** — kubelet refuses to start (`Invalid kernel flag`) if `protectKernelDefaults: true` is active before all 6 kernel sysctls (`vm.overcommit_memory=1`, `vm.panic_on_oom=0`, `kernel.panic=10`, `kernel.panic_on_oops=1`, `kernel.keys.root_maxkeys=1000000`, `kernel.keys.root_maxbytes=25000000`) are applied and survive `sysctl --system`. Both `01-prerequisites.sh` AND the init/join scripts (`02-init-cluster.sh`, `02-join-control-plane.sh`, `02-join-worker.sh`) enforce `/etc/sysctl.d/95-kubelet-kernel-defaults.conf` via `scripts/common/lib-kubelet-kernel-defaults.sh` before kubelet starts (protecting Kakao nodes whose old `base` sentinel skipped `01-prerequisites.sh`). Existing clusters run `bash scripts/ops/apply-kubelet-kernel-defaults.sh --kubeconfig FILE --node NAME=USER@HOST ...` (dry-run), then `--apply` (Phase 1 applies and verifies sysctls on all nodes, Phase 2 updates `/var/lib/kubelet/config.yaml` and restarts kubelet serially, Phase 3 patches `kube-system/kubelet-config` CM last; `--rollback` reverts CM first then nodes). Live cluster trial and node reboot-survival test remain pending.

### Kubelet serving TLS rollout

D-OWN-CSR selects the PostFinance approver with the D-OWN-LATEST-SEC pin
`1.2.16`. It runs on the pod network after Cilium and Argo CD adopts the
same Helm render from the mirrored Gitea package. Both consume the exact
node-name-to-IP inventory from provisioning (`kubeletCsrNodes` on `idp-apps`).
Vagrant uses the same names and private IPs assigned to VMs by the Vagrantfile
for the selected topology. Kakao derives `host-A-B-C-D` names and IPs from the
OpenTofu master and worker IP outputs. Kakao provisioning checks that the node
subnet fits inside a private RFC 1918 VPC and rejects missing IPs and collisions
with either LB VIP. Bootstrap and GitOps use one replica in single topology and
two in HA.

The Vagrant provisioner writes node names to the host OS's `/etc/hosts`.
Kakao keeps DHCP hostnames and the provisioned DNS zone covers ingress names.
Neither pod DNS configuration publishes records for the Node hostnames.
The approver bypasses name resolution, keeps the hostname check and one DNS
SAN, and checks names against the exact inventory and IP SANs against individual
`/32` entries. The API VIP and load balancers share the node subnet
(`D-CSR-EXACT-IPS`). The approver checks IP membership across the whole node
inventory, so a node can still request another listed node's IP. Add new node
addresses before joining them. A separate kubelet-serving signer CA can isolate
this trust from the API server's CA. If the cluster provides resolvable node DNS
records, disable the bypass.

Narwhal supplies the approver RBAC through the same chart render. CSR read and
approval plus approval of the kubelet-serving signer are cluster-scoped. Lease
and event access is scoped to `kube-system`. Bootstrap applies using server-side
apply as `narwhal-bootstrap`. The child Application uses Argo CD's client-side
apply by default. A field removed from future Helm output can persist under the
bootstrap field manager. When removing fields, inspect `metadata.managedFields`
and verify those fields are gone after sync. If they persist, explicitly remove
them as part of the rollout.

For an existing cluster, publish the digest-preserving image to the configured
mirror and publish the chart to Gitea. For an `idp-apps` Application that already
references the updated chart templates, publish and patch the inventory directly.
The example is for a single-node cluster. For HA, set `kubeletCsrTopology` to
`ha` and list every control-plane and worker node with its exact name and IP.

```bash
bash scripts/gitops/publish-chart-to-gitea.sh --fetch https://postfinance.github.io/kubelet-csr-approver kubelet-csr-approver 1.2.16
kubectl -n devtools patch application idp-apps --type merge -p '{"spec":{"source":{"helm":{"valuesObject":{"kubeletCsrTopology":"single","kubeletCsrNodes":{"narwhal-master-1":"192.168.56.10"}}}}}}'
```

Platform provisioning also populates `kubeletCsrNodes`
(`vagrant provision master-1 --provision-with phase2-platform` or
`FORCE=1 bash scripts/cloud/provision-kakao.sh phase2`). For an offline rollout,
publish the local package with
`bash scripts/gitops/publish-chart-to-gitea.sh /absolute/kubelet-csr-approver-1.2.16.tgz`.
Sync `idp-apps` and wait for the child Application to report Synced and Healthy
and for the approver Deployment to finish its rollout.

```bash
kubectl --kubeconfig /absolute/admin.conf -n devtools get application kubelet-csr-approver
kubectl --kubeconfig /absolute/admin.conf -n kube-system rollout status deploy/kubelet-csr-approver --timeout=300s
```

Pause joins, kubeadm upgrades and other control-plane manifest changes during
apply and rollback. Run one rollout at a time. Supply one trusted
SSH mapping for every registered node. The operator machine needs
`kubectl`, `jq`, `yq` v4, Python 3 and OpenSSL. Each target needs passwordless
sudo, `yq` v4 and OpenSSL. `--local NAME` can replace the SSH mapping when the
operator runs on a single-node cluster.

```bash
args=(--kubeconfig /absolute/admin.conf --node narwhal-master-1=vagrant@192.168.56.10 --identity /absolute/node-key)
bash scripts/ops/apply-kubelet-serving-tls.sh "${args[@]}" --dry-run
bash scripts/ops/apply-kubelet-serving-tls.sh "${args[@]}"
bash scripts/ops/apply-kubelet-serving-tls.sh "${args[@]}"
kubectl --kubeconfig /absolute/admin.conf get csr
kubectl --kubeconfig /absolute/admin.conf top nodes
```

The first apply should report an Approved+Issued CSR and the matching CA-signed
leaf with the node's InternalIP SAN. The second apply should verify that leaf
without restarting kubelet. Check pod logs, metrics-server and Prometheus
kubelet targets before proceeding to stage 3. Test natural rotation separately.

In the 2026-11-26 orchestrator trial on `fb50d343`, the Gitea package and
single-node inventory patch yielded a Synced/Healthy child and approver image
`sha256:3a09df2f9e1e628863fccbe5479e63b11bbc1f411c82134ee975778e1a25b5e6`.
Dry run, apply and re-run each exited `0` (re-run `true` → `true`). CSR
`csr-wwvp5` was Approved and Issued. The `:10250` issuer changed from
`CN=narwhal-master-1-ca@1794714706` to `CN=kubernetes`, with the IP SAN verified.
`kubectl logs`, `exec` and `top` worked, approver logs showed no forbidden or
error entries, and `test-sso` passed 144 checks with 0 failures.

On Kakao, pass the bastion with `--ssh-opt`, which accepts multiple OpenSSH
options. Configure its
identity and verified host key in `~/.ssh/config` and supply each node mapping.

```bash
bash scripts/ops/apply-kubelet-serving-tls.sh \
  --kubeconfig /absolute/kakao-admin.conf --identity /absolute/node-key \
  --known-hosts /absolute/kakao_known_hosts --ssh-opt 'ProxyJump=narwhal-kakao-bastion' \
  --ssh-opt 'IdentitiesOnly=yes' \
  --node host-172-16-0-10=ubuntu@172.16.0.10 --node host-172-16-0-21=ubuntu@172.16.0.21 --dry-run
```

#### Stage 3 control-plane CA validation

D-R416-ORDER enables `--kubelet-certificate-authority=/etc/kubernetes/pki/ca.crt`
after the post-Cilium serving rollout and before publishing the join commands.
The first kubelet uses its self-signed certificate during init. Enforcing the CA
at init would break API-server-mediated requests such as `logs`, `exec` and
`port-forward` until the approver issues the signed certificate. A newly joined
node becomes available to those requests when its signed certificate is served.

On an existing cluster, run stage 3 after stages 1–2. Each control plane checks
every registered node's `InternalIP:10250` against its local cluster CA and verifies
the IP SAN and TLS server purpose. The API server must prefer `InternalIP`.
Use the same mappings as above.

```bash
bash scripts/ops/apply-kubelet-serving-tls.sh "${args[@]}" --control-plane --dry-run
bash scripts/ops/apply-kubelet-serving-tls.sh "${args[@]}" --control-plane
```

D-R416-SERIAL waits for the expected static Pod command and local readiness
on each control plane before moving on. Each changed static Pod restarts,
so allow for a brief API outage on a single-node cluster. D-R416-PERSIST
updates `kube-system/kubeadm-config` after the rollout so joins and upgrades
retain the flags. If interrupted, fix the failure and repeat the command.
Confirm both checks in Trivy and kube-bench and exercise `logs`, `exec` and
`port-forward` against a Pod on each node. The single-node trial on
2026-11-26 used `--timeout 600` (etcd can stall under host IO; the 180-second
default would trigger an automatic restore and a second restart). Apply,
idempotent re-run and client checks were verified there; joins, upgrades,
rollback and scheduled rotation have no live verification in this change.
If the session dies between the manifest rename and the readiness wait, no
automatic restore runs; the new manifest stays (every kubelet was verified
first), and repeating the command converges.
Metrics-server uses a separate kubelet client TLS configuration.

To disable API server CA validation, run this first:

```bash
bash scripts/ops/apply-kubelet-serving-tls.sh "${args[@]}" --control-plane --rollback
```

D-R416-RESTORE uses the exact previous API server manifest stored in
`/var/lib/narwhal/kubelet-serving-tls/kube-apiserver.pre-ca.yaml` if the current
manifest still matches that rollout. On joined nodes or after later manifest
changes it removes only the CA flag, preserving newer settings such as OIDC.
The rollback also removes the CA flag from kubeadm's configuration and retains
the controller-manager's explicit rotation default. If the API server cannot
start, restore the saved manifest on the affected host using an atomic rename
through a dotfile in `/etc/kubernetes/manifests`, and rerun rollback after the
API becomes available.

If stage 2 apply fails during a new enrollment, it attempts to restore that node's
previous setting. Re-run to resume after investigating, or run the command
below to disable serving bootstrap on all mapped nodes and the shared join
configuration. The rollback preserves both the kubelet client keys and the
previously issued serving material. Keep the approver running until rollback
has restored fallback TLS on all nodes. Inspect the CSR and approver logs before
retrying a denied request.

```bash
bash scripts/ops/apply-kubelet-serving-tls.sh "${args[@]}" --rollback
```

If a reboot between `02-init-cluster.sh` and `03-cni-install.sh` clears
`/tmp/kubeadm-config.yaml`, rerun the initial provisioning sequence before
joining nodes. Run `vagrant provision master-1` for Vagrant or
`FORCE=1 bash scripts/cloud/provision-kakao.sh init` for Kakao. Step 02 detects
the existing `admin.conf`, regenerates the temporary config and pending join
files, then step 03 resumes after CNI is ready. Keep the pending markers under
`/var/lib/narwhal/kubelet-serving-tls` so step 03 can complete enrollment.

### Existing-cluster trial and recovery (orchestrator only)

Use explicit mappings for each real control-plane node, sequentially; no default
kubectl context is consulted. SSH uses BatchMode and normal known-host checks;
pass `--identity` for the existing Vagrant private key or cloud SSH key.
The remote node needs sudo; the operator machine needs python3 and yq v4. Inspect the dry-run diff first:

```bash
bash scripts/ops/apply-control-plane-flags.sh --kubeconfig /absolute/admin.conf --node narwhal-master-1=vagrant@192.168.56.10 --identity /absolute/node-key --dry-run
bash scripts/ops/apply-control-plane-flags.sh --kubeconfig /absolute/admin.conf --node narwhal-master-1=vagrant@192.168.56.10 --identity /absolute/node-key --timeout 180
# Use the exact backup path printed by the successful apply:
bash scripts/ops/apply-control-plane-flags.sh --kubeconfig /absolute/admin.conf --node narwhal-master-1=vagrant@192.168.56.10 --identity /absolute/node-key --rollback /var/lib/narwhal/control-plane-flags/manifest.REPLACE
```

The apply waits for a new mirror-pod UID, Ready=True, exact command, and a
controller-manager Lease whose holder is a current Ready controller pod and whose
renewTime advances after the new process started. Rollback waits for the restored
pod and Lease too. If a later node fails, that node is restored; earlier applied
nodes and exact rollback commands are printed, and the script exits non-zero.
Backups remain root-only 0600 outside manifests. Replacement uses a verified same-
filesystem temporary directory outside manifests. Dry-run renders locally without
remote writes. Local python3/yq v4 are required; remote yq is not required.
SSH uses keepalives and strict host verification; `--known-hosts FILE` selects an
existing trusted hosts file (default: the user's known_hosts).

The helper edits static-pod manifests only. To retain the flag across kubeadm
upgrade/join regeneration, the orchestrator must separately update the published
ClusterConfiguration after review. With local yq v4, run this sequence (render and
patch construction tested offline; API mutation is not yet performed):

```bash
KUBECONFIG=/absolute/admin.conf
export KUBECONFIG
umask 077
work=$(mktemp -d)
kubectl -n kube-system get configmap kubeadm-config -o json > "$work/before.json"
yq -r '.data.ClusterConfiguration' "$work/before.json" > "$work/cluster.yaml"
yq '( .controllerManager.extraArgs // [] ) |= (map(select(.name != "terminated-pod-gc-threshold")) + [{"name": "terminated-pod-gc-threshold", "value": "12500"}])' "$work/cluster.yaml" > "$work/new.yaml"
python3 - "$work" <<'PYCONFIG'
import json, pathlib, sys
w = pathlib.Path(sys.argv[1])
before = json.loads((w / "before.json").read_text())
patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": before["metadata"]["resourceVersion"]},
         {"op": "replace", "path": "/data/ClusterConfiguration", "value": (w / "new.yaml").read_text()}]
(w / "patch.json").write_text(json.dumps(patch))
PYCONFIG
diff -u "$work/cluster.yaml" "$work/new.yaml" || test "$?" = 1
kubectl -n kube-system patch configmap kubeadm-config --type=json --patch-file "$work/patch.json" --dry-run=server -o yaml
# After reviewing the diff and server dry-run:
kubectl -n kube-system patch configmap kubeadm-config --type=json --patch-file "$work/patch.json"
kubectl -n kube-system get configmap kubeadm-config -o json > "$work/after.json"
yq -r '.data.ClusterConfiguration' "$work/after.json" | yq -e '.controllerManager.extraArgs[] | select(.name == "terminated-pod-gc-threshold" and .value == "12500")'
rm -rf "$work"
```

Skipping this update lets kubeadm upgrade or a control-plane join regenerate a
manifest without the explicit flag; reapply the helper after regeneration and
repeat health verification. After a live trial verify kube-bench, Prometheus
scraping and controller-manager logs. Offline stubs do not prove these properties.
