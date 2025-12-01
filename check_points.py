#!/usr/bin/env python
"""포인트 현황 확인 스크립트"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

# 필요한 패키지가 없어도 기본 Django만 로드
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    django.setup()
except Exception as e:
    print(f"Django setup error: {e}")
    sys.exit(1)

from shopping.models import Return, Order
from shopping.models.point import PointHistory
from django.contrib.auth import get_user_model

User = get_user_model()

print("=" * 60)
print("최근 환불 건 확인")
print("=" * 60)

returns = Return.objects.select_related('order', 'user').order_by('-id')[:5]
for r in returns:
    print(f'\n--- Return #{r.id}: {r.return_number} ---')
    print(f'Type: {r.type}, Status: {r.status}')
    print(f'User: {r.user.username} (points: {r.user.points})')
    print(f'Order #{r.order.id}: used_points={r.order.used_points}, earned_points={r.order.earned_points}')

print("\n" + "=" * 60)
print("tempuser2 포인트 현황")
print("=" * 60)

try:
    user = User.objects.get(username='tempuser2')
    print(f'\nUser.points (캐시): {user.points}')
    
    # PointHistory에서 실제 잔액 계산
    latest = PointHistory.objects.filter(user=user).order_by('-created_at').first()
    if latest:
        print(f'PointHistory 최신 잔액: {latest.balance}')
    
    # 유효한 적립 포인트 (FIFO용)
    from django.utils import timezone
    from django.db.models import Sum
    now = timezone.now()
    
    # 만료되지 않은 적립 포인트 합계
    valid_earns = PointHistory.objects.filter(
        user=user,
        type='earn',
        expires_at__gt=now
    ).exclude(
        metadata__contains={'expired': True}
    )
    
    total_earned = 0
    total_used = 0
    for earn in valid_earns:
        used = earn.metadata.get('used_amount', 0) if earn.metadata else 0
        remaining = earn.points - used
        if remaining > 0:
            total_earned += remaining
            print(f'  적립#{earn.id}: {earn.points}P, 사용됨={used}P, 남음={remaining}P, 만료={earn.expires_at.date()}')
    
    print(f'\n유효한 적립 포인트 합계 (usable): {total_earned}')
    
    print('\n최근 포인트 이력 10건:')
    histories = PointHistory.objects.filter(user=user).order_by('-created_at')[:10]
    for h in histories:
        desc = h.description[:40] if h.description else ''
        print(f'  [{h.type:15}] {h.points:+6d}P -> 잔액 {h.balance:5d}P | {desc}')
        
except User.DoesNotExist:
    print('tempuser2 사용자가 없습니다.')
