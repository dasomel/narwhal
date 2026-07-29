# Kakao Cloud 서비스 도메인 접속 가이드

> **이 문서는 Kakao Cloud 배포 전용이다.** Vagrant 클러스터는
> [`dns-access.md`](./dns-access.md)를 본다. 인프라 프로비저닝은
> [`cloud-deployment.md`](./cloud-deployment.md), Terraform 사용법은
> [`../csp/kakao-cloud/terraform/README.ko.md`](../csp/kakao-cloud/terraform/README.ko.md).

## 1. 왜 `kakao.*`이고, 왜 `/etc/hosts`인가

**도메인이 다른 이유.** Kakao 클러스터는 `*.kakao.narwhal.internal`, Vagrant 클러스터는
`*.local.narwhal.internal`을 쓴다. 두 클러스터가 **같은 서비스 이름**(argocd, grafana, …)을
서빙하고, 두 이름 모두 오퍼레이터 PC의 `/etc/hosts`로만 해석된다. 도메인을 공유하면 한쪽
항목이 다른 쪽을 덮어써서 **자신도 모르는 채 엉뚱한 클러스터를 조작하게 된다.** 도메인을
분리하면 두 클러스터를 동시에 열어둘 수 있다.

**DNS가 없는 이유.** Vagrant에서는 master 노드의 dnsmasq가 이름을 서빙하고 리졸버로 지정한다.
Kakao에서는 그 방식이 성립하지 않는다:

- `PROVIDER=kakao`는 `10-dnsmasq.sh`를 건너뛴다
- 노드가 프라이빗 서브넷(`172.16.0.0/24`)에 있어 PC에서 리졸버로 지정할 수 없다
- `.internal`은 ICANN이 사설 전용으로 예약한 TLD라 공개 DNS에 등록할 수 없다

이름을 서빙하는 것은 **worker LoadBalancer의 공인 IP** 하나뿐이고, 그 IP를 이름에 붙이는
유일한 수단이 `/etc/hosts`다.

### 요청이 흐르는 경로

```
브라우저 (오퍼레이터 PC)
  │  https://argocd.kakao.narwhal.internal
  │
  ├─ /etc/hosts 가 이름 → 210.109.82.157 로 해석
  ↓
worker LB (공인, TCP 443 → NodePort 31443)
  ↓
worker 노드 3대 (172.16.0.21-23)
  ↓
APISIX Gateway (platform-system)
  │  ├─ TLS 종료 (narwhal CA 발급 인증서)
  │  ├─ Host 헤더로 ApisixRoute 매칭
  │  └─ 라우트에 따라 OIDC 인증 / SSO 부트스트랩 / 그대로 전달
  ↓
ApisixUpstream → <service>.<namespace>.svc.cluster.local
```

`master LB`(공인)는 kubectl 전용(6443)이고 서비스 도메인과 무관하다. 서비스 트래픽은
**전부 worker LB 하나**를 지난다.

### 이름은 클러스터 안에서도 해석돼야 한다

`/etc/hosts`는 브라우저 쪽 절반일 뿐이다. 나머지 절반은 **클러스터 내부**에 있다 — 게이트웨이
OIDC 라우트는 APISIX가 Keycloak의 discovery URL을 **이름으로** 조회하기 때문이다:

```
APISIX 파드 → https://keycloak.kakao.narwhal.internal/realms/narwhal/.well-known/openid-configuration
```

이 이름이 파드 안에서 해석되지 않으면 해당 라우트는 전부 **HTTP 500**이 된다. Vagrant는
master의 dnsmasq가 받아주지만 Kakao에는 그런 것이 없으므로, `10-dnsmasq.sh`의
`PROVIDER=kakao` 분기가 CoreDNS에 hairpin 존을 넣는다:

```
kakao.narwhal.internal:53 {
    errors
    cache 30
    template IN A {
        answer "{{ .Name }} 30 IN A <apisix-gateway ClusterIP>"
    }
}
```

**존 전체를 와일드카드로 답한다.** 이름을 하나씩 나열하지 않는 이유는, Kakao에는 이 존을
넘길 상위 리졸버가 없어서 목록에서 빠진 이름은 갈 곳이 없기 때문이다 (Vagrant의 hosts 블록은
dnsmasq로 fallthrough가 되므로 누락이 드러나지 않는다). 라우트가 추가돼도 손댈 필요가 없다.

내부 트래픽은 LB를 우회해 APISIX ClusterIP로 바로 간다 — 클러스터 밖으로 나갔다 들어오지
않는다. 확인:

```bash
kubectl -n kube-system get cm coredns -o jsonpath='{.data.Corefile}' | head -10
```

---

## 2. 서비스 도메인 일람

모두 `https://<이름>.kakao.narwhal.internal`. 라우트 정의는
[`gitops/charts/narwhal-platform/templates/apisix-routes.yaml`](../gitops/charts/narwhal-platform/templates/apisix-routes.yaml)가
단일 출처이며, `baseDomain` 값 하나로 전체 도메인이 결정된다.

| 서비스 | 도메인 | 용도 | 백엔드 (ns/service) | 인증 |
|--------|--------|------|---------------------|------|
| Keycloak | `keycloak.` | IAM / SSO 발급자 | `iam/keycloak-service:8080` | 자체 (admin 콘솔) |
| ArgoCD | `argocd.` | GitOps CD | `devtools/argocd-server:80` | 앱 OIDC + 무클릭 리다이렉트 |
| Narwhal Portal | `portal.` | 클러스터 관리 UI | `devtools/narwhal-portal:3000` | 앱 OIDC (NextAuth) |
| Gitea | `gitea.` | Git 서버 (GitOps 소스) | `devtools/gitea-http:3000` | 게이트웨이 OIDC (+ git CLI 우회) |
| Harbor | `harbor.` | 컨테이너 레지스트리 | `devtools/harbor:80` | 앱 OIDC + 로컬 admin |
| Headlamp | `headlamp.` | Kubernetes UI | `devtools/headlamp:80` | 앱 OIDC |
| K8s Dashboard | `dashboard.` | Kubernetes UI (공식) | `devtools/kubernetes-dashboard-kong-proxy:443` | 제로클릭 SSO (public+PKCE) |
| Grafana | `grafana.` | 모니터링 대시보드 | `monitoring/prometheus-stack-grafana:80` | 앱 OIDC + 로컬 admin |
| Prometheus | `prometheus.` | 메트릭 조회 | `monitoring/…-prometheus:9090` | 게이트웨이 OIDC |
| Alertmanager | `alertmanager.` | 알림 라우팅 | `monitoring/…-alertmanager:9093` | 게이트웨이 OIDC |
| OpenBao | `openbao.` | 시크릿 관리 | `storage/openbao:8200` | 제로클릭 SSO + root token |
| Velero UI | `velero-ui.` | 백업/복구 UI | `storage/velero-ui:3000` | 제로클릭 SSO |
| Hubble | `hubble.` | Cilium 네트워크 관찰 | `kube-system/hubble-ui:80` | 게이트웨이 OIDC |
| NFS Quota | `nfs-quota.` | 스토리지 쿼터 대시보드 | `nfs-quota-agent/nfs-quota-agent:8080` | 게이트웨이 OIDC (hubble 클라이언트 공유) |

**클러스터에서 직접 확인** — 위 표는 커밋 시점 기준이므로, 실제 목록은 클러스터에 묻는다:

```bash
kubectl get apisixroute -A -o jsonpath='{range .items[*].spec.http[*]}{.match.hosts[*]}{"\n"}{end}' | sort -u
```

### 인증 방식 4종류

각 서비스가 SSO를 어떻게 붙였는지가 다르고, **실패했을 때 어디를 봐야 하는지**가 달라진다.

| 방식 | 대상 | 동작 | 실패 시 확인 |
|------|------|------|--------------|
| **게이트웨이 OIDC** | Gitea, Prometheus, Alertmanager, Hubble, NFS Quota | APISIX `openid-connect` 플러그인이 로그인 전 트래픽을 Keycloak으로 보낸다. 백엔드는 인증을 모른다 | `platform-system`의 `*-oidc-secret`, 리다이렉트 URI `…/apisix/callback` |
| **앱 OIDC** | ArgoCD, Grafana, Harbor, Headlamp, Portal | 앱이 직접 OIDC 클라이언트로 동작. APISIX는 전달만 | 앱 자체 설정 + Keycloak 클라이언트 시크릿 |
| **제로클릭 SSO** | K8s Dashboard, OpenBao, Velero UI | APISIX가 서빙하는 동일 오리진 부트스트랩 페이지(`/sso`, `/sso/callback`)의 브라우저 JS가 code를 교환하고 토큰을 앱에 주입 | `/sso` 라우트, Dashboard는 **public + PKCE** 클라이언트 |
| **자체** | Keycloak | 자기 자신이 발급자 | `iam/keycloak-initial-admin` |

두 개의 예외 라우트가 있다:

- `gitea-git-bypass` (priority 100) — `git clone`/`push`, `/api/v1/`, OAuth 콜백 경로는 OIDC를
  **우회**한다. 브라우저가 아닌 git CLI는 리다이렉트를 따라갈 수 없기 때문이다.
- `argocd-redirect` / `harbor-redirect` — 세션 쿠키가 없는 첫 방문을 SSO 로그인 경로로 302
  시켜 "로그인 버튼 클릭" 한 단계를 없앤다.

---

## 3. 접속 준비 (3단계)

### 1) kubectl 컨텍스트 — 먼저 필요하다

`setup-hosts-kakao.sh`가 ApisixRoute에서 호스트명을 읽어오므로 클러스터 접근이 선행돼야 한다.

```bash
cd /Users/m/Documents/IdeaProjects/20.dasomel/idp/narwhal
./scripts/cloud/set-config-kakao.sh
```

master LB로 SSH 터널을 열고 `narwhal-kakao` 컨텍스트를 만든다. 자세한 내용은
[`kubeconfig.md`](./kubeconfig.md).

### 2) `/etc/hosts` 등록

```bash
./scripts/cloud/setup-hosts-kakao.sh            # 무엇이 써질지 미리 본다
./scripts/cloud/setup-hosts-kakao.sh --apply    # 실제 등록 (sudo)
./scripts/cloud/setup-hosts-kakao.sh --remove   # 되돌리기
```

호스트명을 하드코딩하지 않고 **클러스터의 ApisixRoute에서 읽는다** — 라우트가 추가되면
다시 실행하는 것만으로 따라간다. 기록은 마커 사이에만 들어가므로 제거가 정확하다:

```
# BEGIN narwhal-kakao
210.109.82.157 alertmanager.kakao.narwhal.internal argocd.kakao.narwhal.internal ...
# END narwhal-kakao
```

### 3) CA 신뢰 (선택)

인증서는 클러스터 자체 CA가 발급하므로 신뢰 전에는 브라우저가 경고한다. `--apply`가
`./narwhal-ca.crt`로 CA를 내보내고 설치 명령을 안내한다. 설치는 시스템 신뢰 저장소를
바꾸는 일이라 **자동으로 하지 않는다**:

```bash
# macOS
sudo security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain ./narwhal-ca.crt

# 되돌리기
sudo security delete-certificate -c 'Narwhal IDP Root CA' /Library/Keychains/System.keychain

# Linux
sudo cp ./narwhal-ca.crt /usr/local/share/ca-certificates/narwhal.crt && sudo update-ca-certificates
```

---

## 4. 계정과 비밀번호

비밀번호는 전부 클러스터가 생성해 Secret에 넣으므로 문서에 적을 값이 없다. 조회는 한 곳에서:

```bash
ssh narwhal-master-1 'sudo env KUBECONFIG=/home/vagrant/.kube/config-local \
  DOMAIN=kakao.narwhal.internal bash /home/vagrant/scripts/test/show-credentials.sh'
```

`DOMAIN`을 넘기지 않으면 출력의 URL이 Vagrant 도메인으로 나온다 (값 자체는 동일).

SSO 로그인에는 realm `narwhal`의 시드 계정 `admin` / `dev` / `view` / `guest`를 쓴다 —
비밀번호는 `iam/keycloak-user-passwords` Secret에 있고 위 명령이 함께 출력한다.

---

## 5. `/etc/hosts` 없이 검증

`/etc/hosts`를 건드리지 않고 도달성만 확인하려면 `curl --resolve`를 쓴다. CI나 남의 PC에서
유용하다:

```bash
LB=$(cd csp/kakao-cloud/terraform && tofu output -raw worker_lb_public_ip)

curl -sk -o /dev/null -w '%{http_code}\n' \
  --resolve "argocd.kakao.narwhal.internal:443:$LB" \
  https://argocd.kakao.narwhal.internal/
```

전체를 한 번에 훑는다:

```bash
LB=$(cd csp/kakao-cloud/terraform && tofu output -raw worker_lb_public_ip)
for h in $(kubectl get apisixroute -A -o jsonpath='{range .items[*].spec.http[*]}{.match.hosts[*]}{"\n"}{end}' | sort -u); do
  code=$(curl -sk -o /dev/null -w '%{http_code}' --resolve "$h:443:$LB" "https://$h/" || echo ERR)
  printf '%-45s %s\n' "$h" "$code"
done
```

정상 판정과 고장 판정:

| 코드 | 의미 |
|------|------|
| `200` `302` `307` | 정상 — 302/307은 SSO 리다이렉트이므로 라우팅이 살아있다는 증거다 |
| `000` | TCP/TLS 단계 실패. LB, APISIX 파드, 또는 **SNI에 맞는 인증서 없음** |
| `404` | Host에 매칭되는 ApisixRoute 없음 |
| `500` | 게이트웨이 OIDC 실패 — 대부분 **클러스터 내부에서 Keycloak 이름이 안 풀림** |
| `502` | 백엔드 다운 (Keycloak 502면 `iam/keycloak-0` 확인) |

302가 어디로 가는지까지 보면 OIDC 경로 전체를 검증할 수 있다:

```bash
curl -sk -o /dev/null -w '%{redirect_url}\n' --max-time 20 \
  --resolve "prometheus.kakao.narwhal.internal:443:$LB" \
  https://prometheus.kakao.narwhal.internal/
# → https://keycloak.kakao.narwhal.internal/realms/narwhal/protocol/openid-connect/auth?client_id=...
```

---

## 6. Vagrant 클러스터와 병행할 때

두 클러스터를 동시에 띄워도 도메인이 갈리므로 충돌하지 않는다. 다만 헷갈릴 여지가 남는 곳:

| | Vagrant | Kakao |
|---|---|---|
| 서비스 도메인 | `*.local.narwhal.internal` | `*.kakao.narwhal.internal` |
| 이름 해석 | master dnsmasq (또는 `/etc/hosts`) | `/etc/hosts` 전용 |
| 서비스 진입점 | MetalLB VIP `192.168.56.200` | worker LB 공인 IP |
| kubectl 컨텍스트 | `narwhal` | `narwhal-kakao` |
| `/etc/hosts` 마커 | (수동) | `# BEGIN narwhal-kakao` |

**CA가 다르다.** 두 클러스터는 각자 CA를 발급하므로 둘 다 신뢰시키려면 인증서를 각각
등록해야 한다. 두 CA의 CN이 같으면(`Narwhal IDP Root CA`) macOS 키체인에서 구분이 어려우니,
삭제할 때 지문을 확인한다:

```bash
openssl x509 -in ./narwhal-ca.crt -noout -fingerprint -sha256
```

**kubectl 컨텍스트를 반드시 확인하고 작업한다** — 도메인은 갈렸어도 `kubectl`은 컨텍스트
하나로 결정된다:

```bash
kubectl config current-context
```

---

## 7. 문제 해결

### 이름이 해석되지 않는다

```bash
grep -A2 'BEGIN narwhal-kakao' /etc/hosts     # 블록이 있는가
./scripts/cloud/setup-hosts-kakao.sh          # 다시 생성해 비교
```

라우트가 추가된 뒤라면 `--apply`를 다시 돌린다. 이 스크립트는 기존 블록을 지우고 새로
쓰므로 반복 실행이 안전하다.

### 이름은 맞는데 연결이 안 된다

```bash
LB=$(cd csp/kakao-cloud/terraform && tofu output -raw worker_lb_public_ip)
nc -vz "$LB" 443                              # LB가 443을 받는가

kubectl -n platform-system get pods -l app.kubernetes.io/name=apisix
kubectl -n platform-system get svc apisix-gateway    # NodePort 31443 인가
```

`PROVIDER=kakao`에서 APISIX는 **NodePort**로 뜨고 앞단을 Kakao LB가 받는다 (MetalLB는
Vagrant 전용이라 렌더링되지 않는다). `LoadBalancer` 타입으로 보이면 provider 값이 잘못
들어간 것이다:

```bash
kubectl -n devtools get application narwhal-apps \
  -o jsonpath='{.spec.source.helm.parameters}' | tr ',' '\n' | grep -i provider
```

### TLS 핸드셰이크에서 끊긴다 (`000`, `tlsv1 alert internal error`)

APISIX에 SNI에 맞는 인증서가 없다는 뜻이다. 인증서 자체가 아니라 **APISIX가 들고 있는
SSL 객체**가 옛것일 수 있으니 양쪽을 따로 본다:

```bash
kubectl -n platform-system logs deploy/apisix --tail=50 | grep "match any SSL certificate"

kubectl -n platform-system get certificate narwhal-wildcard-tls -o jsonpath='{.spec.dnsNames}{"\n"}'
kubectl -n platform-system get apisixtls narwhal-wildcard \
  -o jsonpath='{.spec.snis} gen={.metadata.generation} obs={.status.conditions[0].observedGeneration}{"\n"}'
```

**`generation`과 `observedGeneration`이 다르면 ingress controller가 반영을 멈춘 것이다.**
CR은 새 도메인인데 APISIX는 옛 도메인을 서빙하는 상태로, 재시작이 전체 재동기화를 강제한다:

```bash
kubectl -n platform-system rollout restart deploy/apisix-ingress-controller
kubectl -n platform-system rollout status deploy/apisix-ingress-controller --timeout=180s
```

APISIX에 실제로 올라간 값은 admin API로 확인한다 — APISIX/etcd 컨테이너에는 셸도 curl도
없으므로 임시 파드를 쓴다:

```bash
kubectl -n platform-system run probe --image=curlimages/curl:8.11.0 --restart=Never --command -- sleep 300
kubectl -n platform-system wait --for=condition=Ready pod/probe --timeout=60s
ADMIN_KEY=$(kubectl -n platform-system get secret apisix-admin-key -o jsonpath='{.data.key}' | base64 -d)
BASE="http://apisix-admin.platform-system.svc.cluster.local:9180/apisix/admin"

kubectl -n platform-system exec probe -- curl -s -H "X-API-KEY: ${ADMIN_KEY}" "${BASE}/ssls"
kubectl -n platform-system exec probe -- curl -s -H "X-API-KEY: ${ADMIN_KEY}" "${BASE}/routes"
kubectl -n platform-system delete pod probe
```

`404 {"message":"Key not found"}`가 돌아오면 SSL 문제가 아니라 etcd 빈-prefix 교착이다 —
[`apisix-etcd-recovery.md`](./apisix-etcd-recovery.md)를 따른다.

### 게이트웨이 OIDC 서비스만 500이다

Prometheus, Alertmanager, Gitea, Hubble, NFS Quota만 500이고 나머지는 정상이라면, 원인은
거의 항상 **클러스터 내부에서 Keycloak 이름이 안 풀리는 것**이다:

```bash
kubectl -n platform-system logs deploy/apisix --tail=50 | grep -i "openidc\|parse_domain"
# → accessing discovery url (...) failed: failed to parse domain: ... 3 name error
```

CoreDNS hairpin 존이 없거나 APISIX ClusterIP가 바뀐 경우다. 재적용은 멱등이다:

```bash
ssh narwhal-master-1 'sudo env KUBECONFIG=/home/vagrant/.kube/config-local \
  PROVIDER=kakao DOMAIN=kakao.narwhal.internal bash /home/vagrant/scripts/cluster/10-dnsmasq.sh'
```

확인 — 존에 없는 이름까지 답해야 정상이다(와일드카드):

```bash
kubectl -n platform-system exec probe -- nslookup keycloak.kakao.narwhal.internal
```

### 404가 돌아온다

Host 헤더에 맞는 ApisixRoute가 없다는 뜻이다. 도메인 전환 직후라면 GitOps가 아직 새 도메인을
싱크하지 않았을 수 있다:

```bash
kubectl get apisixroute -A -o jsonpath='{range .items[*].spec.http[*]}{.match.hosts[*]}{"\n"}{end}' | sort -u
kubectl -n devtools get applications
```

`baseDomain`은 app-of-apps 파라미터로 내려가므로, 값을 바꿨다면 Gitea에 푸시돼 있어야 한다
([`gitops-push.md`](./gitops-push.md)) — `kubectl apply`는 selfHeal이 되돌린다.

### 로그인 후 무한 리다이렉트 / `Invalid parameter: redirect_uri`

Keycloak 클라이언트의 redirect URI가 **이전 도메인**에 남아 있다. 도메인을 바꿨다면
클라이언트 등록을 다시 돌려야 한다:

```bash
ssh narwhal-master-1 'sudo env KUBECONFIG=/home/vagrant/.kube/config-local \
  DOMAIN=kakao.narwhal.internal bash /home/vagrant/scripts/cluster/11-3-keycloak-clients.sh'
```

현재 등록된 값 확인:

```bash
kubectl -n iam exec keycloak-0 -c keycloak -- \
  /opt/keycloak/bin/kcadm.sh get clients -r narwhal --fields clientId,redirectUris
```

### 인증서 경고가 계속 뜬다

인증서의 SAN이 이전 도메인일 수 있다. 실제 발급된 이름을 본다:

```bash
LB=$(cd csp/kakao-cloud/terraform && tofu output -raw worker_lb_public_ip)
curl -skv --resolve "argocd.kakao.narwhal.internal:443:$LB" \
  https://argocd.kakao.narwhal.internal/ 2>&1 | grep -i 'subject\|subjectAltName'
```

도메인 변경 후라면 cert-manager가 재발급하도록 인증서를 지운다:

```bash
kubectl -n platform-system get certificate
```

---

## 참고

- [`cloud-deployment.md`](./cloud-deployment.md) — 클라우드 토폴로지, 프록시, airgap 레지스트리
- [`../csp/kakao-cloud/terraform/README.ko.md`](../csp/kakao-cloud/terraform/README.ko.md) — Terraform 사용법
- [`kubeconfig.md`](./kubeconfig.md) — kubectl 인증 방식(cert / token / OIDC)
- [`dns-access.md`](./dns-access.md) — Vagrant 클러스터의 DNS 접속
- [`gitops-push.md`](./gitops-push.md) — GitOps 변경 반영 절차
