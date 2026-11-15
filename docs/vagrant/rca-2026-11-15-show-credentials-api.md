# RCA: `show-credentials.sh` reported "Cannot reach the cluster API" on a running cluster (2026-11-15)

Status: root cause identified (host-side storage latency, inferred from guest metrics); script
diagnosis fixed in the PR that adds this file (D-CRED-PREFLIGHT-DIAG). Related: #660 (etcd
instability under host load), #658 (trivy-server write volume).

## Symptom

On the local single-node Vagrant cluster (rebuilt the same day from `a048411b`), the operator ran
`scripts/test/show-credentials.sh` at 16:34:22 KST and got:

```text
Cannot reach the cluster API at https://192.168.56.100:6443 — nothing to read.
  Are the VMs running?  vagrant status
```

The VM was running. A rerun at 16:35:45 KST with the same kubeconfig succeeded.

## Timeline (KST)

| Time | Event | Source |
|---|---|---|
| 12:44–13:30 | Fresh single provision from `a048411b`; verify-cluster FAIL 0, SSO 144/144 | `up.sh` log |
| 16:04 | kube-controller-manager and kube-scheduler restart (9th each; leader lease lost) | pod `lastState` |
| 16:26, 16:32 | etcd backend commit p99 6.84 s, then ≥ 8.19 s (top histogram bucket) | Prometheus |
| 16:27 | etcd WAL fsync p99 2.45 s | Prometheus |
| 16:32:46–16:33:51 | ClusterDeck rewrites `~/.kube/config` 6 times (backups in `~/.kube/bak`) | file mtimes |
| 16:34 | kube-apiserver logs 129 warn/error lines: etcd `DeadlineExceeded`, `timeout.go`, `writers.go` | apiserver pod log |
| 16:34:22 | `show-credentials.sh` pre-flight `kubectl get --raw /readyz` fails → misleading message | operator terminal |
| 16:35:45 | Rerun succeeds | session log |
| 16:41, 16:43 | etcd backend commit p99 again ≥ 8.19 s | Prometheus |

apiserver warn/error lines per minute show a periodic pattern: 16:25 = 74, 16:30–31 = 57/79,
16:34 = 129, 16:36 = 140, 16:40 ≈ 100, with 2–4 in the minutes between.

## Evidence

- **API reachable, CA current.** `/healthz` on 192.168.56.100 and 192.168.56.10 returned 200; the
  `vagrant-narwhal` kubeconfig CA hash equals the live `/etc/kubernetes/pki/ca.crt` hash; kube-apiserver
  and etcd had 0 restarts. This rules out a stale kubeconfig or a down VM at 16:35.
- **etcd stalls (Prometheus, 1-minute p99).** Backend commit: 16:26 = 6.84 s, 16:32 = 8.19 s,
  16:41 = 8.19 s, 16:43 = 8.19 s (normal 0.01–0.06 s). WAL fsync: 16:27 = 2.45 s, 16:41 = 0.66 s.
  No leader changes (single member).
- **Guest disk latency spikes without guest load.** Average write latency per write (max over guest
  disks): 16:24 = 403 ms, 16:26 = 498 ms, 16:27 = 957 ms, 16:41 = 1281 ms; most other minutes 1–10 ms.
  Total guest block-device writes in 16:24–16:44 were only ~722 MB (~0.6 MB/s,
  `node_disk_written_bytes_total` on the root device). Per-container cAdvisor write counters for the
  same window (etcd 560 MB, istio-cni-node 209 MB, kube-apiserver 110 MB) sum to more than that because
  they count filesystem writes before page-cache coalescing; they rank writers, they do not add up to
  device writes. Guest load1 stayed 1.2–5.2 on 10 vCPU, iowait share
  ≤ 0.14, IO pressure ≤ 0.30.
- **Host contention at the time of analysis (snapshot, not the incident minute).** A second VM, colima
  `beluga` (10 CPU / 24 GiB), was running beside the Narwhal VM (10 vCPU / 24 GiB) on the 14-core /
  64 GB host; host load average 11.0 / 9.0 / 8.3; `vmware-vmx` 75 % CPU, the Virtualization.framework
  (colima) service 56 %, and an antivirus daemon 46 %. etcd logs for 16:25–16:45 had already rotated
  away (oldest retained line 16:52), so etcd-side `slow fdatasync` lines are not available.

## Root cause

1. **Trigger (inferred, strong):** write latency on the VM's virtual disk spiked to ~0.5–1.3 s per write
   for short windows while the guest itself was nearly idle on IO. The stall originates below the guest
   — host storage contention from concurrent workloads (a second 10-CPU VM, VMware, real-time antivirus
   scanning). The exact host process at 16:34 was not captured.
2. **Propagation:** etcd backend commits blocked for up to ≥ 8 s and kube-apiserver logged etcd
   `DeadlineExceeded` / timeout errors in the incident minute (both measured). That the 16:34:22 probe
   failed for this reason is inferred: the script discarded kubectl's stderr, so whether `/readyz`
   returned not-ok or exceeded the 10 s request timeout was not recorded. The same stalls explain the controller-manager/scheduler lease losses.
3. **Misdiagnosis (measured, fixed):** `show-credentials.sh` discarded kubectl's stderr and printed
   "Cannot reach … Are the VMs running?" for every failure, so a not-ready API looked like a stopped VM.

The ClusterDeck kubeconfig rewrites at 16:32–16:33 are coincident: the final config has the live CA
and works, and the failure at 16:34:22 came after the last rewrite. They are not the cause.

## Fixes and follow-ups

- **Done (this PR):** the pre-flight retries the readiness probe and reports kubectl's real error,
  separating unreachable, stale CA (x509), credentials, and "API up but not ready (etcd slow)"
  (D-CRED-PREFLIGHT-DIAG), with a regression check.
- **Operator guidance:** while the local cluster is used, avoid running a second large VM (colima
  `beluga`) and heavy host test lanes at the same time (existing rule: at most 2 host test lanes, #660).
  Excluding the VMware VM directory from real-time antivirus scanning is a candidate mitigation
  (not tested).
- **Evidence retention gap:** etcd pod logs rotate within ~1 h on this cluster, so incident-minute etcd
  logs were lost. Prometheus retained the metrics used here.
- **Not done:** host-side IO tracing at the incident minute (would turn the trigger from inferred to
  measured), and any change to etcd timeouts (#660 options).

## Reproduce the measurements

```bash
kubectl --context vagrant-narwhal -n monitoring port-forward svc/prometheus-operated 19090:9090 &
# etcd backend commit p99 per minute
curl -s --get http://127.0.0.1:19090/api/v1/query_range \
  --data-urlencode 'query=histogram_quantile(0.99, sum by (le) (rate(etcd_disk_backend_commit_duration_seconds_bucket[1m])))' \
  --data-urlencode start=2026-11-15T07:24:00Z --data-urlencode end=2026-11-15T07:44:00Z --data-urlencode step=60s
# guest write latency (ms per write)
#   max(rate(node_disk_write_time_seconds_total[1m]) / clamp_min(rate(node_disk_writes_completed_total[1m]),1)) * 1000
```

The apiserver per-minute counts came from `/var/log/pods/kube-system_kube-apiserver-*/*/*.log*` on
master-1, filtered to `"level":"warn"|"error"` and `E1115`/`W1115` lines.
