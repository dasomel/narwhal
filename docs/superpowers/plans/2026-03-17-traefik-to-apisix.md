# Traefik → APISIX Migration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Traefik (ingress) + OAuth2-Proxy (OIDC auth) with Apache APISIX, using native ApisixRoute CRDs and the built-in `openid-connect` plugin for Keycloak SSO.

**Architecture:** APISIX serves as the single API gateway handling TLS termination, routing, and OIDC authentication via its `openid-connect` plugin. A standalone etcd (using `registry.k8s.io/etcd`, no Bitnami) stores APISIX configuration. The APISIX Ingress Controller watches ApisixRoute/ApisixTls CRDs and syncs them to APISIX via Admin API. APISIX Dashboard provides a management UI at `apisix-dashboard.local.narwhal.io`. Secret values (OIDC client_secret, session_secret) are stored in a K8s Secret and exposed to APISIX via the Kubernetes Secret Provider (`$secret://kubernetes/...`).

**Tech Stack:** Apache APISIX 3.11.0, APISIX Ingress Controller 1.8.0, APISIX Dashboard 3.0.0, etcd 3.5.21 (`registry.k8s.io/etcd`), cert-manager wildcard TLS, MetalLB LoadBalancer (192.168.56.200), Keycloak OIDC (`openid-connect` plugin)

---

## File Map

### Files to CREATE
| File | Purpose |
|------|---------|
| `gitops/apps/apisix.yaml` | ArgoCD Application — APISIX Helm chart (includes Ingress Controller) |
| `gitops/apps/apisix-dashboard.yaml` | ArgoCD Application — APISIX Dashboard Helm chart |
| `gitops/resources/apisix-infra.yaml` | etcd Deployment/Service + wildcard TLS Certificate + ApisixTls |
| `gitops/resources/apisix-routes.yaml` | ApisixRoute for all apps (ArgoCD, Grafana, Harbor, Gitea, Headlamp, OpenBao, Hubble, Dashboard) |
| `gitops/apps/apisix-routes.yaml` | ArgoCD Application managing apisix-routes.yaml |

### Files to MODIFY
| File | Change |
|------|--------|
| `gitops/apps/app-of-apps.yaml` | Add apisix/apisix-dashboard/apisix-routes, remove traefik/oauth2-proxy refs |
| `scripts/cluster/08-1-networking.sh` | Replace Traefik section with APISIX + etcd bootstrap |
| `scripts/cluster/08-3-security.sh` | Remove OAuth2-Proxy section; rename echo header |
| `scripts/cluster/08-6-tls-routes.sh` | Remove Traefik route apply; add ApisixPluginConfig secret patch |
| `scripts/cluster/11-3-keycloak-clients.sh` | Add `apisix` OIDC client with correct redirect URIs |
| `VERSIONS.md` | Add APISIX/etcd versions, remove Traefik/OAuth2-Proxy |

### Files to DELETE
| File | Reason |
|------|--------|
| `gitops/apps/traefik.yaml` | Replaced by apisix.yaml |
| `gitops/apps/oauth2-proxy.yaml` | Replaced by openid-connect plugin |
| `gitops/resources/traefik-routes.yaml` | Replaced by apisix-routes.yaml |
| `gitops/apps/traefik-routes.yaml` | Replaced by apisix-routes.yaml ArgoCD app |

---

## Chunk 1: GitOps — Remove Old, Add APISIX Apps

### Task 1: Delete obsolete GitOps files

**Files:**
- Delete: `gitops/apps/traefik.yaml`
- Delete: `gitops/apps/oauth2-proxy.yaml`
- Delete: `gitops/resources/traefik-routes.yaml`
- Delete: `gitops/apps/traefik-routes.yaml`

- [ ] **Step 1: Delete the four files**

```bash
rm gitops/apps/traefik.yaml
rm gitops/apps/oauth2-proxy.yaml
rm gitops/resources/traefik-routes.yaml
rm gitops/apps/traefik-routes.yaml
```

- [ ] **Step 2: Verify deletion**

```bash
ls gitops/apps/ gitops/resources/
```
Expected: traefik.yaml, oauth2-proxy.yaml, traefik-routes.yaml absent

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove Traefik and OAuth2-Proxy GitOps files"
```

---

### Task 2: Create `gitops/apps/apisix.yaml`

**Files:**
- Create: `gitops/apps/apisix.yaml`

- [ ] **Step 1: Create APISIX ArgoCD Application**

```yaml
# gitops/apps/apisix.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: apisix
  namespace: devtools
  annotations:
    argocd.argoproj.io/sync-wave: "2"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://charts.apiseven.com
    chart: apisix
    targetRevision: "2.9.0"
    helm:
      valuesObject:
        apisix:
          enabled: true
          image:
            repository: apache/apisix
            tag: "3.11.0-debian"
          podLabels:
            istio.io/dataplane-mode: "none"
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          # Kubernetes Secret Provider for OIDC credentials
          # Used by openid-connect plugin: $secret://kubernetes/apisix-oidc-config/<key>
          config:
            apisix:
              secret_providers:
                - name: kubernetes
                  uid: k8s-1
                  auth_type: serviceaccount
                  apiservers:
                    - https://kubernetes.default.svc

        # LoadBalancer: same IP as former Traefik
        gateway:
          type: LoadBalancer
          annotations:
            metallb.universe.tf/loadBalancerIPs: "192.168.56.200"
          http:
            enabled: true
            servicePort: 80
            containerPort: 9080
          tls:
            enabled: true
            servicePort: 443
            containerPort: 9443

        # Admin API (internal only)
        admin:
          enabled: true
          type: ClusterIP
          port: 9180

        # Use external etcd (registry.k8s.io/etcd — no Bitnami)
        etcd:
          enabled: false
          host:
            - "http://apisix-etcd.platform-system.svc.cluster.local:2379"
          prefix: "/apisix"
          timeout: 30

        # APISIX Ingress Controller (watches ApisixRoute CRDs)
        ingressController:
          enabled: true
          image:
            repository: apache/apisix-ingress-controller
            tag: "1.8.0"
          podLabels:
            istio.io/dataplane-mode: "none"
          config:
            apisix:
              serviceNamespace: platform-system
              adminAPIVersion: "v3"
            kubernetes:
              watchNamespaces: []  # All namespaces
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 200m
              memory: 256Mi

        # Metrics for Prometheus
        serviceMonitor:
          enabled: true
          namespace: platform-system

        tolerations:
          - key: "node.kubernetes.io/disk-pressure"
            operator: "Exists"
            effect: "NoSchedule"
  destination:
    server: https://kubernetes.default.svc
    namespace: platform-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 2: Validate YAML**

```bash
yq eval '.' gitops/apps/apisix.yaml > /dev/null && echo "OK"
```
Expected: OK

- [ ] **Step 3: Commit**

```bash
git add gitops/apps/apisix.yaml
git commit -m "feat(apisix): add APISIX ArgoCD application"
```

---

### Task 3: Create `gitops/apps/apisix-dashboard.yaml`

**Files:**
- Create: `gitops/apps/apisix-dashboard.yaml`

- [ ] **Step 1: Create Dashboard ArgoCD Application**

```yaml
# gitops/apps/apisix-dashboard.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: apisix-dashboard
  namespace: devtools
  annotations:
    argocd.argoproj.io/sync-wave: "4"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://charts.apiseven.com
    chart: apisix-dashboard
    targetRevision: "0.9.0"
    helm:
      valuesObject:
        image:
          repository: apache/apisix-dashboard
          tag: "3.0.0"
        podLabels:
          istio.io/dataplane-mode: "none"
        config:
          conf:
            listen:
              host: "0.0.0.0"
              port: 9000
            etcd:
              endpoints:
                - "apisix-etcd.platform-system.svc.cluster.local:2379"
              prefix: "/apisix"
              timeout: 30
            authentication:
              secret: "apisix-dashboard-secret"  # overridden by script
              expire_time: 3600
              users:
                - username: admin
                  password: "admin"  # overridden via env var
        service:
          type: ClusterIP
          port: 9000
        resources:
          requests:
            cpu: 50m
            memory: 128Mi
          limits:
            cpu: 200m
            memory: 256Mi
        tolerations:
          - key: "node.kubernetes.io/disk-pressure"
            operator: "Exists"
            effect: "NoSchedule"
  destination:
    server: https://kubernetes.default.svc
    namespace: platform-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 2: Validate YAML**

```bash
yq eval '.' gitops/apps/apisix-dashboard.yaml > /dev/null && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add gitops/apps/apisix-dashboard.yaml
git commit -m "feat(apisix): add APISIX Dashboard ArgoCD application"
```

---

### Task 4: Update `gitops/apps/app-of-apps.yaml`

app-of-apps는 `apps/` 디렉토리 전체를 sync하는 구조이므로 파일 추가/삭제만으로 자동 반영됨. 별도 수정 불필요.

- [ ] **Step 1: Verify app-of-apps structure**

```bash
cat gitops/apps/app-of-apps.yaml
```
Expected: `path: apps` — 디렉토리 기반 sync 확인

- [ ] **Step 2: No changes needed if directory-based**

app-of-apps가 `path: apps`로 디렉토리 전체를 sync한다면 Task 1~3의 파일 추가/삭제가 자동 반영됨. 만약 개별 Application을 명시적으로 나열한 구조라면 traefik/oauth2-proxy 제거, apisix/apisix-dashboard 추가 필요.

---

## Chunk 2: GitOps — etcd + TLS + APISIX Routes

### Task 5: Create `gitops/resources/apisix-infra.yaml`

etcd, 와일드카드 TLS 인증서, ApisixTls 리소스.

**Files:**
- Create: `gitops/resources/apisix-infra.yaml`

- [ ] **Step 1: Create infrastructure manifest**

```yaml
# gitops/resources/apisix-infra.yaml
---
# etcd Deployment (registry.k8s.io/etcd — no Bitnami)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: apisix-etcd
  namespace: platform-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: apisix-etcd
  template:
    metadata:
      labels:
        app: apisix-etcd
        istio.io/dataplane-mode: "none"
    spec:
      containers:
        - name: etcd
          # registry.k8s.io: K8s 공식 레지스트리, ARM64 지원
          image: registry.k8s.io/etcd:3.5.21-0
          command:
            - etcd
            - --data-dir=/var/etcd/data
            - --listen-client-urls=http://0.0.0.0:2379
            - --advertise-client-urls=http://apisix-etcd.platform-system.svc.cluster.local:2379
            - --listen-peer-urls=http://0.0.0.0:2380
            - --initial-advertise-peer-urls=http://apisix-etcd.platform-system.svc.cluster.local:2380
            - --initial-cluster=default=http://apisix-etcd.platform-system.svc.cluster.local:2380
            - --initial-cluster-state=new
            - --auto-compaction-mode=revision
            - --auto-compaction-retention=1000
          ports:
            - containerPort: 2379
            - containerPort: 2380
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          volumeMounts:
            - name: data
              mountPath: /var/etcd/data
          readinessProbe:
            exec:
              command: ["etcdctl", "endpoint", "health"]
            initialDelaySeconds: 10
            periodSeconds: 5
      tolerations:
        - key: "node.kubernetes.io/disk-pressure"
          operator: "Exists"
          effect: "NoSchedule"
      volumes:
        - name: data
          emptyDir: {}  # dev 환경: 재시작 시 etcd 재초기화, APISIX가 재설정
---
apiVersion: v1
kind: Service
metadata:
  name: apisix-etcd
  namespace: platform-system
spec:
  selector:
    app: apisix-etcd
  ports:
    - name: client
      port: 2379
      targetPort: 2379
    - name: peer
      port: 2380
      targetPort: 2380
---
# Wildcard TLS Certificate (cert-manager, narwhal-ca-issuer)
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: narwhal-wildcard-tls
  namespace: platform-system
spec:
  secretName: narwhal-wildcard-tls
  issuerRef:
    name: narwhal-ca-issuer
    kind: ClusterIssuer
  dnsNames:
    - "*.local.narwhal.io"
  duration: 8760h
  renewBefore: 720h
---
# ApisixTls: APISIX가 와일드카드 인증서로 TLS 종료
apiVersion: apisix.apache.org/v2
kind: ApisixTls
metadata:
  name: narwhal-wildcard
  namespace: platform-system
spec:
  hosts:
    - "*.local.narwhal.io"
  secret:
    name: narwhal-wildcard-tls
    namespace: platform-system
```

- [ ] **Step 2: Validate YAML**

```bash
yq eval '.' gitops/resources/apisix-infra.yaml > /dev/null && echo "OK"
```

- [ ] **Step 3: Create ArgoCD app for infra resources**

`gitops/apps/apisix-infra.yaml` 파일 생성:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: apisix-infra
  namespace: devtools
  annotations:
    argocd.argoproj.io/sync-wave: "1"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git
    targetRevision: HEAD
    path: resources
    directory:
      include: "apisix-infra.yaml"
  destination:
    server: https://kubernetes.default.svc
    namespace: platform-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 4: Commit**

```bash
git add gitops/resources/apisix-infra.yaml gitops/apps/apisix-infra.yaml
git commit -m "feat(apisix): add etcd, wildcard TLS, ApisixTls infra resources"
```

---

### Task 6: Create `gitops/resources/apisix-routes.yaml`

모든 앱의 ApisixRoute 정의. OIDC 플러그인 포함.

**Files:**
- Create: `gitops/resources/apisix-routes.yaml`

- [ ] **Step 1: Create routes manifest**

각 앱별 ApisixRoute. `openid-connect` 플러그인은 Keycloak `apisix` 클라이언트 사용. OIDC 시크릿은 `$secret://kubernetes/k8s-1/apisix-oidc-config/<key>` 참조.

```yaml
# gitops/resources/apisix-routes.yaml
---
# ArgoCD
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: argocd
  namespace: platform-system
spec:
  http:
    - name: argocd
      match:
        hosts:
          - argocd.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: argocd-server
          servicePort: 80
          serviceNamespace: devtools
          resolveGranularity: service
      plugins:
        - name: openid-connect
          enable: true
          config:
            client_id: "apisix"
            client_secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/client_secret"
            discovery: "https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration"
            redirect_uri: "https://argocd.local.narwhal.io/apisix/callback"
            scope: "openid email profile groups"
            bearer_only: false
            ssl_verify: false
            logout_path: "/apisix/logout"
            set_userinfo_header: true
            set_access_token_header: true
            access_token_in_authorization_header: true
            session:
              secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/session_secret"
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
                X-Frame-Options: "SAMEORIGIN"
                X-XSS-Protection: "1; mode=block"
---
# Grafana
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: grafana
  namespace: platform-system
spec:
  http:
    - name: grafana
      match:
        hosts:
          - grafana.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: prometheus-stack-grafana
          servicePort: 80
          serviceNamespace: monitoring
          resolveGranularity: service
      plugins:
        - name: openid-connect
          enable: true
          config:
            client_id: "apisix"
            client_secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/client_secret"
            discovery: "https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration"
            redirect_uri: "https://grafana.local.narwhal.io/apisix/callback"
            scope: "openid email profile groups"
            bearer_only: false
            ssl_verify: false
            logout_path: "/apisix/logout"
            set_userinfo_header: true
            set_access_token_header: true
            access_token_in_authorization_header: true
            session:
              secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/session_secret"
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
                X-Frame-Options: "SAMEORIGIN"
---
# Gitea
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: gitea
  namespace: platform-system
spec:
  http:
    - name: gitea
      match:
        hosts:
          - gitea.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: gitea-http
          servicePort: 3000
          serviceNamespace: devtools
          resolveGranularity: service
      plugins:
        - name: openid-connect
          enable: true
          config:
            client_id: "apisix"
            client_secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/client_secret"
            discovery: "https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration"
            redirect_uri: "https://gitea.local.narwhal.io/apisix/callback"
            scope: "openid email profile groups"
            bearer_only: false
            ssl_verify: false
            logout_path: "/apisix/logout"
            set_userinfo_header: true
            set_access_token_header: true
            access_token_in_authorization_header: true
            session:
              secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/session_secret"
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
---
# Harbor (100MB body limit for image push)
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: harbor
  namespace: platform-system
spec:
  http:
    - name: harbor
      match:
        hosts:
          - harbor.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: harbor-nginx
          servicePort: 80
          serviceNamespace: devtools
          resolveGranularity: service
      plugins:
        - name: openid-connect
          enable: true
          config:
            client_id: "apisix"
            client_secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/client_secret"
            discovery: "https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration"
            redirect_uri: "https://harbor.local.narwhal.io/apisix/callback"
            scope: "openid email profile groups"
            bearer_only: false
            ssl_verify: false
            logout_path: "/apisix/logout"
            set_userinfo_header: true
            set_access_token_header: true
            access_token_in_authorization_header: true
            session:
              secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/session_secret"
        - name: proxy-rewrite
          enable: true
          config:
            # 100MB body limit for Docker image push
            headers:
              set: {}
        - name: client-control
          enable: true
          config:
            max_body_size: 104857600  # 100MB
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
---
# Headlamp
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: headlamp
  namespace: platform-system
spec:
  http:
    - name: headlamp
      match:
        hosts:
          - headlamp.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: headlamp
          servicePort: 80
          serviceNamespace: devtools
          resolveGranularity: service
      plugins:
        - name: openid-connect
          enable: true
          config:
            client_id: "apisix"
            client_secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/client_secret"
            discovery: "https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration"
            redirect_uri: "https://headlamp.local.narwhal.io/apisix/callback"
            scope: "openid email profile groups"
            bearer_only: false
            ssl_verify: false
            logout_path: "/apisix/logout"
            set_userinfo_header: true
            session:
              secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/session_secret"
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
---
# OpenBao
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: openbao
  namespace: platform-system
spec:
  http:
    - name: openbao
      match:
        hosts:
          - openbao.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: openbao
          servicePort: 8200
          serviceNamespace: storage
          resolveGranularity: service
      plugins:
        - name: openid-connect
          enable: true
          config:
            client_id: "apisix"
            client_secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/client_secret"
            discovery: "https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration"
            redirect_uri: "https://openbao.local.narwhal.io/apisix/callback"
            scope: "openid email profile groups"
            bearer_only: false
            ssl_verify: false
            logout_path: "/apisix/logout"
            set_userinfo_header: true
            session:
              secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/session_secret"
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
---
# Hubble UI
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: hubble
  namespace: platform-system
spec:
  http:
    - name: hubble
      match:
        hosts:
          - hubble.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: hubble-ui
          servicePort: 80
          serviceNamespace: kube-system
          resolveGranularity: service
      plugins:
        - name: openid-connect
          enable: true
          config:
            client_id: "apisix"
            client_secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/client_secret"
            discovery: "https://keycloak.local.narwhal.io/realms/kubernetes/.well-known/openid-configuration"
            redirect_uri: "https://hubble.local.narwhal.io/apisix/callback"
            scope: "openid email profile groups"
            bearer_only: false
            ssl_verify: false
            logout_path: "/apisix/logout"
            set_userinfo_header: true
            session:
              secret: "$secret://kubernetes/k8s-1/apisix-oidc-config/session_secret"
        - name: response-rewrite
          enable: true
          config:
            headers:
              set:
                Strict-Transport-Security: "max-age=31536000; includeSubDomains"
                X-Content-Type-Options: "nosniff"
---
# APISIX Dashboard (no OIDC — dashboard has its own auth)
apiVersion: apisix.apache.org/v2
kind: ApisixRoute
metadata:
  name: apisix-dashboard
  namespace: platform-system
spec:
  http:
    - name: apisix-dashboard
      match:
        hosts:
          - apisix-dashboard.local.narwhal.io
        paths:
          - "/*"
      backends:
        - serviceName: apisix-dashboard
          servicePort: 9000
          serviceNamespace: platform-system
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

- [ ] **Step 2: Validate YAML**

```bash
yq eval '.' gitops/resources/apisix-routes.yaml > /dev/null && echo "OK"
```

- [ ] **Step 3: Create apisix-routes ArgoCD app**

`gitops/apps/apisix-routes.yaml` 파일 생성:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: apisix-routes
  namespace: devtools
  annotations:
    argocd.argoproj.io/sync-wave: "5"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: http://gitea-http.devtools.svc.cluster.local:3000/gitea-admin/narwhal-gitops.git
    targetRevision: HEAD
    path: resources
    directory:
      include: "apisix-routes.yaml"
  destination:
    server: https://kubernetes.default.svc
    namespace: platform-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
      - ServerSideApply=true
```

- [ ] **Step 4: Commit**

```bash
git add gitops/resources/apisix-routes.yaml gitops/apps/apisix-routes.yaml
git commit -m "feat(apisix): add ApisixRoute for all platform apps"
```

---

## Chunk 3: Script Updates

### Task 7: Update `scripts/cluster/08-1-networking.sh` — Replace Traefik with APISIX

**Files:**
- Modify: `scripts/cluster/08-1-networking.sh`

Traefik 설치 블록 (라인 40~79) 전체를 APISIX 설치 블록으로 교체.

- [ ] **Step 1: Read current file and identify Traefik block boundaries**

```bash
grep -n "Traefik\|apisix\|cert-manager" scripts/cluster/08-1-networking.sh
```

- [ ] **Step 2: Replace Traefik section with APISIX**

Traefik 블록을 다음으로 교체:

```bash
#=========================================
# APISIX (API Gateway — replaces Traefik + OAuth2-Proxy)
#=========================================
echo "=== Installing APISIX ==="

helm repo add apisix https://charts.apiseven.com
helm repo update apisix

# Install APISIX CRDs with server-side apply to avoid field manager conflicts
echo "Applying APISIX CRDs..."
helm pull apisix/apisix --version 2.9.0 --untar --untardir /tmp/apisix-chart
for f in /tmp/apisix-chart/apisix/crds/*.yaml; do
  kubectl apply --server-side --force-conflicts -f "${f}" 2>&1 | tail -1
done
rm -rf /tmp/apisix-chart

# Deploy etcd (uses registry.k8s.io/etcd — no Bitnami)
# etcd is deployed via apisix-infra.yaml GitOps resource; here we apply it directly for bootstrap
echo "Deploying etcd for APISIX..."
kubectl apply -f /home/vagrant/configs/gitops/resources/apisix-infra.yaml || true

# Wait for etcd to be ready
echo "Waiting for etcd..."
kubectl wait --for=condition=Available deployment/apisix-etcd -n platform-system --timeout=120s || true

# Install APISIX + Ingress Controller (etcd.enabled=false → uses external etcd)
cat > /tmp/apisix-values.yaml << 'EOF'
apisix:
  enabled: true
  image:
    repository: apache/apisix
    tag: "3.11.0-debian"
  podLabels:
    istio.io/dataplane-mode: "none"
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
gateway:
  type: LoadBalancer
  annotations:
    metallb.universe.tf/loadBalancerIPs: "192.168.56.200"
  http:
    enabled: true
    servicePort: 80
    containerPort: 9080
  tls:
    enabled: true
    servicePort: 443
    containerPort: 9443
admin:
  enabled: true
  type: ClusterIP
  port: 9180
etcd:
  enabled: false
  host:
    - "http://apisix-etcd.platform-system.svc.cluster.local:2379"
  prefix: "/apisix"
  timeout: 30
ingressController:
  enabled: true
  image:
    repository: apache/apisix-ingress-controller
    tag: "1.8.0"
  podLabels:
    istio.io/dataplane-mode: "none"
  config:
    apisix:
      serviceNamespace: platform-system
      adminAPIVersion: "v3"
    kubernetes:
      watchNamespaces: []
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi
tolerations:
  - key: "node.kubernetes.io/disk-pressure"
    operator: "Exists"
    effect: "NoSchedule"
EOF

helm upgrade --install apisix apisix/apisix \
  --namespace platform-system \
  --create-namespace \
  --version 2.9.0 \
  --skip-crds \
  -f /tmp/apisix-values.yaml || echo "WARN: APISIX install issue, continuing..."

rm /tmp/apisix-values.yaml

# Wait for APISIX gateway to be ready
echo "Waiting for APISIX gateway..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=apisix -n platform-system --timeout=180s || true

echo "APISIX installed"
```

- [ ] **Step 3: Verify bash syntax**

```bash
bash -n scripts/cluster/08-1-networking.sh && echo "Syntax OK"
```
Expected: Syntax OK

- [ ] **Step 4: Run shellcheck**

```bash
shellcheck scripts/cluster/08-1-networking.sh -x
```
Expected: No errors (style warnings acceptable)

- [ ] **Step 5: Commit**

```bash
git add scripts/cluster/08-1-networking.sh
git commit -m "feat(apisix): replace Traefik with APISIX in 08-1-networking.sh"
```

---

### Task 8: Update `scripts/cluster/08-3-security.sh` — Remove OAuth2-Proxy

**Files:**
- Modify: `scripts/cluster/08-3-security.sh`

- [ ] **Step 1: Read current file**

OAuth2-Proxy 섹션 (라인 83~131) 전체를 제거.

- [ ] **Step 2: Remove OAuth2-Proxy section**

헤더도 업데이트:
```bash
# 변경 전:
echo "=== Installing Security Apps (Kyverno, Headlamp, OAuth2-Proxy) ==="
# 변경 후:
echo "=== Installing Security Apps (Kyverno, Headlamp) ==="
```

OAuth2-Proxy 설치 블록 전체 삭제:
```bash
# 삭제 대상 (라인 83~131):
#=========================================
# OAuth2 Proxy (Gateway Authentication)
#=========================================
... (전체 블록)
```

- [ ] **Step 3: Verify syntax and shellcheck**

```bash
bash -n scripts/cluster/08-3-security.sh && echo "OK"
shellcheck scripts/cluster/08-3-security.sh -x
```

- [ ] **Step 4: Commit**

```bash
git add scripts/cluster/08-3-security.sh
git commit -m "feat(apisix): remove OAuth2-Proxy from 08-3-security.sh"
```

---

### Task 9: Update `scripts/cluster/11-3-keycloak-clients.sh` — Add `apisix` OIDC Client

**Files:**
- Modify: `scripts/cluster/11-3-keycloak-clients.sh`

기존 `oauth2-proxy` 클라이언트에 더해 `apisix` 클라이언트를 추가. APISIX의 `/apisix/callback` redirect URI 사용.

- [ ] **Step 1: Read current client creation code**

기존 클라이언트 목록 확인: `oauth2-proxy`, `argocd`, `grafana`, `gitea`, `harbor`, `headlamp`, `kubernetes`

- [ ] **Step 2: Add `apisix` client creation after existing clients**

```bash
#=========================================
# APISIX OIDC Client
#=========================================
echo "Creating apisix OIDC client..."

APISIX_CLIENT_SECRET=$(generate_password)

kubectl exec -n iam "${KEYCLOAK_POD}" -- \
  /opt/keycloak/bin/kcadm.sh create clients \
  -r kubernetes \
  -s clientId=apisix \
  -s enabled=true \
  -s protocol=openid-connect \
  -s publicClient=false \
  -s secret="${APISIX_CLIENT_SECRET}" \
  -s 'redirectUris=["https://argocd.local.narwhal.io/apisix/callback","https://grafana.local.narwhal.io/apisix/callback","https://gitea.local.narwhal.io/apisix/callback","https://harbor.local.narwhal.io/apisix/callback","https://headlamp.local.narwhal.io/apisix/callback","https://openbao.local.narwhal.io/apisix/callback","https://hubble.local.narwhal.io/apisix/callback","https://apisix-dashboard.local.narwhal.io/apisix/callback"]' \
  -s 'webOrigins=["https://*.local.narwhal.io"]' \
  -s standardFlowEnabled=true \
  -s directAccessGrantsEnabled=false 2>/dev/null || echo "WARN: apisix client may already exist"

# Get apisix client ID for mapper creation
APISIX_CLIENT_ID=$(kubectl exec -n iam "${KEYCLOAK_POD}" -- \
  /opt/keycloak/bin/kcadm.sh get clients -r kubernetes 2>/dev/null | \
  jq -r '.[] | select(.clientId=="apisix") | .id')

# Add audience mapper (required for OIDC token validation)
if [[ -n "${APISIX_CLIENT_ID}" ]]; then
  kubectl exec -n iam "${KEYCLOAK_POD}" -- \
    /opt/keycloak/bin/kcadm.sh create \
    clients/${APISIX_CLIENT_ID}/protocol-mappers/models \
    -r kubernetes \
    -s name=apisix-audience \
    -s protocol=openid-connect \
    -s protocolMapper=oidc-audience-mapper \
    -s 'config={"included.client.audience":"apisix","access.token.claim":"true"}' \
    2>/dev/null || echo "WARN: audience mapper may already exist"
fi

# Store APISIX OIDC credentials + session secret in K8s Secret
APISIX_SESSION_SECRET=$(generate_password 32)

kubectl create secret generic apisix-oidc-config \
  --namespace platform-system \
  --from-literal=client_id=apisix \
  --from-literal=client_secret="${APISIX_CLIENT_SECRET}" \
  --from-literal=session_secret="${APISIX_SESSION_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "apisix OIDC client created, secret stored in apisix-oidc-config"
```

- [ ] **Step 3: Verify syntax and shellcheck**

```bash
bash -n scripts/cluster/11-3-keycloak-clients.sh && echo "OK"
shellcheck scripts/cluster/11-3-keycloak-clients.sh -x
```

- [ ] **Step 4: Commit**

```bash
git add scripts/cluster/11-3-keycloak-clients.sh
git commit -m "feat(apisix): add apisix OIDC client to Keycloak"
```

---

### Task 10: Update `scripts/cluster/08-6-tls-routes.sh` — Remove Traefik routes, add APISIX route apply

**Files:**
- Modify: `scripts/cluster/08-6-tls-routes.sh`

- [ ] **Step 1: Read current file**

현재 파일에서 Traefik routes 적용 부분 확인.

- [ ] **Step 2: Replace Traefik route apply with APISIX route apply**

Traefik HTTPRoute/Middleware apply 부분을:
```bash
# Apply APISIX routes (after cert is ready and APISIX is running)
echo "Applying APISIX routes..."
kubectl apply -f /home/vagrant/configs/gitops/resources/apisix-routes.yaml || true

# Wait for APISIX Ingress Controller to sync routes
echo "Waiting for APISIX routes to sync..."
sleep 10
kubectl get apisixroute -n platform-system
```

- [ ] **Step 3: Verify syntax**

```bash
bash -n scripts/cluster/08-6-tls-routes.sh && echo "OK"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/cluster/08-6-tls-routes.sh
git commit -m "feat(apisix): update tls-routes script for APISIX"
```

---

## Chunk 4: VERSIONS.md + dnsmasq + Final Validation

### Task 11: Update `VERSIONS.md`

**Files:**
- Modify: `VERSIONS.md`

- [ ] **Step 1: Remove Traefik/OAuth2-Proxy entries**

```
# 제거:
| Traefik | v39.0.0 (chart) / v3.6.7 (app) | ... |
| OAuth2-Proxy | 10.1.3 (chart) | ... |
```

- [ ] **Step 2: Add APISIX entries**

```markdown
| APISIX | 3.11.0 (app) / 2.9.0 (chart) | API Gateway, OIDC authentication |
| APISIX Ingress Controller | 1.8.0 | K8s CRD controller for APISIX |
| APISIX Dashboard | 3.0.0 (app) / 0.9.0 (chart) | APISIX management UI |
| etcd (APISIX) | 3.5.21 (registry.k8s.io/etcd:3.5.21-0) | APISIX configuration store |
```

- [ ] **Step 3: Commit**

```bash
git add VERSIONS.md
git commit -m "docs: update VERSIONS.md for APISIX migration"
```

---

### Task 12: Update `scripts/cluster/10-dnsmasq.sh` — Update DNS test domains

**Files:**
- Modify: `scripts/cluster/10-dnsmasq.sh`

- [ ] **Step 1: Find DNS test references**

```bash
grep -n "oauth2-proxy\|traefik" scripts/cluster/10-dnsmasq.sh
```

- [ ] **Step 2: Replace test domain references**

`oauth2-proxy.local.narwhal.io` → `apisix.local.narwhal.io` 또는 `argocd.local.narwhal.io`
`traefik.local.narwhal.io` → `apisix-dashboard.local.narwhal.io`

- [ ] **Step 3: Commit**

```bash
git add scripts/cluster/10-dnsmasq.sh
git commit -m "feat(apisix): update dnsmasq DNS test domains"
```

---

### Task 13: Final Validation

- [ ] **Step 1: Validate all new/modified YAML files**

```bash
yq eval '.' gitops/apps/apisix.yaml > /dev/null && echo "apisix.yaml OK"
yq eval '.' gitops/apps/apisix-dashboard.yaml > /dev/null && echo "apisix-dashboard.yaml OK"
yq eval '.' gitops/apps/apisix-infra.yaml > /dev/null && echo "apisix-infra.yaml OK"
yq eval '.' gitops/apps/apisix-routes.yaml > /dev/null && echo "apisix-routes.yaml OK"
yq eval '.' gitops/resources/apisix-infra.yaml > /dev/null && echo "apisix-infra resources OK"
yq eval '.' gitops/resources/apisix-routes.yaml > /dev/null && echo "apisix-routes resources OK"
```
Expected: 모두 OK

- [ ] **Step 2: Shellcheck all modified scripts**

```bash
shellcheck \
  scripts/cluster/08-1-networking.sh \
  scripts/cluster/08-3-security.sh \
  scripts/cluster/08-6-tls-routes.sh \
  scripts/cluster/10-dnsmasq.sh \
  scripts/cluster/11-3-keycloak-clients.sh \
  -x 2>&1 | grep -E "error:|SC[0-9]+" | head -30
```
Expected: error 없음

- [ ] **Step 3: Verify deleted files are gone**

```bash
ls gitops/apps/traefik.yaml 2>/dev/null && echo "ERROR: should be deleted" || echo "OK: deleted"
ls gitops/apps/oauth2-proxy.yaml 2>/dev/null && echo "ERROR: should be deleted" || echo "OK: deleted"
ls gitops/resources/traefik-routes.yaml 2>/dev/null && echo "ERROR: should be deleted" || echo "OK: deleted"
```
Expected: 모두 OK: deleted

- [ ] **Step 4: Verify no Traefik/OAuth2-Proxy references remain in active scripts**

```bash
grep -r "traefik\|oauth2.proxy" scripts/cluster/ --include="*.sh" \
  | grep -v "bak/\|deprecated\|#" | grep -v "WARN\|echo"
```
Expected: 출력 없음 (참조 없음)

- [ ] **Step 5: Verify VERSIONS.md consistency**

```bash
grep -i "apisix" VERSIONS.md
grep -i "traefik\|oauth2" VERSIONS.md
```
Expected: APISIX 항목 존재, Traefik/OAuth2-Proxy 항목 없음

- [ ] **Step 6: Final commit**

```bash
git add -A
git status
git commit -m "feat(apisix): complete Traefik+OAuth2-Proxy → APISIX migration" --allow-empty
```

---

## Post-Migration Verification (클러스터 실행 시)

클러스터 프로비저닝 후 다음으로 검증:

```bash
# APISIX pods 상태
vagrant ssh master-1 -c "kubectl get pods -n platform-system -l app.kubernetes.io/name=apisix"

# etcd 상태
vagrant ssh master-1 -c "kubectl get pods -n platform-system -l app=apisix-etcd"

# ApisixRoute 등록 확인
vagrant ssh master-1 -c "kubectl get apisixroute -n platform-system"

# ApisixTls 확인
vagrant ssh master-1 -c "kubectl get apisixtls -n platform-system"

# 와일드카드 TLS 인증서
vagrant ssh master-1 -c "kubectl get certificate -n platform-system"

# APISIX LoadBalancer IP (192.168.56.200이어야 함)
vagrant ssh master-1 -c "kubectl get svc -n platform-system -l app.kubernetes.io/name=apisix"

# 엔드포인트 테스트
curl -k https://argocd.local.narwhal.io/healthz
curl -k https://grafana.local.narwhal.io/api/health
```

---

## Known Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| APISIX Kubernetes Secret Provider 버전 호환성 | `$secret://kubernetes/` 미지원 시 env var 방식으로 폴백 (`extraEnvVarsSecret` + `$ENV::`) |
| etcd `emptyDir` 재시작 시 데이터 소실 | APISIX Ingress Controller가 재시작 시 CRD에서 etcd 재동기화하므로 자동 복구 |
| cross-namespace backend refs | APISIX IC `watchNamespaces: []` (전체) + 각 서비스 ns 확인 필요 |
| ARM64 이미지 | `apache/apisix:3.11.0-debian`, `apache/apisix-ingress-controller:1.8.0` — multi-arch 확인 필요. 미지원 시 `3.x.x-centos` 태그 시도 |
| APISIX Dashboard 인증 | 기본 admin/admin → 프로비저닝 스크립트에서 패스워드 변경 필요 |

---

## Migration Rollback

롤백이 필요한 경우:
```bash
# Traefik 재설치
helm upgrade --install traefik traefik/traefik -n platform-system --version 39.0.0 ...

# APISIX 제거
helm uninstall apisix -n platform-system
kubectl delete deployment apisix-etcd -n platform-system

# 이전 GitOps 파일 git revert
git revert HEAD~N
```
