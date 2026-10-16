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
  `rollout restart deployment/seaweedfs-s3`; it is never hot-reloaded.
- Loki/Tempo/CNPG already send signed requests (with `admin`/`admin`), so switching them to the new
  keys while the server is still anonymous does not change the request shape. Velero is held back
  until auth is on: signed uploads to an unauthenticated server are not universally safe
  (seaweedfs/seaweedfs#6262) and its repositories could never initialise anyway.
- ArgoCD syncs the whole chart from Gitea, so one push would flip SeaweedFS auth on and move the
  clients at the same instant. Push in two stages with the path arguments of
  `scripts/gitops/push-to-gitea.sh`.

## 0. Preconditions and baseline (read only)

```bash
git fetch origin && git checkout feat/seaweedfs-s3-auth     # or the merged main
kubectl -n storage get deploy seaweedfs-s3 -o jsonpath='{.status.readyReplicas}{"\n"}'
kubectl -n storage exec seaweedfs-filer-0 -- sh -c "echo 's3.bucket.list' | weed shell"   # note sizes
kubectl get backup,schedule -n storage; kubectl -n database get cluster narwhal-db
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

Gate (server still anonymous, so this only proves the pods start with the Secret keys): Loki and
Tempo Ready, no `NoCredentialProviders`/`InvalidAccessKey` in their logs, `narwhal-db` Ready, and
`kubectl -n database exec narwhal-db-1 -c postgres -- env | grep -c AWS_` is `2` or more.
Do not print the values.

## 3. Enable authentication (stage 2 push) and restart the server

```bash
scripts/gitops/push-to-gitea.sh "feat(storage): authenticate SeaweedFS S3" \
  charts/narwhal-apps/templates/seaweedfs.yaml
kubectl -n devtools get app seaweedfs -o jsonpath='{.status.sync.status}{"\n"}'   # Synced
kubectl -n storage rollout restart deployment/seaweedfs-s3
kubectl -n storage rollout status deployment/seaweedfs-s3
kubectl -n storage get deploy seaweedfs-s3 -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -c 'config=/etc/sw/seaweedfs_s3_config'
kubectl -n storage get pods -l app.kubernetes.io/component=filer -o jsonpath='{.items[0].spec.containers[0].args}' | grep -c -- '-s3'   # 0 (filer S3 off)
```

Gate: restart complete, the first grep prints `1`, the second `0`, and
`kubectl -n storage get svc seaweedfs-filer -o jsonpath='{.spec.ports[*].port}'` has no `8333`.

## 4. Verify every client with real I/O

```bash
bash scripts/test/verify-backup.sh --section=seaweedfs   # anonymous request must be 403
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
kubectl -n database get backup s3auth-check -w            # phase completed
kubectl -n database exec narwhal-db-1 -c postgres -- psql -U postgres -c 'select pg_switch_wal()'
kubectl -n database get cluster narwhal-db -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status}{"\n"}'   # True
```

**Velero** (the actual goal): BSL, repository init and a real backup + restore of a test namespace.

```bash
kubectl -n storage get backupstoragelocation default -o jsonpath='{.status.phase}{"\n"}'   # Available
kubectl create ns s3auth-test
kubectl -n s3auth-test run w --image=busybox:1.37 --restart=Never --overrides='{"spec":{"volumes":[{"name":"d","emptyDir":{}}],"containers":[{"name":"w","image":"busybox:1.37","command":["sh","-c","echo s3auth-ok >/data/f; sleep 3600"],"volumeMounts":[{"name":"d","mountPath":"/data"}]}]}}'
kubectl -n s3auth-test annotate pod w backup.velero.io/backup-volumes=d
velero backup create s3auth-test --include-namespaces s3auth-test --wait
velero backup describe s3auth-test --details | grep -i 'phase\|PodVolumeBackup\|Completed'
kubectl -n storage get backuprepository                  # Ready (was: never initialised)
kubectl delete ns s3auth-test --wait
velero restore create --from-backup s3auth-test --wait
kubectl -n s3auth-test exec w -- cat /data/f              # s3auth-ok
```

Use a Velero CLI that matches the server (v1.18.x); if it is not installed, create the `Backup` and
`Restore` custom resources in `storage` instead. Delete the test namespace and backup afterwards.

**Multipart, delete and cross-bucket deny** (needs `boto3`, present in the CNPG postgres image; keys
are passed on stdin, never on the command line):

```bash
PY='import sys,boto3,io
from botocore.config import Config
from botocore.exceptions import ClientError
ak,sk,b,other=[sys.stdin.readline().strip() for _ in range(4)]
mk=lambda sig=None,**k: boto3.client("s3",endpoint_url="http://seaweedfs-s3.storage.svc.cluster.local:8333",region_name="us-east-1",config=Config(signature_version=sig,s3={"addressing_style":"path"}),**k)
c=mk(aws_access_key_id=ak,aws_secret_access_key=sk)
c.put_object(Bucket=b,Key="s3auth/small",Body=b"x"); print("PASS put")
c.upload_fileobj(io.BytesIO(b"y"*(11*1024*1024)),b,"s3auth/big"); print("PASS multipart")
assert c.get_object(Bucket=b,Key="s3auth/small")["Body"].read()==b"x"; print("PASS get")
c.delete_objects(Bucket=b,Delete={"Objects":[{"Key":"s3auth/small"},{"Key":"s3auth/big"}]}); print("PASS delete")
def denied(f):
    try: f()
    except ClientError as e: return e.response["Error"]["Code"] in ("AccessDenied","403","SignatureDoesNotMatch")
    return False
print("PASS cross-bucket deny" if denied(lambda: c.put_object(Bucket=other,Key="s3auth/x",Body=b"x")) else "FAIL cross-bucket")
print("PASS anonymous deny" if denied(lambda: mk("unsigned").list_objects_v2(Bucket=b)) else "FAIL anonymous")'
get() { kubectl -n "$1" get secret "$2" -o jsonpath="{.data.$3}" | base64 -d; echo; }
{ get monitoring loki-s3-credentials S3_ACCESS_KEY_ID; get monitoring loki-s3-credentials S3_SECRET_ACCESS_KEY; echo loki; echo tempo; } \
  | kubectl -n database exec -i narwhal-db-1 -c postgres -- python3 -c "$PY"
```

Repeat with the `tempo`, `velero` (`storage/velero-s3-credentials`: `access-key`, `secret-key`) and
`cnpg` Secrets and their own bucket as the third line. Every line must be `PASS`; a `FAIL` or a
`ClientError` other than the expected denies is a stop.

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
