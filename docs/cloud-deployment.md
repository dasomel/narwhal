# Narwhal - Cloud Deployment Architecture

Narwhal은 원래 Vagrant VM 위에서 자라난 프로젝트다. 이 문서는 **같은 스크립트로 퍼블릭 클라우드에
배포할 때 무엇이 달라지는가**를 다룬다. 플랫폼 자체의 구조(APISIX, Keycloak, ArgoCD, 스토리지 등)는
[`architecture.md`](./architecture.md)에 있고 배포 대상과 무관하다.

현재 지원 대상은 Kakao Cloud이며, 2026-07-27 전 구간 검증을 마쳤다 — 노드 6/6 Ready,
ArgoCD 33개 앱 Synced/Healthy, worker LB 경유 외부 접근 정상.

IaC와 운영 명령은 [`csp/kakao-cloud/terraform/README.md`](../csp/kakao-cloud/terraform/README.md)에 있다.

---

## 1. 두 배포 대상 비교

| | Vagrant (로컬) | Kakao Cloud |
|---|---|---|
| OS | Ubuntu 26.04 (`dasomel/ubuntu-26.04-xfs`) | Ubuntu 24.04 (26.04 이미지 없음) |
| 노드 네트워크 | 192.168.56.0/24 | 172.16.0.0/24 |
| 컨트롤플레인 VIP | kube-vip (192.168.56.100) | Kakao NLB (사설 VIP 172.16.0.236) |
| Ingress 외부 노출 | MetalLB LoadBalancer (192.168.56.200) | worker NLB(공인) → NodePort 31080/31443 |
| 노드 공인 IP | 없음 (호스트 전용 네트워크) | 없음 (bastion 경유) |
| 노드 egress | 호스트 NAT | **bastion squid 프록시** |
| 파일 배포 | `synced_folder` 마운트 | tar over ssh (`stage-kakao-nodes.sh`) |
| 실행 순서 제어 | Vagrantfile provisioner | **`provision-kakao.sh`** |
| 로그인 계정 | `vagrant` | `ubuntu` (+ `vagrant` 계정 생성) |
| airgap 레지스트리 | 클러스터 내 워크로드 | **bastion (클러스터 밖)** |

핵심은 마지막 세 줄이다. Vagrant가 무료로 제공하던 것들 — 파일 마운트, 실행 순서, 계정 — 이
클라우드에는 없고, 그 부재가 스크립트의 **암묵적 전제**를 드러냈다(§5).

---

## 2. 클라우드 토폴로지

```
                            인터넷
                                |
        +-----------------------+-----------------------+
        |                       |                       |
    Bastion                 Master LB               Worker LB
  공인 IP + 사설 .207      공인 IP :6443          공인 IP :80/443
        |                       |                       |
  ┌─────┴──────┐            사설 VIP .236           사설 VIP .110
  │ sshd       │  ProxyJump     | K8s API               | → NodePort 31080/31443
  │ squid:3128 │  운영 접근      |                       |    (APISIX)
  │ registry   │  ←── 노드 egress                        |
  │   :5000    │  ←── 이미지 pull                        |
  └─────┬──────┘                |                       |
        v                       v                       v
   +---------------------------------------------------------+
   |  ▸ 노드는 사설 IP만 보유 — 공인 IP 없음                    |
   |                                                          |
   |  master-1 .10   master-2 .11   master-3 .12              |
   |  worker-1 .21   worker-2 .22   worker-3 .23              |
   |                                                          |
   |  NFS 서버: master-1 (/srv/nfs/k8s, export = 172.16.0.0/24)|
   +---------------------------------------------------------+
              VPC 172.16.0.0/16 · Subnet 172.16.0.0/24
       (위 .xx 표기는 모두 172.16.0.xx 사설 주소)
```

bastion은 세 가지 역할을 겸한다. 하나의 VM에 몰아둔 것은 노드를 프라이빗으로 유지하면서
필요한 경로만 여는 가장 단순한 방법이기 때문이다.

| 역할 | 포트 | 이유 |
|------|------|------|
| SSH 점프 호스트 | 22 | 노드에 공인 IP가 없다 |
| 포워드 프록시 (squid) | 3128 | provider 0.4.4에 NAT 게이트웨이 리소스가 없다 |
| airgap 부트스트랩 레지스트리 | 5000 | 클러스터 노드에 컨테이너 런타임 CLI가 없다 (§4) |

---

## 3. 노드 egress — 왜 프록시인가

노드는 프라이빗 서브넷에 있고 공인 IP가 없다. 그런데 프로비저닝은 **완전 오프라인이 아니다**:

- apt가 containerd, kube* 패키지를 받는다
- GitHub 릴리스에서 cilium-cli, hubble, metrics-server, nfs-quota-agent를 받는다
- Helm 저장소 6곳이 추가된다

airgap 번들은 **컨테이너 이미지와 차트 tarball만** 미러링하므로 이 트래픽은 여전히 VPC를 나가야 한다.
선택지는 셋이었다:

1. 노드마다 공인 IP — SG가 `0.0.0.0/0`으로 열어둔 22·6443이 그대로 노출된다
2. NAT 게이트웨이 — **provider 0.4.4의 리소스 35종에 없다** (스키마 확인)
3. **bastion 프록시** ← 채택

`configure-node-proxy.sh`가 세 소비자에 각각 설정을 심는다. 서로의 설정을 읽지 않기 때문이다:

```
apt        → /etc/apt/apt.conf.d/01narwhal-proxy   (+ Acquire::Retries "5")
로그인 셸   → /etc/environment                      (curl, helm, git)
containerd → systemd drop-in                       (미러에 없는 이미지용)
```

### NO_PROXY가 이 설정의 핵심

프록시로 새어 나가면 **에러가 아니라 행(hang)**이 나는 대상들이다:

```
localhost, 127.0.0.1, ::1,
VPC CIDR + 내부 IP 리터럴 7개,          ← curl 은 CIDR 을 파싱하지 못한다
10.244.0.0/16 (podSubnet),
10.96.0.0/12 (serviceSubnet),
169.254.169.254 (메타데이터),
registry 주소, .svc, .svc.cluster.local, .cluster.local, .local.narwhal.internal
```

CIDR은 Go 계열 클라이언트(containerd, helm, kubectl)를 위해 남기고, **curl은 정확한 호스트와
도메인 접미사만 매칭**하므로 내부 IP를 리터럴로 함께 나열한다. 이걸 빠뜨리면 VPC 내부 요청이
프록시로 나가고, 프록시는 그 주소에 닿지 못한다.

---

## 4. airgap 레지스트리 배치

Vagrant와 클라우드가 갈리는 지점이다.

`04-bootstrap-registry.sh`는 **nerdctl 또는 docker**를 요구하는데, 확인해 보면 둘 다 없다 —
Vagrant 박스에도, Kakao 노드에도 `ctr`뿐이다. 그래서 배치가 환경별로 달라진다.

| | 레지스트리 위치 | 기동 방법 |
|---|---|---|
| Kakao Cloud | **bastion** (클러스터 밖) | docker 설치 후 `04-bootstrap-registry.sh` |
| Vagrant | 클러스터 내 워크로드 | `04-bootstrap-registry-k8s.yaml` |

클러스터 내 배치에는 두 가지 제약이 붙는다:

- **이미지 side-load** — 미러가 없는 상태에서 레지스트리 자신의 이미지를 받을 수 없다(닭-달걀).
  `ctr -n k8s.io images import` + `imagePullPolicy: Never`로 끊는다.
- **hostPort, NodePort 아님** — containerd는 Service 계층 **아래**에서 이미지를 당기므로
  kube-proxy에 의존하면 안 된다. 단 `disallow-host-ports`가 Enforce라
  exclude 목록(`kube-system, istio-system, platform-system, devtools, security-system`)의
  네임스페이스에 배치해야 한다.

### 미러 경로 규약

`hosts.toml`의 호스트 경로는 반드시 **`/v2/<upstream>`**이다:

```toml
server = "https://registry.k8s.io"
[host."http://<REG>/v2/registry.k8s.io"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
  override_path = true
```

`override_path`가 켜지면 containerd가 이 경로 뒤에 `<repo>/manifests/<tag>`를 그대로 붙인다.
`/<upstream>`으로 쓰면 `<REG>/<upstream>/v2/<repo>`를 요청하는데 저장 위치는
`<REG>/v2/<upstream>/<repo>`라 어긋나고, 레지스트리가 404 **HTML**을 돌려주므로 kubelet에는

```
unexpected media type text/html for sha256:...: not found
```

으로 보인다. URL도 404도 알려주지 않는 메시지라 원인 추적이 어렵다.

### 미러가 실제로 쓰이는지 판정하는 법

```bash
# 레지스트리 로그에 containerd 요청이 있는가
docker logs airgap-registry | grep 'useragent="containerd'
#   /v2/docker.io/library/busybox/manifests/1.28?ns=docker.io  [containerd/2.2.2]

# 진짜 폐쇄망 검증: 프록시를 내리고 pull 이 되는지
sudo systemctl stop squid
sudo ctr -n k8s.io images pull --hosts-dir /etc/containerd/certs.d <image>
```

프록시가 살아 있으면 미러 실패가 인터넷 폴백으로 가려진다. 2026-07-28 검증에서 squid를 내린 채
`kubeadm config images list` 7개를 전부 미러에서 받아 **폐쇄망 kubeadm init 가능**을 확인했다.

---

## 5. Vagrant 박스가 감추고 있던 전제

클라우드 배포에서 드러난 것들이다. 모두 "박스에 이미 있어서 스크립트로 만든 적이 없는" 부류다.

| 전제 | 없을 때 증상 | 조치 |
|------|-------------|------|
| `net.ipv4.ip_forward`, `br_netfilter` | kubeadm preflight 실패 | `01-prerequisites.sh`가 생성 |
| `nfs-common` (NFS **클라이언트**) | CSI 마운트가 110초 타임아웃 | 동상 |
| `vagrant` **계정** (경로만이 아니라) | `chown: invalid user 'vagrant:vagrant'` | `stage-kakao-nodes.sh`가 생성 |
| 노드 호스트명 규약 | `nodes "narwhal-master" not found` | `MASTER_HOSTNAME` 유도 |
| `/home/vagrant/{scripts,configs}` 레이아웃 | 31개 스크립트가 경로를 하드코딩 | tar로 동일 레이아웃 재현 |

`ip_forward`는 리포 **전체에 설정이 없었다** — 박스의 `/etc/sysctl.d/k8s-network.conf`에 의존하고
있었다. 모듈을 먼저 로드하고 그다음 bridge sysctl을 적용해야 한다. `net.bridge.*` 키는
`br_netfilter`가 올라오기 전에는 존재하지 않고, sysctl은 없는 키를 조용히 건너뛴다.

### 환경 변수는 이미 파라미터화돼 있었다

`NFS_SERVER_IP`, `HOST_NETWORK_CIDR`, `MASTER_HOSTNAME`은 전부 `${VAR:-default}` 형태로 열려
있었는데 기본값이 Vagrant 주소였다. **전체 env 집합을 조립한 주체가 없었을 뿐**이고, 그것이
`provision-kakao.sh`의 존재 이유다.

> **`sudo -E`는 이 이미지에서 무시된다** — `sudo: preserving the entire environment is not
> supported, '-E' is ignored`. 호출 셸에서 export한 값이 전부 사라지므로 반드시
> `sudo env VAR=... script.sh` 형태로 넘긴다. (단 `sudo -E VAR=value cmd` 형태의 명시적 할당은 통과한다.)

---

## 6. 프로비저닝 오케스트레이션

Vagrantfile의 provisioner 목록이 하던 순서 제어를 클라우드에서는 `provision-kakao.sh`가 한다.

```
base    01-prerequisites → 02-containerd → 03-k8s-install → 06-boot-heal   (전 노드)
runtime 02-containerd + mirror 재적용                                       (전 노드)
mirror  06-configure-mirrors --local-only                                   (전 노드)
init    01-nfs-server → 02-init-cluster → 03-cni-install                    (master-1)
join    아티팩트 배포 → 컨트롤플레인 순차 → 워커                              (나머지)
nfs     01-nfs-server 재실행 (kubeadm init 재실행 없이 export 갱신)          (master-1)
phase1  04-addons → 05-nfs-quota-agent                                      (master-1)
phase2  06-phase2-start (플랫폼 스크립트 18개)                               (master-1)
```

설계상 세 가지가 중요하다:

- **주소는 전부 OpenTofu state에서** 읽는다. 유도가 필요한 값도 유도한다 —
  `MASTER_IP_BASE`는 고정 사설 IP에서, `MASTER_HOSTNAME`은 master-1에 직접 물어서.
- **단계별 멱등성** — `/home/vagrant/.narwhal-stage/` 센티넬. 재실행이 반복이 아니라 이어서 진행이 된다.
  `FORCE=1`로 특정 단계만 다시 돌린다.
- **컨트롤플레인은 한 번에 하나씩** join한다. etcd 멤버 둘이 동시에 들어오면 쿼럼을 잃을 수 있다.

`nfs`를 `init`에서 분리한 이유가 있다. NFS export만 갱신하려고 `FORCE=1 init`을 돌리면
**kubeadm init까지 다시 돌아 컨트롤플레인이 날아간다.**

### join 아티팩트 배포

`02-join-*.sh`의 `PROVIDER=kakao` 분기는 join 파일이 이미 `/tmp`에 있다고 가정한다 —
클라우드 이미지에는 sshpass가 쓸 `vagrant` 비밀번호가 없기 때문이다.
`distribute-join-kakao.sh`가 그 "operator" 역할을 하며, master-1 → bastion → 노드로 옮긴다.
**노드끼리는 통신하지 않는다.**

---

## 7. provider-aware GitOps

`gitops/charts/narwhal-apps`가 `provider` 값을 받아 렌더를 바꾼다. 값은 스크립트의
`${PROVIDER:-vagrant}`와 같은 어휘를 쓰고, `14-gitops-bootstrap.sh`가 app-of-apps에 주입한다.

| | `vagrant` | `kakao` |
|---|---|---|
| MetalLB Application | 렌더됨 | **렌더 안 됨** (L2/ARP 불가) |
| APISIX gateway Service | `LoadBalancer` + MetalLB VIP | `NodePort` 31080/31443 |

NodePort를 **차트에 선언**하는 것이 요점이다. 명령형 패치로 두면 ArgoCD selfHeal이 차트 렌더 결과로
되돌려 클라우드 ingress가 죽는다. chart 2.13.0의 `service-gateway.yaml`은
`service.{http,tls}.nodePort`를 `type: NodePort`일 때만 반영한다.

`vagrant` 분기는 **바이트 단위로 이전과 동일하게** 렌더된다(분기 설명을 YAML 주석이 아니라
Helm 주석으로 둔 이유). 기존 클러스터에 드리프트가 생기지 않는다.

---

## 8. 검증 방법

배포가 끝났다고 종료 코드를 믿지 않는다. `up.sh`가 Phase 2 실패를 `rc=0`으로 보고한 전례가 있다.

```bash
# 클러스터 실물
kubectl get nodes --no-headers | grep -c " Ready "                    # 6
kubectl get pods -A --no-headers | awk '$4!="Running" && $4!="Completed"' | wc -l   # 0
kubectl get applications -n devtools --no-headers | awk '$2"/"$3' | sort | uniq -c  # 전부 Synced/Healthy

# provider 반영
kubectl get application idp-apps -n devtools -o jsonpath='{.spec.source.helm.valuesObject.provider}'
kubectl get application metallb -n devtools 2>/dev/null | wc -l       # kakao 면 0
kubectl get svc apisix-gateway -n platform-system                     # NodePort 31080/31443

# 외부 접근 (worker LB 공인 IP 경유)
LB=$(cd csp/kakao-cloud/terraform && tofu output -raw worker_lb_public_ip)
curl -sk -o /dev/null -w '%{http_code}\n' --resolve "portal.local.narwhal.internal:443:$LB" \
  https://portal.local.narwhal.internal/          # 307 (SSO 리다이렉트) = 정상
```

---

## 참고

- [`csp/kakao-cloud/terraform/README.md`](../csp/kakao-cloud/terraform/README.md) — IaC, 리소스, 소요 시간
- [`scripts/airgap/README.md`](../scripts/airgap/README.md) — 번들 생성·전송·로드
- [`architecture.md`](./architecture.md) — 플랫폼 구조 (배포 대상 무관)
- [`lessons-log.md`](./lessons-log.md) — 사건별 상세 기록
