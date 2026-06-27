# Keycloak → Authentik Migration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Keycloak (Operator + kcadm.sh CLI) with Authentik (Helm + REST API), maintaining the same OIDC architecture: 2 providers (kubernetes for K8s API server, apisix for APISIX gateway), same groups/users, same K8s RBAC.

**Architecture:** Authentik deployed via Helm in `iam` namespace, backed by narwhal-db (CNPG, primary owner changed from `keycloak` to `authentik`) and standalone Valkey 8 (Redis alternative, Bitnami 배제). Configuration via Authentik REST API (`/api/v3/`) using a bootstrap token injected at deploy time as a K8s Secret. APISIX keeps the same `openid-connect` plugin pattern; only discovery URLs and the IdP route change.

**Tech Stack:** Authentik 2025.4.0 (`ghcr.io/goauthentik/server`, ARM64 지원), Valkey 8 (`docker.io/valkey/valkey:8-alpine`), CNPG narwhal-db (existing, primary DB renamed), Authentik REST API `/api/v3/`, APISIX `openid-connect` plugin, K8s OIDC (`--oidc-issuer-url`)

---

## File Map

### Create
| File | Purpose |
|------|---------|
| `gitops/apps/authentik.yaml` | ArgoCD Application: Authentik Helm chart |
| `scripts/cluster/11-authentik.sh` | Valkey + Helm install + ApisixRoute bootstrap |
| `scripts/cluster/11-2-authentik-config.sh` | REST API: groups, users, providers, applications |

### Modify
| File | Change |
|------|--------|
| `gitops/resources/narwhal-db.yaml` | Primary owner `keycloak` → `authentik`; ExternalName service rename |
| `scripts/cluster/07-cnpg.sh` | Credentials: `username=keycloak` → `username=authentik`; ExternalName service rename |
| `scripts/cluster/06-phase2-start.sh` | Replace 11-1/11-2/11-3-keycloak-*.sh with 11-authentik.sh + 11-2-authentik-config.sh |
| `scripts/cluster/11-4-keycloak-apiserver.sh` | OIDC issuer URL: keycloak realm → authentik application/o/kubernetes/ |
| `gitops/resources/apisix-routes.yaml` | Keycloak route → Authentik route; all discovery URLs updated |
| `scripts/cluster/10-dnsmasq.sh` | Hairpin zone: `keycloak.${DOMAIN}` → `authentik.${DOMAIN}` |
| `VERSIONS.md` | Remove Keycloak v26.5.3; add Authentik 2025.4.0 + Valkey 8 |

### Delete
| File | Reason |
|------|--------|
| `scripts/cluster/11-1-keycloak-operator.sh` | Replaced by 11-authentik.sh |
| `scripts/cluster/11-2-keycloak-realm.sh` | Replaced by 11-2-authentik-config.sh |
| `scripts/cluster/11-3-keycloak-clients.sh` | Replaced by 11-2-authentik-config.sh |

---

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Redis alternative | Valkey 8 (docker.io/valkey/valkey:8-alpine) | Bitnami 사용 금지, Valkey는 Redis 포크 (BSD 라이선스) |
| OIDC providers | 2개: `kubernetes` (public) + `apisix` (confidential) | K8s API server용 + APISIX gateway용 분리 |
| Groups claim | Custom scope mapping (Python expression) | Authentik built-in profile scope에 groups 클레임 없음 |
| Bootstrap | `AUTHENTIK_BOOTSTRAP_TOKEN` env var → K8s Secret | Operator 없이 초기 API 토큰 주입 |
| DB primary owner | `authentik` (new cluster) | Dev env: fresh provision이 표준 |
| OIDC issuer (K8s) | `https://authentik.${DOMAIN}/application/o/kubernetes/` | Authentik application slug 기반 |
| OIDC issuer (APISIX) | `https://authentik.${DOMAIN}/application/o/apisix/` | 앱별 별도 provider slug |

---

## Migration Note for Existing Clusters

> 이 태스크들은 **신규 프로비저닝** 기준으로 작성되었습니다.
>
> 기존 narwhal-db가 실행 중이라면 CNPG는 이미 `keycloak` primary owner로 초기화되어 있습니다. 이 경우:
>
> **Option A (권장 — dev env):** `vagrant destroy -f && vagrant up` (클러스터 재생성)
>
> **Option B (인플레이스):** Task 1에서 narwhal-db.yaml의 bootstrap 변경은 skip하고, 기존 클러스터에 직접 psql로 authentik 유저/DB 추가:
> ```bash
> PRIMARY=$(kubectl get pods -n database -l role=primary -o jsonpath='{.items[0].metadata.name}')
> PASS=$(kubectl get secret narwhal-db-credentials -n database -o jsonpath='{.data.password}' | base64 -d)
> kubectl exec -n database "${PRIMARY}" -- psql -U keycloak -d keycloak -c "CREATE USER authentik WITH PASSWORD '${PASS}';"
> kubectl exec -n database "${PRIMARY}" -- psql -U keycloak -d keycloak -c "CREATE DATABASE authentik OWNER authentik;"
> ```

---

## Tasks

### Task 1: Database Layer — keycloak → authentik primary owner

**Files:**
- Modify: `gitops/resources/narwhal-db.yaml`
- Modify: `scripts/cluster/07-cnpg.sh`

**Context:** CNPG `bootstrap.initdb.database/owner` 설정이 primary PostgreSQL 사용자를 결정합니다. `keycloak` → `authentik`으로 변경. `07-cnpg.sh`의 변수명과 ExternalName 서비스도 함께 업데이트.

- [ ] **Step 1: Update narwhal-db.yaml**

  `gitops/resources/narwhal-db.yaml`에서 3곳 변경:

  1. `narwhal-db-credentials` Secret (`username` 필드):
  ```yaml
  stringData:
    username: authentik        # was: keycloak
    password: authentik-db-password
  ```

  2. `bootstrap.initdb` 블록 (database, owner 필드 + postInitSQL에서 keycloak 줄 제거):
  ```yaml
  bootstrap:
    initdb:
      database: authentik      # was: keycloak
      owner: authentik         # was: keycloak
      dataChecksums: true
      secret:
        name: narwhal-db-credentials
      postInitSQL:
        - CREATE USER harbor WITH PASSWORD 'harbor-db-password'
        - CREATE USER gitea WITH PASSWORD 'gitea-db-password'
        - CREATE DATABASE harbor OWNER harbor
        - CREATE DATABASE gitea OWNER gitea
        - GRANT ALL PRIVILEGES ON DATABASE harbor TO harbor
        - GRANT ALL PRIVILEGES ON DATABASE gitea TO gitea
  ```
  (authentik은 primary owner이므로 postInitSQL에 별도 CREATE USER/DATABASE 불필요)

  3. ExternalName 서비스: `keycloak-db-rw` → `authentik-db-rw` (이름만 변경):
  ```yaml
  apiVersion: v1
  kind: Service
  metadata:
    name: authentik-db-rw      # was: keycloak-db-rw
    namespace: iam
  spec:
    type: ExternalName
    externalName: narwhal-db-rw.database.svc.cluster.local
    ports:
      - port: 5432
  ```

- [ ] **Step 2: Update 07-cnpg.sh — credentials + ExternalName**

  `scripts/cluster/07-cnpg.sh`에서 4곳 변경:

  1. Secret 생성 블록 (line ~68): `KEYCLOAK_DB_PASS` → `AUTHENTIK_DB_PASS`, `username=keycloak` → `username=authentik`
  ```bash
  if ! kubectl get secret narwhal-db-credentials -n database &>/dev/null; then
    AUTHENTIK_DB_PASS=$(generate_password)
    HARBOR_DB_PASS=$(generate_password)
    GITEA_DB_PASS=$(generate_password)
    kubectl create secret generic narwhal-db-credentials \
      --from-literal=username=authentik \
      --from-literal=password="${AUTHENTIK_DB_PASS}" \
      --from-literal=harbor-password="${HARBOR_DB_PASS}" \
      --from-literal=gitea-password="${GITEA_DB_PASS}" \
      -n database
  ```

  2. Secret 조회 블록 (line ~80): `KEYCLOAK_DB_PASS` → `AUTHENTIK_DB_PASS`
  ```bash
  else
    AUTHENTIK_DB_PASS=$(kubectl get secret narwhal-db-credentials -n database \
      -o jsonpath='{.data.password}' | base64 -d)
  ```

  3. Cluster manifest의 `bootstrap.initdb` 블록: `database: keycloak` → `database: authentik`, `owner: keycloak` → `owner: authentik`, postInitSQL에서 keycloak 줄 제거, `${HARBOR_DB_PASS}`/`${GITEA_DB_PASS}` 유지.

  4. ExternalName 서비스 블록 (line ~257): `name: keycloak-db-rw` → `name: authentik-db-rw`
  ```bash
  cat <<EOF | kubectl apply -f -
  apiVersion: v1
  kind: Service
  metadata:
    name: authentik-db-rw
    namespace: iam
  spec:
    type: ExternalName
    externalName: narwhal-db-rw.database.svc.cluster.local
    ports:
      - port: 5432
  EOF
  ```

  5. 하단 echo 요약: `keycloak` → `authentik` 레퍼런스 업데이트.

- [ ] **Step 3: Commit**
  ```bash
  git add gitops/resources/narwhal-db.yaml scripts/cluster/07-cnpg.sh
  git commit -m "feat(db): change primary CNPG owner from keycloak to authentik"
  ```

---

### Task 2: Authentik GitOps ArgoCD Application

**Files:**
- Create: `gitops/apps/authentik.yaml`

**Context:** Authentik Helm chart (`https://charts.goauthentik.io`). External PostgreSQL (narwhal-db) + external Redis (Valkey). 이미지는 `ghcr.io` (ARM64 지원). Secrets는 `11-authentik.sh` 스크립트가 사전 생성. sync-wave: 2 (platform infra wave 1 이후).

- [ ] **Step 1: Create gitops/apps/authentik.yaml**

  ```yaml
  # Authentik IAM (Keycloak 대체)
  # Helm chart: https://charts.goauthentik.io
  # 이미지: ghcr.io/goauthentik/* (ARM64 지원 — docker.io 불필요)
  # External PostgreSQL: narwhal-db CNPG (authentik user/db)
  # External Redis: Valkey 8 standalone (iam namespace)
  # Secrets: 11-authentik.sh 스크립트가 ArgoCD sync 이전에 생성
  apiVersion: argoproj.io/v1alpha1
  kind: Application
  metadata:
    name: authentik
    namespace: devtools
    annotations:
      argocd.argoproj.io/sync-wave: "2"
  spec:
    project: default
    source:
      repoURL: https://charts.goauthentik.io
      chart: authentik
      targetRevision: "2025.4.0"
      helm:
        releaseName: authentik
        values: |
          global:
            image:
              registry: ghcr.io

          authentik:
            postgresql:
              host: "authentik-db-rw.iam.svc.cluster.local"
              name: "authentik"
              user: "authentik"
            redis:
              host: "authentik-valkey.iam.svc.cluster.local"
              port: 6379

          server:
            ingress:
              enabled: false
            additionalEnv:
              - name: AUTHENTIK_SECRET_KEY
                valueFrom:
                  secretKeyRef:
                    name: authentik-bootstrap-secret
                    key: secret_key
              - name: AUTHENTIK_POSTGRESQL__PASSWORD
                valueFrom:
                  secretKeyRef:
                    name: narwhal-db-credentials
                    key: password
              - name: AUTHENTIK_BOOTSTRAP_TOKEN
                valueFrom:
                  secretKeyRef:
                    name: authentik-bootstrap-secret
                    key: bootstrap_token
              - name: AUTHENTIK_BOOTSTRAP_PASSWORD
                valueFrom:
                  secretKeyRef:
                    name: authentik-bootstrap-secret
                    key: bootstrap_password
              - name: AUTHENTIK_BOOTSTRAP_EMAIL
                value: "admin@local.narwhal.internal"
            resources:
              requests:
                memory: "512Mi"
                cpu: "200m"
              limits:
                memory: "1Gi"
                cpu: "1"

          worker:
            additionalEnv:
              - name: AUTHENTIK_SECRET_KEY
                valueFrom:
                  secretKeyRef:
                    name: authentik-bootstrap-secret
                    key: secret_key
              - name: AUTHENTIK_POSTGRESQL__PASSWORD
                valueFrom:
                  secretKeyRef:
                    name: narwhal-db-credentials
                    key: password
              - name: AUTHENTIK_BOOTSTRAP_TOKEN
                valueFrom:
                  secretKeyRef:
                    name: authentik-bootstrap-secret
                    key: bootstrap_token
              - name: AUTHENTIK_BOOTSTRAP_PASSWORD
                valueFrom:
                  secretKeyRef:
                    name: authentik-bootstrap-secret
                    key: bootstrap_password
              - name: AUTHENTIK_BOOTSTRAP_EMAIL
                value: "admin@local.narwhal.internal"
            resources:
              requests:
                memory: "256Mi"
                cpu: "100m"
              limits:
                memory: "512Mi"
                cpu: "500m"

          postgresql:
            enabled: false

          redis:
            enabled: false

    destination:
      server: https://kubernetes.default.svc
      namespace: iam
    syncPolicy:
      automated:
        prune: true
        selfHeal: true
      syncOptions:
        - CreateNamespace=true
        - ServerSideApply=true
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add gitops/apps/authentik.yaml
  git commit -m "feat(authentik): add Authentik ArgoCD Application (replaces Keycloak)"
  ```

---

### Task 3: Authentik Installation Script

**Files:**
- Create: `scripts/cluster/11-authentik.sh`

**Context:** `11-1-keycloak-operator.sh`를 대체. Valkey 8 standalone 배포, K8s secrets 생성 (`authentik-bootstrap-secret`), Helm install, `authentik.local.narwhal.internal` ApisixRoute 생성, 준비 대기. 생성된 bootstrap token은 `11-2-authentik-config.sh`가 REST API 호출에 사용.

- [ ] **Step 1: Create scripts/cluster/11-authentik.sh**

  ```bash
  #!/bin/bash
  set -euo pipefail
  source /home/vagrant/scripts/common/lib.sh

  # 11-authentik.sh
  # Phase: Authentik IAM 설치 (Keycloak Operator 대체)
  # - Valkey 8 standalone (Redis 대체, Bitnami 배제)
  # - authentik-bootstrap-secret 생성 (secret_key, bootstrap_token, bootstrap_password)
  # - Helm install: ghcr.io/goauthentik/* (ARM64 지원)
  # - ApisixRoute bootstrap: authentik.local.narwhal.internal → authentik-server:9000
  # Depends on: 07-cnpg.sh (narwhal-db ready), 08-1-networking.sh (APISIX ready)

  AUTHENTIK_VERSION="${AUTHENTIK_VERSION:-2025.4.0}"
  DOMAIN="${DOMAIN:-local.narwhal.internal}"
  export KUBECONFIG=/home/vagrant/.kube/config-local

  echo "=== Installing Authentik ${AUTHENTIK_VERSION} ==="

  # Wait for narwhal-db
  echo "Waiting for PostgreSQL (narwhal-db) to be ready..."
  kubectl wait --for=condition=Ready cluster/narwhal-db -n database --timeout=300s || true
  kubectl wait --for=condition=Ready pod -l cnpg.io/cluster=narwhal-db -n database --timeout=120s || true

  kubectl create namespace iam --dry-run=client -o yaml | kubectl apply -f -

  #=========================================
  # Deploy Valkey 8 (Redis alternative)
  # docker.io/valkey/valkey:8-alpine
  # 사유: Bitnami 사용 금지, Valkey는 Redis 포크 (BSD 라이선스), ghcr.io/quay.io 미제공
  #=========================================
  echo "=== Deploying Valkey 8 (Redis alternative) ==="

  cat <<'EOF' | kubectl apply -f -
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: authentik-valkey
    namespace: iam
  spec:
    replicas: 1
    selector:
      matchLabels:
        app: authentik-valkey
    template:
      metadata:
        labels:
          app: authentik-valkey
          istio.io/dataplane-mode: "none"
      spec:
        containers:
          - name: valkey
            image: docker.io/valkey/valkey:8-alpine
            ports:
              - containerPort: 6379
            resources:
              requests:
                memory: "64Mi"
                cpu: "50m"
              limits:
                memory: "256Mi"
                cpu: "200m"
            livenessProbe:
              exec:
                command: ["valkey-cli", "ping"]
              initialDelaySeconds: 10
              periodSeconds: 30
            readinessProbe:
              exec:
                command: ["valkey-cli", "ping"]
              initialDelaySeconds: 5
              periodSeconds: 10
  ---
  apiVersion: v1
  kind: Service
  metadata:
    name: authentik-valkey
    namespace: iam
  spec:
    selector:
      app: authentik-valkey
    ports:
      - port: 6379
        targetPort: 6379
  EOF

  echo "Waiting for Valkey pod..."
  kubectl wait --for=condition=Ready pod -l app=authentik-valkey -n iam --timeout=120s

  #=========================================
  # Create Authentik bootstrap secrets
  # Idempotent: 재실행 시 기존 secret 재사용
  #=========================================
  echo "=== Creating Authentik bootstrap secrets ==="

  if ! kubectl get secret authentik-bootstrap-secret -n iam &>/dev/null; then
    # secret_key: 64자 이상 필요 (두 generate_password 연결)
    AUTHENTIK_SECRET_KEY="$(generate_password)$(generate_password)"
    AUTHENTIK_BOOTSTRAP_TOKEN="$(generate_password)"
    AUTHENTIK_BOOTSTRAP_PASSWORD="$(generate_password)"
    kubectl create secret generic authentik-bootstrap-secret -n iam \
      --from-literal=secret_key="${AUTHENTIK_SECRET_KEY}" \
      --from-literal=bootstrap_token="${AUTHENTIK_BOOTSTRAP_TOKEN}" \
      --from-literal=bootstrap_password="${AUTHENTIK_BOOTSTRAP_PASSWORD}"
    echo "Authentik bootstrap secret created (authentik-bootstrap-secret in iam)"
  else
    AUTHENTIK_BOOTSTRAP_TOKEN="$(kubectl get secret authentik-bootstrap-secret -n iam \
      -o jsonpath='{.data.bootstrap_token}' | base64 -d)"
    echo "Authentik bootstrap secret already exists, reusing token"
  fi

  #=========================================
  # Helm install Authentik
  #=========================================
  echo "=== Installing Authentik via Helm ==="

  AUTHENTIK_DB_PASS="$(kubectl get secret narwhal-db-credentials -n database \
    -o jsonpath='{.data.password}' | base64 -d)"

  helm repo add authentik https://charts.goauthentik.io
  helm repo update

  helm upgrade --install authentik authentik/authentik \
    --namespace iam \
    --version "${AUTHENTIK_VERSION}" \
    --set "global.image.registry=ghcr.io" \
    --set "authentik.postgresql.host=authentik-db-rw.iam.svc.cluster.local" \
    --set "authentik.postgresql.name=authentik" \
    --set "authentik.postgresql.user=authentik" \
    --set "authentik.redis.host=authentik-valkey.iam.svc.cluster.local" \
    --set "authentik.redis.port=6379" \
    --set "postgresql.enabled=false" \
    --set "redis.enabled=false" \
    --set "server.ingress.enabled=false" \
    --set "server.resources.requests.memory=512Mi" \
    --set "server.resources.requests.cpu=200m" \
    --set "server.resources.limits.memory=1Gi" \
    --set "server.resources.limits.cpu=1" \
    --set "worker.resources.requests.memory=256Mi" \
    --set "worker.resources.limits.memory=512Mi" \
    --set-json 'server.additionalEnv=[
      {"name":"AUTHENTIK_SECRET_KEY","valueFrom":{"secretKeyRef":{"name":"authentik-bootstrap-secret","key":"secret_key"}}},
      {"name":"AUTHENTIK_POSTGRESQL__PASSWORD","valueFrom":{"secretKeyRef":{"name":"narwhal-db-credentials","key":"password"}}},
      {"name":"AUTHENTIK_BOOTSTRAP_TOKEN","valueFrom":{"secretKeyRef":{"name":"authentik-bootstrap-secret","key":"bootstrap_token"}}},
      {"name":"AUTHENTIK_BOOTSTRAP_PASSWORD","valueFrom":{"secretKeyRef":{"name":"authentik-bootstrap-secret","key":"bootstrap_password"}}},
      {"name":"AUTHENTIK_BOOTSTRAP_EMAIL","value":"admin@local.narwhal.internal"}
    ]' \
    --set-json 'worker.additionalEnv=[
      {"name":"AUTHENTIK_SECRET_KEY","valueFrom":{"secretKeyRef":{"name":"authentik-bootstrap-secret","key":"secret_key"}}},
      {"name":"AUTHENTIK_POSTGRESQL__PASSWORD","valueFrom":{"secretKeyRef":{"name":"narwhal-db-credentials","key":"password"}}},
      {"name":"AUTHENTIK_BOOTSTRAP_TOKEN","valueFrom":{"secretKeyRef":{"name":"authentik-bootstrap-secret","key":"bootstrap_token"}}},
      {"name":"AUTHENTIK_BOOTSTRAP_PASSWORD","valueFrom":{"secretKeyRef":{"name":"authentik-bootstrap-secret","key":"bootstrap_password"}}},
      {"name":"AUTHENTIK_BOOTSTRAP_EMAIL","value":"admin@local.narwhal.internal"}
    ]' \
    --timeout 10m \
    || echo "WARN: Helm install timed out, waiting manually..."

  # Wait for Authentik server pod
  echo "Waiting for Authentik server pods..."
  sleep 30
  kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=authentik \
    -n iam --timeout=600s || true

  #=========================================
  # Create ApisixRoute for Authentik HTTPS access
  # OIDC verification (11-2-authentik-config.sh) 이전에 반드시 존재해야 함
  # GitOps bootstrap(14) 이전이므로 여기서 직접 apply
  #=========================================
  echo "=== Applying Authentik ApisixRoute ==="

  kubectl apply -f - << 'ROUTE_EOF'
  apiVersion: apisix.apache.org/v2
  kind: ApisixRoute
  metadata:
    name: authentik
    namespace: platform-system
  spec:
    http:
      - name: authentik
        match:
          hosts:
            - authentik.local.narwhal.internal
          paths:
            - "/*"
        backends:
          - serviceName: authentik-server
            servicePort: 9000
            serviceNamespace: iam
            resolveGranularity: service
        plugins:
          - name: response-rewrite
            enable: true
            config:
              headers:
                set:
                  Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                  X-Content-Type-Options: "nosniff"
  ROUTE_EOF

  sleep 5

  # Verify HTTPS endpoint reachable (Authentik health check: GET /-/health/ready/ → 204)
  echo "Verifying Authentik HTTPS endpoint..."
  AUTHENTIK_REACHABLE=false
  for attempt in $(seq 1 20); do
    HTTP_CODE=$(curl -sk -o /dev/null -w '%{http_code}' \
      "https://authentik.${DOMAIN}/-/health/ready/" 2>/dev/null || echo "000")
    if [ "${HTTP_CODE}" = "204" ] || [ "${HTTP_CODE}" = "200" ]; then
      AUTHENTIK_REACHABLE=true
      echo "Authentik ready (HTTP ${HTTP_CODE})"
      break
    fi
    echo "Authentik not ready (HTTP ${HTTP_CODE}), attempt ${attempt}/20..."
    sleep 15
  done

  if [ "${AUTHENTIK_REACHABLE}" = "false" ]; then
    echo "WARN: Authentik HTTPS endpoint not reachable. Possible causes:"
    echo "  - cert-manager TLS certificate not yet issued"
    echo "  - APISIX Gateway not routing authentik.${DOMAIN}"
    echo "  - Authentik pod still starting (check: kubectl get pods -n iam)"
    echo "  Run 11-2-authentik-config.sh after Authentik is ready."
  fi

  echo "=== [11-authentik.sh] 완료 ==="
  ```

- [ ] **Step 2: Make executable and commit**
  ```bash
  chmod +x scripts/cluster/11-authentik.sh
  git add scripts/cluster/11-authentik.sh
  git commit -m "feat(authentik): add Authentik install script (Valkey + Helm + ApisixRoute)"
  ```

---

### Task 4: Authentik Configuration Script (REST API)

**Files:**
- Create: `scripts/cluster/11-2-authentik-config.sh`

**Context:** `11-2-keycloak-realm.sh` + `11-3-keycloak-clients.sh` 대체. Authentik REST API (`/api/v3/`)로 완전히 구성. 생성 항목:
- Groups: `cluster-admin`, `developer`, `viewer`, `guest`
- Users: `admin`/`dev`/`view`/`guest` + 그룹 할당
- Custom scope mapping: `groups` 클레임 (Python expression)
- OAuth2 Provider: `kubernetes` (public, K8s API server용)
- OAuth2 Provider: `apisix` (confidential, APISIX gateway용)
- Applications: `kubernetes`, `apisix` (slug 기반 → issuer URL 결정)
- K8s Secrets: `apisix-oidc-config` (platform-system), `grafana-oauth-secret` (monitoring), `headlamp-oidc-secret` (devtools), `authentik-user-passwords` (iam)

**Authentik API 패턴:**
- Base URL: `http://authentik-server.iam.svc.cluster.local:9000/api/v3` (in-cluster HTTP, TLS 불필요)
- Auth: `Authorization: Bearer ${BOOTSTRAP_TOKEN}`
- Idempotent: 생성 전 GET으로 존재 확인 → 없으면 POST

- [ ] **Step 1: Create scripts/cluster/11-2-authentik-config.sh**

  ```bash
  #!/bin/bash
  set -euo pipefail
  source /home/vagrant/scripts/common/lib.sh

  # 11-2-authentik-config.sh
  # Phase: Authentik REST API 구성
  # - Groups: cluster-admin, developer, viewer, guest
  # - Users: admin, dev, view, guest (그룹 할당 포함)
  # - Custom scope mapping: groups 클레임
  # - OAuth2 Providers: kubernetes (public), apisix (confidential)
  # - Applications: kubernetes, apisix
  # - K8s Secrets: apisix-oidc-config, grafana-oauth-secret, headlamp-oidc-secret
  # Depends on: 11-authentik.sh (Authentik pod ready, HTTPS 라우트 존재)

  DOMAIN="${DOMAIN:-local.narwhal.internal}"
  # in-cluster HTTP: TLS 검증 불필요, 빠름
  AUTHENTIK_URL="http://authentik-server.iam.svc.cluster.local:9000"
  export KUBECONFIG=/home/vagrant/.kube/config-local

  echo "=== Configuring Authentik via REST API ==="

  BOOTSTRAP_TOKEN=$(kubectl get secret authentik-bootstrap-secret -n iam \
    -o jsonpath='{.data.bootstrap_token}' | base64 -d)

  # Helper: GET
  ak_get() {
    curl -sf "${AUTHENTIK_URL}/api/v3/${1}" \
      -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
      -H "Content-Type: application/json"
  }

  # Helper: POST
  ak_post() {
    curl -sf -X POST "${AUTHENTIK_URL}/api/v3/${1}" \
      -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "${2}"
  }

  # Helper: GET-or-create (idempotent). Returns pk/id of existing or newly created object.
  # Usage: ak_get_or_create <endpoint> <filter_param> <filter_value> <json_payload>
  ak_get_or_create() {
    local endpoint="${1}" filter_key="${2}" filter_value="${3}" payload="${4}"
    local existing_id
    existing_id=$(ak_get "${endpoint}/?${filter_key}=${filter_value}" 2>/dev/null \
      | jq -r '.results[0].pk // .results[0].id // empty' 2>/dev/null || echo "")
    if [ -n "${existing_id}" ]; then
      echo "${existing_id}"
    else
      ak_post "${endpoint}/" "${payload}" 2>/dev/null | jq -r '.pk // .id'
    fi
  }

  # Wait for Authentik API
  echo "Waiting for Authentik API to be ready..."
  for attempt in $(seq 1 30); do
    if ak_get "core/users/?page_size=1" &>/dev/null; then
      echo "Authentik API ready"
      break
    fi
    echo "  API not ready, attempt ${attempt}/30..."
    sleep 10
  done

  #=========================================
  # Groups
  #=========================================
  echo "=== Creating groups ==="

  declare -A GROUP_IDS
  for group_name in cluster-admin developer viewer guest; do
    GROUP_ID=$(ak_get_or_create "core/groups" "name" "${group_name}" \
      "{\"name\":\"${group_name}\",\"is_superuser\":false}")
    GROUP_IDS["${group_name}"]="${GROUP_ID}"
    echo "  -> group '${group_name}' (ID: ${GROUP_ID})"
  done

  #=========================================
  # Users + passwords
  #=========================================
  echo "=== Creating users ==="

  if ! kubectl get secret authentik-user-passwords -n iam &>/dev/null; then
    ADMIN_PASS="$(generate_password)"
    DEV_PASS="$(generate_password)"
    VIEW_PASS="$(generate_password)"
    GUEST_PASS="$(generate_password)"
    kubectl create secret generic authentik-user-passwords -n iam \
      --from-literal=admin="${ADMIN_PASS}" \
      --from-literal=dev="${DEV_PASS}" \
      --from-literal=view="${VIEW_PASS}" \
      --from-literal=guest="${GUEST_PASS}"
    echo "User passwords secret created (authentik-user-passwords in iam)"
  else
    ADMIN_PASS="$(kubectl get secret authentik-user-passwords -n iam \
      -o jsonpath='{.data.admin}' | base64 -d)"
    DEV_PASS="$(kubectl get secret authentik-user-passwords -n iam \
      -o jsonpath='{.data.dev}' | base64 -d)"
    VIEW_PASS="$(kubectl get secret authentik-user-passwords -n iam \
      -o jsonpath='{.data.view}' | base64 -d)"
    GUEST_PASS="$(kubectl get secret authentik-user-passwords -n iam \
      -o jsonpath='{.data.guest}' | base64 -d)"
    echo "User passwords loaded from existing secret"
  fi

  # Create users: each user gets one group
  # group assignment: groups array takes group PKs
  for row in \
    "admin:${ADMIN_PASS}:${GROUP_IDS[cluster-admin]}:admin@local.narwhal.internal" \
    "dev:${DEV_PASS}:${GROUP_IDS[developer]}:dev@local.narwhal.internal" \
    "view:${VIEW_PASS}:${GROUP_IDS[viewer]}:view@local.narwhal.internal" \
    "guest:${GUEST_PASS}:${GROUP_IDS[guest]}:guest@local.narwhal.internal"; do

    IFS=':' read -r username password group_pk email <<< "${row}"

    USER_ID=$(ak_get_or_create "core/users" "username" "${username}" \
      "{\"username\":\"${username}\",\"name\":\"${username}\",\"email\":\"${email}\",\"type\":\"internal\",\"is_active\":true,\"groups\":[\"${group_pk}\"]}")

    # Set password (separate endpoint, idempotent)
    curl -sf -X POST "${AUTHENTIK_URL}/api/v3/core/users/${USER_ID}/set_password/" \
      -H "Authorization: Bearer ${BOOTSTRAP_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"password\":\"${password}\"}" &>/dev/null || true

    echo "  -> user '${username}' (ID: ${USER_ID})"
  done

  #=========================================
  # Custom scope mapping: groups claim
  # Authentik built-in profile scope에 groups 클레임 없음 → 별도 생성 필요
  # expression: Python, ak_groups는 User의 그룹 queryset
  #=========================================
  echo "=== Creating groups scope mapping ==="

  GROUPS_SCOPE_ID=$(ak_get_or_create "propertymappings/scope" "scope_name" "groups" \
    "{\"name\":\"groups\",\"scope_name\":\"groups\",\"description\":\"User group membership list\",\"expression\":\"return [group.name for group in request.user.ak_groups.all()]\"}")
  echo "  -> groups scope mapping (ID: ${GROUPS_SCOPE_ID})"

  #=========================================
  # Get built-in scope mapping PKs (openid, profile, email)
  #=========================================
  echo "=== Gathering built-in scope mappings ==="

  SCOPE_OPENID=$(ak_get "propertymappings/scope/?scope_name=openid" | jq -r '.results[0].pk')
  SCOPE_PROFILE=$(ak_get "propertymappings/scope/?scope_name=profile" | jq -r '.results[0].pk')
  SCOPE_EMAIL=$(ak_get "propertymappings/scope/?scope_name=email" | jq -r '.results[0].pk')
  echo "  -> openid: ${SCOPE_OPENID}, profile: ${SCOPE_PROFILE}, email: ${SCOPE_EMAIL}, groups: ${GROUPS_SCOPE_ID}"

  # Get default authorization flow PK (implicit consent)
  AUTH_FLOW_PK=$(ak_get "flows/instances/?slug=default-provider-authorization-implicit-consent" \
    | jq -r '.results[0].pk')
  # Get invalidation flow PK
  INVALIDATION_FLOW_PK=$(ak_get "flows/instances/?slug=default-provider-invalidation-flow" \
    | jq -r '.results[0].pk')

  #=========================================
  # OAuth2 Provider: kubernetes (public)
  # K8s API server OIDC: --oidc-client-id=kubernetes --oidc-issuer-url=https://authentik.*/application/o/kubernetes/
  #=========================================
  echo "=== Creating 'kubernetes' OAuth2 provider (public) ==="

  PROVIDER_KUBERNETES_ID=$(ak_get_or_create "providers/oauth2" "name" "kubernetes" \
    "{
      \"name\": \"kubernetes\",
      \"client_type\": \"public\",
      \"client_id\": \"kubernetes\",
      \"authorization_flow\": \"${AUTH_FLOW_PK}\",
      \"invalidation_flow\": \"${INVALIDATION_FLOW_PK}\",
      \"include_claims_in_id_token\": true,
      \"sub_mode\": \"hashed_user_id\",
      \"access_code_validity\": \"minutes=1\",
      \"access_token_validity\": \"minutes=5\",
      \"refresh_token_validity\": \"days=30\",
      \"property_mappings\": [\"${SCOPE_OPENID}\",\"${SCOPE_PROFILE}\",\"${SCOPE_EMAIL}\",\"${GROUPS_SCOPE_ID}\"],
      \"redirect_uris\": [{\"matching_mode\": \"strict\", \"url\": \"*\"}]
    }")
  echo "  -> kubernetes provider (ID: ${PROVIDER_KUBERNETES_ID})"

  #=========================================
  # OAuth2 Provider: apisix (confidential)
  # APISIX openid-connect plugin: 모든 보호 라우트에서 이 provider 사용
  #=========================================
  echo "=== Creating 'apisix' OAuth2 provider (confidential) ==="

  # Generate or reuse apisix client secret
  if ! kubectl get secret apisix-oidc-config -n platform-system &>/dev/null; then
    APISIX_CLIENT_SECRET=$(generate_password)
  else
    APISIX_CLIENT_SECRET=$(kubectl get secret apisix-oidc-config -n platform-system \
      -o jsonpath='{.data.client_secret}' | base64 -d)
  fi

  PROVIDER_APISIX_ID=$(ak_get_or_create "providers/oauth2" "name" "apisix" \
    "{
      \"name\": \"apisix\",
      \"client_type\": \"confidential\",
      \"client_id\": \"apisix\",
      \"client_secret\": \"${APISIX_CLIENT_SECRET}\",
      \"authorization_flow\": \"${AUTH_FLOW_PK}\",
      \"invalidation_flow\": \"${INVALIDATION_FLOW_PK}\",
      \"include_claims_in_id_token\": true,
      \"sub_mode\": \"hashed_user_id\",
      \"property_mappings\": [\"${SCOPE_OPENID}\",\"${SCOPE_PROFILE}\",\"${SCOPE_EMAIL}\",\"${GROUPS_SCOPE_ID}\"],
      \"redirect_uris\": [{\"matching_mode\": \"regex\", \"url\": \"https://.*\\\\.local\\\\.narwhal\\\\.io/apisix/callback\"}]
    }")
  echo "  -> apisix provider (ID: ${PROVIDER_APISIX_ID})"

  # Retrieve final client_secret (provider may already exist with existing secret)
  APISIX_CLIENT_SECRET_FINAL=$(ak_get "providers/oauth2/?name=apisix" \
    | jq -r '.results[0].client_secret')

  #=========================================
  # Applications (slug → OIDC issuer URL)
  # kubernetes app → slug: kubernetes → issuer: https://authentik.*/application/o/kubernetes/
  # apisix app     → slug: apisix     → issuer: https://authentik.*/application/o/apisix/
  #=========================================
  echo "=== Creating applications ==="

  ak_get_or_create "core/applications" "slug" "kubernetes" \
    "{\"name\":\"kubernetes\",\"slug\":\"kubernetes\",\"provider\":${PROVIDER_KUBERNETES_ID},\"meta_description\":\"Kubernetes API Server OIDC\"}" > /dev/null
  echo "  -> kubernetes application (issuer: https://authentik.${DOMAIN}/application/o/kubernetes/)"

  ak_get_or_create "core/applications" "slug" "apisix" \
    "{\"name\":\"apisix\",\"slug\":\"apisix\",\"provider\":${PROVIDER_APISIX_ID},\"meta_description\":\"APISIX API Gateway SSO\"}" > /dev/null
  echo "  -> apisix application (issuer: https://authentik.${DOMAIN}/application/o/apisix/)"

  #=========================================
  # Store OIDC credentials in K8s secrets
  #=========================================
  echo "=== Storing OIDC credentials in K8s secrets ==="

  APISIX_SESSION_SECRET=$(openssl rand -hex 32)

  kubectl create namespace platform-system --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic apisix-oidc-config \
    --namespace platform-system \
    --from-literal=client_id=apisix \
    --from-literal=client_secret="${APISIX_CLIENT_SECRET_FINAL}" \
    --from-literal=session_secret="${APISIX_SESSION_SECRET}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "APISIX OIDC config stored (apisix-oidc-config in platform-system)"

  kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic grafana-oauth-secret -n monitoring \
    --from-literal=client_secret="${APISIX_CLIENT_SECRET_FINAL}" \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "Grafana OAuth secret updated (grafana-oauth-secret in monitoring)"

  kubectl create namespace devtools --dry-run=client -o yaml | kubectl apply -f -
  kubectl create secret generic headlamp-oidc-secret -n devtools \
    --from-literal=clientID=apisix \
    --from-literal=clientSecret="${APISIX_CLIENT_SECRET_FINAL}" \
    --from-literal=issuerURL="https://authentik.${DOMAIN}/application/o/apisix/" \
    --from-literal=scopes=openid,profile,email,groups \
    --dry-run=client -o yaml | kubectl apply -f -
  echo "Headlamp OIDC secret updated (headlamp-oidc-secret in devtools)"

  echo ""
  echo "=== [11-2-authentik-config.sh] 완료 ==="
  echo ""
  echo "=========================================="
  echo "Authentik Configuration Summary"
  echo "=========================================="
  echo "Admin UI:    https://authentik.${DOMAIN}"
  echo "Admin email: admin@local.narwhal.internal"
  echo "Admin pass:  kubectl get secret authentik-bootstrap-secret -n iam"
  echo ""
  echo "OIDC Issuers:"
  echo "  K8s API:  https://authentik.${DOMAIN}/application/o/kubernetes/"
  echo "  APISIX:   https://authentik.${DOMAIN}/application/o/apisix/"
  echo ""
  echo "K8s Secrets updated:"
  echo "  platform-system/apisix-oidc-config"
  echo "  monitoring/grafana-oauth-secret"
  echo "  devtools/headlamp-oidc-secret"
  echo "  iam/authentik-user-passwords"
  ```

- [ ] **Step 2: Make executable and commit**
  ```bash
  chmod +x scripts/cluster/11-2-authentik-config.sh
  git add scripts/cluster/11-2-authentik-config.sh
  git commit -m "feat(authentik): add Authentik REST API configuration script"
  ```

---

### Task 5: Phase 2 Orchestration — script list update

**Files:**
- Modify: `scripts/cluster/06-phase2-start.sh`

**Context:** `06-phase2-start.sh`의 Phase 2 스크립트 배열과 실행 루프에서 Keycloak 스크립트 3개를 Authentik 스크립트 2개로 교체. 순서: `11-authentik.sh` → `11-2-authentik-config.sh` → `11-4-keycloak-apiserver.sh`.

- [ ] **Step 1: Update phase2 script list in 06-phase2-start.sh**

  Line ~44-52 부근에서:
  ```bash
  # 변경 전
  "11-1-keycloak-operator.sh"
  "11-2-keycloak-realm.sh"
  "11-3-keycloak-clients.sh"
  "11-4-keycloak-apiserver.sh"
  ```
  ```bash
  # 변경 후
  "11-authentik.sh"
  "11-2-authentik-config.sh"
  "11-4-keycloak-apiserver.sh"
  ```

  실행 루프 문자열(line ~52)도 동일하게 업데이트:
  ```bash
  for script in "07-cnpg.sh" "08-1-networking.sh" "08-2-monitoring.sh" "08-3-security.sh" \
    "08-4-storage.sh" "08-5-registry.sh" "08-6-tls-routes.sh" "09-istio-ambient.sh" \
    "10-dnsmasq.sh" "11-authentik.sh" "11-2-authentik-config.sh" "11-4-keycloak-apiserver.sh" \
    "12-gitea.sh" "13-argocd.sh" "14-gitops-bootstrap.sh"; do
  ```

  주석(line ~50)도 업데이트:
  ```bash
  # Run all scripts in order (07→08-1~08-6→09→10→11-authentik~11-4→12→13→14)
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add scripts/cluster/06-phase2-start.sh
  git commit -m "feat(phase2): replace Keycloak scripts with Authentik scripts in phase2 sequence"
  ```

---

### Task 6: K8s API Server OIDC — issuer URL update

**Files:**
- Modify: `scripts/cluster/11-4-keycloak-apiserver.sh`

**Context:** OIDC issuer URL만 변경. 나머지 플래그(`--oidc-client-id=kubernetes`, `--oidc-username-claim=preferred_username`, `--oidc-groups-claim=groups`, `--oidc-groups-prefix=oidc:`) 동일 유지. CA cert 추출 로직(`narwhal-root-ca-secret`)도 동일.

- [ ] **Step 1: Update OIDC_ISSUER_URL (line ~105)**

  변경 전:
  ```bash
  OIDC_ISSUER_URL="https://keycloak.${DOMAIN}/realms/kubernetes"
  ```
  변경 후:
  ```bash
  OIDC_ISSUER_URL="https://authentik.${DOMAIN}/application/o/kubernetes/"
  ```

- [ ] **Step 2: Update comment (line ~7)**
  ```bash
  # Depends on: 11-2-authentik-config.sh (Authentik HTTPS must be accessible for OIDC endpoint)
  ```

- [ ] **Step 3: Update WARN message (line ~128)**
  ```bash
  echo "  Run scripts: 08-1-networking.sh → 10-dnsmasq.sh → 11-authentik.sh → 11-2-authentik-config.sh"
  ```

- [ ] **Step 4: Commit**
  ```bash
  git add scripts/cluster/11-4-keycloak-apiserver.sh
  git commit -m "feat(oidc): update K8s API server OIDC issuer from Keycloak to Authentik"
  ```

---

### Task 7: APISIX Routes — Keycloak → Authentik

**Files:**
- Modify: `gitops/resources/apisix-routes.yaml`

**Context:** 두 가지 변경:
1. 파일 첫 번째 라우트 (Keycloak IdP 라우트) → Authentik 라우트로 교체 (hostname, backend service, metadata 변경)
2. 모든 `openid-connect` 플러그인의 `discovery` URL: `keycloak.*/realms/kubernetes/.well-known/openid-configuration` → `authentik.*/application/o/apisix/.well-known/openid-configuration`

- [ ] **Step 1: Replace Keycloak route (lines 1-28) with Authentik route**

  ```yaml
  ---
  # Authentik (Identity Provider — no OIDC plugin, Authentik IS the IdP)
  apiVersion: apisix.apache.org/v2
  kind: ApisixRoute
  metadata:
    name: authentik
    namespace: platform-system
  spec:
    http:
      - name: authentik
        match:
          hosts:
            - authentik.local.narwhal.internal
          paths:
            - "/*"
        backends:
          - serviceName: authentik-server
            servicePort: 9000
            serviceNamespace: iam
            resolveGranularity: service
        plugins:
          - name: response-rewrite
            enable: true
            config:
              headers:
                set:
                  Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                  X-Content-Type-Options: "nosniff"
  ```

- [ ] **Step 2: Update all discovery URLs (7개 라우트: ArgoCD, Grafana, Gitea, Harbor, Headlamp, OpenBao, Hubble)**

  각 `openid-connect` 플러그인의 `discovery` 필드:

  변경 전:
  ```yaml
  discovery: "https://keycloak.local.narwhal.internal/realms/kubernetes/.well-known/openid-configuration"
  ```
  변경 후:
  ```yaml
  discovery: "https://authentik.local.narwhal.internal/application/o/apisix/.well-known/openid-configuration"
  ```

- [ ] **Step 3: Commit**
  ```bash
  git add gitops/resources/apisix-routes.yaml
  git commit -m "feat(apisix): update routes from Keycloak to Authentik OIDC discovery URLs"
  ```

---

### Task 8: dnsmasq Hairpin Fix — keycloak → authentik

**Files:**
- Modify: `scripts/cluster/10-dnsmasq.sh`

**Context:** CoreDNS hairpin zone (line ~221)에 `keycloak.${DOMAIN}`이 하드코딩되어 있음. in-cluster pod들이 Authentik에 접근하려면 `authentik.${DOMAIN}`으로 변경 필요.

- [ ] **Step 1: Update hairpin hosts zone (line ~221)**

  변경 전:
  ```bash
          ${APISIX_IP} keycloak.${DOMAIN}
  ```
  변경 후:
  ```bash
          ${APISIX_IP} authentik.${DOMAIN}
  ```

- [ ] **Step 2: Commit**
  ```bash
  git add scripts/cluster/10-dnsmasq.sh
  git commit -m "fix(dnsmasq): update CoreDNS hairpin zone from keycloak to authentik"
  ```

---

### Task 9: Cleanup + VERSIONS.md

**Files:**
- Delete: `scripts/cluster/11-1-keycloak-operator.sh`
- Delete: `scripts/cluster/11-2-keycloak-realm.sh`
- Delete: `scripts/cluster/11-3-keycloak-clients.sh`
- Modify: `VERSIONS.md`

- [ ] **Step 1: Delete Keycloak scripts**
  ```bash
  git rm scripts/cluster/11-1-keycloak-operator.sh
  git rm scripts/cluster/11-2-keycloak-realm.sh
  git rm scripts/cluster/11-3-keycloak-clients.sh
  ```

- [ ] **Step 2: Update VERSIONS.md — Identity & Access section**

  변경 전:
  ```markdown
  | Keycloak | v26.5.3 | IAM / SSO (1 instance, Operator) |
  | K8s OIDC | - | API Server OIDC integration |
  ```
  변경 후:
  ```markdown
  | Authentik | 2025.4.0 | IAM / SSO (Helm, REST API configuration) |
  | Valkey | 8 (8-alpine) | Redis alternative for Authentik session store |
  | K8s OIDC | - | API Server OIDC integration |
  ```

- [ ] **Step 3: Commit**
  ```bash
  git add VERSIONS.md
  git commit -m "feat(authentik): remove Keycloak scripts, update VERSIONS.md to Authentik 2025.4.0"
  ```

---

## Verification

신규 프로비저닝 완료 후 확인:

```bash
# 1. Authentik pod 상태
kubectl get pods -n iam

# 2. Authentik 웹 UI 접근 (브라우저)
# https://authentik.local.narwhal.internal

# 3. OIDC discovery 엔드포인트 확인
curl -sk https://authentik.local.narwhal.internal/application/o/kubernetes/.well-known/openid-configuration | jq .issuer
# expected: "https://authentik.local.narwhal.internal/application/o/kubernetes/"

curl -sk https://authentik.local.narwhal.internal/application/o/apisix/.well-known/openid-configuration | jq .issuer
# expected: "https://authentik.local.narwhal.internal/application/o/apisix/"

# 4. K8s API server OIDC 동작 확인 (OIDC token으로 kubectl)
OIDC_TOKEN=$(curl -sk -X POST \
  https://authentik.local.narwhal.internal/application/o/token/ \
  -d "grant_type=password&client_id=kubernetes&username=admin&password=$(kubectl get secret authentik-user-passwords -n iam -o jsonpath='{.data.admin}' | base64 -d)&scope=openid profile email groups" \
  | jq -r .id_token)

KUBECONFIG=/dev/null kubectl --server=https://192.168.56.100:6443 \
  --certificate-authority=/etc/kubernetes/pki/ca.crt \
  --token="${OIDC_TOKEN}" \
  get nodes

# 5. APISIX 라우트 SSO 동작: 브라우저에서 https://argocd.local.narwhal.internal 접근 → Authentik 로그인 페이지로 리다이렉트 확인
```
