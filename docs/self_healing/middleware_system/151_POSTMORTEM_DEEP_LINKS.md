# 151. Postmortem 딥링크 및 알림 연동 (Deep Links)

**문서 버전:** 1.0
**작성일:** 2026-01-28
**선행 문서:** [146_POSTMORTEM_LIFECYCLE_INTEGRATION.md](146_POSTMORTEM_LIFECYCLE_INTEGRATION.md)
**관련 코드:** `services/circuit_breaker/actionable_alert_urls.py`, `services/chaos/notification.py`
**상태:** 구현 완료

---

## 1. 목적

Postmortem 생성 시 관련 시스템으로의 딥링크를 생성하여 운영자가 빠르게 컨텍스트에 접근할 수 있도록 함

---

## 2. 문제 정의

### 2.1 현재 상태

Postmortem 생성 시 외부 시스템 링크 부재:

| 항목 | 현재 상태 |
|------|----------|
| Grafana 대시보드 링크 | ❌ 미포함 |
| 알림 연동 | ❌ 미구현 |
| Runbook 링크 | ❌ 미포함 |
| Admin 제어판 링크 | ❌ 미포함 |

### 2.2 운영 불편

| 문제 | 영향 |
|------|------|
| 수동 URL 탐색 | 분석 시간 증가 |
| 컨텍스트 전환 비용 | 집중력 분산 |
| Runbook 미연결 | 표준 대응 누락 위험 |

---

## 3. 기존 코드 분석

### 3.1 ActionableAlertUrlBuilder

**파일:** `services/circuit_breaker/actionable_alert_urls.py`

| 항목 | 설명 |
|------|------|
| 용도 | CB 알림에 실행 가능한 링크 생성 |
| 지원 URL | Dashboard, Admin, Runbook |
| 환경 변수 | `CB_DASHBOARD_URL`, `CB_ADMIN_BASE_URL`, `CB_RUNBOOK_URL` |

**핵심 기능:**

| 메서드 | 설명 |
|--------|------|
| `build_cb_open_urls()` | CB OPEN 이벤트용 URL 생성 |
| `build_cb_closed_urls()` | CB CLOSED 이벤트용 URL 생성 |
| `_build_dashboard_url()` | Grafana 대시보드 URL |
| `_build_admin_url()` | Admin 제어판 URL (쿼리 파라미터 포함) |
| `_build_runbook_url()` | Runbook URL |

### 3.2 ActionableUrls 데이터클래스

**파일:** `services/circuit_breaker/actionable_alert_urls.py`

| 필드 | 타입 | 설명 |
|------|------|------|
| `dashboard_url` | `str` | Grafana 대시보드 |
| `admin_url` | `str` | Admin 제어판 |
| `runbook_url` | `str` | Runbook |

| 메서드 | 설명 |
|--------|------|
| `to_dict()` | 딕셔너리 변환 |
| `has_any_url()` | 최소 1개 URL 존재 확인 |

### 3.3 Chaos Notification

**파일:** `services/chaos/notification.py`

| 항목 | 설명 |
|------|------|
| 용도 | Chaos 실험 알림 발송 |
| 지원 채널 | Slack, Email |
| 패턴 | Postmortem 알림에 재사용 가능 |

---

## 4. PostmortemDeepLinkBuilder 설계

### 4.1 설계 원칙

| 원칙 | 설명 |
|------|------|
| 재사용 | 기존 `ActionableAlertUrlBuilder` 패턴 활용 |
| 확장성 | Postmortem 전용 URL 추가 |
| 일관성 | 기존 URL 빌더와 동일 인터페이스 |

### 4.2 파일 위치

| 파일 | 용도 |
|------|------|
| `services/postmortem/deep_links.py` | Postmortem 전용 URL 빌더 |

---

## 5. 지원 URL 유형

### 5.1 기존 URL (ActionableAlertUrlBuilder 재사용)

| URL 유형 | 용도 | 환경 변수 |
|---------|------|----------|
| Dashboard | Grafana 대시보드 | `CB_DASHBOARD_URL` |
| Runbook | 장애 대응 매뉴얼 | `CB_RUNBOOK_URL` |

### 5.2 신규 URL (Postmortem 전용)

| URL 유형 | 용도 | 환경 변수 |
|---------|------|----------|
| Postmortem Detail | Postmortem 상세 페이지 | `POSTMORTEM_BASE_URL` |
| Timeline | 타임라인 뷰 | `POSTMORTEM_TIMELINE_URL` |
| Audit Log | 관련 감사 로그 | `AUDIT_LOG_BASE_URL` |
| Metrics | Prometheus 메트릭 | `PROMETHEUS_URL` |

---

## 6. PostmortemDeepLinks 데이터 모델

### 6.1 필드 정의

| 필드 | 타입 | 설명 |
|------|------|------|
| `dashboard_url` | `str` | Grafana 대시보드 |
| `runbook_url` | `str` | Runbook |
| `postmortem_url` | `str` | Postmortem 상세 페이지 |
| `timeline_url` | `str` | 타임라인 뷰 |
| `audit_log_url` | `str` | 감사 로그 |
| `metrics_url` | `str` | Prometheus 메트릭 |
| `admin_url` | `str` | Admin 제어판 |

### 6.2 메서드

| 메서드 | 설명 |
|--------|------|
| `to_dict()` | 딕셔너리 변환 |
| `has_any_url()` | 최소 1개 URL 존재 확인 |
| `get_primary_links()` | 주요 링크만 반환 |

---

## 7. PostmortemDeepLinkBuilder API

### 7.1 주요 메서드

| 메서드 | 설명 |
|--------|------|
| `build_postmortem_links()` | Postmortem용 전체 링크 생성 |
| `build_notification_links()` | 알림용 간소화된 링크 |
| `build_grafana_url()` | 시간 범위 지정 Grafana URL |
| `build_prometheus_url()` | 메트릭 쿼리 URL |

### 7.2 build_postmortem_links 상세

**입력:**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `incident_id` | `str` | Postmortem ID |
| `service_name` | `str` | 서비스명 |
| `start_time` | `str` | 인시던트 시작 시각 (ISO) |
| `end_time` | `str` | 인시던트 종료 시각 (ISO) |
| `namespace` | `str` | 네임스페이스 |

**출력:** `PostmortemDeepLinks` 객체

### 7.3 build_grafana_url 상세

**기능:** 시간 범위를 포함한 Grafana 대시보드 URL 생성

| 쿼리 파라미터 | 설명 |
|--------------|------|
| `var-service` | 서비스명 필터 |
| `from` | 시작 시각 (Unix ms) |
| `to` | 종료 시각 (Unix ms) |
| `orgId` | 조직 ID |

### 7.4 build_prometheus_url 상세

**기능:** 메트릭 쿼리 URL 생성

| 쿼리 파라미터 | 설명 |
|--------------|------|
| `g0.expr` | PromQL 쿼리 |
| `g0.range_input` | 시간 범위 |
| `g0.tab` | 탭 선택 (graph/table) |

---

## 8. 알림 통합

### 8.1 알림 트리거

| 시점 | 동작 |
|------|------|
| Postmortem 자동 생성 | 알림 발송 |
| 그룹 Postmortem 생성 | 알림 발송 |
| Emergency Postmortem 생성 | 알림 발송 |

### 8.2 알림 채널

**참조:** `services/chaos/notification.py` 패턴 활용

| 채널 | 지원 |
|------|------|
| Slack | ✅ |
| Email | ✅ |
| Webhook | ✅ |

### 8.3 알림 내용

| 항목 | 설명 |
|------|------|
| 제목 | `[Postmortem] {incident_id} 자동 생성` |
| 요약 | 영향받은 서비스, 지속 시간 |
| 링크 | Postmortem 상세, Grafana, Runbook |
| 액션 | 바로가기 버튼 (Slack Block Kit) |

---

## 9. Slack 메시지 구조

### 9.1 Block Kit 구조

| 블록 | 내용 |
|------|------|
| Header | Postmortem 생성 알림 |
| Section | 인시던트 요약 |
| Fields | 서비스, 지속시간, 영향도 |
| Actions | 딥링크 버튼들 |
| Context | 생성 시각, 자동/수동 구분 |

### 9.2 버튼 구성

| 버튼 | URL | 스타일 |
|------|-----|--------|
| 상세 보기 | `postmortem_url` | primary |
| 대시보드 | `dashboard_url` | - |
| Runbook | `runbook_url` | - |
| 감사 로그 | `audit_log_url` | - |

---

## 10. Settings

### 10.1 URL 설정

| 설정 | 환경 변수 | 설명 |
|------|----------|------|
| Postmortem 기본 URL | `POSTMORTEM_BASE_URL` | Postmortem 웹 UI |
| Grafana URL | `CB_DASHBOARD_URL` | 기존 설정 재사용 |
| Runbook URL | `CB_RUNBOOK_URL` | 기존 설정 재사용 |
| Prometheus URL | `PROMETHEUS_URL` | Prometheus 웹 UI |
| Audit Log URL | `AUDIT_LOG_BASE_URL` | 감사 로그 UI |

### 10.2 알림 설정

| 설정 | 환경 변수 | 기본값 | 설명 |
|------|----------|--------|------|
| 알림 활성화 | `POSTMORTEM_NOTIFICATION_ENABLED` | `true` | 알림 발송 여부 |
| Slack Webhook | `POSTMORTEM_SLACK_WEBHOOK` | - | Slack Incoming Webhook |
| 알림 채널 | `POSTMORTEM_NOTIFICATION_CHANNELS` | `slack` | 활성화할 채널 |

---

## 11. Postmortem 통합

### 11.1 추가 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `deep_links` | `dict` | 딥링크 모음 |

### 11.2 호출 시점

| 시점 | 동작 |
|------|------|
| `_generate_postmortem_data()` | `build_postmortem_links()` 호출 |
| `_generate_emergency_postmortem_data()` | `build_postmortem_links()` 호출 |
| 그룹 Postmortem 생성 | `build_postmortem_links()` 호출 |

---

## 12. 구현 체크리스트

### 12.1 URL 빌더

- [x] `PostmortemDeepLinks` 데이터클래스 정의
- [x] `PostmortemDeepLinkBuilder` 클래스 구현
- [x] `ActionableAlertUrlBuilder` 재사용
- [x] 시간 범위 포함 Grafana URL 생성
- [x] Prometheus 쿼리 URL 생성

### 12.2 Postmortem 통합

- [x] `_generate_postmortem_data()` 수정
- [x] `deep_links` 필드 추가
- [x] Emergency Postmortem 통합

### 12.3 알림 구현

- [x] `PostmortemNotifier` 클래스 구현
- [x] Slack Block Kit 메시지 생성
- [x] Webhook 발송 로직

### 12.4 설정

- [x] 환경 변수 추가
- [x] Settings 클래스 확장

### 12.5 테스트

- [x] URL 생성 테스트
- [x] 시간 범위 파라미터 테스트
- [x] 알림 발송 테스트 (Mock)

---

## 13. 보안 고려사항

### 13.1 URL 노출

| 항목 | 대책 |
|------|------|
| 내부 URL 노출 | 내부 네트워크에서만 접근 가능 |
| 토큰 포함 URL | 단기 만료 토큰 사용 |
| 민감 정보 | URL에 민감 정보 미포함 |

### 13.2 알림 보안

| 항목 | 대책 |
|------|------|
| Webhook URL | 환경 변수로 관리 |
| 알림 내용 | 최소 정보만 포함 |
| 상세 정보 | 링크를 통해 인증 후 접근 |

---

## 14. CascadeEvent 감사 증적 연결

### 14.1 배경

Postmortem은 장애 분석 문서지만, 기술적 증거(Audit Trail)와의 연결이 필요:

| 요소 | 역할 |
|------|------|
| `Postmortem` | 분석 및 조치 사항 문서화 |
| `CascadeEvent` | 인과관계 체인 기술적 증적 |
| 연결 | $500M+ Exit 감사 대비 |

### 14.2 CascadeEvent 구조 분석

**파일:** `audit/cascade_event.py`

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | `str` | 이벤트 고유 ID |
| `trigger` | `str` | 트리거 이벤트 ID |
| `effects` | `list[str]` | 영향받은 이벤트 ID 목록 |
| `previous_hash` | `str` | 이전 이벤트 해시 |
| `current_hash` | `str` | 현재 이벤트 해시 |
| `is_test` | `bool` | X-Test 환경 여부 |

**주요 메서드:**

| 메서드 | 반환 | 설명 |
|--------|------|------|
| `get_causation_chain()` | `list[str]` | 인과관계 체인 이벤트 ID 목록 |
| `calculate_hash()` | `str` | 해시 계산 (무결성 검증용) |

### 14.3 Postmortem 스키마 확장

| 필드 | 타입 | 설명 |
|------|------|------|
| `cascade_event_id` | `str \| None` | 연결된 CascadeEvent ID |
| `causation_chain` | `list[str]` | 인과관계 체인 (스냅샷) |
| `evidence_hash` | `str` | CascadeEvent 해시 (변조 방지) |

### 14.4 연결 시점

| 시점 | 동작 |
|------|------|
| Postmortem 생성 | 관련 CascadeEvent 조회 |
| CascadeEvent 존재 | `cascade_event_id` 설정, `causation_chain` 복사 |
| CascadeEvent 미존재 | 필드 null 유지 |

### 14.5 조회 로직

| 단계 | 동작 |
|------|------|
| 1 | Postmortem 대상 이벤트에서 `service_name`, `namespace` 추출 |
| 2 | CascadeEvent 저장소에서 관련 이벤트 조회 |
| 3 | `get_causation_chain()` 호출하여 인과관계 확보 |
| 4 | 해시 검증 후 Postmortem에 연결 |

### 14.6 딥링크 확장

| URL 유형 | 환경 변수 | 설명 |
|---------|----------|------|
| `audit_evidence_link` | `AUDIT_EVIDENCE_BASE_URL` | CascadeEvent 상세 페이지 |

**URL 형식:**
```
{AUDIT_EVIDENCE_BASE_URL}/cascade/{cascade_event_id}?verify={evidence_hash}
```

### 14.7 PostmortemDeepLinks 필드 추가

| 필드 | 타입 | 설명 |
|------|------|------|
| `audit_evidence_link` | `str` | CascadeEvent 증적 링크 |

### 14.8 감사 대응

| 상황 | 활용 |
|------|------|
| 내부 감사 | Postmortem → CascadeEvent 추적 |
| 외부 감사 | 해시 체인으로 무결성 증명 |
| 법적 분쟁 | 인과관계 기술적 증거 제시 |

### 14.9 구현 체크리스트 (추가)

- [x] Postmortem 스키마에 `cascade_event_id`, `causation_chain`, `evidence_hash` 필드 추가
- [x] `_generate_postmortem_data()`에서 CascadeEvent 조회 로직 추가
- [x] `PostmortemDeepLinks`에 `audit_evidence_link` 필드 추가
- [x] `build_postmortem_links()`에서 증적 링크 생성

---

## 15. 관련 문서

- [146_POSTMORTEM_LIFECYCLE_INTEGRATION.md](146_POSTMORTEM_LIFECYCLE_INTEGRATION.md) - 생명주기 통합
- [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md) - 인시던트 병합
- [149_POSTMORTEM_DEPLOYMENT_CORRELATOR.md](149_POSTMORTEM_DEPLOYMENT_CORRELATOR.md) - 배포 연관성
- [150_POSTMORTEM_VERSIONING.md](150_POSTMORTEM_VERSIONING.md) - 버전 관리
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md) - CascadeEvent 설계
