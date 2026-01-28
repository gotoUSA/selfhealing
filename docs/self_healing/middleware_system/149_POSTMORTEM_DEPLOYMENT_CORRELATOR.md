# 149. Postmortem 배포 연관성 분석 (DeploymentCorrelator)

**문서 버전:** 1.0
**작성일:** 2026-01-28
**선행 문서:** [146_POSTMORTEM_LIFECYCLE_INTEGRATION.md](146_POSTMORTEM_LIFECYCLE_INTEGRATION.md), [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md)
**관련 코드:** `services/canary/rollback_resolver.py`, `services/runtime_config/base.py`
**상태:** 설계 완료

---

## 1. 목적

인시던트 발생 시점 전후의 배포 이력을 수집하여 Postmortem에 포함시킴으로써 장애 원인 분석의 정확도를 높임

---

## 2. 문제 정의

### 2.1 현재 상태

Postmortem에 배포 관련 정보가 포함되지 않음:

| 항목 | 현재 상태 |
|------|----------|
| 배포 이력 | ❌ 미포함 |
| Config 변경 | ❌ 미포함 |
| Rollback 이력 | ❌ 미포함 |

### 2.2 분석 한계

| 문제 | 영향 |
|------|------|
| 배포 후 장애 연관성 파악 불가 | RCA 시간 증가 |
| 설정 변경과 장애 상관관계 불명 | 반복 장애 위험 |
| Rollback 이력 부재 | 복구 패턴 학습 불가 |

---

## 3. 기존 코드 분석

### 3.1 Canary Rollback Resolver

**파일:** `services/canary/rollback_resolver.py`

| 항목 | 설명 |
|------|------|
| 용도 | Canary 배포 실패 시 롤백 판단 |
| 핵심 기능 | 이전 버전 정보 추출 |
| 활용 가능성 | 배포 버전 정보 수집에 재사용 |

### 3.2 RuntimeConfig 버전 히스토리

**파일:** `services/runtime_config/base.py`

| 항목 | 설명 |
|------|------|
| 용도 | 런타임 설정 관리 |
| 버전 추적 | 설정 변경 시 버전 기록 |
| 활용 가능성 | 설정 변경 이력 수집에 재사용 |

### 3.3 부재 기능

| 기능 | 상태 |
|------|------|
| Kubernetes Deployment 연동 | ❌ 미구현 |
| ArgoCD/Flux 연동 | ❌ 미구현 |

---

## 4. 설계 원칙: Kubernetes-First + 외부 연동

### 4.1 자체 배포 히스토리 저장소를 만들지 않는 이유

| 자체 구현 문제점 | 설명 |
|-----------------|------|
| 데이터 중복 | Kubernetes가 이미 Deployment/ReplicaSet 이력 보관 |
| 동기화 복잡도 | 배포 도구와 자체 저장소 간 불일치 위험 |
| 저장소 관리 부담 | 별도 스토리지, 보존 정책, 정리 로직 필요 |
| 배포 도구 변경 시 재작업 | ArgoCD → Flux 전환 시 마이그레이션 필요 |

### 4.2 외부 시스템 연동 장점

| 장점 | 설명 |
|------|------|
| Single Source of Truth | Kubernetes API = 배포 이력의 유일한 진실 |
| 실시간 최신 데이터 | 저장소 동기화 지연 없음 |
| 운영 부담 제로 | 별도 저장소 관리 불필요 |
| 어댑터 교체 용이 | 배포 도구 변경 시 어댑터만 교체 |

### 4.3 Kubernetes-First 전략

| 항목 | 설명 |
|------|------|
| 업계 현황 | CNCF Survey 2023 기준 프로덕션 K8s 채택률 84% |
| 본 프로젝트 | `k8s/` 폴더에 PDB, ConfigMap 이미 존재 |
| 기본 어댑터 | `KubernetesDeploymentAdapter` |
| Fallback | Mock 어댑터 (비-K8s 환경, 테스트) |

### 4.4 비-Kubernetes 환경 지원

| 환경 | 지원 방법 |
|------|----------|
| 온프레미스 VM | 수동 입력 API 제공 |
| Docker Compose | Mock 어댑터 + 수동 입력 |
| 서버리스 | 해당 플랫폼 어댑터 확장 |

---

## 5. DeploymentCorrelator 설계

### 5.1 아키텍처

| 컴포넌트 | 역할 |
|---------|------|
| `DeploymentCorrelator` | 배포 정보 수집 조율 |
| `ExternalDeploymentAdapter` | 외부 시스템 연동 추상화 |

### 5.2 파일 위치

| 파일 | 용도 |
|------|------|
| `services/postmortem/deployment_correlator.py` | 메인 조율자 |
| `adapters/deployment/base.py` | 어댑터 인터페이스 |
| `adapters/deployment/kubernetes.py` | K8s 연동 |
| `adapters/deployment/mock.py` | 테스트용 Mock |

---

## 6. 외부 시스템 연동

### 6.1 지원 대상

| 시스템 | 연동 방법 | 수집 정보 |
|--------|----------|----------|
| Kubernetes | K8s API | Deployment 이력 |
| ArgoCD | REST API | Application Sync 이력 |
| Helm | ConfigMap 조회 | Release 이력 |
| 수동 입력 | API 제공 | 사용자 등록 정보 |

### 6.2 어댑터 인터페이스

| 메서드 | 설명 |
|--------|------|
| `get_deployments_in_range()` | 시간 범위 내 배포 조회 |
| `get_deployment_by_version()` | 특정 버전 배포 상세 |
| `get_current_version()` | 현재 배포 버전 |
| `get_rollback_history()` | 롤백 이력 조회 |

---

## 7. 데이터 모델

### 7.1 DeploymentEvent

| 필드 | 타입 | 설명 |
|------|------|------|
| `deployment_id` | `str` | 배포 고유 ID |
| `service_name` | `str` | 대상 서비스 |
| `version_from` | `str` | 이전 버전 |
| `version_to` | `str` | 새 버전 |
| `deployed_at` | `str` | 배포 시각 (ISO) |
| `deployed_by` | `str` | 배포자 |
| `deployment_type` | `str` | `rolling`, `canary`, `blue-green` |
| `source` | `str` | `kubernetes`, `argocd`, `helm`, `manual` |
| `metadata` | `dict` | 추가 메타데이터 |

### 7.2 ConfigChangeEvent

| 필드 | 타입 | 설명 |
|------|------|------|
| `change_id` | `str` | 변경 고유 ID |
| `config_key` | `str` | 변경된 설정 키 |
| `old_value` | `str` | 이전 값 (마스킹됨) |
| `new_value` | `str` | 새 값 (마스킹됨) |
| `changed_at` | `str` | 변경 시각 (ISO) |
| `changed_by` | `str` | 변경자 |

---

## 8. 시간 윈도우 설정

### 8.1 조회 범위

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `pre_incident_window` | 60분 | 인시던트 전 배포 조회 범위 |
| `post_incident_window` | 30분 | 인시던트 후 배포 조회 범위 |

### 8.2 상관관계 분석

| 패턴 | 조건 | 신뢰도 |
|------|------|--------|
| `deployment_triggered` | 배포 후 30분 내 인시던트 | 높음 |
| `config_changed` | 설정 변경 후 10분 내 | 높음 |
| `possible_correlation` | 배포 후 1시간 내 | 중간 |
| `unlikely` | 배포 없음 또는 2시간 이상 | 낮음 |

---

## 9. DeploymentCorrelator API

### 9.1 주요 메서드

| 메서드 | 설명 |
|--------|------|
| `correlate_incident()` | 인시던트에 배포 정보 연결 |
| `get_deployments_for_postmortem()` | Postmortem용 배포 데이터 수집 |
| `analyze_correlation()` | 배포-인시던트 상관관계 분석 |

### 9.2 correlate_incident 상세

**입력:**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `incident_time` | `datetime` | 인시던트 발생 시각 |
| `service_name` | `str` | 영향받은 서비스 |
| `namespace` | `str` | 네임스페이스 |

**출력:**

| 필드 | 타입 | 설명 |
|------|------|------|
| `deployments` | `list[DeploymentEvent]` | 관련 배포 목록 |
| `config_changes` | `list[ConfigChangeEvent]` | 관련 설정 변경 |
| `correlation_score` | `float` | 상관관계 점수 (0-1) |
| `correlation_type` | `str` | 상관관계 유형 |

---

## 10. Postmortem 통합

### 10.1 추가 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `deployment_context` | `dict` | 배포 컨텍스트 |
| `recent_deployments` | `list` | 최근 배포 목록 |
| `config_changes` | `list` | 최근 설정 변경 |
| `deployment_correlation` | `str` | 상관관계 분석 결과 |

### 10.2 타임라인 통합

| 이벤트 유형 | 표시 |
|------------|------|
| Deployment | `[DEPLOY] v1.2.3 → v1.2.4` |
| ConfigChange | `[CONFIG] payment.timeout: 30 → 60` |
| Rollback | `[ROLLBACK] v1.2.4 → v1.2.3` |

---

## 11. 어댑터 구현 우선순위

### 11.1 Phase 1: Mock 어댑터

| 항목 | 설명 |
|------|------|
| 용도 | 개발/테스트 환경 |
| 구현 | 정적 데이터 반환 |
| 설정 | `DEPLOYMENT_ADAPTER=mock` |

### 11.2 Phase 2: Kubernetes 어댑터

| 항목 | 설명 |
|------|------|
| 용도 | K8s 직접 연동 |
| API 사용 | `apps/v1` Deployment API |
| 인증 | ServiceAccount Token |

### 11.3 Phase 3: ArgoCD/Helm 어댑터

| 항목 | 설명 |
|------|------|
| ArgoCD | Application Sync 이력 조회 |
| Helm | Release 이력 조회 |

---

## 12. Settings

### 12.1 설정 항목

| 설정 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `deployment_correlator_enabled` | `bool` | `True` | 기능 활성화 |
| `deployment_adapter` | `str` | `mock` | 어댑터 선택 |
| `deployment_pre_window_minutes` | `int` | `60` | 사전 조회 범위 |
| `deployment_post_window_minutes` | `int` | `30` | 사후 조회 범위 |

### 12.2 환경 변수

```
SELFHEALING_DEPLOYMENT_CORRELATOR_ENABLED=true
SELFHEALING_DEPLOYMENT_ADAPTER=kubernetes
SELFHEALING_DEPLOYMENT_PRE_WINDOW_MINUTES=60
SELFHEALING_DEPLOYMENT_POST_WINDOW_MINUTES=30
```

---

## 13. Kubernetes 어댑터 상세

### 13.1 필요 권한 (RBAC)

| 리소스 | 권한 |
|--------|------|
| `deployments` | `get`, `list` |
| `replicasets` | `get`, `list` |
| `events` | `get`, `list` |

### 13.2 데이터 추출

| K8s 필드 | 매핑 |
|---------|------|
| `metadata.annotations["deployment.kubernetes.io/revision"]` | `version_to` |
| `spec.template.metadata.labels["version"]` | 버전 레이블 |
| `metadata.creationTimestamp` | `deployed_at` |

---

## 14. Fallback 전략

### 14.1 어댑터 장애 시

| 상황 | 동작 |
|------|------|
| K8s API 연결 실패 | Mock 어댑터로 전환 |
| 타임아웃 | 빈 배포 목록 반환 |
| 인증 실패 | 경고 로그 + 빈 목록 |

### 14.2 데이터 부재 시

| 상황 | Postmortem 표시 |
|------|----------------|
| 배포 정보 없음 | `"deployment_context": null` |
| 어댑터 비활성화 | `"deployment_context": "disabled"` |

---

## 15. 구현 체크리스트

### 15.1 코어 구현

- [ ] `DeploymentCorrelator` 클래스 구현
- [ ] `ExternalDeploymentAdapter` 인터페이스 정의
- [ ] `MockDeploymentAdapter` 구현
- [ ] Settings 추가

### 15.2 어댑터 구현

- [ ] `KubernetesDeploymentAdapter` 구현
- [ ] K8s RBAC 설정 문서화
- [ ] 연결 테스트

### 15.3 Postmortem 통합

- [ ] `_generate_postmortem_data()` 수정
- [ ] 배포 컨텍스트 필드 추가
- [ ] 타임라인에 배포 이벤트 삽입

### 15.4 테스트

- [ ] Mock 어댑터 테스트
- [ ] 상관관계 분석 테스트
- [ ] Fallback 동작 테스트

---

## 16. 관련 문서

- [146_POSTMORTEM_LIFECYCLE_INTEGRATION.md](146_POSTMORTEM_LIFECYCLE_INTEGRATION.md) - 생명주기 통합
- [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md) - 인시던트 병합
- [150_POSTMORTEM_VERSIONING.md](150_POSTMORTEM_VERSIONING.md) - 버전 관리
