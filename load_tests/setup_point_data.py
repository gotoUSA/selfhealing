"""
포인트 부하 테스트용 사용자 포인트 설정

사용법:
    python manage.py shell < shopping/tests/performance/setup_point_data.py

목적:
    - load_test_user_* 사용자들에게 충분한 포인트 제공
    - 포인트 부하 테스트 실행 전 필수 작업
"""

import os
import sys
import django

# Django 설정
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from django.db import transaction
from shopping.models import User

# 설정
POINTS_PER_USER = 500_000  # 사용자당 50만 포인트 (넉넉하게)
USERNAME_PREFIX = "load_test_user_"

print("=" * 80)
print("포인트 부하 테스트 데이터 설정 시작")
print("=" * 80)

# 트랜잭션 내에서 일괄 처리
with transaction.atomic():
    # load_test_user_ 로 시작하는 모든 사용자 조회
    users = User.objects.filter(username__startswith=USERNAME_PREFIX)
    user_count = users.count()

    if user_count == 0:
        print(f"\n❌ '{USERNAME_PREFIX}' 패턴의 사용자가 없습니다.")
        print("먼저 테스트 사용자를 생성해주세요:")
        print("  python manage.py shell < shopping/tests/performance/setup_test_data.py")
        sys.exit(1)

    print(f"\n📊 대상 사용자: {user_count}명")
    print(f"💰 설정할 포인트: {POINTS_PER_USER:,}P (사용자당)")

    # 포인트 일괄 업데이트
    updated_count = users.update(points=POINTS_PER_USER)

    print(f"\n✅ {updated_count}명의 포인트를 {POINTS_PER_USER:,}P로 설정 완료")
    print(f"📈 총 포인트: {updated_count * POINTS_PER_USER:,}P")

print("\n" + "=" * 80)
print("포인트 설정 완료!")
print("=" * 80)
print("\n이제 Locust 테스트를 실행할 수 있습니다:")
print("  locust -f shopping/tests/performance/point_concurrent_load_test.py\n")
