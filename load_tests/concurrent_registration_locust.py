"""
동시 회원가입 테스트용 Locust 스크립트

사용법:
    locust -f test_concurrent_registration.py --host=http://localhost:8000 --headless -u 3 -r 3 -t 10s

설명:
    -u 3: 3명의 동시 사용자
    -r 3: 초당 3명씩 동시 시작
    -t 10s: 10초간 실행
"""

from locust import HttpUser, task, between, events
import time


class ConcurrentRegistrationUser(HttpUser):
    """동일한 이메일로 동시 회원가입 시도"""

    wait_time = between(0, 0)  # 대기 없이 즉시 실행

    # 전역 카운터 (모든 사용자가 공유)
    registration_attempts = []

    @task
    def register_same_email(self):
        """동일한 이메일로 회원가입 시도"""
        # 고유한 사용자명 (timestamp + random)
        timestamp = int(time.time() * 1000)
        username = f"user_{timestamp}_{id(self)}"

        # 동일한 이메일 사용!
        email = "concurrent_test@test.com"

        response = self.client.post("/api/auth/register/", json={
            "username": username,
            "email": email,  # 모든 요청이 같은 이메일
            "password": "testpass123!",
            "password2": "testpass123!",
            "phone_number": f"010-{timestamp % 10000:04d}-{id(self) % 10000:04d}",
        }, name="/api/auth/register/ (same email)")

        # 결과 기록
        result = {
            "username": username,
            "email": email,
            "status_code": response.status_code,
            "timestamp": time.time(),
        }

        if response.status_code == 201:
            result["success"] = True
            result["message"] = "✅ 201 Created - 성공"
        elif response.status_code == 400:
            result["success"] = False
            result["message"] = f"❌ 400 Bad Request - {response.json()}"
        else:
            result["success"] = False
            result["message"] = f"⚠️  {response.status_code} - {response.text[:100]}"

        self.__class__.registration_attempts.append(result)

        # 한 번만 실행하고 종료
        self.environment.runner.quit()


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """테스트 종료 시 결과 출력"""
    attempts = ConcurrentRegistrationUser.registration_attempts

    print("\n" + "=" * 70)
    print("🔍 동시 회원가입 테스트 결과")
    print("=" * 70)

    success_count = sum(1 for a in attempts if a.get("success"))
    fail_count = len(attempts) - success_count

    print(f"\n📊 총 시도: {len(attempts)}명")
    print(f"✅ 성공 (201): {success_count}명")
    print(f"❌ 실패 (400): {fail_count}명")

    print("\n📝 상세 결과:")
    for i, attempt in enumerate(attempts, 1):
        status = "✅" if attempt.get("success") else "❌"
        print(f"{i}. {status} {attempt['username']} - {attempt['message']}")

    print("\n" + "=" * 70)
    print("🎯 예상 결과:")
    print("  - 성공: 1명 (201 Created)")
    print("  - 실패: 2명 (400 Bad Request - 이미 사용중인 이메일)")
    print("=" * 70)

    # 검증
    if success_count == 1 and fail_count == 2:
        print("\n✅ 테스트 통과! 동시성 제어가 올바르게 작동합니다.")
    elif success_count == 3:
        print("\n❌ 테스트 실패! 3명 모두 성공 - race condition 발생!")
    else:
        print(f"\n⚠️  예상치 못한 결과: 성공 {success_count}, 실패 {fail_count}")
