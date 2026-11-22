# OpenBao TLS 갱신과 listener reload (#766)

## 근거와 선택

- **관측(오케스트레이터, 2026-11-22):** Secret은 revision 4(만료 2027-02-20, serial 5CCB…)인데 listener는 이전 인증서(2027-02-15, 44E5…)였다. 이 sandbox에서 재측정하지 않았다.
- **관측(저장소):** `scripts/cluster/08-4-storage.sh`만 `Certificate/storage/openbao-tls`를 만든다. GitOps는 이 Secret을 마운트하지만 Certificate를 생성하지 않는다. 소유권을 이전하거나 GitOps 사본을 추가하지 않았다. 따라서 현재 저장소 정의끼리 ArgoCD 소유권 충돌은 없다. 라이브 tracking annotation은 아래에서 별도로 확인한다.
- **결정 D-BAO-VALIDITY:** 다른 플랫폼 leaf와 같은 `duration: 8760h`, `renewBefore: 720h`(365일, 30일 전 갱신)를 명시한다. cert-manager 기본 90일이 89일 표시의 원인이다. 기존 Certificate의 duration 변경은 한 번 재발급을 유발한다([cert-manager 문서](https://cert-manager.io/docs/usage/certificate/#reissuance-triggered-by-user-actions)); 실제 notAfter는 발급 결과로 확인한다.
- **관측(버전 고정 upstream):** [2.5.4 TCP 문서](https://github.com/openbao/openbao/blob/v2.5.4/website/content/docs/configuration/listener/tcp.mdx)는 시작 시 설정한 cert/key 경로를 SIGHUP으로 다시 읽는다고 명시한다. [2.5.4 server 소스](https://github.com/openbao/openbao/blob/v2.5.4/command/server.go)의 SIGHUP 분기는 listener reload와 audit/plugin/config reload를 수행하며 shutdown 분기와 분리돼 있다. [system API 소스](https://github.com/openbao/openbao/blob/v2.5.4/vault/logical_system.go)의 `handleConfigReload`는 `logical.ErrUnsupportedPath`를 반환한다. `/sys/config/reload/{subsystem}` 경로 자체는 [등록돼 있지만](https://github.com/openbao/openbao/blob/v2.5.4/vault/logical_system_paths.go) listener TLS reload API로 쓸 수 없다. [자동 TLS polling은 2.7 기능](https://openbao.org/docs/release-notes/2-7-0/)이므로 2.5.4 설정에 넣지 않는다.
- **관측(버전 고정 chart):** [0.28.3 args helper](https://github.com/openbao/openbao-helm/blob/openbao-0.28.3/charts/openbao/templates/_helpers.tpl)는 `/bin/sh -ec` 안에서 config 치환 후 `/usr/local/bin/docker-entrypoint.sh bao server -config=/tmp/storageconfig.hcl` 뒤에 `server.extraArgs`를 붙인다. [StatefulSet template](https://github.com/openbao/openbao-helm/blob/openbao-0.28.3/charts/openbao/templates/server-statefulset.yaml)의 command와 preStop은 그대로 사용한다.

**D-BAO-HUP:** 기존 컨테이너의 `extraArgs`에서 서버를 백그라운드로 시작한 후 서버 PID를 저장하고, 독립된 subshell 감시 루프를 시작한 다음 서버 종료를 `wait`한다. 기존 wrapper·docker-entrypoint·dumb-init·bao 실행 체인을 유지한다. 감시 루프가 죽어도 서버는 계속 실행되며 wrapper의 종료 상태는 서버 종료 상태다. postStart의 분리된 자식 생존에 의존하지 않는다. 차트 helper가 extraArgs 줄바꿈을 들여쓰기하지 않으므로 `>-` folded scalar로 한 줄의 셸 명령을 전달한다.

감시 루프는 시작 시 `/openbao/tls/tls.crt`의 SHA-256을 기억하고 30초마다 같은 파일을 읽는다. kubelet의 원자적 `..data` symlink 교체를 따라간다. 해시가 바뀌면 `comm == bao`와 `argv[1] == server`를 모두 만족하는 PID(1 제외)에 한 번 SIGHUP을 보낸 후 해시를 갱신한다. PID 부재·signal 실패는 ERROR로 기록하고 해시를 유지해 다음 tick에 재시도한다. OpenSSL·served 비교·추가 이미지·볼륨·공유 PID namespace는 사용하지 않는다. 비용은 30초마다 파일 해시 한 번이며, 탈출 경로는 두 values의 `extraArgs` 제거와 수동 HUP이다. 감시 루프의 비정상 종료나 HUP 후 실제 listener reload 실패는 외부 verifier로 탐지한다.

**라운드 1 교정:** 같은 UID 100의 사이드카와 `shareProcessNamespace` 조합은 `/proc/<bao pid>/environ` 및 `/proc/<pid>/root`를 통해 TLS 개인키·raft 데이터·ServiceAccount token을 읽을 수 있어 cert-only mount 격리가 성립하지 않았다. 이 설계는 제거했다. 오케스트레이터 관측상 PID 1은 wrapper shell, PID 11은 dumb-init, PID 12는 bao였다. **`kill -HUP 1`은 절대 실행하지 않는다:** wrapper 종료·컨테이너 재시작·Shamir seal을 유발한다. PID 숫자를 고정하지 않는다. 오케스트레이터는 실제 bao server에 HUP 후 served serial == Secret serial, Sealed=false, restarts=0을 관측했다(이 작업자는 재측정하지 않음).

rollout restart는 Shamir seal을 유발하므로 인증서 갱신에 사용하지 않는다. 변경 없는 주기적 HUP도 audit/plugin/config reload를 수반하므로 사용하지 않는다.

**D-BAO-WINDOW:** `scripts/test/verify-openbao-tls.sh <kubeconfig>`는 Secret의 공개 `tls.crt`와 Pod port-forward를 통한 listener의 leaf를 **호스트 OpenSSL**로 비교한다. Pod 내부 OpenSSL이나 exec가 필요 없다. 30초 간격 7회(180초 대기 + 제한된 API/port-forward/handshake 시간) 관측한다. API·연결·파싱 오류도 이 창 안에서 재시도하며 끝까지 일치하지 않으면 FAIL이다. 전역 context는 사용하지 않는다. 상시 exporter/알림은 추가하지 않았다.

## 오케스트레이터 실행 순서 (이 sandbox에서 미실행)

공유 클러스터 변경은 소유자 승인 범위에서 실행한다. 아래 wrapper는 모든 명령에 지정 kubeconfig를 전달한다.

```bash
KC=/private/tmp/claude-501/-Users-m-Documents-IdeaProjects-20-dasomel-narwhal/cb748b7d-4376-47ef-a504-41984680279b/scratchpad/kc-narwhal
k() { kubectl --kubeconfig "$KC" "$@"; }
k -n storage get certificate openbao-tls -o 'jsonpath={.metadata.annotations.argocd\.argoproj\.io/tracking-id}{"\n"}{.status.notAfter}{"\n"}'
```

1. **실행 중인 현재 Pod 즉시 복구, restart 없음.** 먼저 Secret과 mounted 파일 지문이 같은지 확인한다. 다르면 kubelet projection을 기다리고 재확인한다. 개인키는 읽거나 출력하지 않는다.

```bash
k -n storage get secret openbao-tls -o 'jsonpath={.data.tls\.crt}' | openssl base64 -d -A | openssl x509 -noout -fingerprint -sha256 -serial -enddate
k -n storage exec openbao-0 -c openbao -- cat /openbao/tls/tls.crt | openssl x509 -noout -fingerprint -sha256 -serial -enddate
k -n storage exec openbao-0 -c openbao -- /bin/sh -ec '
  found=false
  for proc in /proc/[0-9]*; do
    [ "${proc##*/}" != 1 ] || continue
    [ "$(cat "$proc/comm" 2>/dev/null)" = bao ] || continue
    [ "$(tr "\000" "\n" < "$proc/cmdline" | sed -n "2p")" = server ] || continue
    kill -HUP "${proc##*/}"
    found=true
  done
  [ "$found" = true ]'
k -n storage exec openbao-0 -c openbao -- bao status -tls-skip-verify
k -n storage get pod openbao-0 -o 'jsonpath={.metadata.uid}{"\n"}{.status.containerStatuses[*].restartCount}{"\n"}'
# 별도 터미널에서 유지한다(ready endpoint에 의존하지 않는 Pod 직접 연결).
k -n storage port-forward pod/openbao-0 18200:8200
# 원래 터미널에서 serial == Secret serial 및 notAfter 확인 후 port-forward 종료.
openssl s_client -connect 127.0.0.1:18200 -servername openbao.storage.svc </dev/null 2>/dev/null | openssl x509 -noout -serial -enddate
```

`bao status`의 `Sealed false`와 Pod UID/restartCount 불변도 확인한다. skip-verify는 기존 복구 CLI 방식이며 지문 비교가 신뢰 기준이다. TLS handshake는 새 연결로 관측하며 이미 연결된 클라이언트 세션은 계속 동작할 수 있다.

2. **명시적 유효기간 적용.** bootstrap 전체를 재실행하지 않고 기존 bootstrap 소유 리소스만 patch한다. 재발급된 새 Secret을 기다리고 1번 mounted 일치 확인과 HUP·served serial 비교를 반복한다.

```bash
k -n storage patch certificate openbao-tls --type=merge -p '{"spec":{"duration":"8760h","renewBefore":"720h"}}'
k -n storage get certificate openbao-tls -w
# revision 증가와 발급 완료 후 Ctrl-C. 이전 Ready=True만으로 새 발급 완료라고 판단하지 않는다.
```

3. **영구 watcher 배포.** 기존 GitOps 배포 경로(`scripts/gitops/push-to-gitea.sh`, 저장소 루트 직접 push 금지)로 배포한다. StatefulSet은 이미 **OnDelete**이므로 sync만으로 실행 중 Pod가 바뀌지 않는다. template에 추가 컨테이너·공유 PID namespace가 없고 openbao 컨테이너 args에 감시 루프가 있는지 확인한다. **최초 watcher 설치 및 향후 모든 이미지/차트 변경에도 계획된 Pod 삭제와 unseal이 필요하다.** 승인된 유지보수 창에서 아래 순서를 실행한다. 이후 인증서 갱신은 Pod 교체 없이 처리한다.

```bash
k -n storage get statefulset openbao -o yaml
k -n storage delete pod openbao-0
# Running 상태를 확인한다. sealed이면 Ready 대기는 완료되지 않는다.
k -n storage get pod openbao-0 -w
# 새 터미널에서 Running 확인 후 한 번 실행(동일 이름 Job이 있으면 새 이름 사용).
k -n storage create job --from=cronjob/openbao-auto-unseal openbao-tls-first-unseal
k -n storage wait --for=condition=complete job/openbao-tls-first-unseal --timeout=180s
k -n storage wait --for=condition=Ready pod/openbao-0 --timeout=180s
scripts/test/verify-openbao-tls.sh "$KC"
k -n storage logs openbao-0 -c openbao --tail=20
```

4. **실제 갱신 검증(승인된 경우).** `cmctl --kubeconfig "$KC" renew -n storage openbao-tls` 후 revision/Secret 교체를 확인하고 verifier를 실행한다. mounted 지문·served serial 일치, Sealed=false, Pod UID/restartCount 불변을 확인해야 hot reload 증명이 된다. portal 접근·injector·snapshot 다음 성공도 확인한다. 이 작업자는 라이브 실행하지 않았다.

5. **롤백.** 두 values의 `extraArgs`를 제거하고 sync한다. OnDelete이므로 실행 중 감시 루프 제거에는 승인된 Pod 교체+unseal이 필요하다. 그 전에는 수동 HUP 경로를 유지한다. 유효기간 롤백은 bootstrap 소유 Certificate의 명시적 duration을 이전 값으로 복구하고, 재발급 후 mounted 일치를 확인해 수동 HUP한다. 개인키·이전 Secret·raft 데이터를 되돌리지 않는다.

## 로컬 증거와 한계

`R390`은 Helm/promtool 없이 YAML 계약·bootstrap 동일성·`/bin/sh` 감시 루프(변경 없음, symlink 교체 후 1회 signal, PID 부재 후 재등장, PID 1/CLI/다른 comm 제외, watcher crash 격리)와 변이를 검증한다. verifier는 bash 3.2/5 fake kubectl/OpenSSL로 성공·일시 API/연결 오류 복구·지속 오류/불일치/빈 응답의 fail-closed를 확인한다. 실제 OpenBao·kubelet·admission·ArgoCD·클라이언트 경로의 증거를 대신하지 않는다.

리뷰어 환경에서는 **정확한 chart 0.28.3 `helm template`이 성공**했다. 라운드 1의 DNS 차단 기록은 그 실행 sandbox에 한정되며 차트의 렌더링 불가를 의미하지 않는다. 라운드 2의 현재 sandbox에서도 아래 다운로드는 DNS 오류로 실패했다. 배포 전 최종 values로 정확한 차트를 다시 렌더링해 확인한다.

```bash
yq '.spec.source.helm.valuesObject' gitops/charts/narwhal-apps/templates/openbao.yaml > /tmp/openbao-tls-values.yaml
helm template openbao openbao --repo https://openbao.github.io/openbao-helm --version 0.28.3 --namespace storage -f /tmp/openbao-tls-values.yaml > /tmp/openbao-tls-rendered.yaml
```
