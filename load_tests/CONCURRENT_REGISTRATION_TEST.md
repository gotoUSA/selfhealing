# 동시 회원가입 동시성 테스트

## 문제점
pytest의 `APIClient`를 사용한 스레드 기반 동시성 테스트는 Django의 `transaction=True` 설정으로 인해 각 스레드가 별도의 DB connection과 transaction을 가지므로, 실제 동시성 상황을 재현하지 못합니다.

## 해결 방법
실제 HTTP 요청을 보내는 **Locust**를 사용하여 동시성 테스트를 수행합니다.

## 테스트 실행

### 1. 테스트 데이터 정리
```bash
docker compose exec web python manage.py shell -c "from shopping.models.user import User; User.objects.filter(email='concurrent_test@test.com').delete()"
```

### 2. Locust 동시성 테스트 실행
```bash
docker compose exec web locust -f shopping/tests/performance/test_concurrent_registration.py --host=http://localhost:8000 --headless -u 3 -r 3 -t 10s
```

### 3. 예상 결과
```
✅ 성공 (201): 1명
❌ 실패 (400): 2명 - "이미 사용중인 이메일입니다."
```

## 구현 상세

### serializers/user_serializers.py
- `IntegrityError`를 `ValidationError`로 변환
- PostgreSQL/SQLite 모두 지원하는 에러 메시지 파싱
- 명확한 에러 응답 반환

### views/auth_views.py
- `transaction.atomic()` 사용
- 트랜잭션 내에서 사용자 생성 및 후처리
- 동시성 상황에서 IntegrityError 발생 시 롤백

## 검증 완료
✅ DB unique constraint 존재 확인
✅ Locust 테스트: 1명 성공, 2명 실패
✅ 실제 HTTP 동시 요청에서 정상 작동
