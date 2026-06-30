#!/bin/bash
set -euo pipefail

echo "=== Installing Storage Apps (SeaweedFS, OpenBao, Velero) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# SeaweedFS (S3-compatible Object Storage)
#=========================================
echo "=== Installing SeaweedFS ==="
for attempt in 1 2 3 4 5; do
  if helm repo add seaweedfs https://seaweedfs.github.io/seaweedfs/helm && helm repo update seaweedfs; then
    break
  fi
  echo "Helm repo seaweedfs attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

for attempt in 1 2 3 4 5; do
  if helm upgrade --install seaweedfs seaweedfs/seaweedfs \
    --namespace storage \
    --create-namespace \
    --version 4.34.0 \
    --set global.storageClass=nfs-csi \
    --set master.enabled=true \
    --set master.replicas=1 \
    --set master.data.type=persistentVolumeClaim \
    --set master.data.size=1Gi \
    --set master.data.storageClass=nfs-csi \
    --set volume.enabled=true \
    --set volume.replicas=1 \
    --set volume.data.type=persistentVolumeClaim \
    --set volume.data.size=50Gi \
    --set volume.data.storageClass=nfs-csi \
    --set filer.enabled=true \
    --set filer.replicas=1 \
    --set filer.data.type=persistentVolumeClaim \
    --set filer.data.size=5Gi \
    --set filer.data.storageClass=nfs-csi \
    --set filer.s3.enabled=true \
    --set filer.s3.port=8333 \
    --set filer.s3.allowEmptyFolder=true \
    --set s3.enabled=true; then
    break
  fi
  echo "SeaweedFS install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# Create S3 buckets for platform apps
# All apps using SeaweedFS S3: Tempo, Velero, Loki, CNPG backup
echo "Creating SeaweedFS S3 buckets..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=seaweedfs,app.kubernetes.io/component=filer -n storage --timeout=120s || true
for bucket in tempo velero loki cnpg-backup; do
  echo "  Creating bucket: ${bucket}"
  kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.create -name ${bucket}' | weed shell" 2>/dev/null || true
done

# Verify buckets were created
echo "Verifying S3 buckets..."
BUCKET_LIST=$(kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.list' | weed shell" 2>/dev/null || true)
for bucket in tempo velero loki cnpg-backup; do
  if echo "${BUCKET_LIST}" | grep -q "${bucket}"; then
    echo "  + ${bucket}"
  else
    echo "  - ${bucket} - WARN: bucket may not exist"
  fi
done
echo "S3 buckets ready"

echo "SeaweedFS installed"

#=========================================
# OpenBao (Secret Management)
#=========================================
echo "=== Installing OpenBao ==="
for attempt in 1 2 3 4 5; do
  if helm repo add openbao https://openbao.github.io/openbao-helm && helm repo update openbao; then
    break
  fi
  echo "Helm repo openbao attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# OpenBao runs a TLS listener (raft storage). The server cert MUST exist before the
# pod starts, otherwise it crash-loops on missing tls_cert_file. Issue it from the
# cluster CA (narwhal-ca-issuer, created in 08-1-networking) and wait for the secret.
kubectl create namespace storage --dry-run=client -o yaml | kubectl apply -f -
cat <<'EOF' | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: openbao-tls
  namespace: storage
spec:
  secretName: openbao-tls
  issuerRef:
    name: narwhal-ca-issuer
    kind: ClusterIssuer
  dnsNames:
    - openbao.storage.svc.cluster.local
    - openbao.storage.svc
    - openbao
    - openbao-0.openbao-internal
    - localhost
  ipAddresses:
    - 127.0.0.1
EOF
echo "Waiting for openbao-tls certificate..."
kubectl wait --for=condition=Ready certificate/openbao-tls -n storage --timeout=120s 2>/dev/null || true

# Helm values mirror gitops/apps/openbao.yaml so a clean install matches what ArgoCD
# manages afterward (no config drift / reseal on the first GitOps sync):
#  - global.tlsDisable=false  -> BAO_ADDR/BAO_API_ADDR + readiness probe use https
#                                (else pod NotReady -> Service loses endpoints)
#  - standalone.config        -> raft storage + TLS listener (the chart default is
#                                file storage + tls_disable, which does NOT match)
#  - extraLabels dataplane-mode=none -> opt out of the ambient mesh (storage ns is
#                                ambient; ztunnel otherwise resets plain-TLS clients)
#  - BAO_UI=true              -> server serves /ui/
#  - volumes/volumeMounts     -> mount the openbao-tls cert at /openbao/tls
cat > /tmp/openbao-values.yaml <<'EOF'
global:
  tlsDisable: false
server:
  image:
    tag: "2.5.4"
  extraLabels:
    istio.io/dataplane-mode: none
  extraEnvironmentVars:
    BAO_UI: "true"
  standalone:
    enabled: true
    config: |
      ui = true
      disable_mlock = true

      listener "tcp" {
        address = "[::]:8200"
        cluster_address = "[::]:8201"
        tls_cert_file = "/openbao/tls/tls.crt"
        tls_key_file = "/openbao/tls/tls.key"
      }

      storage "raft" {
        path = "/openbao/data"
      }
  ha:
    enabled: false
    replicas: 1
    raft:
      enabled: true
  dataStorage:
    enabled: true
    storageClass: nfs-csi
    size: 10Gi
  auditStorage:
    enabled: true
    storageClass: nfs-csi
    size: 5Gi
  volumes:
    - name: userconfig-openbao-tls
      secret:
        secretName: openbao-tls
  volumeMounts:
    - name: userconfig-openbao-tls
      mountPath: /openbao/tls
      readOnly: true
ui:
  enabled: true
EOF

for attempt in 1 2 3 4 5; do
  if helm upgrade --install openbao openbao/openbao \
    --namespace storage \
    --create-namespace \
    --version 0.28.3 \
    -f /tmp/openbao-values.yaml; then
    break
  fi
  echo "OpenBao install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# Auto init + unseal OpenBao. The listener is HTTPS with the self-signed cluster CA,
# so every bao CLI call needs -tls-skip-verify (BAO_ADDR is https via tlsDisable=false).
#
# Wait for openbao-0 to be Running (not necessarily Ready — a fresh sealed/uninitialised
# pod fails its readiness probe so it never becomes Ready, which is expected here).
echo "Waiting for OpenBao pod to be Running..."
for i in $(seq 1 36); do
  POD_PHASE=$(kubectl get pod openbao-0 -n storage \
    -o jsonpath='{.status.phase}' 2>/dev/null || true)
  if [ "${POD_PHASE}" = "Running" ]; then
    break
  fi
  echo "  [${i}/36] phase=${POD_PHASE}, retrying in 5s..."
  sleep 5
done

# Query bao status in a pipefail-safe subshell: `bao status` exits 2 when sealed
# (non-zero), which under set -o pipefail would make the whole pipeline fail and
# cause `|| echo ""` to swallow the output — leaving OPENBAO_INITIALIZED="" and
# silently falling into the "could not determine" else branch.  Run the JSON query
# in a subshell that suppresses pipefail so the python3 parser always gets the JSON.
BAO_STATUS_JSON=$(set +o pipefail; \
  kubectl exec openbao-0 -n storage -- \
    bao status -tls-skip-verify -format=json 2>/dev/null || true)
OPENBAO_INITIALIZED=$(printf '%s' "${BAO_STATUS_JSON}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("True" if d.get("initialized") else "False")' \
  2>/dev/null || echo "")

if [ "${OPENBAO_INITIALIZED}" = "True" ]; then
  echo "OpenBao already initialized, checking seal status..."
  OPENBAO_SEALED=$(printf '%s' "${BAO_STATUS_JSON}" \
    | python3 -c 'import sys,json; print("true" if json.load(sys.stdin).get("sealed") else "false")' \
    2>/dev/null || echo "true")
  if [ "${OPENBAO_SEALED}" = "true" ]; then
    echo "OpenBao is sealed — retrieving unseal key from openbao-init secret..."
    UNSEAL_KEY=$(kubectl get secret openbao-init -n storage \
      -o jsonpath='{.data.unseal_keys_b64}' 2>/dev/null \
      | base64 -d 2>/dev/null || echo "")
    if [ -n "${UNSEAL_KEY}" ]; then
      echo "Unsealing OpenBao..."
      kubectl exec openbao-0 -n storage -- \
        bao operator unseal -tls-skip-verify "${UNSEAL_KEY}" || true
    else
      echo "WARN: OpenBao initialized but unseal key not found in openbao-init secret"
    fi
  else
    echo "OpenBao already unsealed — nothing to do"
  fi
elif [ "${OPENBAO_INITIALIZED}" = "False" ]; then
  echo "Initializing OpenBao (key-shares=1 key-threshold=1)..."
  INIT_JSON=$(kubectl exec openbao-0 -n storage -- \
    bao operator init -tls-skip-verify \
    -key-shares=1 -key-threshold=1 -format=json 2>/dev/null || echo "")
  if [ -n "${INIT_JSON}" ]; then
    UNSEAL_KEY=$(printf '%s' "${INIT_JSON}" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["unseal_keys_b64"][0])')
    ROOT_TOKEN=$(printf '%s' "${INIT_JSON}" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["root_token"])')
    echo "Unsealing OpenBao..."
    kubectl exec openbao-0 -n storage -- \
      bao operator unseal -tls-skip-verify "${UNSEAL_KEY}" || true
    echo "Saving credentials to openbao-init secret (consumed by auto-unseal CronJob)..."
    kubectl create secret generic openbao-init -n storage \
      --from-literal=unseal_keys_b64="${UNSEAL_KEY}" \
      --from-literal=root_token="${ROOT_TOKEN}" \
      --dry-run=client -o yaml | kubectl apply -f -
    echo "OpenBao initialized and unsealed"
  else
    echo "WARN: OpenBao init failed — manual init required"
  fi
else
  echo "WARN: Could not determine OpenBao state (got: '${OPENBAO_INITIALIZED}') — skipping init"
fi

rm -f /tmp/openbao-values.yaml
echo "OpenBao installed"

# NOTE: an auto-unseal CronJob (gitops/apps/openbao-unseal.yaml + resources/openbao-unseal.yaml)
# re-unseals OpenBao after any restart (Shamir seal, no KMS). It is deployed by ArgoCD
# during GitOps bootstrap (step 14), reading the openbao-init secret created above.

#=========================================
# Velero (Backup & Restore)
#=========================================
echo "=== Installing Velero ==="
for attempt in 1 2 3 4 5; do
  if helm repo add vmware-tanzu https://vmware-tanzu.github.io/helm-charts && helm repo update vmware-tanzu; then
    break
  fi
  echo "Helm repo vmware-tanzu attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

# S3 credentials — prefer environment overrides, fall back to Secret or defaults
S3_ACCESS_KEY="${S3_ACCESS_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d || echo "admin")}"
# D-velero: default to "admin" (matches S3_ACCESS_KEY), not empty. An empty
# aws_secret_access_key makes the AWS SDK fall through to the EC2 IMDS provider
# (169.254.169.254), which times out every BSL reconcile and crashloops velero.
# SeaweedFS S3 is anonymous here, so any non-empty key satisfies the SDK.
S3_SECRET_KEY="${S3_SECRET_KEY:-$(kubectl get secret velero-s3-credentials -n storage \
  -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d || echo "admin")}"

# Persist S3 credentials into a dedicated Secret (idempotent)
# 'cloud' key uses AWS credentials file format required by velero-plugin-for-aws
# (referenced by gitops/apps/velero.yaml as existingSecret: velero-s3-credentials)
kubectl create namespace storage --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic velero-s3-credentials \
  --from-literal=access-key="${S3_ACCESS_KEY}" \
  --from-literal=secret-key="${S3_SECRET_KEY}" \
  --from-literal=cloud="[default]
aws_access_key_id = ${S3_ACCESS_KEY}
aws_secret_access_key = ${S3_SECRET_KEY}" \
  -n storage --dry-run=client -o yaml | kubectl apply -f -

cat > /tmp/velero-values.yaml << EOF
initContainers:
  - name: velero-plugin-for-aws
    image: velero/velero-plugin-for-aws:v1.14.1
    volumeMounts:
      - mountPath: /target
        name: plugins
configuration:
  backupStorageLocation:
    - name: default
      provider: aws
      bucket: velero
      config:
        region: us-east-1
        s3ForcePathStyle: "true"
        s3Url: http://seaweedfs-s3.storage.svc.cluster.local:8333
  volumeSnapshotLocation:
    - name: default
      provider: aws
      config:
        region: us-east-1
  defaultBackupStorageLocation: default
  uploaderType: kopia
  defaultVolumesToFsBackup: true
credentials:
  useSecret: true
  secretContents:
    cloud: |
      [default]
      aws_access_key_id = ${S3_ACCESS_KEY}
      aws_secret_access_key = ${S3_SECRET_KEY}
snapshotsEnabled: false
# Disable CRD upgrade hook - alpine/k8s musl binaries can't exec in velero glibc container
upgradeCRDs: false
kubectl:
  image:
    # docker.io: registry.k8s.io/kubectl은 distroless(shell 없음), ghcr.io 대안 부재
    repository: docker.io/alpine/k8s
    tag: "1.31.4"
deployNodeAgent: true
nodeAgent:
  podVolumePath: /var/lib/kubelet/pods
  privileged: true
  tolerations:
    - key: node-role.kubernetes.io/control-plane
      operator: Exists
      effect: NoSchedule
EOF

for attempt in 1 2 3 4 5; do
  if helm upgrade --install velero vmware-tanzu/velero \
    --namespace storage \
    --create-namespace \
    --version 12.0.3 \
    -f /tmp/velero-values.yaml; then
    break
  fi
  echo "Velero install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done

rm /tmp/velero-values.yaml
echo "Velero installed"

echo "=== Storage Apps Installation Complete ==="
