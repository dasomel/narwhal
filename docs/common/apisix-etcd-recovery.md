# apisix-etcd recovery: empty-prefix bootstrap deadlock

## Background

`apisix-etcd` (`gitops/charts/narwhal-platform/templates/apisix-infra.yaml`) uses an
`emptyDir` data volume by design ("dev environment: etcd re-initializes on restart,
APISIX Ingress Controller re-syncs from CRDs" per the in-file comment). That resync is
**not reliable** in the current version combination (apisix chart 2.13.0 / etcd 3.5.31 /
apisix-ingress-controller 0.14.1) — any event that recreates the `apisix-etcd` pod (a
`kubectl apply`/`helm` spec change, crash, OOM, node drain, or a `08-1-networking.sh`
re-run) can leave routing config **empty** and stuck that way without manual
intervention.

## Symptoms

- `apisix-etcd` pod is `Running`, `etcdctl endpoint health` reports healthy, but
  `etcdctl get /apisix --prefix --keys-only` returns **zero keys**.
- `GET /apisix/admin/routes` (and `/upstreams`, `/ssls`, etc.) on the APISIX admin API
  returns **HTTP 404** `{"message":"Key not found"}` instead of an empty list
  (`{"total":0,"list":[]}`). This is the actual bug: APISIX's etcd storage layer 404s
  a collection prefix that has *never* had a key written under it, as opposed to one
  that is empty after deletions.
- `apisix-ingress-controller` logs loop forever on startup:
  `failed to list routes/upstreams/ssl in APISIX: unexpected status code 404` →
  `failed to sync cache` → `failed to wait the default cluster to be ready` → retry.
  It never reaches the point of writing real routes from the `ApisixRoute` CRs.
- **User-facing traffic can still be fine** during this window: the live `apisix`
  gateway pod (data-plane) keeps serving from its in-memory route cache as long as
  *it* hasn't restarted. The moment it does restart while etcd is in this state, it
  boots with zero routes — a full outage. This is the real risk: the failure is
  silent until something else forces a gateway pod restart.

## Recovery: seed the empty prefixes to break the bootstrap deadlock

The ingress controller's startup "cluster ready" gate lists every resource type before
it will write anything. Any type that still 404s aborts the whole cycle before real
routes get written, and — confusingly — a genuinely orphaned placeholder row is *not*
pruned by the controller (it coexists with real synced routes), so seeding is safe.

Get the admin key first:

```bash
ADMIN_KEY=$(kubectl -n platform-system get secret apisix-admin-key -o jsonpath='{.data.key}' | base64 -d)
BASE="http://apisix-admin.platform-system.svc.cluster.local:9180/apisix/admin"
```

Seed every collection the controller's startup sync touches, from a throwaway pod
inside the cluster (the apisix/etcd containers have no shell/curl):

```bash
kubectl -n platform-system run seed-batch --image=curlimages/curl:8.11.0 --restart=Never --command -- sleep 180
kubectl -n platform-system wait --for=condition=Ready pod/seed-batch --timeout=30s

kubectl -n platform-system exec seed-batch -- curl -s -X PUT -H "X-API-KEY: ${ADMIN_KEY}" -H "Content-Type: application/json" \
  -d '{"uri":"/__bootstrap-seed-delete-me__","host":"bootstrap.invalid.local","upstream":{"type":"roundrobin","nodes":{"127.0.0.1:1":1}}}' \
  "${BASE}/routes/bootstrap-seed"

kubectl -n platform-system exec seed-batch -- curl -s -X PUT -H "X-API-KEY: ${ADMIN_KEY}" -H "Content-Type: application/json" \
  -d '{"type":"roundrobin","nodes":{"127.0.0.1:1":1}}' \
  "${BASE}/upstreams/bootstrap-seed"

kubectl -n platform-system exec seed-batch -- curl -s -X PUT -H "X-API-KEY: ${ADMIN_KEY}" -H "Content-Type: application/json" \
  -d '{"upstream":{"type":"roundrobin","nodes":{"127.0.0.1:1":1}}}' \
  "${BASE}/services/bootstrap-seed"

kubectl -n platform-system exec seed-batch -- curl -s -X PUT -H "X-API-KEY: ${ADMIN_KEY}" -H "Content-Type: application/json" \
  -d '{"username":"bootstrap-seed"}' \
  "${BASE}/consumers/bootstrap-seed"

kubectl -n platform-system exec seed-batch -- curl -s -X PUT -H "X-API-KEY: ${ADMIN_KEY}" -H "Content-Type: application/json" \
  -d '{"plugins":{"response-rewrite":{"headers":{"X-Bootstrap-Seed":"1"}}}}' \
  "${BASE}/global_rules/bootstrap-seed"

kubectl -n platform-system exec seed-batch -- curl -s -X PUT -H "X-API-KEY: ${ADMIN_KEY}" -H "Content-Type: application/json" \
  -d '{"plugins":{"response-rewrite":{"headers":{"X-Bootstrap-Seed":"1"}}}}' \
  "${BASE}/plugin_configs/bootstrap-seed"

kubectl -n platform-system delete pod seed-batch --wait=false
```

`ssls` needs a real cert/key pair (APISIX validates the payload), generate a throwaway
self-signed one and `kubectl cp` it in rather than trying to inline it:

```bash
openssl req -x509 -newkey rsa:2048 -keyout /tmp/seed.key -out /tmp/seed.crt -days 1 -nodes \
  -subj "/CN=bootstrap.invalid.local"
python3 -c "
import json
payload = {'cert': open('/tmp/seed.crt').read(), 'key': open('/tmp/seed.key').read(), 'snis': ['bootstrap.invalid.local']}
json.dump(payload, open('/tmp/seed_ssl_payload.json', 'w'))
"
kubectl -n platform-system run seed-ssl --image=curlimages/curl:8.11.0 --restart=Never --command -- sleep 60
kubectl -n platform-system wait --for=condition=Ready pod/seed-ssl --timeout=30s
kubectl -n platform-system cp /tmp/seed_ssl_payload.json seed-ssl:/tmp/seed_ssl_payload.json
kubectl -n platform-system exec seed-ssl -- curl -s -X PUT -H "X-API-KEY: ${ADMIN_KEY}" -H "Content-Type: application/json" \
  --data-binary @/tmp/seed_ssl_payload.json "${BASE}/ssls/bootstrap-seed"
kubectl -n platform-system delete pod seed-ssl --wait=false
```

Note: the admin API path for SSL certs is `/apisix/admin/ssls` (plural) — `/ssl` (singular)
returns `{"error_msg":"Unsupported resource type: ssl"}`, easy to misdiagnose as a
different problem.

Then force the controller to redo its startup sync:

```bash
kubectl -n platform-system rollout restart deployment/apisix-ingress-controller
kubectl -n platform-system rollout status deployment/apisix-ingress-controller --timeout=90s
```

Wait ~1-2 minutes (it converges over several reconcile passes, not instantly), then
confirm real routes landed:

```bash
kubectl -n platform-system run recheck --image=curlimages/curl:8.11.0 --restart=Never --rm -i --command -- \
  curl -s -H "X-API-KEY: ${ADMIN_KEY}" "${BASE}/routes" > /tmp/routes.json
grep -o '"total":[0-9]*' /tmp/routes.json   # should be ~16+ real routes, not just the seed
```

Finally, delete the 7 placeholder entries (they don't get auto-pruned):

```bash
for res in routes upstreams services consumers ssls; do
  kubectl -n platform-system run cleanup-$res --image=curlimages/curl:8.11.0 --restart=Never --rm -i --command -- \
    curl -s -X DELETE -H "X-API-KEY: ${ADMIN_KEY}" "${BASE}/${res}/bootstrap-seed"
done
kubectl -n platform-system run cleanup-globalrules --image=curlimages/curl:8.11.0 --restart=Never --rm -i --command -- \
  curl -s -X DELETE -H "X-API-KEY: ${ADMIN_KEY}" "${BASE}/global_rules/bootstrap-seed"
kubectl -n platform-system run cleanup-pluginconfigs --image=curlimages/curl:8.11.0 --restart=Never --rm -i --command -- \
  curl -s -X DELETE -H "X-API-KEY: ${ADMIN_KEY}" "${BASE}/plugin_configs/bootstrap-seed"
```

(`kubectl run --restart=Never ... _global_rules` / `_plugin_configs` as a pod *name*
is invalid — Kubernetes names can't contain `_`. Use a hyphenated pod name like
`cleanup-globalrules` even though the admin API path itself is `global_rules`.)

## Real fix (not done here — needs a decision)

This bootstrap sequence is a workaround, not a fix. Options for someone to pick up:

1. **Switch `apisix-etcd` to a real PVC** instead of `emptyDir`, so a pod recreation
   never wipes routing config in the first place. Removes the failure mode entirely.
2. **Fix/upgrade** the apisix / etcd / ingress-controller version combination so the
   "empty prefix returns 404" behavior no longer wedges the controller's startup gate,
   if a newer combination handles it (empty-list response instead of 404).

Until one of these lands, treat any `apisix-etcd` pod recreation as an event that
needs the routes/upstreams count checked afterward, not just pod `Running` status.
