# Notification channels (#610)

D610-2/D610-4: `notification-channels` follows the existing `prometheus-alerts`
resource Application pattern. Git creates only an empty Opaque Secret and a null-only
AlertmanagerConfig, both `monitoring/narwhal-notification-channels`, labelled
`narwhal.io/notification-channels: "true"`. No credentials belong in Git.

The portal owns Secret `/data` and AlertmanagerConfig `/spec`. The Application ignores
only those exact object/field pairs, with `RespectIgnoreDifferences=true` so a sync
of another field cannot overwrite saved settings. Labels/identity remain Git-owned.
First creation is disabled; deleting/recreating either object loses settings. Back up
credentials through the approved secret-management path, never into Git or logs.
Rollback: disable channels in the portal before revoking the RoleBinding; remove
ignore entries only when intentionally returning data/spec ownership to Git.

D610-5 grants only get/update/patch for those resourceNames in monitoring, bound to
`devtools/narwhal-portal`. Portal API authorization must enforce cluster-admin.
Secret key names and channel completeness/routing behavior are defined by the shared
#610 lane contract; this lane does not implement portal API validation or delivery.

D610-6: the explicit portal egress policy allows the exact Alertmanager pods on TCP
9093 (+15008 HBONE). Existing devtools allow-all egress remains. Read-only inspection
on 2026-11-09 found only monitoring-egress and no monitoring AuthorizationPolicy.
`monitoring-permissive` accepts the portal's ambient-opt-out plaintext connection.
No new AuthorizationPolicy is needed: a new ALLOW policy would deny other existing
callers by default. This change does not claim to introduce monitoring ingress isolation.
API: `http://prometheus-stack-kube-prom-alertmanager.monitoring.svc:9093/api/v2/alerts`.

## Existing-cluster rollout (owner executed; not performed by this lane)

1. Before enabling kubeEtcd in Argo, confirm all master addresses and inspect each
   etcd pod's `--listen-metrics-urls`. Master-1 on 2026-11-09 exposed only
   `http://127.0.0.1:2381`, so merely enabling ServiceMonitor cannot work.
2. D610-1/D610-3 use HTTP 2381, not the TLS client API on 2379. The Service selects
   `component: etcd` in kube-system, discovers hostNetwork pod IPs dynamically and
   scrapes every master; no provider-specific IP list or client certificate needed.
   The ServiceMonitor has no bearerTokenFile (do not send a SA token over HTTP).
   Restrict host firewall/cloud security-group ingress 2381 to the Prometheus node/
   pod source addresses actually used by Cilium, before exposing metrics. The new
   listener exposes unauthenticated metrics on all node interfaces; Kubernetes
   NetworkPolicy alone does not protect hostNetwork etcd. Do not expose it publicly.
3. Save a backup of `kube-system/kubeadm-config` securely. In
   `data.ClusterConfiguration`, merge into `etcd.local.extraArgs` (v1beta4 list):
   `{name: listen-metrics-urls, value: "http://0.0.0.0:2381"}`. Preserve all other
   arguments. This persists for joins/upgrades; `02-init-cluster.sh` sets it for new
   clusters, and joining control planes inherit it.
4. On **one master at a time**, securely back up `/etc/kubernetes/manifests/etcd.yaml`
   outside the manifest directory. Replace only its listen-metrics-urls argument with
   `--listen-metrics-urls=http://0.0.0.0:2381` using an atomic file replacement.
   Wait for the static pod to restart, become Ready, and verify etcd quorum/health
   before proceeding to the next master. Keep loopback health probes unchanged.
   If unhealthy, restore that master's backup and stop; never restart all masters together.
5. Publish through the normal reviewed GitOps flow, sync prometheus-stack,
   notification-channels, narwhal-platform and network-policies. Inspect the rendered
   Alertmanager CR selectors and matcher strategy None; confirm both disabled objects
   exist before portal settings are saved. Run impersonated RBAC checks: the portal
   can get/update/patch only the named objects, cannot list/create/delete Secrets or
   AlertmanagerConfigs, and cannot write any other object's name.
6. Confirm kube-etcd Service endpoints include every master's IP:2381, all scrape
   targets are UP, and
   `count(etcd_disk_backend_commit_duration_seconds_bucket) > 0` in Prometheus.
   Verify EtcdHighCommitDuration's expression has input series. From the portal path,
   GET firing alerts and POST a test alert; configure a real webhook/SMTP channel and
   confirm receipt. Force an Argo sync and confirm saved configuration survives
   without printing Secret data. Verify disabled channels stop delivery.

Rollback etcd: disable kubeEtcd, restore loopback-only listener sequentially and
restore the same setting in kubeadm-config. The rule expression from #525 is unchanged.

## Offline evidence

Chart kube-prometheus-stack **86.2.3**, Operator **v0.91.0**. `helm template` verifies
all three Alertmanager selector/matcher fields against its bundled CRD schema, etcd
Service selector/2381 port, HTTP ServiceMonitor and script/Argo values parity.
R334 rejects selector, matcher, object labels, RBAC and ignoreDifferences mutations.
Offline success does not prove delivery, selfHeal preservation or scrape reachability.
