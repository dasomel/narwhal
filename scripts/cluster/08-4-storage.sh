#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle, never a public repository.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh

echo "=== Installing Storage Apps (SeaweedFS, OpenBao, Velero) ==="

export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# SeaweedFS (S3-compatible Object Storage)
#=========================================
echo "=== Installing SeaweedFS ==="

SEAWEEDFS_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install seaweedfs "$(chart seaweedfs)" \
    --force-conflicts \
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
    SEAWEEDFS_OK=true; break
  fi
  echo "SeaweedFS install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${SEAWEEDFS_OK}" != true ]; then
  echo "ERROR: SeaweedFS install failed after 5 attempts." >&2
  exit 1
fi

# Create S3 buckets for platform apps
# All apps using SeaweedFS S3: Tempo, Velero, Loki, CNPG backup
#
# D-bucket-race: `kubectl wait --for=condition=Ready` only confirms the filer
# container passed its own readiness probe, not that the embedded S3 API is
# actually accepting requests yet. Observed live (2026-07-05): the single
# fire-and-forget `weed shell` bucket-create below silently no-op'd for
# tempo/velero/loki (only cnpg-backup happened to land, apparently created
# later by CNPG's own first write) while the verify loop printed WARNs and
# the script still exited 0 — Tempo then crashlooped on "bucket does not
# exist", Loki logged continuous NoSuchBucket errors on every index/ruler
# sync, and Velero's backup-location went Unavailable. Retry each bucket's
# create+verify individually and fail loudly (not `|| true`) if one never
# lands, since a silently-missing bucket breaks three other components
# worse than a stopped install here would.
echo "Creating SeaweedFS S3 buckets..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=seaweedfs,app.kubernetes.io/component=filer -n storage --timeout=120s || true
for bucket in tempo velero loki cnpg-backup; do
  bucket_ready="false"
  for attempt in 1 2 3 4 5; do
    kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.create -name ${bucket}' | weed shell" >/dev/null 2>&1 || true
    BUCKET_LIST=$(kubectl exec -n storage seaweedfs-filer-0 -- sh -c "echo 's3.bucket.list' | weed shell" 2>/dev/null || true)
    if echo "${BUCKET_LIST}" | grep -q "${bucket}"; then
      echo "  + ${bucket} (attempt ${attempt}/5)"
      bucket_ready="true"
      break
    fi
    echo "  bucket '${bucket}' not confirmed yet, attempt ${attempt}/5, waiting 10s..."
    sleep 10
  done
  if [[ "${bucket_ready}" != "true" ]]; then
    echo "ERROR: bucket '${bucket}' could not be confirmed after 5 attempts" >&2
    exit 1
  fi
done
echo "S3 buckets ready"

echo "SeaweedFS installed"

#=========================================
# OpenBao (Secret Management)
#=========================================
echo "=== Installing OpenBao ==="

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

OPENBAO_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install openbao "$(chart openbao)" \
    --force-conflicts \
    --namespace storage \
    --create-namespace \
    --version 0.28.3 \
    -f /tmp/openbao-values.yaml; then
    OPENBAO_OK=true; break
  fi
  echo "OpenBao install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${OPENBAO_OK}" != true ]; then
  echo "ERROR: OpenBao install failed after 5 attempts." >&2
  exit 1
fi

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
rbac:
  create: false
  clusterAdministrator: false
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
# D2: Keep each schedule's includedResources equal to the generated READ inventory.
# D3: events and coordination Leases stay excluded from that inventory.
schedules:
  daily-full:
    disabled: false
    schedule: 0 2 * * *
    template:
      ttl: 168h
      includedResources: &velero_included
      - alertmanagerconfigs.monitoring.coreos.com
      - alertmanagers.monitoring.coreos.com
      - alloys.collectors.grafana.com
      - apiservices.apiregistration.k8s.io
      - apisixclusterconfigs.apisix.apache.org
      - apisixconsumers.apisix.apache.org
      - apisixglobalrules.apisix.apache.org
      - apisixpluginconfigs.apisix.apache.org
      - apisixroutes.apisix.apache.org
      - apisixtlses.apisix.apache.org
      - apisixupstreams.apisix.apache.org
      - applications.argoproj.io
      - applicationsets.argoproj.io
      - appprojects.argoproj.io
      - authorizationpolicies.security.istio.io
      - backups.postgresql.cnpg.io
      - bfdprofiles.metallb.io
      - bgpadvertisements.metallb.io
      - bgppeers.metallb.io
      - bgpsessionstates.frrk8s.metallb.io
      - certificaterequests.cert-manager.io
      - certificates.cert-manager.io
      - challenges.acme.cert-manager.io
      - ciliumcidrgroups.cilium.io
      - ciliumclusterwideenvoyconfigs.cilium.io
      - ciliumclusterwidenetworkpolicies.cilium.io
      - ciliumendpoints.cilium.io
      - ciliumenvoyconfigs.cilium.io
      - ciliumgatewayclassconfigs.cilium.io
      - ciliumidentities.cilium.io
      - ciliuml2announcementpolicies.cilium.io
      - ciliumloadbalancerippools.cilium.io
      - ciliumnetworkpolicies.cilium.io
      - ciliumnodeconfigs.cilium.io
      - ciliumnodes.cilium.io
      - ciliumpodippools.cilium.io
      - cleanuppolicies.kyverno.io
      - clustercleanuppolicies.kyverno.io
      - clusterephemeralreports.reports.kyverno.io
      - clusterimagecatalogs.postgresql.cnpg.io
      - clusterissuers.cert-manager.io
      - clusterpolicies.kyverno.io
      - clusterpolicyreports.wgpolicyk8s.io
      - clusterrolebindings.rbac.authorization.k8s.io
      - clusterroles.rbac.authorization.k8s.io
      - clusters.postgresql.cnpg.io
      - communities.metallb.io
      - configmaps
      - configurationstates.metallb.io
      - controllerrevisions.apps
      - cronjobs.batch
      - csidrivers.storage.k8s.io
      - csinodes.storage.k8s.io
      - csistoragecapacities.storage.k8s.io
      - customresourcedefinitions.apiextensions.k8s.io
      - daemonsets.apps
      - databases.postgresql.cnpg.io
      - deletingpolicies.policies.kyverno.io
      - deployments.apps
      - destinationrules.networking.istio.io
      - deviceclasses.resource.k8s.io
      - endpoints
      - endpointslices.discovery.k8s.io
      - envoyfilters.networking.istio.io
      - ephemeralreports.reports.kyverno.io
      - failoverquorums.postgresql.cnpg.io
      - flowschemas.flowcontrol.apiserver.k8s.io
      - frrconfigurations.frrk8s.metallb.io
      - frrk8sconfigurations.frrk8s.metallb.io
      - frrnodestates.frrk8s.metallb.io
      - gatewayclasses.gateway.networking.k8s.io
      - gateways.gateway.networking.k8s.io
      - gateways.networking.istio.io
      - generatingpolicies.policies.kyverno.io
      - globalcontextentries.kyverno.io
      - grpcroutes.gateway.networking.k8s.io
      - horizontalpodautoscalers.autoscaling
      - httproutes.gateway.networking.k8s.io
      - imagecatalogs.postgresql.cnpg.io
      - imagevalidatingpolicies.policies.kyverno.io
      - ingressclasses.networking.k8s.io
      - ingresses.networking.k8s.io
      - ipaddresses.networking.k8s.io
      - ipaddresspools.metallb.io
      - issuers.cert-manager.io
      - jobs.batch
      - keycloakrealmimports.k8s.keycloak.org
      - keycloaks.k8s.keycloak.org
      - l2advertisements.metallb.io
      - limitranges
      - mutatingpolicies.policies.kyverno.io
      - mutatingwebhookconfigurations.admissionregistration.k8s.io
      - namespaceddeletingpolicies.policies.kyverno.io
      - namespacedgeneratingpolicies.policies.kyverno.io
      - namespacedimagevalidatingpolicies.policies.kyverno.io
      - namespacedmutatingpolicies.policies.kyverno.io
      - namespacedvalidatingpolicies.policies.kyverno.io
      - namespaces
      - networkpolicies.networking.k8s.io
      - orders.acme.cert-manager.io
      - peerauthentications.security.istio.io
      - persistentvolumeclaims
      - persistentvolumes
      - poddisruptionbudgets.policy
      - podmonitors.monitoring.coreos.com
      - pods
      - podtemplates
      - policies.kyverno.io
      - policyexceptions.kyverno.io
      - policyexceptions.policies.kyverno.io
      - policyreports.wgpolicyk8s.io
      - poolers.postgresql.cnpg.io
      - priorityclasses.scheduling.k8s.io
      - prioritylevelconfigurations.flowcontrol.apiserver.k8s.io
      - probes.monitoring.coreos.com
      - prometheusagents.monitoring.coreos.com
      - prometheuses.monitoring.coreos.com
      - prometheusrules.monitoring.coreos.com
      - proxyconfigs.networking.istio.io
      - publications.postgresql.cnpg.io
      - quotapolicies.quota.nfs.io
      - referencegrants.gateway.networking.k8s.io
      - replicasets.apps
      - replicationcontrollers
      - requestauthentications.security.istio.io
      - resourceclaims.resource.k8s.io
      - resourceclaimtemplates.resource.k8s.io
      - resourcequotas
      - resourceslices.resource.k8s.io
      - rolebindings.rbac.authorization.k8s.io
      - roles.rbac.authorization.k8s.io
      - runtimeclasses.node.k8s.io
      - scheduledbackups.postgresql.cnpg.io
      - scrapeconfigs.monitoring.coreos.com
      - secrets
      - serviceaccounts
      - servicebgpstatuses.metallb.io
      - servicecidrs.networking.k8s.io
      - serviceentries.networking.istio.io
      - servicel2statuses.metallb.io
      - servicemonitors.monitoring.coreos.com
      - services
      - sidecars.networking.istio.io
      - statefulsets.apps
      - storageclasses.storage.k8s.io
      - subscriptions.postgresql.cnpg.io
      - telemetries.telemetry.istio.io
      - thanosrulers.monitoring.coreos.com
      - trafficextensions.extensions.istio.io
      - updaterequests.kyverno.io
      - validatingadmissionpolicies.admissionregistration.k8s.io
      - validatingadmissionpolicybindings.admissionregistration.k8s.io
      - validatingpolicies.policies.kyverno.io
      - validatingwebhookconfigurations.admissionregistration.k8s.io
      - virtualservices.networking.istio.io
      - volumeattachments.storage.k8s.io
      - volumeattributesclasses.storage.k8s.io
      - wasmplugins.extensions.istio.io
      - workloadentries.networking.istio.io
      - workloadgroups.networking.istio.io
      includedNamespaces:
      - '*'
      excludedNamespaces:
      - kube-system
      - storage
      - monitoring
      includeClusterResources: true
      storageLocation: default
      defaultVolumesToFsBackup: true
  daily-databases:
    disabled: false
    schedule: 0 1 * * *
    template:
      ttl: 336h
      includedResources: *velero_included
      includedNamespaces:
      - iam
      - devtools
      labelSelector:
        matchLabels:
          cnpg.io/cluster: ''
      includeClusterResources: false
      storageLocation: default
      defaultVolumesToFsBackup: true
  daily-gitea:
    disabled: false
    schedule: 0 3 * * *
    template:
      ttl: 336h
      includedResources: *velero_included
      includedNamespaces:
      - devtools
      includeClusterResources: false
      storageLocation: default
      defaultVolumesToFsBackup: true
  daily-harbor:
    disabled: false
    schedule: 0 4 * * *
    template:
      ttl: 168h
      includedResources: *velero_included
      includedNamespaces:
      - devtools
      includeClusterResources: false
      storageLocation: default
      defaultVolumesToFsBackup: true
  daily-openbao:
    disabled: false
    schedule: 0 5 * * *
    template:
      ttl: 336h
      includedResources: *velero_included
      includedNamespaces:
      - storage
      includeClusterResources: false
      storageLocation: default
      defaultVolumesToFsBackup: true
EOF

# Apply the same RBAC source before Helm creates velero-server, so its ServiceAccount
# never starts without the dedicated restore permissions bound to it.
if [[ ! -f /home/vagrant/configs/gitops/resources/velero-server-rbac.yaml ]]; then
  echo "ERROR: Velero RBAC manifest is missing from synced GitOps resources" >&2
  exit 1
fi
# roleRef is immutable. Remove the chart's legacy binding only when it still grants
# cluster-admin; leave any same-named binding with a different roleRef untouched.
LEGACY_VELERO_ROLE_REF="$(kubectl get clusterrolebinding velero-server \
  -o jsonpath='{.roleRef.name}' 2>/dev/null || true)"
if [[ "${LEGACY_VELERO_ROLE_REF}" == cluster-admin ]]; then
  kubectl delete clusterrolebinding velero-server
fi
kubectl apply -f /home/vagrant/configs/gitops/resources/velero-server-rbac.yaml

VELERO_OK=false
for attempt in 1 2 3 4 5; do
  if helm upgrade --install velero "$(chart velero)" \
    --force-conflicts \
    --namespace storage \
    --create-namespace \
    --version 12.0.3 \
    -f /tmp/velero-values.yaml; then
    VELERO_OK=true; break
  fi
  echo "Velero install attempt ${attempt}/5 failed, waiting 15s..."
  sleep 15
done
if [ "${VELERO_OK}" != true ]; then
  echo "ERROR: Velero install failed after 5 attempts." >&2
  exit 1
fi

rm /tmp/velero-values.yaml
echo "Velero installed"

echo "=== Storage Apps Installation Complete ==="
