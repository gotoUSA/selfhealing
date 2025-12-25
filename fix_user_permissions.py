#!/usr/bin/env python
"""load_test_user_0에 Self-Healing Admin 권한 부여"""
import os
import sys
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()

try:
    user = User.objects.get(username="load_test_user_0")
    print(f"사용자 발견: {user.username}")
    print(f"현재 is_staff: {user.is_staff}")
    print(f"현재 is_superuser: {user.is_superuser}")
    
    # superuser 권한 부여
    user.is_superuser = True
    user.is_staff = True
    user.save()
    print(f"권한 변경 완료: is_superuser={user.is_superuser}")
    
    # selfhealing_admin 그룹 추가
    admin_group, created = Group.objects.get_or_create(name="selfhealing_admin")
    if created:
        print("selfhealing_admin 그룹 생성됨")
    user.groups.add(admin_group)
    print(f"selfhealing_admin 그룹 추가 완료")
    print(f"현재 그룹: {list(user.groups.values_list('name', flat=True))}")
    print("\n✅ load_test_user_0 권한 설정 완료!")
    
except User.DoesNotExist:
    print("❌ load_test_user_0 사용자가 존재하지 않습니다.")
    print("먼저 실행: python manage.py create_load_test_users")
    sys.exit(1)
