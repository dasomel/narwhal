# Master Node Memory Pressure & NodeNotReady Flapping (diagnosis)

> **Scope:** the control-plane memory budget applies to any deployment target. The knob
> named here (`MASTER_MEMORY`) is the Vagrantfile's; on Kakao Cloud the equivalent is the
> master instance flavor in `csp/kakao-cloud/terraform`.

> Status: **fixed** (`MASTER_MEMORY` 4096→6144, 2026-06-07).
> Root constraint: 4GB master nodes were undersized for the current control-plane
> + mandatory DaemonSet footprint. Bumped to 6GB.

## Symptoms

- Control-plane pods (`kube-apiserver`, `kube-controller-manager`, `kube-scheduler`,
  `kube-vip`) show high restart counts (9–13+ over ~25h).
- Masters intermittently flap `Ready` → `NotReady` → `Ready` (all three masters'
  Ready condition observed transitioning within a ~15-min window).
- Side effect: `metrics-server` (on a master) flapped too → `metrics.k8s.io`
  APIService `MissingEndpoints` → portal Architecture page showed blank CPU/memory.
  (That symptom was separately mitigated — see `gitops-push.md` worked example.)

## Evidence (2026-06-07)

| Node | Mem usage | Headroom |
|------|-----------|----------|
| narwhal-master-1 | 71% | ~1.1 GB |
| narwhal-master-2 | 74% | ~1.0 GB |
| narwhal-master-3 | **89%** | **567 Mi** |

- `kube-apiserver` has **no memory limit** (`requests: cpu=250m` only) and uses
  **~1.8–2.3 GB each**.
- apiserver last termination: `reason=Error, exitCode=137` (SIGKILL) — **not**
  `OOMKilled` at the container cgroup. Mechanism: node memory pressure → apiserver
  slow → kubelet `/livez` probe times out → kubelet SIGKILLs it. The same latency
  also delays kubelet heartbeat → node briefly `NotReady`.
- Masters are correctly tainted (`node-role.kubernetes.io/control-plane`); heavy
  platform apps (prometheus, argocd-application-controller, loki, keycloak, grafana)
  are correctly on **workers**. So this is NOT a mis-scheduling problem.

## Per-master memory budget (~3.9 GB allocatable)

| Component | Approx mem |
|-----------|-----------|
| kube-apiserver | ~1.8–2.3 GB |
| etcd + controller-manager + scheduler + kube-vip | ~0.6 GB |
| DaemonSets: cilium ~250Mi, **falco ~300Mi**, alloy-logs/node-exporter/istio-cni/ztunnel/metallb-speaker/velero node-agent/ca-installer | ~0.8 GB |
| **Total** | **~3.4 GB** → leaves only ~0.5 GB headroom |

The dominant consumer (apiserver, ~2 GB) is irreducible. DaemonSets add ~0.8 GB
that masters cannot avoid (they tolerate all taints by design).

## Fix options (when ready to act)

1. **Increase `MASTER_MEMORY` 4096 → 6144 in `Vagrantfile`** (recommended, durable).
   - Apply rolling: `vagrant reload master-1` → verify `kubectl get nodes` stable →
     master-2 → master-3 (one at a time; see CLAUDE.md "Limit parallel cluster
     modifications", "modify one → verify → next").
   - This is the only fix that gives apiserver real headroom.
2. **Exclude `falco` DaemonSet from masters** (partial relief ~300Mi/master, GitOps
   push via `scripts/gitops/push-to-gitea.sh`). Trade-off: loses runtime-security
   visibility on control-plane nodes; apiserver can still spike past the saved margin.
3. **(Optional) tighten apiserver footprint** — limited upside; apiserver genuinely
   needs the RAM. Not recommended as a primary fix.

## Related

- `docs/common/gitops-push.md` — how to push GitOps changes so ArgoCD applies them.
- `scripts/cluster/04-addons.sh` — metrics-server probe loosening (already applied).
- CLAUDE.md Mistakes Log: "API server OOM restart on Master 4GB RAM" (2026-02-14).
