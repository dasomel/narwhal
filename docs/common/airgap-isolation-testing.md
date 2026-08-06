# 격리(Isolation) 검증과 전환 — 로컬 · 카카오 공통

> airgap 번들이 완전한지가 아니라, **클러스터가 정말 인터넷과 끊겨 있는지**를 다룬다.
> 이 둘은 다른 질문이다: 번들이 완벽해도 격리가 새면 검증은 아무것도 증명하지 않고,
> 그 반대도 마찬가지다. 사건 기록은 [`lessons-log.md`](lessons-log.md)의 2026-08-05 ~ 08-06 행.

## 격리의 3층

| 층 | 무엇 | 지속성 | 어디서 |
|----|------|--------|--------|
| ① 라우트 제거 | `ip route del default` | **DHCP 갱신까지만** | `01-prerequisites.sh` (AIRGAP=1) |
| ② networkd drop-in | `[DHCPv4] UseGateway=false` | 재부팅 포함 영구 | 같은 곳. 파일: `/etc/systemd/network/<망파일>.d/airgap.conf` |
| ③ iptables REJECT | VPC 밖 전부 거부 | 메모리 (재부팅에 소멸) | `scripts/test/airgap-isolate-kakao.sh` — 테스트 전용 |

①만으로는 유지되지 않는다 — 측정 결과 카카오 6노드 전부 DHCP 갱신으로 라우트가
부활했다. ②가 본 메커니즘이고, ③은 base를 프록시로 이미 깐 노드를 재설치 없이
격리해볼 때만 쓴다.

**카카오 주의**: drop-in은 `UseGateway=false` **한 줄만**. 카카오 DHCP는 메타데이터·NTP
경로(`169.254.169.254` 등)를 classless 라우트로 내려주므로 `UseRoutes=false`를 넣으면
그것까지 버려져 다음 부팅에서 cloud-init이 깨진다. 리스가 뭘 나르는지는
`ip route | grep "proto dhcp"`로 먼저 확인한다.

## 검증: verify-isolation.sh

```bash
scripts/test/verify-isolation.sh local              # 로컬,  격리 기대
scripts/test/verify-isolation.sh kakao              # 카카오, 격리 기대
EXPECT=open scripts/test/verify-isolation.sh kakao  # 의도적으로 개방한 상태 검증
```

노드마다 6가지를 묻고, 종료 코드는 불일치 노드 수다.

| 항목 | 통과 조건 (격리 기대) | 이 항목이 잡아낸 실제 사건 |
|------|----------------------|---------------------------|
| `route` | 기본 라우트 없음 | `proto dhcp`로 부활한 라우트 (08-05) |
| `dropin` | airgap.conf 존재 | 지금은 격리, 다음 갱신에 개방되는 노드 |
| `direct` | `curl --noproxy '*'` 실패 | **프록시 중지 ≠ 오프라인** — VPC 자체 egress (08-05) |
| `mirror` | 번들 레지스트리 200 | 미러까지 끊긴 "죽은 클러스터" 오탐 방지 |
| `apt` | 온라인 소스 0, 번들 소스만 | 프록시 뒤에 숨어 있던 apt 인터넷 의존 |
| `meta` | (카카오) 169.254 경로 유지 | UseRoutes=false가 메타데이터 경로를 삭제 (08-06) |

## 수동 판별법 — 스크립트가 답 못 하는 것들

- **미러가 실제로 쓰이는가**: 레지스트리 로그에서 `useragent="containerd"`를 센다.
  0이면 이미지가 있어도 무용지물이다 — `config_path` 콜론 버그를 찾아낸 그 한 줄.
- **GitOps가 오프라인에서 도는가**: `Synced`는 *마지막* 비교의 성공을 말할 뿐이다.
  **hard refresh를 강제하고 다시 읽어라** — 라우트가 있을 때 동기화된 클러스터는
  격리 후에도 한동안 전부 Synced로 보인다 (08-05).
- **미러 폴백이 성립하는가**: 개방 상태에서 미러에 없는 이미지를 pull해본다.
  `NotFound`가 나면 hosts.toml의 `server` 줄이 레지스트리가 아닌 호스트를 가리키는
  것이다 — docker.io는 `registry-1.docker.io`여야 한다 (08-07, postgres:18.3-alpine).

## 전환 절차

**격리 해제** (노드별):

```bash
sudo rm -f /etc/systemd/network/*.d/airgap.conf
sudo networkctl reload && sudo networkctl renew <iface>   # 라우트가 다시 내려온다
```

**재격리** (노드별):

```bash
nwf=$(networkctl status <iface> | awk -F': ' '/Network File:/ {print $2}' | tr -d ' ')
d="/etc/systemd/network/$(basename "$nwf").d"
sudo mkdir -p "$d"
printf '[DHCPv4]\nUseGateway=false\n' | sudo tee "$d/airgap.conf" >/dev/null
sudo networkctl reload
sudo ip route del default 2>/dev/null || true   # reload가 갱신을 촉발하면 한 번 더
```

망 파일은 반드시 `networkctl status`로 얻는다 — netplan은 렌더 파일을 **netplan id**
이름으로 만들므로 인터페이스명 글롭은 조용히 빗나간다 (`10-netplan-main_if.network`).
적용 후 `verify-isolation.sh`로 확인하고, reload가 촉발한 DHCP 갱신과 `route del`이
경합해 라우트가 남는 노드가 있을 수 있으니(6노드 중 1노드에서 측정) FAIL이 나면
`route del`만 다시 실행한다.

**전환 후에는 반드시 `verify-isolation.sh`를 돌린다.** 격리를 풀었으면 `EXPECT=open`으로 —
"의도한 상태"와 "실제 상태"의 불일치가 이 파일에 기록된 사건 대부분의 뿌리다.
