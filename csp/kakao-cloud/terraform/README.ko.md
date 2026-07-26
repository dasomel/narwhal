# Narwhal IDP on Kakao Cloud - Terraform Infrastructure

[English](README.md) | Korean

Kakao Cloud에 Narwhal IDP 클러스터용 VM 인프라를 Terraform으로 프로비저닝하는 프로젝트입니다. K8s 및 플랫폼 앱은 VM 생성 후 별도로 설치합니다.

## 생성되는 리소스

| 리소스 | 수량 | 설명 |
|--------|------|------|
| VPC | 1 | 172.16.0.0/16 |
| Subnet | 1 | 172.16.0.0/24 |
| Security Group | 1 | K8s + Cilium + NFS 포트 |
| Master VM | 3 | Control-plane 노드 (t1i.large, 4GB) |
| Worker VM | 3 | Data-plane 노드 (t1i.xlarge, 8GB) |
| Master LB | 1 | K8s API Server (6443) + etcd (2379) |
| Worker LB | 1 | Ingress HTTP (80) + HTTPS (443) |
| Public IP | 8 | VM 6개 + LB 2개 |

## 아키텍처

```
                              Internet
                                  |
                +-----------------+------------------+
                |                                    |
          Master LB                            Worker LB
     (<Public IP>:6443)               (<Public IP>:80/443)
                |                                    |
     +----------+----------+              +----------+----------+
     |          |          |              |          |          |
  Master-1  Master-2  Master-3       Worker-1  Worker-2  Worker-3
     |          |          |              |          |          |
     +----------+----------+--------------+----------+----------+
                            |
                      VPC: 172.16.0.0/16
                   Subnet: 172.16.0.0/24
```

## 사전 요구사항

- **OpenTofu** >= 1.6.0 (`tofu`; Terraform CLI도 동작)
- **Kakao Cloud 계정** + Application Credential (ID + Secret)
- **SSH KeyPair** (Kakao Cloud에 등록)
- **할당량**: 인스턴스 6, vCPU 24, RAM 48GB+, 볼륨 1.2TB, Public IP 8, LB 2

## 빠른 시작

```bash
cd csp/kakao-cloud/terraform

# 설정
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars에 자격증명 입력

# 배포
tofu init
tofu plan
tofu apply

# API 응답이 느린 환경
tofu apply -parallelism=3
```

## 배포 후

VM은 Ubuntu 24.04 + SSH 키 접근으로 생성됩니다. K8s와 Narwhal 플랫폼은 별도 설치:

```bash
# 접속 정보 확인
tofu output

# master-1 SSH 접속
ssh -i <your-key.pem> ubuntu@$(tofu output -json master_public_ips | jq -r '.[0]')
```

## 디렉토리 구조

```
terraform/
  main.tf              # Root: network -> security -> compute -> loadbalancer
  variables.tf         # 변수 정의
  outputs.tf           # VM IP, LB 엔드포인트, SSH 명령어
  provider.tf          # kakaocloud provider v0.4.4
  cloud-init.yaml      # 기본 VM 초기화 (SSH pubkey only)
  terraform.tfvars.example
  modules/
    network/           # VPC + Subnet
    security/          # Security Group (Cilium 포트)
    compute/           # Master/Worker VM + Public IP
    loadbalancer/      # Master LB (6443) + Worker LB (80/443)
```

## 주요 설정

`terraform.tfvars` 주요 항목:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `master_count` | 3 | Master 노드 수 (HA: 3) |
| `worker_count` | 3 | Worker 노드 수 |
| `master_flavor` | t1i.large | 2 vCPU, 4GB RAM |
| `worker_flavor` | t1i.xlarge | 4 vCPU, 8GB RAM |
| `volume_size` | 200 | 디스크 크기 (GB) |
| `availability_zone` | kr-central-2-a | 가용 영역 |

## 배포 소요 시간

| 모듈 | 소요 시간 | 비고 |
|------|----------|------|
| Network | ~60분 | VPC 생성 시 Kakao Cloud API가 느림 |
| Security | ~5분 | |
| Compute | ~15분 | VM 6개 + Public IP 6개 |
| LoadBalancer | ~10분 | NLB 2개 + 타겟 그룹 |
| **합계** | **~90분** | |

## 리소스 정리

```bash
tofu destroy
```

## 알려진 이슈

없음. 하드코딩을 강제하던 v0.3.3의 `kakaocloud_vpc` 버그는 v0.4.4에서 해결됐다 —
검증 근거는 `modules/network/README.md` 참고. VPC 값은 `terraform.tfvars`로 바꾼다.

## 버전 정보

- **OpenTofu**: >= 1.6.0
- **Provider**: kakaocloud v0.4.4
- **OS**: Ubuntu 24.04 LTS
