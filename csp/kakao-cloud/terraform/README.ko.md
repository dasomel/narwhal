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
거기서 도는 squid 프록시를 탄다 — Kakao Cloud provider 0.4.4에는 NAT 게이트웨이 리소스가 없고,
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

- **OpenTofu** >= 1.6.0 (`tofu`; Terraform CLI도 동작)
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
./scripts/cloud/provision-kakao.sh all   # base → mirror → init → join → nfs → phase1 → phase2
```

`provision-kakao.sh`는 모든 주소를 OpenTofu state에서 읽으므로 같은 값을 두 번 적지 않는다.
단계는 멱등이며(`/home/vagrant/.narwhal-stage/` 센티넬), `FORCE=1`로 특정 단계를 다시 돌린다.

## 배포 후

노드에 공인 IP가 없으므로 접근은 모두 bastion 경유다:

```bash
tofu output bastion_ssh          # 바로 쓸 수 있는 ssh / ProxyJump 명령
tofu output -raw bastion_public_ip

ssh -i "$(tofu output -raw ssh_key_path)" \
    -J ubuntu@"$(tofu output -raw bastion_public_ip)" \
    ubuntu@"$(tofu output -json master_private_ips | jq -r '.[0]')"
```

`assign_node_public_ips`를 켜지 않는 한 `master_public_ips`·`worker_public_ips`는 **빈 배열**이다 —
실패가 아니라 정상이다.

## 클러스터 접근

### 로컬에서 kubectl

apiserver 인증서의 SAN에는 `localhost`, `127.0.0.1`, 마스터 사설 IP, 사설 VIP가 들어 있고
**LB 공인 IP는 없다.** 공인 엔드포인트로 바로 붙으면 TLS 검증에 실패하므로, 인증서가 이미
포함하는 이름으로 터널을 판다:

```bash
KEY=$(tofu output -raw ssh_key_path)
BASTION=$(tofu output -raw bastion_public_ip)
MASTER1=$(tofu output -json master_private_ips | jq -r '.[0]')

# 1) master-1 에서 kubeconfig 가져오기 (root 소유라 sudo cat)
ssh -i "$KEY" -J ubuntu@"$BASTION" ubuntu@"$MASTER1" \
  'sudo cat /etc/kubernetes/admin.conf' > ~/.kube/kakao.conf
sed -i '' 's#server: https://.*#server: https://127.0.0.1:6443#' ~/.kube/kakao.conf   # GNU sed 는 -i

# 2) 터널을 열고 사용
ssh -f -N -i "$KEY" -L 6443:127.0.0.1:6443 -J ubuntu@"$BASTION" ubuntu@"$MASTER1"
KUBECONFIG=~/.kube/kakao.conf kubectl get nodes
```

2026-07-28 검증: 운영 호스트에서 노드 6대와 ArgoCD 앱 33개 조회 확인.
터널 종료는 `pkill -f '6443:127.0.0.1:6443'`.

공인 주소로 직접 붙고 싶다면 `02-init-cluster.sh`의 `certSANs`에 LB 공인 IP를 넣고 apiserver
인증서를 재발급한다 — TLS 검증을 끄지 말 것.

### 노드에서 kubectl

```bash
ssh -i "$KEY" -J ubuntu@"$BASTION" ubuntu@"$MASTER1"
sudo -E env KUBECONFIG=/home/vagrant/.kube/config-local kubectl get nodes
```

이 이미지에서 `sudo -E`만으로는 환경변수가 넘어가지 않으므로(`docs/cloud-deployment.md`의
관련 항목 참고) 위처럼 `KUBECONFIG`를 명시해 넘긴다.

### 서비스 계정과 비밀번호

플랫폼이 생성한 자격증명은 스크립트 하나로 전부 출력된다:

```bash
ssh -i "$KEY" -J ubuntu@"$BASTION" ubuntu@"$MASTER1" \
  'sudo env KUBECONFIG=/home/vagrant/.kube/config-local DOMAIN=local.narwhal.internal \
     bash /home/vagrant/scripts/test/show-credentials.sh'
```

Keycloak(관리자 + 사전 생성된 `admin`/`dev`/`view`/`guest` realm 사용자), ArgoCD, Gitea,
Harbor, Grafana, OpenBao 루트·언실 키, APISIX admin API 키, PostgreSQL 서비스 계정,
OIDC 클라이언트 시크릿을 포함한다. 마지막 "SSH / Node access" 블록은 아직 Vagrant 주소를
출력하므로, 이 배포의 주소는 `tofu output`으로 확인한다.

값 하나만 필요하면:

```bash
kubectl get secret <name> -n <ns> -o jsonpath='{.data.<key>}' | base64 -d
```

웹 UI는 `https://<서비스>.local.narwhal.internal`에서 Keycloak SSO를 거치며 worker LB로
들어간다. `--resolve`나 `/etc/hosts`로 호스트명을 LB에 연결한다:

```bash
LB=$(tofu output -raw worker_lb_public_ip)
curl -sk --resolve "argocd.local.narwhal.internal:443:$LB" https://argocd.local.narwhal.internal/
```

## 디렉토리 구조

```
terraform/
  main.tf              # 루트: network → security → compute → loadbalancer
  bastion.tf           # 점프 호스트 + 공인 IP
  variables.tf         # 변수 정의
  outputs.tf           # 노드 IP, LB 엔드포인트, bastion, ssh_key_path, subnet_cidr, vpc_cidr
  provider.tf          # kakaocloud provider v0.4.4
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

## 정리

```bash
tofu destroy
```

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
[`docs/lessons-log.md`](../../../docs/lessons-log.md)에 있다.

## 버전 정보

- **OpenTofu**: >= 1.6.0
- **Provider**: kakaocloud v0.4.4
- **OS**: Ubuntu 24.04 LTS (Kakao Cloud에 26.04 이미지가 없다; Vagrant 경로는 26.04)
