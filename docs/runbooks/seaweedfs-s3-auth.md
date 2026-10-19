# Runbook: enable SeaweedFS S3 authentication with per-client random keys (live cluster)

**REQUIRES EXPLICIT OWNER APPROVAL before execution** (shared cluster, credentials, backups
are touched). The repository change (PR `feat/seaweedfs-s3-auth`) is static-verified only; none of
the steps below has been run. Decision D-s3auth.

Why: SeaweedFS S3 ran with no identities (anonymous). Velero's kopia repositories cannot
initialise against an anonymous server ("Signed request requires setting up SeaweedFS S3
authentication"), so no volume data was ever backed up. The 07/08-4 scripts also defaulted every
client to the inline pair `admin`/`admin`.

Target state: identities `loki`, `tempo`, `velero`, `cnpg`, each limited to its own bucket
(`Read/Write/List/Tagging:<bucket>`, never `Admin`), random keys. The client Secrets are the single
source of truth; `storage/seaweedfs-s3-config` (key `seaweedfs_s3_config`) and Velero's `cloud`
file are derived from them by `ensure_s3_identities` (`scripts/common/lib.sh`).

| Client | Secret (keys) | Bucket | Consumed by |
|---|---|---|---|
| loki | `monitoring/loki-s3-credentials` (`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`) | `loki` | envFrom + expand-env |
| tempo | `monitoring/tempo-s3-credentials` (same keys) | `tempo` | envFrom + expand-env |
| velero | `storage/velero-s3-credentials` (`access-key`, `secret-key`, `cloud`) | `velero` | `credentials.existingSecret` |
| cnpg | `database/cnpg-s3-credentials` (`ACCESS_KEY_ID`, `ACCESS_SECRET_KEY`) | `cnpg-backup` | `barmanObjectStore.s3Credentials` |

## Why the cutover is staged

- Both S3 servers must be authenticated. `seaweedfs-s3:8333` is the standalone `s3` Deployment;
  the embedded filer S3 is also published by the `seaweedfs-filer` Service, so the chart now
  disables it (`filer.s3.enabled=false`). Nothing used it.
- The server reads the identities file once at start. A changed Secret needs
  a rollout; it is never hot-reloaded. `s3_sync_server_config` (lib.sh) compares the sha256 of the
  config Secret with the pod-template annotation `narwhal.io/s3-config-sha256` and patches on any
  difference, so a repaired or earlier-written config still rolls the server.
- Loki/Tempo/CNPG already send signed requests (with `admin`/`admin`), so switching them to the new
  keys while the server is still anonymous does not change the request shape. Velero is held back
  until auth is on: signed uploads to an unauthenticated server are not universally safe
  (seaweedfs/seaweedfs#6262) and its repositories could never initialise anyway.
- ArgoCD syncs the whole chart from Gitea, so one push would flip SeaweedFS auth on and move the
  clients at the same instant. Push in two stages with the path arguments of
  `scripts/gitops/push-to-gitea.sh`.

- `scripts/cluster/08-4-storage.sh` refuses to start the transition on its own (D-s3ops-4, #414).
  `guard_s3_auth_cutover` (`lib.sh`, read-only) runs before the node label, the identities and
  helm. It allows a clean install (no `seaweedfs-s3` Deployment), a server that already runs a
  non-empty `-config=` in any container's `command` or `args`, and an anonymous server once the
  `monitoring/loki` and `monitoring/tempo` StatefulSets both reference `loki-s3-credentials` and
  `tempo-s3-credentials` through `envFrom` (step 2 done; a StatefulSet that does not exist is
  skipped). The Secrets alone do not count: `07-cnpg.sh` creates them before `08-4` runs on every
  full Phase 2 re-run. Otherwise it aborts with
  `existing SeaweedFS S3 has no -config; Loki/Tempo do not consume their S3 credential Secrets yet`:
  follow this runbook.
  An approved immediate cutover (accepting that Loki/Tempo may lose access until they are moved)
  sets `NARWHAL_ALLOW_S3_AUTH_CUTOVER=true` for that run. The override bypasses only this
  transition abort, never a `kubectl`, RBAC or parse error (including reading the StatefulSets). `envFrom` proves the
  workloads are wired, not that their pods restarted and loaded the keys: the stage 1 gate below does.
- The roll marker `narwhal.io/s3-config-sha256` is set by `kubectl patch`, which Argo CD self-heal
  would revert. The seaweedfs Application therefore lists exactly that annotation in
  `ignoreDifferences` with `RespectIgnoreDifferences=true` (D-s3ops-1, #419). Not yet verified on a
  live cluster: after step 3 confirm the annotation survives a self-heal (below).

## 0. Preconditions and baseline (read only)

```bash
git fetch origin && git checkout feat/seaweedfs-s3-auth     # or the merged main
kubectl -n storage get deploy seaweedfs-s3 -o jsonpath='{.status.readyReplicas}{"\n"}'
kubectl -n storage exec seaweedfs-filer-0 -- sh -c "echo 's3.bucket.list' | weed shell"   # note sizes
kubectl get backups.velero.io,schedules.velero.io -n storage; kubectl -n database get cluster narwhal-db
```

Gate: no Backup `InProgress`, `narwhal-db` Ready, all four buckets listed. Pick a window with no
scheduled backup (Velero 01:00-05:00, CNPG `narwhal-db-daily`).

## 1. Create the Secrets (server still anonymous, no client changes yet)

On master-1 with the repo checked out (replaces only the known `admin`/`admin` pair, once; keeps any
existing random key; fails closed and writes nothing on a partial pair or a read error):

```bash
export KUBECONFIG=/home/vagrant/.kube/config-local
bash -c 'set -euo pipefail; source scripts/common/lib.sh; ensure_s3_identities; echo "changed=${S3_IDENTITIES_CHANGED}"'
```

Two clients sharing one access key are resolved by rotating the later ones (SeaweedFS maps identities by access key, later wins); a Secret that exists but has empty or missing fields aborts instead of being regenerated.

Gate: output lists four identities as `generated` or `replacing the default`, `changed=true`, no
key printed. `kubectl -n storage get secret seaweedfs-s3-config velero-s3-credentials` and the
`monitoring`/`database` Secrets exist. Nothing is affected yet (the server ignores keys).

## 2. Roll Loki, Tempo and CNPG onto the new keys (stage 1 push)

```bash
scripts/gitops/push-to-gitea.sh "feat(storage): Loki/Tempo S3 keys from Secrets" \
  charts/narwhal-apps/templates/loki.yaml charts/narwhal-apps/templates/tempo.yaml
```

Do not push `seaweedfs.yaml` yet. Then restart so the env is read (env is read at start):

```bash
kubectl -n monitoring rollout restart statefulset/loki statefulset/tempo
kubectl -n monitoring rollout status statefulset/loki; kubectl -n monitoring rollout status statefulset/tempo
# CNPG reads credentials at pod start: rolling restart (replica first, then switchover)
kubectl -n database annotate cluster narwhal-db kubectl.kubernetes.io/restartedAt="$(date -u +%FT%TZ)" --overwrite
kubectl -n database get cluster narwhal-db -w     # until Ready, 2/2 instances
```

`narwhal-db.yaml` no longer carries the Secret, so `14-gitops-bootstrap.sh` cannot revert it. If
`kubectl -n monitoring get statefulset` shows different names, use the real ones.

Gate (server still anonymous, so this only proves the pods start with the Secret keys and that
archiving works): Loki and Tempo Ready, no `NoCredentialProviders`/`InvalidAccessKey` in their
logs, `narwhal-db` Ready. The in-tree `barmanObjectStore` passes its credentials to the archiver
without exporting `AWS_*` into the postgres container, so `env | grep AWS_` proves nothing. Use
WAL archiving progress on the current primary and an on-demand Backup instead (never print values):

```bash
PRIMARY=$(kubectl -n database get pod -l cnpg.io/cluster=narwhal-db,cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}')
pq() { kubectl -n database exec "$PRIMARY" -c postgres -- psql -U postgres -Atc "$1"; }
BEFORE=$(pq 'select last_archived_wal from pg_stat_archiver'); pq 'select pg_switch_wal()' >/dev/null; sleep 30
AFTER=$(pq 'select last_archived_wal from pg_stat_archiver'); [ "$AFTER" != "$BEFORE" ] && echo ARCHIVE-PROGRESS || echo ARCHIVE-STUCK
kubectl cnpg backup narwhal-db -n database --backup-name s3gate-$(date +%s)
kubectl -n database get backups.postgresql.cnpg.io -w      # newest object reaches phase completed
```

An anonymous-server pass does not prove the credentials work (an anonymous server accepts any
key). Repeat both checks after step 3, when authentication is on.

## 3. Enable authentication (stage 2 push) and restart the server

```bash
scripts/gitops/push-to-gitea.sh "feat(storage): authenticate SeaweedFS S3" \
  charts/narwhal-apps/templates/seaweedfs.yaml
kubectl -n devtools get app seaweedfs -o jsonpath='{.status.sync.status}{"\n"}'   # Synced
# the S3 server reads its identities once: roll it when the config differs from what the pods loaded
# (sha256 annotation on the pod template; also what 08-4 does)
bash -c 'set -euo pipefail; source scripts/common/lib.sh; s3_sync_server_config'
kubectl -n storage rollout status deployment/seaweedfs-s3
kubectl -n storage get deploy seaweedfs-s3 -o jsonpath='{.spec.template.spec.containers[0].command}{.spec.template.spec.containers[0].args}' | grep -c -- '-config=/etc/sw/seaweedfs_s3_config'   # the chart puts the flag in command, so read both
kubectl -n storage get pods -l app.kubernetes.io/component=filer -o jsonpath='{.items[0].spec.containers[0].args}' | grep -c -- '-s3'   # 0 (filer S3 off)
```

Gate: restart complete, the first grep prints `1`, the second `0`, and
`kubectl -n storage get svc seaweedfs-filer -o jsonpath='{.spec.ports[*].port}'` has no `8333`.
Also wait a few minutes (self-heal interval) and confirm the roll marker is still on the Deployment:
`kubectl -n storage get deploy seaweedfs-s3 -o jsonpath='{.spec.template.metadata.annotations}'` must
still show `narwhal.io/s3-config-sha256` and no old ReplicaSet may come back (D-s3ops-1).

## 4. Verify every client with real I/O

```bash
bash scripts/test/verify-backup.sh --section=seaweedfs   # anonymous request must be 403 (probed with python3 in a narwhal-db pod)
```

**Loki**: push and read back.

```bash
kubectl -n monitoring port-forward svc/loki 3100:3100 &
curl -fsS -XPOST localhost:3100/loki/api/v1/push -H 'Content-Type: application/json' \
  -d "{\"streams\":[{\"stream\":{\"job\":\"s3auth\"},\"values\":[[\"$(date +%s)000000000\",\"s3-auth-check\"]]}]}"
curl -fsS -G localhost:3100/loki/api/v1/query_range --data-urlencode 'query={job="s3auth"}' | grep -c s3-auth-check
kubectl -n monitoring logs statefulset/loki --since=10m | grep -ci 'AccessDenied\|InvalidAccessKey\|403'   # expect 0
```

Chunks reach the bucket only after a flush: `curl -XPOST localhost:3100/flush`, then confirm the
ruler/compactor lines have no 403 and `s3.bucket.list` shows `loki` growing.

**Tempo**: emit a trace (or let the platform traffic do it), query it, flush, check logs:
`kubectl -n monitoring logs statefulset/tempo --since=10m | grep -ci 'AccessDenied\|403'` is `0` and
`s3.bucket.list` shows `tempo` growing after `curl -XPOST localhost:3200/flush`.

**CNPG backup and WAL**:

```bash
kubectl cnpg backup narwhal-db -n database --backup-name s3auth-check
kubectl -n database get backups.postgresql.cnpg.io s3auth-check -w   # phase completed
kubectl -n database exec "$PRIMARY" -c postgres -- psql -U postgres -c 'select pg_switch_wal()'   # PRIMARY from the step 2 gate
kubectl -n database get cluster narwhal-db -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}{"\n"}'   # True
```

**Velero** (the actual goal): BSL, repository init and a real backup + restore of a test namespace.

```bash
kubectl -n storage get backupstoragelocation default -o jsonpath='{.status.phase}{"\n"}'   # Available
kubectl create ns s3auth-test
# the pod only sleeps: nothing in it can recreate the marker after a restore
kubectl -n s3auth-test run w --image=busybox:1.37 --restart=Never --overrides='{"spec":{"volumes":[{"name":"d","emptyDir":{}}],"containers":[{"name":"w","image":"busybox:1.37","command":["sleep","3600"],"volumeMounts":[{"name":"d","mountPath":"/data"}]}]}}'
kubectl -n s3auth-test wait --for=condition=Ready pod/w --timeout=120s
kubectl -n s3auth-test annotate pod w backup.velero.io/backup-volumes=d
TOKEN="$(openssl rand -hex 16)"                                  # random, written AFTER the pod runs
kubectl -n s3auth-test exec w -- sh -c "echo $TOKEN >/data/token"
velero backup create s3auth-test --include-namespaces s3auth-test --wait
velero backup describe s3auth-test --details | grep -i 'phase\|PodVolumeBackup\|Completed'
kubectl -n storage get backuprepository                  # Ready (was: never initialised)
kubectl delete ns s3auth-test --wait
velero restore create --from-backup s3auth-test --wait
kubectl -n s3auth-test wait --for=condition=Ready pod/w --timeout=180s
[ "$(kubectl -n s3auth-test exec w -- cat /data/token)" = "$TOKEN" ] && echo RESTORE-OK || echo RESTORE-FAIL
```

Use a Velero CLI that matches the server (v1.18.x); if it is not installed, create the `Backup` and
`Restore` custom resources in `storage` instead. Delete the test namespace and backup afterwards.

**Multipart, delete and cross-bucket deny** (`scripts/verify/s3-client-check.py`, needs `boto3`, present
in the CNPG postgres image; tested by R272). Keys go over stdin, never on the command line: access key,
secret key, own bucket, another client's bucket.

```bash
get() { kubectl -n "$1" get secret "$2" -o jsonpath="{.data.$3}" | base64 -d; echo; }
{ get monitoring loki-s3-credentials S3_ACCESS_KEY_ID; get monitoring loki-s3-credentials S3_SECRET_ACCESS_KEY; echo loki; echo tempo; } \
  | kubectl -n database exec -i narwhal-db-1 -c postgres -- python3 -c "$(cat scripts/verify/s3-client-check.py)"
```

Repeat with `tempo` (own bucket `tempo`), `velero` (`storage/velero-s3-credentials`: `access-key`,
`secret-key`, bucket `velero`) and `cnpg` (`database/cnpg-s3-credentials`, bucket `cnpg-backup`), each
with a different bucket as the fourth line. Six `PASS` lines per client are required; any `FAIL` or a
non-zero exit is a stop.

## 5. Finish

Re-enable nothing else; ArgoCD already tracks the chart. Run `./scripts/test/regression-check-kakao.sh --static`
and `scripts/test/verify-backup.sh`. Add the Gitea tag per the usual release flow
(`push-to-gitea.sh --tag`).

## Rollback

Rollback restores **anonymous access** and the original defect: Velero's kopia repositories fail again
and the endpoint is open to every pod. Use it only if a gate fails and cannot be fixed forward.

1. `scripts/gitops/push-to-gitea.sh "revert: SeaweedFS S3 auth off" charts/narwhal-apps/templates/seaweedfs.yaml`
   with `enableAuth: false` (keep `filer.s3.enabled: false`), then
   `kubectl -n storage rollout restart deployment/seaweedfs-s3` and wait for rollout.
2. Loki/Tempo/CNPG keep working: an anonymous server ignores their keys. Do not restore `admin`/`admin`.
3. The Secrets stay; a later retry of step 3 reuses them (`ensure_s3_identities` keeps random keys).
4. Remove the test objects (`s3auth/*`, backup `s3auth-test`, backup `s3auth-check`).

If only one client fails, fix that client's Secret (delete only that one Secret and rerun step 1, which
regenerates it and rewrites `seaweedfs-s3-config`), restart the S3 server and that client.

## Clean installs and Kakao

- Vagrant/Kakao clean installs: `07-cnpg.sh` calls `ensure_s3_identities` before the CNPG cluster;
  `08-4-storage.sh` calls it again before `helm upgrade --install seaweedfs` (auth on from the start)
  and installs Velero with `credentials.existingSecret`.
- `14-gitops-bootstrap.sh` aborts if `database/cnpg-s3-credentials` is missing and no longer applies one.
- Kakao ArgoCD reads the in-cluster Gitea, not GitHub: nothing here takes effect until pushed with
  `scripts/gitops/push-to-gitea.sh` (see the staging in step 2/3). `kubectl apply` of managed objects is
  reverted by selfHeal.
- Key rotation: delete one client Secret, rerun `ensure_s3_identities`, restart the S3 server and that client.
