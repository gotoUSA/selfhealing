# 150. Postmortem 리비전 관리 (PostmortemRevision)

**문서 버전:** 1.0
**작성일:** 2026-01-28
**선행 문서:** [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md), [132_POSTMORTEM_PERSISTENT_STORAGE.md](132_POSTMORTEM_PERSISTENT_STORAGE.md)
**관련 코드:** `audit/integrity/local_manager.py`, `services/runtime_config/base.py`
**상태:** 설계 완료

---

## 1. 목적

Postmortem 데이터의 수정 이력을 관리하여 변경 추적, 감사, 롤백 기능을 제공함

---

## 2. 문제 정의

### 2.1 현재 상태

Postmortem 데이터 수정 시 이력 관리 부재:

| 항목 | 현재 상태 |
|------|----------|
| 수정 이력 | ❌ 미추적 |
| 변경 사유 | ❌ 기록 안됨 |
| 이전 버전 조회 | ❌ 불가 |
| 롤백 | ❌ 불가 |

### 2.2 운영 요구사항

| 요구사항 | 설명 |
|---------|------|
| 감사 추적 | 누가, 언제, 무엇을 수정했는지 기록 |
| 변경 비교 | 버전 간 차이점 확인 |
| 롤백 | 이전 버전으로 복원 |
| 불변성 | 특정 시점 데이터 보존 (법적 요건) |

---

## 3. 기존 코드 분석

### 3.1 RuntimeConfig 버전 관리

**파일:** `services/runtime_config/base.py`

| 항목 | 설명 |
|------|------|
| 패턴 | 버전 번호 + 변경 이력 저장 |
| 저장 | Redis Hash 또는 DB |
| 활용 | Postmortem 버전 관리에 유사 패턴 적용 가능 |

### 3.2 HashChainManager 불변성

**파일:** `audit/integrity/local_manager.py`

| 항목 | 설명 |
|------|------|
| 용도 | 데이터 무결성 보장 |
| 메서드 | `add_integrity()` - 해시 체인 추가 |
| 활용 | 각 리비전에 무결성 해시 적용 |

---

## 4. PostmortemRevision 설계

### 4.1 용어 정의

| 용어 | 정의 |
|------|------|
| `Postmortem` | 인시던트 분석 문서 (최신 버전) |
| `PostmortemRevision` | Postmortem의 특정 시점 스냅샷 |
| `revision_number` | 리비전 순번 (1부터 시작) |
| `latest_revision` | 현재 활성 리비전 |

### 4.2 파일 위치

| 파일 | 용도 |
|------|------|
| `services/postmortem/revision.py` | 리비전 관리자 |
| `models/postmortem_revision.py` | Django 모델 (선택적) |

---

## 5. 데이터 모델

### 5.1 PostmortemRevision

| 필드 | 타입 | 설명 |
|------|------|------|
| `revision_id` | `str` | 리비전 고유 ID |
| `incident_id` | `str` | 원본 Postmortem ID |
| `revision_number` | `int` | 리비전 순번 |
| `created_at` | `str` | 리비전 생성 시각 |
| `created_by` | `str` | 생성/수정자 |
| `change_reason` | `str` | 변경 사유 |
| `change_type` | `str` | 변경 유형 |
| `data_snapshot` | `dict` | 해당 시점 전체 데이터 |
| `diff_from_previous` | `dict` | 이전 버전과의 차이 |
| `integrity_hash` | `str` | 무결성 해시 |

### 5.2 변경 유형 (change_type)

| 유형 | 설명 |
|------|------|
| `initial` | 최초 생성 |
| `analysis_update` | 분석 내용 수정 |
| `timeline_correction` | 타임라인 수정 |
| `improvement_added` | 개선사항 추가 |
| `annotation` | 주석/코멘트 추가 |
| `correction` | 오류 수정 |
| `sealed` | 최종 봉인 (이후 수정 불가) |

---

## 6. PostmortemRevisionManager API

### 6.1 주요 메서드

| 메서드 | 설명 |
|--------|------|
| `create_revision()` | 새 리비전 생성 |
| `get_revision()` | 특정 리비전 조회 |
| `get_all_revisions()` | 모든 리비전 목록 |
| `get_latest_revision()` | 최신 리비전 조회 |
| `compare_revisions()` | 두 리비전 비교 |
| `rollback_to_revision()` | 특정 리비전으로 롤백 |
| `seal_postmortem()` | Postmortem 봉인 |

### 6.2 create_revision 상세

**입력:**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `incident_id` | `str` | Postmortem ID |
| `new_data` | `dict` | 수정된 데이터 |
| `changed_by` | `str` | 수정자 |
| `change_reason` | `str` | 변경 사유 |
| `change_type` | `str` | 변경 유형 |

**동작:**

| 단계 | 설명 |
|------|------|
| 1 | 봉인 상태 확인 (봉인 시 거부) |
| 2 | 이전 리비전 조회 |
| 3 | diff 계산 |
| 4 | 새 리비전 번호 할당 |
| 5 | HashChain 무결성 추가 |
| 6 | 리비전 저장 |
| 7 | Postmortem 최신 데이터 업데이트 |

**반환:** `PostmortemRevision` 객체

### 6.3 compare_revisions 상세

**입력:**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `incident_id` | `str` | Postmortem ID |
| `revision_a` | `int` | 비교 리비전 A |
| `revision_b` | `int` | 비교 리비전 B |

**출력:**

| 필드 | 타입 | 설명 |
|------|------|------|
| `added` | `dict` | 추가된 필드 |
| `removed` | `dict` | 제거된 필드 |
| `modified` | `dict` | 수정된 필드 (old/new) |
| `unchanged` | `list` | 변경 없는 필드 목록 |

---

## 7. 봉인 (Sealing) 메커니즘

### 7.1 봉인 목적

| 목적 | 설명 |
|------|------|
| 불변성 보장 | 분석 완료 후 수정 방지 |
| 법적 증거 | 감사/조사 시 원본 보존 |
| 책임 명확화 | 최종 분석 결과 확정 |

### 7.2 봉인 프로세스

| 단계 | 설명 |
|------|------|
| 1 | 봉인 요청 (권한 확인) |
| 2 | 최종 리비전 생성 (`change_type=sealed`) |
| 3 | HashChain 최종 무결성 기록 |
| 4 | `is_sealed=True` 플래그 설정 |
| 5 | 이후 수정 요청 거부 |

### 7.3 봉인 해제

| 항목 | 정책 |
|------|------|
| 기본 | 봉인 해제 불가 |
| 예외 | Admin + 사유 + 감사 기록 필수 |
| 해제 후 | 새 리비전 체인 시작 |

---

## 8. 저장소 설계

### 8.1 PostgreSQL 스키마 (선택적)

| 테이블 | 용도 |
|--------|------|
| `postmortem_revisions` | 리비전 메타데이터 |
| `postmortem_snapshots` | 전체 데이터 스냅샷 (JSONB) |

### 8.2 Redis 구조 (대안)

| 키 패턴 | 타입 | 용도 |
|--------|------|------|
| `selfhealing:pm:revisions:{incident_id}` | `ZSET` | 리비전 목록 (score=번호) |
| `selfhealing:pm:revision:{revision_id}` | `HASH` | 리비전 상세 |
| `selfhealing:pm:sealed:{incident_id}` | `STRING` | 봉인 상태 |

### 8.3 하이브리드 전략

| 데이터 | 저장소 |
|--------|--------|
| 메타데이터 | Redis (빠른 조회) |
| 스냅샷 | PostgreSQL JSONB (영구 저장) |
| 봉인 상태 | 양쪽 모두 |

---

## 9. RBAC 통합

### 9.1 권한 매핑

**참조:** `api/django/permissions.py`

| 동작 | 필요 권한 |
|------|----------|
| 리비전 조회 | `IsViewer` |
| 분석 수정 | `IsOperator` |
| 봉인 | `IsAdmin` |
| 봉인 해제 | `IsAdmin` + 2차 인증 |

### 9.2 감사 기록

| 동작 | 기록 항목 |
|------|----------|
| 리비전 생성 | actor_id, change_reason, timestamp |
| 봉인 | actor_id, approval_id, timestamp |
| 봉인 해제 | actor_id, reason, approval_chain |

---

## 10. Settings

### 10.1 설정 항목

| 설정 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `postmortem_versioning_enabled` | `bool` | `True` | 버전 관리 활성화 |
| `postmortem_max_revisions` | `int` | `50` | 최대 리비전 수 |
| `postmortem_auto_seal_days` | `int` | `30` | 자동 봉인 일수 (0=비활성화) |
| `postmortem_revision_storage` | `str` | `hybrid` | 저장소 유형 |

### 10.2 환경 변수

```
SELFHEALING_POSTMORTEM_VERSIONING_ENABLED=true
SELFHEALING_POSTMORTEM_MAX_REVISIONS=50
SELFHEALING_POSTMORTEM_AUTO_SEAL_DAYS=30
SELFHEALING_POSTMORTEM_REVISION_STORAGE=hybrid
```

---

## 11. API 엔드포인트

### 11.1 신규 엔드포인트

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/postmortem/{id}/revisions/` | 리비전 목록 |
| `GET` | `/postmortem/{id}/revisions/{num}/` | 특정 리비전 |
| `POST` | `/postmortem/{id}/revisions/` | 새 리비전 생성 |
| `GET` | `/postmortem/{id}/revisions/compare/` | 리비전 비교 |
| `POST` | `/postmortem/{id}/seal/` | Postmortem 봉인 |

### 11.2 요청/응답 예시

**리비전 생성 요청:**

| 필드 | 설명 |
|------|------|
| `data` | 수정된 Postmortem 데이터 |
| `change_reason` | 변경 사유 |
| `change_type` | 변경 유형 |

**리비전 목록 응답:**

| 필드 | 설명 |
|------|------|
| `revisions` | 리비전 배열 |
| `total_count` | 전체 리비전 수 |
| `is_sealed` | 봉인 상태 |
| `latest_revision` | 최신 리비전 번호 |

---

## 12. 구현 체크리스트

### 12.1 코어 구현

- [ ] `PostmortemRevision` 데이터클래스 정의
- [ ] `PostmortemRevisionManager` 클래스 구현
- [ ] diff 계산 로직 구현
- [ ] HashChain 연동

### 12.2 저장소 구현

- [ ] Redis 리비전 저장 구현
- [ ] PostgreSQL 스냅샷 저장 구현 (선택적)
- [ ] 하이브리드 조회 로직

### 12.3 봉인 기능

- [ ] 봉인 메서드 구현
- [ ] 봉인 상태 체크 로직
- [ ] 봉인 해제 (관리자용)

### 12.4 API

- [ ] 리비전 조회 엔드포인트
- [ ] 리비전 생성 엔드포인트
- [ ] 비교 엔드포인트
- [ ] 봉인 엔드포인트

### 12.5 테스트

- [ ] 리비전 생성 테스트
- [ ] diff 계산 테스트
- [ ] 봉인 테스트
- [ ] 롤백 테스트

---

## 13. 마이그레이션

### 13.1 기존 Postmortem 처리

| 단계 | 설명 |
|------|------|
| 1 | 기존 Postmortem 조회 |
| 2 | 각각에 대해 초기 리비전 생성 (revision_number=1) |
| 3 | `change_type=initial` 설정 |
| 4 | 무결성 해시 추가 |

### 13.2 하위 호환성

| 항목 | 처리 |
|------|------|
| 기존 API | 최신 리비전 반환 |
| 신규 API | 리비전 지정 가능 |

---

## 14. 관련 문서

- [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md) - 인시던트 병합
- [132_POSTMORTEM_PERSISTENT_STORAGE.md](132_POSTMORTEM_PERSISTENT_STORAGE.md) - 영구 저장소
- [149_POSTMORTEM_DEPLOYMENT_CORRELATOR.md](149_POSTMORTEM_DEPLOYMENT_CORRELATOR.md) - 배포 연관성
- [151_POSTMORTEM_DEEP_LINKS.md](151_POSTMORTEM_DEEP_LINKS.md) - 딥링크
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md) - 감사 무결성
