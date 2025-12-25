#!/usr/bin/env python
"""Emergency Mode 해제 스크립트"""
import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

try:
    from selfhealing.core.emergency_mode import EmergencyModeManager
    em = EmergencyModeManager()
    print(f"현재 Emergency Mode: {em.is_active()}")
    
    if em.is_active():
        em.deactivate("admin", "Stage 3 테스트를 위한 해제")
        print("Emergency Mode 해제됨")
    
    print(f"현재 상태: {em.is_active()}")
except Exception as e:
    print(f"오류: {e}")
    # 대안: 직접 Redis에서 해제
    try:
        import redis
        r = redis.Redis(host='redis', port=6379, db=0)
        r.delete('selfhealing:emergency_mode')
        r.delete('emergency_mode')
        print("Redis에서 Emergency Mode 키 삭제됨")
    except Exception as e2:
        print(f"Redis 연결 실패: {e2}")
