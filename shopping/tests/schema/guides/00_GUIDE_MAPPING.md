# 🗺️ Schema Test Guide Mapping

> 이 문서는 테스트 가이드와 실제 테스트 파일 간의 매핑을 정의합니다.
> 신규 API 개발 또는 수정 시, 본 문서를 기준으로 해당 Layer의 테스트를 작성해야 합니다.

---

## 📋 Quick Reference

| Layer | 목적 | 의무 | Trigger 조건 | 가이드 |
|-------|------|:----:|-------------|--------|
| 🔒 Contract | API 응답 규약 안정성 | **Must** | 모든 엔드포인트 | 01~05 |
| 🛡️ Abuse & Negative | 악성/비정상 입력 방어 | **Should** | 외부 입력 존재 시 | 07~08 |
| 🔄 Workflow | Stateful 흐름 검증 | **Conditional** | 이전 단계 결과 필요 시 | 06 |
| ⚡ Performance | 성능 및 안정성 | **Conditional** | 고트래픽/반복 호출 시 | 09 |

### 의무 기준 정의

| 용어 | 의미 | CI/PR 정책 |
|------|------|------------|
| **Must** | 미작성 시 Merge 불가 | ❌ Block |
| **Should** | 작성 여부 리뷰어 판단 | ⚠️ Warning |
| **Conditional** | Trigger 조건 충족 시 Must로 전환 | 🔄 Context-dependent |

---

## 📁 Layer 1: Contract (API 규약)

> **목적**: API 응답 스키마 변경으로 인한 장애 방지

### 적용 기준
- ✅ 모든 GET/POST/PUT/PATCH/DELETE 엔드포인트
- ✅ 5xx 에러 발생 금지
- ✅ 필수 필드 누락 금지
- ✅ Undefined 필드 반환 금지

### 가이드 → 테스트 파일 매핑

| 가이드 | 검증 내용 | 폴더 | 테스트 파일 | 의무 |
|--------|----------|------|------------|:----:|
| [01_SUCCESS_RESPONSE_SCHEMA](01_SUCCESS_RESPONSE_SCHEMA.md) | 성공 응답 필드/타입 검증 | `validation/` | `test_field_validation.py` | Must |
| | | `validation/` | `test_full_schema_validation.py` | Must |
| | | `validation/` | `test_response_structure_validation.py` | Must |
| | | `contract/` | `test_public_endpoints.py` | Must |
| [02_ERROR_RESPONSE_SCHEMA](02_ERROR_RESPONSE_SCHEMA.md) | 에러 응답 구조 일관성 | `validation/` | `test_sensitive_info_validation.py` | Must |
| | | `contract/` | `test_error_responses.py` | Must |
| [03_PATH_PARAMETER](03_PATH_PARAMETER.md) | Path 파라미터 검증 | `contract/` | `test_path_parameters.py` | Must |
| [04_QUERY_PARAMETER](04_QUERY_PARAMETER.md) | Query 파라미터 검증 | `contract/` | `test_query_parameters.py` | Must |
| [05_AUTHENTICATION](05_AUTHENTICATION.md) | 인증 필수 엔드포인트 | `contract/` | `test_authenticated_endpoints.py` | Must |
| | 인증된 쓰기(POST/PUT/DELETE) 검증 | `contract/` | `test_post_endpoints.py` | Must |

---

## 📁 Layer 2: Abuse & Negative Input (입력 방어)

> **목적**: 악성/비정상 입력 시 서버 안정성 보장

### 적용 기준
- ✅ 외부 입력을 받는 모든 API
- ✅ Invalid 입력 시 5xx 금지
- ✅ 민감 정보 노출 금지
- ✅ 에러 응답 구조 일관성

### 가이드 → 테스트 파일 매핑

| 가이드 | 검증 내용 | 폴더 | 테스트 파일 | 의무 |
|--------|----------|------|------------|:----:|
| [07_FUZZ_TESTING](07_FUZZ_TESTING.md) | 예측 불가 입력 처리 | `stress/` | `test_fuzz.py` | Should |
| [08_NEGATIVE_TESTING](08_NEGATIVE_TESTING.md) | 잘못된 입력 처리 | `stress/` | `test_negative.py` | Should |

### 🔐 Security Tests (확장)

> 💡 **Note**: OWASP Category는 보안 레퍼런스 기준일 뿐입니다.  
> 개발자는 해당 분류를 상세히 알 필요 없이, 본 테스트를 작성하거나 유지보수하면 됩니다.

| 보안 유형 | 폴더 | 테스트 파일 | OWASP 분류 |
| SQL Injection | `stress/security/` | `test_sql_injection.py` | A03:2021 |
| XSS | `stress/security/` | `test_xss.py` | A03:2021 |
| SSTI | `stress/security/` | `test_ssti.py` | A03:2021 |
| Path Traversal | `stress/security/` | `test_path_traversal.py` | A01:2021 |
| Sensitive Info Exposure | `stress/security/` | `test_sensitive_info.py` | A02:2021 |

---

## 📁 Layer 3: Workflow (Stateful / Multi-step)

> **목적**: 다중 API 조합 시나리오의 Stateful Transition 검증

### 적용 기준 (하나 이상 해당 시 → Must로 전환)
- ✅ 앞 요청 결과가 다음 API에 전달됨
- ✅ 로그인/권한/Stateful Transition 포함
- ✅ 데이터 저장 후 후속 요청에 반영

### 가이드 → 테스트 파일 매핑

| 가이드 | 검증 내용 | 폴더 | 테스트 파일 | 의무 |
|--------|----------|------|------------|:----:|
| [06_WORKFLOW](06_WORKFLOW.md) | Stateful Transition 검증 | `workflow/` | `test_stateful_workflow.py` | Conditional |
| | 권한 기반 접근 제어 | `workflow/` | `test_access_control.py` | Conditional |

> ⚠️ **Conditional → Must 전환**: 위 적용 기준 중 하나라도 해당 시 Must로 간주

---

## 📁 Layer 4: Performance (성능)

> **목적**: API 응답 속도 및 부하 대응력 검증

### 적용 기준
- ⚠️ 조회량이 많은 API
- ⚠️ 반복 호출 가능성 높은 기능
- ⚠️ 동시성 이슈 위험 기능

### 가이드 → 테스트 파일 매핑

| 가이드 | 검증 내용 | 폴더 | 테스트 파일 | 의무 |
|--------|----------|------|------------|:----:|
| [09_PERFORMANCE_TESTING](09_PERFORMANCE_TESTING.md) | 응답 시간 검증 | `stress/` | `test_performance.py` | Conditional |
| | 동시성 처리 | `stress/` | `test_concurrency.py` | Conditional |

### 성능 기준 (권장)

| 테스트 유형 | 기준 | Threshold | 기본 Iterations |
|------------|------|-----------|:---------------:|
| 단일 요청 | P95 응답 시간 | < 200ms | 1 |
| 반복 요청 | 평균 응답 시간 | < 150ms | 100 |
| 동시 요청 | 성공률 | > 99% | 10 concurrent |
| DB 쿼리 | 쿼리 수 제한 | N+1 금지 | - |

> 📏 **측정 기준 표준화**: iterations 및 샘플 수는 테스트 코드 상단 또는 `constants.py`에 정의하고 문서화한다.  
> 팀별 기준이 다를 경우 반드시 PR description에 명시할 것.

---

## 🏷️ API Tier 분류 체계

> API의 중요도와 공개 범위에 따라 테스트 요구사항을 차등 적용한다.

### Tier 정의

| Tier | 이름 | 대상 | 테스트 요구사항 |
|:----:|------|------|----------------|
| **0** | Prototype/Experimental | PoC, 실험 기능, 미확정 스펙 | Optional (승격 전까지) |
| **1** | Public/Critical | 외부 공개 API, 결제, 인증 | Full (Contract + Abuse + Performance) |
| **2** | Business/Internal | 일반 비즈니스 API | Reduced (Contract + Negative) |
| **3** | Admin/Internal Tools | 관리자 전용, 내부 도구 | Minimal (Contract only) |

### Tier별 상세 요구사항

| 테스트 유형 | Tier 0 | Tier 1 | Tier 2 | Tier 3 |
|------------|:------:|:------:|:------:|:------:|
| Contract (스키마) | - | Must | Must | Must |
| Negative | - | Must | Should | - |
| Fuzz | - | Should | - | - |
| Security | - | Must | Should | - |
| Workflow | - | Conditional | Conditional | - |
| Performance | - | Must | Conditional | - |

### Tier 0 (Prototype) 정책

```
⚠️ Prototype은 "면제"가 아니라 "승격 대기 상태"이다.
```

- Proof-of-concept, 실험 기능
- 문서화 대상이나 테스트는 Optional
- **공개 또는 의존성 생기기 전에 Tier 승격 필수**
- **Tier 0는 영구 상태로 유지될 수 없음**

### Tier 승격 규칙

| 조건 | 필수 조치 |
|------|----------|
| 외부 사용자에게 공개 | → Tier 1로 승격 |
| 다른 서비스가 의존 | → 최소 Tier 2로 승격 |
| 3개월 이상 운영 | → Tier 재분류 필수 |
| 릴리스 노트 포함 | → Tier 0 불가 |

### Tier 지정 프로세스

```
1. 신규 API 개발 시 PR에 Tier 명시
2. Tier 0인 경우 예상 승격 시점 기재
3. Reviewer가 Tier 적정성 검토
4. Tier 변경 시 테스트 요구사항 재검토
```

### Tier 지정 템플릿

```markdown
## 🏷️ API Tier 지정

- **API**: `/api/v1/example/`
- **Tier**: [ ] 0-Prototype / [ ] 1-Public / [ ] 2-Business / [ ] 3-Admin
- **사유**: (Tier 선정 근거)
- **승격 예정** (Tier 0인 경우): (예상 시점 및 목표 Tier)
```

---

## 📊 폴더 구조 Overview

```
shopping/tests/schema/
├── 📁 contract/          # Contract Layer (API 규약)
│   ├── test_authenticated_endpoints.py
│   ├── test_error_responses.py
│   ├── test_path_parameters.py
│   ├── test_post_endpoints.py
│   ├── test_public_endpoints.py
│   └── test_query_parameters.py
│
├── 📁 validation/        # Contract Layer (스키마 검증)
│   ├── test_field_validation.py
│   ├── test_full_schema_validation.py
│   ├── test_response_structure_validation.py
│   ├── test_schema_discovery.py
│   ├── test_schemathesis_native.py
│   ├── test_sensitive_info_validation.py
│   └── test_strict_validation.py
│
├── 📁 stress/            # Abuse & Performance Layer
│   ├── test_concurrency.py
│   ├── test_fuzz.py
│   ├── test_negative.py
│   ├── test_performance.py
│   └── 📁 security/      # Security 확장
│       ├── test_path_traversal.py
│       ├── test_sensitive_info.py
│       ├── test_sql_injection.py
│       ├── test_ssti.py
│       └── test_xss.py
│
├── 📁 workflow/          # Workflow Layer
│   ├── test_access_control.py
│   └── test_stateful_workflow.py
│
└── 📁 guides/            # 가이드 문서
    ├── 00_GUIDE_MAPPING.md (현재 문서)
    ├── 01_SUCCESS_RESPONSE_SCHEMA.md
    ├── 02_ERROR_RESPONSE_SCHEMA.md
    ├── ...
    └── 09_PERFORMANCE_TESTING.md
```

---

## ✅ 체크리스트: 신규 API 개발 시

### Must — 미작성 시 Merge 불가
- [ ] Contract Test 작성 (성공/에러 응답 스키마)
- [ ] Path/Query Parameter 검증 테스트
- [ ] 인증 필요 시 Authentication 테스트

### Should — 리뷰어 판단 하에 작성
- [ ] Negative Test (잘못된 입력 처리)
- [ ] Fuzz Test (예측 불가 입력)
- [ ] Security Test (보안 취약점 방어)

### Conditional — Trigger 조건 충족 시 Must
- [ ] Workflow Test → Trigger: Stateful Transition 존재
- [ ] Performance Test → Trigger: 고트래픽/반복 호출 예상
- [ ] Concurrency Test → Trigger: 동시성 이슈 가능성

---

## 🔗 관련 문서

| 문서 | 설명 |
|------|------|
| [SCHEMA_TEST_CHECKLIST.md](../SCHEMA_TEST_CHECKLIST.md) | 테스트 체크리스트 상세 |
| [VERIFICATION_REPORT.md](../stress/VERIFICATION_REPORT.md) | 스트레스 테스트 검증 리포트 |

---

## 📝 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0 | 2025-12-05 | 초기 버전 작성 |
| 1.1 | 2025-12-05 | Trigger 컬럼 추가, Must/Should/Conditional 표준화, OWASP 부담 완화 문구, Stateful Workflow 용어 통일, 성능 측정 기준 명시 |
| 1.2 | 2025-12-05 | Test Waiver 섹션 추가 |
| 2.0 | 2025-12-05 | Waiver → Tier 시스템으로 전환 (3 Tier + Tier 0 Prototype), 승격 규칙 추가 |

