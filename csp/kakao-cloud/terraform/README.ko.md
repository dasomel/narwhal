# Narwhal IDP on Kakao Cloud - Terraform Infrastructure

[English](README.md) | Korean

Kakao Cloud에 Narwhal IDP 클러스터용 VM 인프라를 프로비저닝하는 OpenTofu 프로젝트.
Kubernetes와 플랫폼 앱은 이후
[`scripts/cloud/provision-kakao.sh`](../../../scripts/cloud/provision-kakao.sh)가 설치한다.

2026-07-27 전 구간 검증: 노드 6/6 Ready, ArgoCD 33개 앱 Synced/Healthy, worker LB 경유 서비스 접근 정상.

## 생성되는 리소스

| 리소스 | 수량 | 설명 |
|--------|------|------|
| VPC | 1 | 172.16.0.0/16 |
| Subnet | 1 | 172.16.0.0/24 |
| Security Group | 1 | K8s + Cilium + NFS + 프록시/레지스트리 포트 |
| Master VM | 3 | 컨트롤플레인 (t1i.large, 2 vCPU / 4GB), 고정 **사설** IP .10 .11 .12 |
| Worker VM | 3 | 데이터플레인 (t1i.xlarge, 4 vCPU / 8GB), 고정 **사설** IP .21 .22 .23 |
| Bastion VM | 1 | SSH 점프 호스트, 포워드 프록시, airgap 레지스트리 (공인 + 사설 .207) |
| Master LB | 1 | K8s API (6443) + etcd (2379) |
| Worker LB | 1 | Ingress HTTP (80) + HTTPS (443) → NodePort 31080/31443 |
| Public IP | 3 | bastion + LB 2개. **클러스터 노드에는 없다** |

노드를 프라이빗으로 두는 것은 의도된 설계다. 접근은 전부 bastion을 경유하고, 노드의 아웃바운드는
거기서 도는 squid 프록시를 탄다 — Kakao Cloud provider 0.5.1(0.4.4와 동일)에는 NAT 게이트웨이 리소스가 없고,
노드마다 공인 IP를 붙이면 이 보안그룹이 `0.0.0.0/0`으로 열어둔 SSH·6443 규칙이 그대로 노출된다.
직접 egress가 필요하면 `assign_node_public_ips = true`가 탈출구다.

## 아키텍처

```
                            인터넷
                                |
        +-----------------------+-----------------------+
        |                       |                       |
    Bastion                 Master LB               Worker LB
  공인 IP  (+사설 .207)    공인 IP :6443          공인 IP :80/443
  ssh · squid:3128            |                       |
  registry:5000               |                       |
        |                     |                       |
        |  ProxyJump          | 사설 VIP .236          | 사설 VIP .110
        |  (운영 접근)         |                       | → NodePort 31080/31443
        v                     v                       v
   +---------------------------------------------------------+
   |  ▸ 노드는 사설 IP만 보유 — 공인 IP 없음                    |
   |                                                          |
   |  master-1 .10   master-2 .11   master-3 .12              |
   |  worker-1 .21   worker-2 .22   worker-3 .23              |
   |                                                          |
   |  egress → bastion squid:3128                             |
   +---------------------------------------------------------+
                    VPC 172.16.0.0/16 · Subnet 172.16.0.0/24
       (위 .xx 표기는 모두 172.16.0.xx 사설 주소)
```

## 사전 요구사항

- **OpenTofu** >= 1.13.0 (`tofu`; Terraform CLI도 동작)
- **Kakao Cloud 계정** + Application Credential (ID + Secret)
- **쿼터**: 인스턴스 7, vCPU 26, RAM 52GB+, 볼륨 1.2TB, 공인 IP 3, LB 2

SSH 키페어는 이 프로젝트가 생성해(`tls_private_key` + `kakaocloud_keypair`) `var.ssh_key_path`에
쓴다 — 미리 등록해 둘 필요 없다.

## 빠른 시작

```bash
cd csp/kakao-cloud/terraform

cp terraform.tfvars.example terraform.tfvars
# application_credential_id / application_credential_secret 입력

tofu init
tofu plan
tofu apply
```

이후 리포 루트에서 클러스터를 올린다:

```bash
./scripts/cloud/stage-kakao-nodes.sh     # 리포 → 노드, 스크립트가 기대하는 레이아웃으로
./scripts/cloud/provision-kakao.sh all   # proxy → base → mirror → init → join → nfs → phase1 → phase2
```

`provision-kakao.sh`는 모든 주소를 OpenTofu state에서 읽으므로 같은 값을 두 번 적지 않는다.
단계는 멱등이며(`/home/vagrant/.narwhal-stage/` 센티넬), `FORCE=1`로 특정 단계를 다시 돌린다.

## 배포 후

노드에 공인 IP가 없으므로 모든 접속이 bastion을 경유한다. `~/.ssh/config`에 한 번 등록해 둔다:

```bash
tofu output -raw ssh_config >> ~/.ssh/config
ssh narwhal-master-1
```

`narwhal-bastion`과 `narwhal-master-1..3` / `narwhal-worker-1..3` 항목이 생성되며, 각각 키와
`ProxyJump narwhal-bastion`을 갖는다.

명령줄 `-J`는 **동작하지 않는다.** 점프 호스트용 ssh 프로세스가 따로 뜨는데 명령줄의 `-i`가
전달되지 않아 bastion 홉에서 `Permission denied (publickey)`로 끊긴다. `-o IdentityFile`도
마찬가지다. 위 config를 쓰거나, `ProxyCommand`로 양쪽 홉에 키를 명시해야 한다 —
`tofu output -json bastion_ssh`가 그 형태의 실행 가능한 명령을 출력한다.

`assign_node_public_ips`를 켜지 않는 한 `master_public_ips`·`worker_public_ips`는 **빈 배열**이다 —
실패가 아니라 정상이다.

## 클러스터 접근

### 로컬에서 kubectl

`scripts/cloud/set-config-kakao.sh`가 아래 과정을 한 번에 처리한다 — 터널을 열고 CA·클라이언트
인증서를 받아 `narwhal-kakao` context를 등록한다:

```bash
scripts/cloud/set-config-kakao.sh          # cert, 기본값
scripts/cloud/set-config-kakao.sh token    # 서비스 어카운트 토큰, 1년
scripts/cloud/set-config-kakao.sh oidc     # Keycloak, kubelogin + /etc/hosts 필요
```

이미 열린 터널은 재사용하며, 6443이 점유돼 있으면 `PORT=7443 ...`으로 옮긴다. context 이름은
`narwhal-kakao`, `narwhal-kakao-token`, `narwhal-kakao-oidc`로 로컬 Vagrant 것과 충돌하지 않는다.
`scripts/common/set-config.sh`의 클라우드 대응물이다.

수동으로 하는 방법과, 애초에 터널이 필요한 이유:

apiserver 인증서의 SAN에는 `localhost`, `127.0.0.1`, 마스터 사설 IP, 사설 VIP가 들어 있고
**LB 공인 IP는 없다.** 공인 엔드포인트로 바로 붙으면 TLS 검증에 실패하므로, 인증서가 이미
포함하는 이름으로 터널을 판다.

위 `~/.ssh/config` 항목이 등록돼 있다고 가정한다. 붙여넣기 안전하도록 인라인 주석을 넣지
않았다 — `interactive_comments`가 꺼진 인터랙티브 zsh는 `#`를 일반 단어로 보기 때문이다.

```bash
mkdir -p ~/.kube
ssh narwhal-master-1 'sudo cat /etc/kubernetes/admin.conf' > ~/.kube/kakao.conf
sed -i '' 's#server: https://.*#server: https://127.0.0.1:6443#' ~/.kube/kakao.conf

ssh -f -N -L 6443:127.0.0.1:6443 narwhal-master-1
KUBECONFIG=~/.kube/kakao.conf kubectl get nodes
```

`admin.conf`는 master-1에서 root 소유라 scp 대신 `sudo cat`으로 읽는다. `sed -i ''`는 macOS용이며
GNU sed는 `-i`만 쓴다. 세 번째 명령이 백그라운드로 포워딩을 연다.

2026-07-29 검증: 운영 호스트에서 노드 6대와 ArgoCD 앱 33개 조회 확인.
터널 종료는 `pkill -f '6443:127.0.0.1:6443'`.

공인 주소로 직접 붙고 싶다면 `02-init-cluster.sh`의 `certSANs`에 LB 공인 IP를 넣고 apiserver
인증서를 재발급한다 — TLS 검증을 끄지 말 것.

### 노드에서 kubectl

```bash
ssh narwhal-master-1
sudo -E env KUBECONFIG=/home/vagrant/.kube/config-local kubectl get nodes
```

이 이미지에서 `sudo -E`만으로는 환경변수가 넘어가지 않으므로(`docs/kakao/cloud-deployment.md`의
관련 항목 참고) 위처럼 `KUBECONFIG`를 명시해 넘긴다.

### 웹 UI

`*.kakao.narwhal.internal`은 공개 DNS가 없고 worker LB가 이름을 서빙한다. Vagrant의
`local.narwhal.internal`과 도메인을 다르게 둔 것은 의도적이다 — 두 클러스터가 같은 서비스 이름을
서빙하고 둘 다 로컬 `/etc/hosts`로만 해석되므로, 도메인이 같으면 서로의 항목을 덮어쓴다.

한 번 매핑해 둔다:

```bash
scripts/cloud/setup-hosts-kakao.sh --apply
```

호스트 목록은 클러스터의 ApisixRoute에서 읽고 `# BEGIN narwhal-kakao` 마커 사이에 기록되며
`--remove`로 되돌린다. 같은 명령이 플랫폼 CA를 내보내고 신뢰 추가 방법을 출력한다 —
인증서 발급자가 `CN=Narwhal IDP Root CA`라 신뢰 전까지 브라우저가 경고한다.

### 서비스 계정과 비밀번호

플랫폼이 생성한 자격증명은 스크립트 하나로 전부 출력된다:

```bash
ssh narwhal-master-1 'sudo env KUBECONFIG=/home/vagrant/.kube/config-local DOMAIN=kakao.narwhal.internal bash /home/vagrant/scripts/test/show-credentials.sh'
```

Keycloak(관리자 + 사전 생성된 `admin`/`dev`/`view`/`guest` realm 사용자), ArgoCD, Gitea,
Harbor, Grafana, OpenBao 루트·언실 키, APISIX admin API 키, PostgreSQL 서비스 계정,
OIDC 클라이언트 시크릿을 포함한다. 마지막 "SSH / Node access" 블록은 아직 Vagrant 주소를
출력하므로, 이 배포의 주소는 `tofu output`으로 확인한다.

값 하나만 필요하면:

```bash
kubectl get secret <name> -n <ns> -o jsonpath='{.data.<key>}' | base64 -d
```

웹 UI는 `https://<서비스>.kakao.narwhal.internal`에서 Keycloak SSO를 거치며 worker LB로
들어간다. `--resolve`나 `/etc/hosts`로 호스트명을 LB에 연결한다:

```bash
LB=$(tofu output -raw worker_lb_public_ip)
curl -sk --resolve "argocd.kakao.narwhal.internal:443:$LB" https://argocd.kakao.narwhal.internal/
```

## 디렉토리 구조

```
terraform/
  main.tf              # 루트: network → security → compute → loadbalancer
  bastion.tf           # 점프 호스트 + 공인 IP
  variables.tf         # 변수 정의
  outputs.tf           # 노드 IP, LB 엔드포인트, bastion, ssh_key_path, subnet_cidr, vpc_cidr
  provider.tf          # kakaocloud provider v0.5.1
  cloud-init.yaml      # 기본 VM 초기화 (SSH 공개키만)
  terraform.tfvars.example
  modules/
    network/           # VPC + Subnet
    security/          # Security Group
    compute/           # Master/Worker VM (고정 사설 IP) + 선택적 공인 IP
    loadbalancer/      # Master LB (6443/2379) + Worker LB (80/443)
```

## 설정

`terraform.tfvars`의 주요 값:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `master_count` | 3 | 마스터 노드 (HA는 3) |
| `worker_count` | 3 | 워커 노드 |
| `master_flavor` | t1i.large | 2 vCPU, 4GB RAM |
| `worker_flavor` | t1i.xlarge | 4 vCPU, 8GB RAM |
| `volume_size` | 200 | 디스크 GB |
| `availability_zone` | kr-central-2-a | Kakao Cloud AZ |
| `assign_node_public_ips` | false | bastion 프록시 대신 노드별 직접 egress |

노드 IP는 연속 고정(`subnet_cidr`에 `cidrhost`)이다. 설치 스크립트의 `${MASTER_IP_BASE}${idx}`
산술이 Vagrant에서와 똑같이 풀리도록 하기 위함이다.

## 보안그룹 포트

| 포트 | 소스 | 용도 |
|------|------|------|
| 22 | 0.0.0.0/0 | SSH |
| 6443 | 0.0.0.0/0 | Kubernetes API |
| 80, 443 | 0.0.0.0/0 | worker LB 경유 Ingress |
| 2379-2380 | VPC | etcd |
| 10250, 10251 | VPC | kubelet / scheduler |
| 111, 2049 | VPC | NFS (rpcbind, nfsd) |
| 4240, 4244, 8472 | VPC | Cilium health, Hubble, VXLAN |
| 3128 | VPC | bastion squid 포워드 프록시 |
| 5000 | VPC | airgap 부트스트랩 레지스트리 |
| 30000-32767 | VPC | NodePort 범위 |

22와 6443은 인터넷에 열려 있다. 노드가 프라이빗인 동안에는 LB를 통해서만 닿지만,
`assign_node_public_ips`를 켠다면 운영자 주소로 좁혀야 한다.

## 배포 소요 시간

2026-07-26 실측, `tofu apply` 구간만:

| 모듈 | 소요 | 비고 |
|------|------|------|
| Network | ~30분 | VPC 생성이 대부분; 이 구간 API가 느리다 |
| Security | ~1분 | |
| Compute | ~2분 | VM 7대 |
| LoadBalancer | ~10분 | NLB 2 + 타깃그룹 6 + 멤버 18 |
| **합계** | **~45분** | 리소스 48개 |

이후 클러스터 프로비저닝에 약 1시간이 더 들고, airgap 번들을 쓴다면 전송(~6GB)이 추가된다.

## 기존 배포를 provider 0.5.1로 업그레이드

provider 0.5.0에서 `kakaocloud_vpc.subnet`이 제거됐고, bastion의 `initial_security_groups`는
제자리 변경이 안 된다(`Changing the initial security group is not allowed`, `-replace`로도
마찬가지; #202 대기 중). 신규 설치는 해당 없다. 운영 중인 배포는 아래 순서대로, 이 디렉토리에서
운영자의 `terraform.tfvars`로 진행한다.

```bash
# 1. state 백업, 그리고 0.4.x 스키마가 아직 보여줄 때 기본 서브넷 CIDR 확인
tofu state pull > ~/narwhal-kakao-state-pre-0.5.1.json   # git 밖에 보관
tofu state show module.network.kakaocloud_vpc.vpc        # `subnet` 블록에는 cidr_block/az만 있고 id는 없다:
# 그 CIDR(이름 "main")의 서브넷 ID를 Kakao Cloud 콘솔(VPC > Subnet)이나
# `data "kakaocloud_subnets"`를 쓴 임시 설정(읽기 전용)으로 조회한다

# 2. 새 provider 받기 (lock 파일은 이미 0.5.1로 고정돼 있다)
tofu init   # 커밋된 lock이 이미 0.5.1을 고정한다. -upgrade는 다시 해석한다

# 3. VPC 기본 서브넷 import (예전 인라인 `subnet` 블록)
tofu import module.network.kakaocloud_subnet.vpc_default_subnet <default-subnet-id>

# 4. bastion 현재 사설 IP 고정: 노드가 DNS, squid 프록시, 레지스트리 미러로 쓴다
tofu output -raw bastion_private_ip   # 이 값을 terraform.tfvars의 bastion_private_ip에 넣는다

# 5. bastion만 교체. 예전 bastion은 -replace가 거부되고(위 참고), 대상 지정 destroy는 공인 IP까지
#    지운다(prevent_destroy가 있으면 에러). 먼저 주소를 state에서 빼고 인스턴스를 지운 뒤
#    다시 만들고 import한다:
tofu show -json | jq -r '.values.root_module.resources[] | select(.address=="kakaocloud_public_ip.bastion_public") | .values.id'   # state rm 전에 <public_ip_id> 기록
tofu state rm kakaocloud_public_ip.bastion_public
tofu destroy -target=kakaocloud_instance.bastion -refresh=false
tofu apply -target=kakaocloud_instance.bastion      # 새 bastion, 주소는 아직 연결 안 됨
tofu import kakaocloud_public_ip.bastion_public <public_ip_id>
tofu apply                                          # 유지한 주소를 다시 연결 (제자리 업데이트)

# 6. bastion에 있던 것(squid, 레지스트리)을 저장소 루트에서 다시 구성
cd ../../..
./scripts/cloud/provision-kakao.sh proxy registry
```

참고:

- 3, 4단계 뒤 `tofu plan`을 읽어 본다. 예상 변경은 bastion 교체뿐이고, import한 서브넷은
  변경이 없어야 한다(`vpc_default_subnet_name` 기본값이 `main`).
- `proxy` 스테이지는 sentinel이 없어 항상 다시 돈다. `registry` 스테이지는 bastion이 이미
  `:5000`을 서빙하면 건너뛴다. 새 bastion은 그렇지 않으므로 `FORCE=1`은 살아 있는 bastion
  레지스트리에 다시 push할 때만 필요하다. sentinel로 보호되는 노드 스테이지(`base`, `mirror` 등)는
  다시 돌지 않는다.
- 새 bastion은 SSH 호스트 키가 바뀐다. `known_hosts`의 옛 항목을 지운다.
- 모든 `kakaocloud_public_ip`에 `prevent_destroy`(D-PUBIP)가 있어 VM, LB를 다시 만들어도 주소가
  유지된다. bastion이 0.5.1이 된 뒤의 교체는 `-replace`(다음 절)로 하며 5단계의 state 조작이 필요 없다.

## VM 교체

공인 IP(bastion, 노드 egress, LB 2개)는 외부 엔드포인트다. DNS, kubeconfig, 운영자 SSH,
허용 목록이 이 주소를 가리킨다. 주소를 지키려면 VM을 destroy하지 말고 교체한다:

```bash
tofu apply -replace=kakaocloud_instance.bastion   # plan에 공인 IP가 "updated in-place"로 나온다
```

- `tofu destroy -target=<instance>`는 쓰지 않는다. 의존하는 공인 IP까지 지우며, 가드가 있으면
  이제 에러가 난다.
- `initial_security_groups`는 제자리 변경이 안 된다. 보안그룹을 바꾸려면 인스턴스를 다시 만들어야
  하며, 이때도 대상 지정 destroy가 아니라 위 `-replace`를 쓴다.
- provider가 `-replace` 자체를 막으면 위 5단계 순서(공인 IP `tofu state rm`, 인스턴스 destroy,
  재생성, `tofu import`, apply)를 쓴다. import 뒤 `tofu plan`에서 공인 IP는 `update in-place`로
  나와야 한다. replace로 나오면 `prevent_destroy`가 의도대로 막는 것이므로 가드를 지우지 말고
  멈춰서 원인을 조사한다. 이 state rm / import 순서는 아직 실제 배포에서 검증되지 않았다.
- 위 jq 조회는 루트 리소스 기준(`.values.root_module.resources[]`)이며, 모듈 안 리소스는
  `.values.root_module.child_modules[]` 아래에 있다.

## 정리

`kakaocloud_public_ip`는 `prevent_destroy`(D-PUBIP)로 보호되므로 그냥 `tofu destroy`는
`Instance cannot be destroyed`로 실패한다. 전부 정리하려면 먼저 가드를 푼다. lifecycle 블록을
없애거나, 주소를 state에서 빼면 된다:

```bash
# 방법 A: 가드 해제(5개 리소스 전부) 후 destroy. 주소는 반납된다
sed -i.bak '/prevent_destroy = true/s/true/false/' bastion.tf modules/compute/main.tf modules/loadbalancer/main.tf
tofu destroy
# 끝나면 가드 복원: mv bastion.tf.bak bastion.tf (모듈 파일 2개도 동일)

# 방법 B: 주소를 OpenTofu 밖에 남긴다 (Kakao Cloud 콘솔 VPC > Public IP에서 직접 반납하기
# 전까지 할당·과금이 유지된다)
tofu state list | grep kakaocloud_public_ip | xargs -n1 tofu state rm
tofu destroy
```

운영 중인 클러스터에서 `assign_node_public_ips = false`도 같은 가드에 막히고,
`assign_node_public_ips = true`일 때 `master_count` / `worker_count`를 줄이는 것도 막힌다
(제거되는 인덱스의 주소가 destroy되므로). 해당 주소는 먼저 방법 B로 처리한다.

리소스 52개는 ~15분에 사라진다. **VPC 삭제만 몇 시간 단위로 늘어질 수 있다.**

2026-07-29 실측: 나머지가 전부 지워진 뒤 VPC 삭제가 **1시간 54분** 동안 `Still destroying...`을
찍었고, 거기서 중단했다. 이후 `tofu refresh`는 VPC를 여전히 state에 남겨 두는데, 실제 상태를
보면 이렇다:

```bash
tofu state show module.network.kakaocloud_vpc.vpc | grep provisioning_status
#   provisioning_status = "PENDING_DELETE"
```

`PENDING_DELETE`는 **살아있지도 없어지지도 않은 상태**다. 이 구분이 중요한 이유는 이 상태의
VPC를 재사용할 수 없기 때문이다 — 남겨둔 채 `tofu apply`를 하면 다음에서 죽는다:

```
Error: create resource: kakaocloud_subnet
VPC status is 'PENDING_DELETE'.
```

`tofu plan`은 `47 to add, 0 to destroy`로 멀쩡해 보이므로 계획만 보고 판단하면 안 된다.
**항상 `provisioning_status`를 확인한다.**

대응은 기다리는 것뿐이다. 삭제가 끝나 VPC가 state에서 사라지면 `tofu apply`가 VPC까지
48개를 새로 만든다. 진행 여부는 refresh를 반복해 확인한다:

```bash
until ! tofu state list | grep -q kakaocloud_vpc.vpc; do
  tofu refresh -input=false >/dev/null 2>&1
  sleep 300
done
tofu apply
```

중단해도 데이터 손실은 없다. 삭제는 서버 쪽에서 계속 진행되며, `tofu destroy`를 다시 돌리는
것과 그냥 기다리는 것의 차이도 없다.

## NFS 데이터 볼륨 (Kakao)

`nfs_data_volume_size`(GB, 기본 `0` = 비활성, 사용 시 50..16384)는 `<master_name>-1-nfs-data` 전용 블록 볼륨을
만들어 master-1에 연결합니다. `scripts/cluster/01-nfs-server.sh`가 이를 XFS(라벨 `narwhal-nfs`)로 포맷하고
`prjquota`로 `/srv/nfs/k8s`에 마운트합니다. `nfs-quota-agent`에 필요하며, XFS 프로젝트 쿼터는 최초 마운트
시에만 켤 수 있고 remount로는 켤 수 없습니다. 볼륨에는 `prevent_destroy`(D-NFS-VOL)가 있고
`is_delete_on_termination = false`로 연결됩니다.

기존 ext4 export 이전: [`docs/runbooks/nfs-xfs-migration.md`](../../../docs/runbooks/nfs-xfs-migration.md),
첫 실행 기록: [`docs/kakao/nfs-xfs-migration-2026-10-16.md`](../../../docs/kakao/nfs-xfs-migration-2026-10-16.md).

4단계 게이트로 적용합니다. 이 절차에서 `-target`은 의도된 예외이며 필수입니다. attach 이후 전체 plan은
깨끗하지 않습니다. master-1을 in-place 갱신하면 계산 속성 `addresses`가 apply 중 unknown이 되어, 이를
`address`로 읽는 로드밸런서 타깃 그룹 멤버 4개(etcd, master, master_http, master_https `["0"]`)가
교체 대상이 됩니다. 볼륨 생성, master-1 단독 attach, 수렴 확인 순서로 진행합니다.

```bash
# a) 볼륨만 생성
tofu plan -target=module.compute.kakaocloud_volume.nfs_data -out=s1.tfplan
#    정확히 1 add / 0 change / 0 destroy가 아니면 중단
tofu apply s1.tfplan

# b) master-1에만 attach
tofu plan -target='module.compute.kakaocloud_instance.master[0]' -out=s2.tfplan
#    0 add / 1 change / 0 destroy가 아니거나, volumes diff가 요소 +1개(id = 새 볼륨,
#    is_delete_on_termination = false)이고 루트 요소가 그대로가 아니면 중단
tofu apply s2.tfplan

# c) 수렴 확인
tofu plan
#    "No changes"가 아니면 중단. LB 멤버가 "must be replaced"면 중단 후 refresh하고 다시 plan
```

d) master-1에서 `01-nfs-server.sh` 실행 전에 확인합니다.

```bash
ls -l /dev/disk/by-id/virtio-<볼륨 id 앞 20자>
sudo blkid -p /dev/disk/by-id/virtio-<볼륨 id 앞 20자>   # 아무것도 출력되면 안 됨
```

링크가 있고 `blkid`가 아무것도 출력하지 않는(빈 볼륨) 경우가 아니면 중단합니다.

VM 안에서 장치는 `/dev/disk/by-id/virtio-<볼륨 id 앞 20자>`(virtio-blk 시리얼)입니다.
`scripts/cloud/provision-kakao.sh`가 `nfs_data_volume_id` output으로 계산해 `NFS_DATA_DEVICE`로 전달하며,
`NFS_DATA_DEVICE`를 직접 export하면 덮어씁니다.

ext4 루트 디스크의 기존 데이터 이전은 별도의 승인 필요 런북(`docs/runbooks/nfs-xfs-migration.md`)입니다.
`01-nfs-server.sh`는 비어 있지 않은 `/srv/nfs/k8s` 위에는 마운트하지 않고 중단합니다.

## 알려진 이슈

이 프로젝트에 미해결 이슈는 없다. 다만 다른 문제로 오해하기 쉬운 두 가지는 알아둘 것:

- **로드밸런서를 인스턴스보다 먼저 만들면 안 된다.** `kakaocloud_load_balancer`는 같은 서브넷에서
  사설 VIP를 자동 할당받는데, `private_vip`는 computed라 고정할 수 없고 `kakaocloud_subnet`에는
  allocation pool이 없다. LB가 먼저 생성되면 compute 모듈이 고정한 주소를 가져가 버린다 —
  `module.loadbalancer`에 `depends_on = [module.compute]`가 있는 이유다.
- **모듈 레벨 `depends_on`은 그 모듈의 data source까지 apply 시점으로 미룬다.** 보안그룹 규칙을
  하나 추가했더니 실행 중인 인스턴스 전체가 재생성 대상으로 잡힌 적이 있다 —
  `data.kakaocloud_images`가 unknown이 되면서 `image_id`가 replacement를 강제했기 때문이다.
  의존성 목록은 실제 의존만큼만 좁게 유지할 것.

프로비저닝 쪽 이슈(컨테이너 런타임, unattended-upgrades, NFS)는
[`docs/common/lessons-log.md`](../../../docs/common/lessons-log.md)에 있다.

## 버전 정보

- **OpenTofu**: >= 1.13.0
- **Provider**: kakaocloud v0.5.1
- **OS**: Ubuntu 24.04 LTS (Kakao Cloud에 26.04 이미지가 없다; Vagrant 경로는 26.04)
