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
- Gitea 브라우저 로그인은 APISIX `openid-connect` 관문을 통과한 뒤 Gitea 자체 OAuth2 소스(`keycloak`, `USERNAME=preferred_username`, `ACCOUNT_LINKING=auto`)로 무클릭 진입한다(D-GITEA-OAUTH, #491). 첫 HTML 탐색 요청에서 세션 마커 쿠키(`narwhal_gitea_sso=1`, 브라우저 세션 수명)를 설정하고 `/user/oauth2/keycloak?redirect_to=...`로 302 리다이렉트하며, 로그인마다 Keycloak 그룹 매핑(`cluster-admin` → 사이트 관리자, `developer` → `narwhal/Developers`, `viewer` → `narwhal/Viewers`, `guest` → 제한 계정)이 동기화된다. 팀 매핑은 양방향(`--group-team-map-removal`)이라 Keycloak 그룹에서 빠지면 다음 로그인 때 해당 팀에서도 제거된다. 그래서 `narwhal/Developers`·`Viewers`에 손으로 넣은 사용자도 그 Keycloak 그룹이 없으면 다음 로그인 때 빠진다. 이 두 팀은 Keycloak 그룹으로 부여하고, `Owners`와 매핑되지 않은 팀은 건드리지 않는다(기존에 연결되지 않은 동일 사용자명의 로컬 계정은 첫 OAuth 로그인에서 자동 연결되고 두 번째 로그인부터 그룹/관리자 동기화가 적용된다). 자동 로그인은 브라우저 세션당 한 번만 실행되므로, Gitea 세션 유실(메모리 세션이라 파드 재시작 또는 유휴 만료) 뒤에는 Sign In을 한 번 클릭하면 `gitea-login-forward`(`GET /user/login`, 60초 `narwhal_gitea_sso_try=1` 루프 가드 포함)가 곧바로 Keycloak OAuth2로 보내고, Gitea에서 Sign Out을 누르면 해당 브라우저 세션이 끝날 때까지 로그아웃 상태가 유지된다. 로컬 `gitea-admin` 비밀번호 로그인 폼은 `/user/login?direct=1` 탈출구로 접근한다.
- APISIX `openid-connect` 플러그인은 **인증만** 한다. 그룹 제한이 없어 위 4명 모두 게이트웨이를
  통과하며, 권한은 각 앱과 Kubernetes RBAC가 적용한다.

### SSO 사용자가 아닌 관리 계정

아래 계정은 realm `narwhal`의 사용자가 아니다. SSO 로그인과 혼동하지 않는다.

| 용도 | 사용자 | Secret (`ns/name` 키) |
|------|--------|-----------------------|
| Keycloak 관리 콘솔 (realm `master`) | Secret의 `username` | `iam/keycloak-initial-admin` `username`·`password` |
| ArgoCD 로컬 관리자 | `admin` | `devtools/argocd-initial-admin-secret` `password` |
| Gitea 로컬 관리자 | `gitea-admin` (12-gitea.sh가 고정) | `devtools/gitea-admin` `admin-password` |
| Harbor 로컬 관리자 | `admin` | `devtools/harbor-secrets` `HARBOR_ADMIN_PASSWORD` |
| Grafana 로컬 관리자 | Secret의 `admin-user` | `monitoring/grafana-secrets` `admin-user`·`admin-password` |

---

## 2. 비밀번호 확인

한 번에 전부 보려면 `scripts/test/show-credentials.sh`를 쓴다. SSO 사용자 4명의 비밀번호는
`Keycloak (IAM)` 섹션의 "Realm 'narwhal' users" 아래에 나온다.

Vagrant:

```bash
vagrant ssh master-1 -c "bash /home/vagrant/scripts/test/show-credentials.sh"
```

Kakao Cloud — Mac의 kubectl이 이미 클러스터에 붙어 있으면(`scripts/cloud/set-config-kakao.sh`가 연
bastion 터널) 저장소 루트에서(`narwhal-kakao`는 `set-config-kakao.sh`가 기본 생성하는 컨텍스트이며, 다르게 생성했다면 `kubectl config get-contexts`로 확인한 이름을 쓴다):

```bash
PROVIDER=kakao scripts/test/show-credentials.sh --context narwhal-kakao
```

터널이 없으면 master-1에서 실행한다 (API LB 인증서에 공인 IP SAN이 없어 터널 없이는 로컬 kubectl이
붙지 않는다):

```bash
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

Mac 터미널(zsh)에 그대로 붙여 넣어도 되도록 썼다. kcadm은 변수(`KC="kubectl exec ..."`)가 아니라
함수로 감싼다 — zsh는 따옴표 없는 변수를 단어로 쪼개지 않아 `$KC`가 명령 이름 하나로 해석되고,
대화형 zsh는 `interactivecomments`가 꺼져 있으면 `#` 줄도 명령으로 실행한다. 그래서 블록 안에
주석을 두지 않았다.

순서: kcadm 함수 정의 → 관리자 자격증명 읽기 → 관리 CLI 로그인(realm `master`) → realm 목록
(`narwhal`, `master` 두 개여야 한다) → 사용자 목록과 상태 → 사용자별 그룹.

```bash
kc() { kubectl exec -n iam keycloak-0 -c keycloak -- /opt/keycloak/bin/kcadm.sh "$@"; }
U=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.username}' | base64 -d)
P=$(kubectl get secret keycloak-initial-admin -n iam -o jsonpath='{.data.password}' | base64 -d)
kc config credentials --server http://localhost:8080 --realm master --user "$U" --password "$P"
kc get realms --fields realm
kc get users -r narwhal --fields username,enabled,emailVerified
for u in admin dev view guest; do
  id=$(kc get users -r narwhal -q username=$u -q exact=true --fields id | jq -r '.[0].id')
  printf '%s: ' "$u"; kc get "users/$id/groups" -r narwhal --fields name | jq -c '[.[].name]'
done
```

정상 출력 (2026-10-26 Kakao 클러스터에서 확인, Mac zsh와 bash 둘 다):

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

`set-config.sh oidc`가 kubelogin exec 자격증명을 만들고, `kubectl auth whoami`가 브라우저 로그인 후
사용자와 그룹을 보여 준다:

```bash
./scripts/common/set-config.sh oidc
kubectl config use-context narwhal-oidc
kubectl auth whoami
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

§3의 `kc` 함수와 관리 CLI 로그인을 먼저 실행한 뒤:

```bash
kc set-password -r narwhal --username dev --new-password '<새 비밀번호>'
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
| Dashboard `/sso/callback`에서 `401 MSG_LOGIN_UNAUTHORIZED_ERROR` (kubectl OIDC·Headlamp도 동일 거부) | apiserver가 Keycloak JWKS를 가져오지 못함: `*.kakao.narwhal.internal` 노드 DNS(bastion dnsmasq `172.17.0.191`)가 다운되어 kube-apiserver 로그에 `get keys failed ... no such host` 기록 | bastion에서 `systemctl status dnsmasq` 확인 및 재시작; 노드: `getent hosts keycloak.kakao.narwhal.internal` (#478 이후 systemd drop-in이 실패 시 자동 재시작) |
| Dashboard `/sso/callback`·Headlamp·`kubectl` OIDC가 **간헐적으로**(요청의 ~2/3) `401 MSG_LOGIN_UNAUTHORIZED_ERROR` / `Unauthorized` 거부 | 클러스터 내부 `kubernetes` Service 엔드포인트는 컨트롤 플레인 3대 전부인데, `master-2`·`master-3`의 `kube-apiserver`에 `--oidc-*` 플래그 7개와 `/etc/kubernetes/pki/oidc-ca.crt`가 빠져 해당 마스터로 라우팅된 토큰이 OIDC 인증기 없이 `"Unable to authenticate the request" err="invalid bearer token"`으로 거부됨 | 모든 apiserver 파드의 `--oidc-*` 플래그 확인: `kubectl -n kube-system get pods -l component=kube-apiserver -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].command}{"\n"}{end}'` (파드가 컨트롤 플레인 노드 수만큼 있고 각 파드에 `--oidc-*` 7개가 모두 있어야 함). 누락 시 Vagrant는 `11-4-keycloak-apiserver.sh`, Kakao는 호스트에서 `./scripts/cloud/apply-apiserver-oidc-kakao.sh` (또는 `./scripts/cloud/provision-kakao.sh oidc`) 실행. 이 수정 이전에 스테이징한 Kakao 노드에는 `11-4b-apiserver-oidc-node.sh`가 없어 스크립트가 해당 노드를 지목하며 중단하므로, 먼저 `./scripts/cloud/stage-kakao-nodes.sh <노드 IP>`로 다시 스테이징한 뒤 실행 |
| Grafana `Failed to get token from provider` | `monitoring/grafana-oidc-secret`의 `client_secret`이 빈 값 (이전 11-3이 kcadm 읽기 실패 시 ""를 기록) | 길이 확인: `kubectl -n monitoring get secret grafana-oidc-secret -o jsonpath='{.data.client_secret}' \| base64 -d \| wc -c`; 11-3 재실행 (현재는 fail-closed) 후 Grafana 파드 재생성 |
| admin으로 Gitea `gitea-admin/narwhal-gitops` 접근 시 404 | Gitea OAuth2 소스(`keycloak`) 미갱신 또는 기존 미연결 로컬 `admin` 계정의 첫 연결 직후(그룹/관리자 동기화는 두 번째 로그인부터 적용) | `11-3-keycloak-clients.sh` 또는 `12-gitea.sh`의 `gitea_upsert_keycloak_source` 실행 확인 후 Gitea에서 한 번 재로그인 (`/user/login` 접속 시 자동 포워딩) |
| Headlamp가 로그인 페이지에 머무름 | `autoLogin` 비활성화 또는 토큰 거부(`oidc-login-attempted.<cluster>`) | `kubectl -n devtools get deploy headlamp -o jsonpath='{.spec.template.spec.containers[0].args}'`에 `-oidc-auto-login=true`가 있는지 확인 (토큰이 401/403으로 거부됐거나 `?logout=true`로 진입한 경우에는 루프 방지를 위해 로그인 화면에 머무름) |

---

## 정본 (Sources of truth)

- 사용자·그룹·비밀번호 Secret·`kubernetes` 클라이언트: [`11-2-keycloak-config.sh`](../../scripts/cluster/11-2-keycloak-config.sh)
- 앱별 OIDC 클라이언트와 Harbor 사용자명 매핑: [`11-3-keycloak-clients.sh`](../../scripts/cluster/11-3-keycloak-clients.sh)
- kube-apiserver OIDC CA·7개 플래그 적용 및 다중 마스터 전파: [`11-4b-apiserver-oidc-node.sh`](../../scripts/cluster/11-4b-apiserver-oidc-node.sh), [`11-4-keycloak-apiserver.sh`](../../scripts/cluster/11-4-keycloak-apiserver.sh), [`apply-apiserver-oidc-kakao.sh`](../../scripts/cloud/apply-apiserver-oidc-kakao.sh)
- `kubeadm upgrade`/신규 컨트롤 플레인 조인 시 매니페스트 재생성에서도 OIDC가 유지되도록 `kube-system/kubeadm-config`의 `ClusterConfiguration.apiServer.extraArgs`에 같은 7개 플래그를 기록(백업 후 `data.ClusterConfiguration`만 패치, 실패 시 아무것도 쓰지 않음): [`11-4c-kubeadm-config-oidc.sh`](../../scripts/cluster/11-4c-kubeadm-config-oidc.sh) (`11-4`와 Kakao `apply-apiserver-oidc-kakao.sh`가 1회 호출, `verify-cluster.sh`가 게이트)
- Gitea Helm 설정(reverse-proxy 인증 비활성화, `USERNAME=preferred_username`) 및 OAuth2 소스 제자리 갱신(`gitea_upsert_keycloak_source`): [`12-gitea.sh`](../../scripts/cluster/12-gitea.sh), [`lib.sh`](../../scripts/common/lib.sh)
- 비밀번호 일괄 조회: [`show-credentials.sh`](../../scripts/test/show-credentials.sh)
- 그룹 → RBAC 계약: [oidc-rbac-contract.md](oidc-rbac-contract.md), 앱별 권한: [app-sso-permission-matrix.md](app-sso-permission-matrix.md)

위 스크립트에서 사용자·그룹·realm·Secret 이름이 바뀌면 이 문서도 같은 변경에서 고친다.
