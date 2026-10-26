# SSO 로그인 계정 확인 가이드

Narwhal의 모든 웹 UI와 OIDC kubectl은 Keycloak realm **`narwhal`** 하나로 로그인한다. 이 문서는
"어떤 계정으로 들어가는가, 그 계정이 살아 있는가, 비밀번호는 어디 있는가"를 확인하는 절차만 다룬다.
계정별로 각 앱에서 무엇을 할 수 있는지는 [앱 SSO 권한 매트릭스](app-sso-permission-matrix.md)가 정본이다.

> 비밀번호는 프로비저닝 때 클러스터가 무작위로 생성해 Secret에 넣는다. 문서에 값이 없는 것이 정상이며,
> `admin/admin` 같은 고정 비밀번호는 존재하지 않는다.

---

## 1. 계정 목록

`scripts/cluster/11-2-keycloak-config.sh`가 realm `narwhal`에 시드하는 사용자 4개:

| 사용자 | Keycloak 그룹 | Kubernetes RBAC | 비밀번호 위치 (`iam` ns) |
|--------|---------------|-----------------|--------------------------|
| `admin` | `cluster-admin` | `oidc:cluster-admin` → `platform-admin` | `keycloak-user-passwords` 의 `admin` 키 |
| `dev` | `developer` | `oidc:developer` → `developer` | `keycloak-user-passwords` 의 `dev` 키 |
| `view` | `viewer` | `oidc:viewer` → `platform-viewer` | `keycloak-user-passwords` 의 `view` 키 |
| `guest` | `guest` | 바인딩 없음 (Portal 전용) | `keycloak-user-passwords` 의 `guest` 키 |

- 모든 시드 사용자는 `enabled=true`, `emailVerified=true`, 이메일은 `<사용자>@<DOMAIN>`이다.
- Harbor는 로컬 `admin`과 이름이 겹치므로 `admin`이 **`narwhal-admin`** 으로 온보딩된다
  (`harbor_username` 속성, `oidc_user_claim=harbor_username`). Harbor 관리자 권한은 이름이 아니라
  `oidc_admin_group=cluster-admin`에서 온다.
- APISIX `openid-connect` 플러그인은 **인증만** 한다. 그룹 제한이 없어 위 4명 모두 게이트웨이를
  통과하며, 권한은 각 앱과 Kubernetes RBAC가 적용한다.

### SSO 사용자가 아닌 관리 계정

아래 계정은 realm `narwhal`의 사용자가 아니다. SSO 로그인과 혼동하지 않는다.

| 용도 | 사용자 | Secret (`ns/name` 키) |
|------|--------|-----------------------|
| Keycloak 관리 콘솔 (realm `master`) | Secret의 `username` | `iam/keycloak-initial-admin` `username`·`password` |
| ArgoCD 로컬 관리자 | `admin` | `devtools/argocd-initial-admin-secret` `password` |
| Gitea 로컬 관리자 | Secret의 `username` | `devtools/gitea-admin` `username`·`admin-password` |
| Harbor 로컬 관리자 | `admin` | `devtools/harbor-secrets` `HARBOR_ADMIN_PASSWORD` |
| Grafana 로컬 관리자 | Secret의 `admin-user` | `monitoring/grafana-secrets` `admin-user`·`admin-password` |

---

## 2. 비밀번호 확인

한 번에 전부 보려면 `scripts/test/show-credentials.sh`를 쓴다. SSO 사용자 4명의 비밀번호는
`Keycloak (IAM)` 섹션의 "Realm 'narwhal' users" 아래에 나온다.

```bash
# Vagrant
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/show-credentials.sh"

# Kakao Cloud (master-1에서 실행 — API LB 인증서에 공인 IP SAN이 없어 로컬 kubectl은 터널이 필요)
ssh narwhal-master-1 'sudo env KUBECONFIG=/home/vagrant/.kube/config-local \
  DOMAIN=kakao.narwhal.internal bash /home/vagrant/scripts/test/show-credentials.sh'
```

한 명만 꺼낼 때:

```bash
kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.dev}' | base64 -d; echo
```

> 출력에 비밀번호가 그대로 찍힌다. 터미널 기록·화면 공유·이슈 본문에 붙이지 않는다.

---

## 3. 계정과 그룹 상태 확인 (kcadm)

로그인이 안 될 때 "계정이 있는가, 활성인가, 그룹이 맞는가"를 Keycloak에서 직접 본다. master-1에서
실행한다 (Kakao는 `ssh narwhal-master-1` 후 `export KUBECONFIG=/home/vagrant/.kube/config-local`,
kubectl 앞에 `sudo -E`). 비밀번호는 출력하지 않는다.

```bash
KC="kubectl exec -n iam keycloak-0 -c keycloak -- /opt/keycloak/bin/kcadm.sh"
U=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.username}' | base64 -d)
P=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' | base64 -d)

# 관리 CLI 로그인 (realm master)
$KC config credentials --server http://localhost:8080 --realm master --user "$U" --password "$P"

# realm 목록 — narwhal, master 두 개여야 한다
$KC get realms --fields realm

# 사용자 목록과 상태
$KC get users -r narwhal --fields username,enabled,emailVerified

# 사용자별 그룹
for u in admin dev view guest; do
  id=$($KC get users -r narwhal -q username=$u -q exact=true --fields id | jq -r '.[0].id')
  printf '%s: ' "$u"; $KC get "users/$id/groups" -r narwhal --fields name | jq -c '[.[].name]'
done
```

정상 출력 (2026-10-26 Kakao 클러스터에서 확인):

```text
[{"realm":"narwhal"},{"realm":"master"}]
admin / dev / guest / view — enabled: true, emailVerified: true
admin: ["cluster-admin"]
dev: ["developer"]
view: ["viewer"]
guest: ["guest"]
```

---

## 4. 로그인 확인

### 브라우저

1. 아무 서비스 URL로 접속 (Vagrant: [dns-access.md](../vagrant/dns-access.md),
   Kakao: [service-domains.md](../kakao/service-domains.md))
2. Keycloak 로그인 페이지(테마 `narwhal`)로 리다이렉트되면 위 계정으로 로그인
3. 이후 다른 서비스는 같은 SSO 세션으로 재로그인 없이 들어간다

계정을 바꿔 시험할 때는 시크릿 창을 쓰거나 `https://keycloak.<DOMAIN>/realms/narwhal/account`에서
로그아웃한다. 세션 쿠키가 남아 있으면 이전 계정으로 자동 로그인된다.

### kubectl (OIDC)

```bash
./scripts/common/set-config.sh oidc          # kubelogin exec 자격증명 생성
kubectl config use-context narwhal-oidc
kubectl auth whoami                          # 브라우저 로그인 후 사용자·그룹 표시
```

`kubectl auth whoami`의 Groups에 `oidc:developer`처럼 `oidc:` 접두사가 붙어 보이면 정상이다
(API 서버가 붙인다. 토큰의 `groups` 클레임에는 접두사가 없다).

### 토큰의 groups 클레임 직접 확인

```bash
kubectl oidc-login get-token \
  --oidc-issuer-url=https://keycloak.local.narwhal.internal/realms/narwhal \
  --oidc-client-id=kubernetes \
  | jq -r '.status.token' | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null | jq '{preferred_username, groups}'
```

> **비밀번호 grant(ROPC)는 쓸 수 없다.** 모든 클라이언트가 `directAccessGrantsEnabled=false`로
> 생성·갱신되고(`11-2-keycloak-config.sh`, `11-3-keycloak-clients.sh`),
> `validate-keycloak-clients.sh`가 예외(`admin-cli`) 외의 ROPC 클라이언트를 실패로 막는다.
> 그래서 `curl ... -d grant_type=password -d client_id=kubernetes` 예제는 `unauthorized_client`로
> 실패한다. 토큰이 필요하면 위의 kubelogin(브라우저 로그인)을 쓴다.

---

## 5. 비밀번호 변경·초기화

```bash
$KC set-password -r narwhal --username dev --new-password '<새 비밀번호>'
kubectl create secret generic keycloak-user-passwords -n iam \
  --from-literal=admin="$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.admin}' | base64 -d)" \
  --from-literal=dev='<새 비밀번호>' \
  --from-literal=view="$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.view}' | base64 -d)" \
  --from-literal=guest="$(kubectl get secret keycloak-user-passwords -n iam -o jsonpath='{.data.guest}' | base64 -d)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

**Secret도 함께 바꿔야 한다.** `11-2-keycloak-config.sh`는 실행될 때마다 Secret 값으로
`set-password`를 다시 건다. Keycloak UI에서만 바꾸면 다음 재실행(재프로비저닝·복구) 때 Secret 값으로
되돌아간다. 반대로 Secret을 지우고 재실행하면 4명 모두 새 무작위 비밀번호를 받는다.

---

## 6. 문제 해결

| 증상 | 원인 | 확인·조치 |
|------|------|-----------|
| 로그인 화면에서 "Invalid username or password" | 비밀번호 불일치 (UI에서만 변경 후 재실행으로 복원 등) | §2로 Secret 값 확인 → §5로 맞춤 |
| `keycloak-user-passwords secret not found` | `11-2-keycloak-config.sh` 미실행 또는 실패 | `kubectl get pods -n iam`, 스크립트 재실행 |
| 로그인은 되는데 앱에서 권한 없음 | 그룹 매핑이 기대와 다름 | §3 그룹 확인 → [권한 매트릭스](app-sso-permission-matrix.md) |
| kubectl `Forbidden` (guest) | guest는 Kubernetes 바인딩이 없음 | 의도된 동작 |
| Harbor에 `admin` 사용자가 없음 | OIDC `admin`은 `narwhal-admin`으로 온보딩 | Harbor 사용자 목록에서 `narwhal-admin` 확인 |
| `.well-known/openid-configuration`이 404 | realm 이름을 `kubernetes`로 씀 (옛 이름) | issuer는 `https://keycloak.<DOMAIN>/realms/narwhal` |
| ROPC curl이 `unauthorized_client` | 비밀번호 grant 비활성화 | §4 kubelogin 사용 |

---

## 정본 (Sources of truth)

- 사용자·그룹·비밀번호 Secret·`kubernetes` 클라이언트: [`11-2-keycloak-config.sh`](../../scripts/cluster/11-2-keycloak-config.sh)
- 앱별 OIDC 클라이언트와 Harbor 사용자명 매핑: [`11-3-keycloak-clients.sh`](../../scripts/cluster/11-3-keycloak-clients.sh)
- 비밀번호 일괄 조회: [`show-credentials.sh`](../../scripts/test/show-credentials.sh)
- 그룹 → RBAC 계약: [oidc-rbac-contract.md](oidc-rbac-contract.md), 앱별 권한: [app-sso-permission-matrix.md](app-sso-permission-matrix.md)

위 스크립트에서 사용자·그룹·realm·Secret 이름이 바뀌면 이 문서도 같은 변경에서 고친다.
