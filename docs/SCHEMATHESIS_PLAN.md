# 📋 Schemathesis 도입 계획

## 🎯 현재 상태 분석

| 항목 | 상태 | 비고 |
|------|------|------|
| OpenAPI 스키마 | ✅ 준비됨 | `drf-spectacular` 사용 |
| 스키마 엔드포인트 | ✅ 활성화 | `/api/schema/` |
| 기존 테스트 | ✅ 풍부함 | pytest 기반, api/unit/integration 분리 |
| Factory | ✅ 구성됨 | `factory-boy`, `conftest.py` fixtures |

---

## 📌 Phase 1: 기본 설정 (1일)

### 1.1 패키지 설치
```bash
# requirements-dev.txt에 추가
schemathesis==3.40.0  # 최신 안정 버전
```

### 1.2 기본 테스트 파일 생성
```
shopping/tests/
└── schema/           # 새로 생성
    ├── __init__.py
    ├── conftest.py   # Schemathesis 전용 설정
    └── test_api_contract.py
```

### 1.3 고려사항
- **인증 처리**: JWT Bearer 토큰 설정 필요
- **테스트 DB**: `pytest-django`와 통합
- **스키마 생성**: 런타임 vs 정적 파일

---

## 📌 Phase 2: 기본 Contract 테스트 (2-3일)

### 2.1 Stateless 테스트 먼저 시작
```python
# shopping/tests/schema/test_api_contract.py
import schemathesis
from django.test import override_settings

schema = schemathesis.from_uri("http://localhost:8000/api/schema/")

@schema.parametrize()
def test_api_contract(case):
    """OpenAPI 스키마 기반 자동 API 테스트"""
    response = case.call()
    case.validate_response(response)
```

### 2.2 인증이 필요한 엔드포인트 처리
```python
@schema.parametrize()
def test_authenticated_endpoints(case, auth_token):
    case.call(headers={"Authorization": f"Bearer {auth_token}"})
```

### 2.3 제외할 엔드포인트 설정
- Webhook 엔드포인트 (외부 서비스 콜백)
- 관리자 전용 엔드포인트
- 이메일 발송 관련

---

## 📌 Phase 3: Stateful 테스트 (3-5일)

### 3.1 링크 기반 워크플로우 테스트
```python
# 예: 회원가입 → 로그인 → 상품 조회 → 장바구니 추가 → 주문
@schema.parametrize()
@settings(stateful_step_count=5)
def test_user_purchase_flow(case):
    ...
```

### 3.2 State Machine 정의
- 사용자 상태: 비인증 → 인증됨 → 장바구니 있음 → 주문 완료
- 상품 상태: 재고 있음 → 품절

---

## 📌 Phase 4: CI 통합 (1-2일)

### 4.1 pytest 마커 추가
```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    # ... 기존 마커들
    "schema: OpenAPI 스키마 기반 계약 테스트",
]
```

### 4.2 GitHub Actions 설정
```yaml
# CI에서 분리 실행
- name: Schema Tests
  run: pytest -m schema --tb=short
```

### 4.3 실행 시간 관리
```python
# 빠른 피드백용 설정
@settings(max_examples=50)  # 기본값 100에서 줄임
```

---

## 📌 Phase 5: 고급 활용 (선택사항)

### 5.1 커스텀 데이터 생성
```python
@st.register_string_format("phone")
def phone_numbers():
    return st.from_regex(r"010-\d{4}-\d{4}")
```

### 5.2 Hook 활용
```python
@schemathesis.hook("before_generate_case")
def before_generate(context, strategy):
    # 특정 엔드포인트에 대한 데이터 조작
    ...
```

### 5.3 리포트 생성
```bash
schemathesis run http://localhost:8000/api/schema/ \
  --report=report.html
```

---

## ⚠️ 주의사항 및 고려사항

### 1. 데이터 격리
| 문제 | 해결책 |
|------|--------|
| 테스트 간 데이터 충돌 | `@pytest.mark.django_db(transaction=True)` |
| 생성된 데이터 정리 | 테스트 후 `teardown`에서 정리 |

### 2. 성능 이슈
| 문제 | 해결책 |
|------|--------|
| 느린 테스트 | `max_examples` 제한, 병렬화 |
| DB 연결 제한 | PostgreSQL connection pooling |

### 3. 스키마 정확성
| 문제 | 해결책 |
|------|--------|
| 불완전한 스키마 | `drf-spectacular` 설정 보완 |
| 누락된 응답 코드 | `@extend_schema` 데코레이터 추가 |

### 4. 현재 프로젝트 특수 고려사항
- **Toss Payment Webhook**: 외부 서비스이므로 제외 또는 모킹
- **Celery 비동기 작업**: `CELERY_TASK_ALWAYS_EAGER=True` 유지
- **Rate Limiting**: 테스트 환경에서 높게 설정 (이미 conftest에 있음 ✅)

---

## 📅 권장 일정

| 단계 | 기간 | 산출물 |
|------|------|--------|
| Phase 1 | 1일 | 기본 설정, 패키지 설치 |
| Phase 2 | 2-3일 | 기본 Contract 테스트 통과 |
| Phase 3 | 3-5일 | Stateful 워크플로우 테스트 |
| Phase 4 | 1-2일 | CI 통합 완료 |
| **총계** | **7-11일** | |

---

## 🚀 시작 추천 순서

1. **`schemathesis` 패키지 설치**
2. **스키마 검증 먼저 수행** (스키마 자체의 완전성 확인)
   ```bash
   schemathesis run http://localhost:8000/api/schema/ --dry-run
   ```
3. **간단한 GET 엔드포인트부터 테스트** (예: 상품 목록)
4. **인증 필요 엔드포인트 추가**
5. **점진적으로 범위 확장**
