"""
Negative 테스트 (Schemathesis Phase 5.2)
========================================

📋 개요
-------
이 모듈은 의도적으로 잘못된 입력을 주입하여 API가 적절한 에러 응답을
반환하는지 검증합니다. Fuzz 테스트가 "무작위" 입력이라면,
Negative 테스트는 "의도적으로 잘못된" 입력입니다.

🎯 테스트 목적
-------------
1. **입력 검증 확인**: 잘못된 입력에 대해 명확한 에러 메시지 반환
2. **보안 검증**: SQL Injection, XSS 등 보안 공격 시도에 대한 방어 확인
3. **경계값 검증**: 최소/최대값, 빈 값, null 등 경계 조건 처리 확인
4. **에러 응답 형식**: 일관된 에러 응답 구조 (status_code, message 등)

🔧 Fuzz vs Negative 테스트
-------------------------
| 구분 | Fuzz 테스트 | Negative 테스트 |
|------|------------|----------------|
| 입력 | 무작위 생성 | 의도적으로 설계 |
| 목적 | 예상치 못한 버그 발견 | 예상된 에러 처리 검증 |
| 도구 | Hypothesis | 수동 테스트 케이스 |
| 속성 | "5xx 없음" | "적절한 4xx + 에러 메시지" |

📊 테스트 파일 구조
------------------
1. test_invalid_inputs.py
   - 잘못된 ID 형식, 필수 필드 누락, 잘못된 데이터 타입
   - 페이지네이션 경계값, 문자열 길이 제한, 숫자 범위

2. test_malformed_requests.py
   - 잘못된 JSON, 잘못된 Content-Type, 빈 바디

3. test_auth_edge_cases.py
   - 만료된 토큰, 잘못된 토큰 형식, 다른 사용자 리소스 접근

4. test_payment_negative.py
   - 결제 API Negative 테스트

5. test_review_negative.py
   - 리뷰 API Negative 테스트 (평점 범위, 내용 검증)

6. test_point_negative.py
   - 포인트 API Negative 테스트 (잔액 초과, 음수 등)

7. test_order_cancel_negative.py
   - 주문 취소 API Negative 테스트

🚀 실행 방법
-----------
```bash
# Negative 테스트만 실행
pytest -m negative --no-cov -v -n 0

# 특정 파일만 실행
pytest shopping/tests/schema/stress/negative/test_auth_edge_cases.py --no-cov -v -n 0

# 특정 테스트만 실행
pytest -m negative -k "test_sql" --no-cov -v
```

⚠️ 주의사항
----------
1. 보안 테스트: 실제 공격 페이로드를 사용하므로, 프로덕션 환경에서는 실행하지 마세요.
2. 응답 검증: 에러 응답에 민감한 정보(스택 트레이스 등)가 포함되지 않아야 합니다.
3. Rate Limiting: 보안 테스트는 많은 요청을 보내므로 Rate Limiting이 비활성화되어 있어야 합니다.

📅 작성 정보
-----------
- 작성일: 2025-12-05
- 관련 Phase: Schemathesis Phase 5 - 고급 테스트
"""
