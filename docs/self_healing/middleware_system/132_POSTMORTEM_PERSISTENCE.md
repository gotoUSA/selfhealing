# 132. Postmortem 영속성 (Persistence)

**문서 버전:** 1.0  
**작성일:** 2026-01-27  
**선행 문서:** [131_POSTMORTEM_NOTIFICATION.md](131_POSTMORTEM_NOTIFICATION.md)  
**상태:** 분석 완료

---

## 1. 현재 저장소 분석

### 1.1 In-Memory 저장소

**파일:** `api/django/views/xtest/base.py`

**현재 구조:**
- `_healing_events`: 힐링 이벤트 리스트 (최대 1000개)
- `_healing_incidents`: 인시던트(Post-mortem) 리스트 (최대 100개)
- 모듈 레벨 전역 변수로 관리
- Thread Lock으로 동시성 제어

### 1.2 현재 동작

| 함수 | 동작 |
|------|------|
| `add_healing_event()` | 리스트 앞에 추가, 1000개 초과 시 오래된 것 삭제 |
| `add_healing_incident()` | 리스트 앞에 추가, 100개 초과 시 오래된 것 삭제 |
| `get_healing_events()` | 리스트에서 limit개 반환 |
| `get_healing_incidents()` | 리스트에서 limit개 반환 |

### 1.3 문제점

| 문제 | 영향 |
|------|------|
| 서버 재시작 시 데이터 소멸 | 장애 이력 손실 |
| 다중 워커 시 데이터 불일치 | 각 워커가 별도 메모리 사용 |
| 최대 100개 제한 | 장기 이력 조회 불가 |
| 조회 성능 | 리스트 순회, 대량 데이터 시 느림 |

---

## 2. 영속성 필요성 분석

### 2.1 Post-mortem 데이터의 가치

| 항목 | 보존 필요성 |
|------|------------|
| 장애 근본 원인 | ✅ 높음 - 재발 방지에 필수 |
| 복구 타임라인 | ✅ 높음 - SLA 분석에 필수 |
| 영향받은 서비스 | ✅ 높음 - 의존성 분석 |
| 수행된 조치 | ✅ 높음 - 대응 개선 |

### 2.2 보존 기간 요구사항

| 데이터 | 권장 보존 기간 |
|--------|---------------|
| Healing Events | 7일 (단기 분석용) |
| Post-mortem | 90일 ~ 1년 (장기 분석용) |

---

## 3. 저장소 옵션 분석

### 3.1 Redis

**장점:**
- 빠른 읽기/쓰기
- 다중 워커 공유 가능
- TTL 지원

**단점:**
- 메모리 기반 (비용)
- 복잡한 쿼리 불가

**적합도:** Healing Events에 적합

### 3.2 PostgreSQL (Django ORM)

**장점:**
- 영구 저장
- 복잡한 쿼리 가능 (기간별, 서비스별)
- 기존 인프라 활용

**단점:**
- 쓰기 지연

**적합도:** Post-mortem에 적합

### 3.3 Hybrid 방식

**구성:**
- Redis: 최근 이벤트 캐시 (7일)
- PostgreSQL: Post-mortem 영구 저장

---

## 4. 구현 설계

### 4.1 Django Model (Post-mortem)

**테이블명:** `selfhealing_postmortem`

**필드:**

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | Primary Key |
| `incident_id` | CharField | 인시던트 ID (unique) |
| `started_at` | DateTimeField | 인시던트 시작 |
| `resolved_at` | DateTimeField | 인시던트 종료 |
| `duration_seconds` | FloatField | 지속 시간 |
| `affected_services` | JSONField | 영향받은 서비스 목록 |
| `timeline` | JSONField | 타임라인 데이터 |
| `auto_actions` | JSONField | 수행된 조치 |
| `recommendations` | JSONField | 권장 사항 |
| `system_snapshot` | JSONField | 시스템 스냅샷 |
| `created_at` | DateTimeField | 생성 시각 |
| `source` | CharField | 생성 출처 (auto/manual) |

### 4.2 Redis Key 설계 (Events)

**Key 패턴:** `selfhealing:events:{date}`

**데이터 타입:** List

**TTL:** 7일

### 4.3 저장 함수 수정

**현재:**
```
add_healing_incident() → _healing_incidents 리스트
```

**변경 후:**
```
add_healing_incident() → PostgreSQL + In-Memory 캐시
```

---

## 5. 마이그레이션 전략

### 5.1 Phase 1: 병렬 저장

- 기존 In-Memory 유지
- PostgreSQL에도 동시 저장
- 읽기는 In-Memory 우선

### 5.2 Phase 2: 읽기 전환

- 읽기를 PostgreSQL로 전환
- In-Memory는 캐시 역할

### 5.3 Phase 3: In-Memory 제거 (선택적)

- In-Memory 완전 제거
- PostgreSQL + Redis 캐시만 사용

---

## 6. API 변경

### 6.1 조회 API 확장

**현재:** `GET /api/self-healing/xtest/healing-incidents/?limit=10`

**확장:**
- `?start_date=2026-01-01`
- `?end_date=2026-01-31`
- `?service=payment`
- `?min_duration=300`

### 6.2 새 API (선택적)

**POST-mortem 상세 조회:**
```
GET /api/self-healing/postmortem/{incident_id}/
```

**통계 API:**
```
GET /api/self-healing/postmortem/stats/?period=30d
```

---

## 7. 구현 체크리스트

### 7.1 Model 생성

- [ ] `Postmortem` Django Model 정의
- [ ] Migration 파일 생성
- [ ] Admin 등록 (선택적)

### 7.2 저장 함수 수정

- [ ] `add_healing_incident()` PostgreSQL 저장 추가
- [ ] 트랜잭션 처리
- [ ] 에러 핸들링 (DB 실패 시 In-Memory fallback)

### 7.3 조회 함수 수정

- [ ] `get_healing_incidents()` DB 조회로 변경
- [ ] 필터링 파라미터 지원
- [ ] 페이지네이션 지원

### 7.4 Redis 통합 (선택적)

- [ ] Healing Events Redis 저장
- [ ] TTL 설정
- [ ] 다중 워커 동기화

---

## 8. 영향 분석

### 8.1 기존 기능

| 기능 | 영향 |
|------|------|
| 수동 Post-mortem API | 저장소만 변경, API 동일 |
| 자동 트리거 | 저장 함수만 교체 |
| 인시던트 조회 API | 반환 형식 동일 |

### 8.2 성능

| 항목 | 변경 |
|------|------|
| 쓰기 지연 | 약간 증가 (DB 쓰기) |
| 읽기 성능 | 개선 (인덱스 활용) |
| 메모리 사용 | 감소 (In-Memory 캐시 최소화) |

---

## 9. 관련 문서

- [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) - 저장소 아키텍처
- [07_HYBRID_STORAGE_ARCHITECTURE.md](07_HYBRID_STORAGE_ARCHITECTURE.md) - 하이브리드 저장소
- [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md) - 자동 트리거 구현
