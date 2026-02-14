# PostgreSQL 데이터베이스 아키텍처

> CloudNative-PG 기반 통합 PostgreSQL 클러스터 문서

## 개요

Narwhal 프로젝트는 CloudNative-PG Operator를 사용하여 단일 통합 PostgreSQL 클러스터를 운영합니다.
기존의 앱별 독립 DB 클러스터 구성에서 하나의 HA 클러스터로 통합하여 리소스 효율성과 관리 편의성을 확보했습니다.

## 아키텍처

### 클러스터 정보
- **Operator**: CloudNative-PG v1.28.1 (Helm chart 0.27.1)
- **클러스터명**: `narwhal-db`
- **네임스페이스**: `database`
- **인스턴스 수**: 2개 (Primary 1개 + Replica 1개)
- **PostgreSQL 버전**: 17
- **커넥션 풀러**: PgBouncer (transaction mode)

### HA 구성
- Primary/Replica 구조로 고가용성 확보
- Primary 장애 시 Replica가 자동으로 Primary로 승격
- CNPG Operator가 etcd 스타일 리더 선출 관리
- WAL 스트리밍 복제로 데이터 동기화

## 데이터베이스 목록

| 데이터베이스 | Owner | 비밀번호 | 사용처 |
|--------------|-------|----------|--------|
| keycloak | keycloak | keycloak-db-password | Keycloak IAM/SSO |
| harbor | harbor | harbor-db-password | Harbor 컨테이너 레지스트리 |
| gitea | gitea | gitea-db-password | Gitea Git 서버 |

## 접속 방법

### PgBouncer를 통한 접속 (권장)
```
Host: narwhal-db-pooler-rw.database.svc.cluster.local
Port: 5432
```

커넥션 풀링으로 성능과 안정성 향상.

### 직접 접속
```
Host: narwhal-db-rw.database.svc.cluster.local
Port: 5432
```

마이그레이션 등 특수 작업 시에만 사용.

### 네임스페이스 간 접속

각 애플리케이션 네임스페이스에 ExternalName Service를 생성하여 짧은 서비스명으로 접속 가능:

| 네임스페이스 | 서비스명 | 대상 |
|--------------|----------|------|
| keycloak | keycloak-db-rw:5432 | narwhal-db-pooler-rw.database |
| harbor | harbor-db-rw:5432 | narwhal-db-pooler-rw.database |
| gitea | gitea-db-rw:5432 | narwhal-db-pooler-rw.database |

**예시 연결 문자열**:
```
# Keycloak namespace 내부에서
jdbc:postgresql://keycloak-db-rw:5432/keycloak
```

## 성능 튜닝 파라미터

### PostgreSQL 설정

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| shared_buffers | 256MB | 공유 버퍼 크기 |
| effective_cache_size | 768MB | OS 캐시 크기 추정값 |
| work_mem | 8MB | 정렬/해시 작업 메모리 |
| maintenance_work_mem | 128MB | VACUUM 등 유지보수 메모리 |
| max_connections | 200 | 최대 동시 연결 수 |
| max_wal_size | 1GB | WAL 최대 크기 |
| min_wal_size | 512MB | WAL 최소 크기 |
| checkpoint_timeout | 15min | 체크포인트 간격 |
| archive_timeout | 300s | WAL 아카이빙 타임아웃 |
| random_page_cost | 1.1 | SSD 최적화 (기본값 4.0) |
| effective_io_concurrency | 200 | SSD 병렬 I/O |
| log_min_duration_statement | 1000ms | 1초 이상 쿼리 로깅 |

### PgBouncer 설정

| 파라미터 | 값 | 설명 |
|----------|-----|------|
| pool_mode | transaction | 트랜잭션 단위 커넥션 재사용 |
| max_client_conn | 1000 | 클라이언트 최대 연결 수 |
| default_pool_size | 25 | DB당 기본 풀 크기 |
| min_pool_size | 5 | 최소 유지 커넥션 |
| reserve_pool_size | 5 | 예약 커넥션 |
| server_idle_timeout | 60s | 유휴 서버 커넥션 타임아웃 |

## 스토리지

- **용량**: 인스턴스당 20Gi
- **StorageClass**: nfs-csi
- **총 사용량**: 40Gi (Primary 20Gi + Replica 20Gi)

## 백업 전략

### CNPG Barman (S3 백업)
- **주기**: 매일 00:00
- **대상**: SeaweedFS S3
- **자격증명**: admin/admin
- **방식**: WAL 아카이빙 + Full Backup

### Velero (PVC 백업)
- **주기**: 매일 02:00
- **대상**: 전체 PVC 스냅샷
- **용도**: 재해복구 (Disaster Recovery)

## 운영 명령어

### 클러스터 상태 확인
```bash
# 클러스터 정보
kubectl get cluster -n database

# Pod 상태
kubectl get pods -n database

# 상세 정보
kubectl describe cluster narwhal-db -n database
```

### 데이터베이스 접속
```bash
# psql 접속
kubectl exec -it narwhal-db-1 -n database -- psql -U postgres

# 특정 데이터베이스 접속
kubectl exec -it narwhal-db-1 -n database -- psql -U keycloak -d keycloak
```

### 복제 상태 확인
```bash
# 복제 상태 조회
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT * FROM pg_stat_replication;"

# 복제 지연 확인
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT client_addr, state, sync_state, write_lag, flush_lag, replay_lag FROM pg_stat_replication;"
```

### 데이터베이스 목록 확인
```bash
# 모든 데이터베이스 목록
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "\l"

# 크기 포함
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT datname, pg_size_pretty(pg_database_size(datname)) AS size FROM pg_database WHERE datname NOT LIKE 'template%' ORDER BY pg_database_size(datname) DESC;"
```

### 커넥션 모니터링
```bash
# 데이터베이스별 연결 수
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT datname, numbackends FROM pg_stat_database WHERE datname NOT LIKE 'template%';"

# 활성 쿼리 확인
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT pid, usename, datname, state, query FROM pg_stat_activity WHERE state = 'active';"
```

### 수동 Failover
```bash
# Replica를 Primary로 승격
kubectl cnpg promote narwhal-db narwhal-db-2 -n database

# 승격 확인
kubectl get cluster narwhal-db -n database -o jsonpath='{.status.currentPrimary}'
```

### 백업 관리
```bash
# 백업 목록 확인
kubectl cnpg backup list narwhal-db -n database

# 수동 백업 실행
kubectl cnpg backup narwhal-db -n database --backup-name manual-backup-$(date +%Y%m%d)

# 백업 상태 확인
kubectl get backups -n database
```

### 복구 (Restore)
```bash
# 특정 시점 복구 (PITR)
kubectl cnpg recovery narwhal-db -n database --target-time "2026-02-14 12:00:00"

# 특정 백업에서 복구
kubectl cnpg recovery narwhal-db -n database --backup-name manual-backup-20260214
```

## 모니터링

### 주요 메트릭

Prometheus로 수집되는 주요 메트릭:

- `cnpg_pg_stat_replication_lag`: 복제 지연 시간
- `cnpg_pg_database_size_bytes`: 데이터베이스 크기
- `cnpg_pg_stat_database_numbackends`: 연결 수
- `cnpg_pg_postmaster_start_time`: PostgreSQL 시작 시간
- `cnpg_pg_settings_max_connections`: 최대 연결 수 설정
- `cnpg_pg_stat_archiver_archived_count`: 아카이빙된 WAL 수

### Grafana 대시보드

ArgoCD를 통해 배포된 Prometheus Stack에 CNPG 대시보드가 포함되어 있습니다.

접속: `http://prometheus.k8s.local/grafana` → Dashboards → CloudNativePG

## 트러블슈팅

### Pod가 시작하지 않을 때
```bash
# 로그 확인
kubectl logs narwhal-db-1 -n database

# 이벤트 확인
kubectl get events -n database --sort-by='.lastTimestamp'

# PVC 상태 확인
kubectl get pvc -n database
```

### 복제가 중단되었을 때
```bash
# Replica PVC 삭제 (CNPG가 자동 재생성)
kubectl delete pvc narwhal-db-2 -n database

# Pod 재시작 대기
kubectl wait --for=condition=Ready pod/narwhal-db-2 -n database --timeout=300s

# 복제 재개 확인
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT * FROM pg_stat_replication;"
```

### 연결 수 초과
```bash
# 현재 연결 수 확인
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT count(*) FROM pg_stat_activity;"

# 유휴 연결 종료
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle' AND state_change < NOW() - INTERVAL '10 minutes';"
```

### 느린 쿼리 분석
```bash
# 1초 이상 실행된 쿼리 확인
kubectl exec narwhal-db-1 -n database -- psql -U postgres -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' AND now() - pg_stat_activity.query_start > interval '1 second';"

# 로그에서 느린 쿼리 검색
kubectl logs narwhal-db-1 -n database | grep "duration:"
```

## 설치 방법

이 클러스터는 `scripts/master/06-cnpg.sh` 스크립트로 자동 설치됩니다.

```bash
# Vagrant 프로비저닝 시 자동 실행
vagrant up

# 수동 실행
vagrant ssh master
sudo /vagrant/scripts/master/06-cnpg.sh
```

## 참고 자료

- [CloudNative-PG 공식 문서](https://cloudnative-pg.io/)
- [PostgreSQL 17 문서](https://www.postgresql.org/docs/17/)
- [PgBouncer 문서](https://www.pgbouncer.org/)
- [CNPG Mistakes Log](/Users/m/Documents/IdeaProjects/narwhal/CLAUDE.md#kuberneteshelm-실수)

---

**마지막 업데이트**: 2026-02-14
**작성자**: Claude (Narwhal DevOps Agent)
