#!/usr/bin/env python
"""API 응답 확인 스크립트"""
import requests
import json

# 로그인
s = requests.Session()
login = s.post('http://localhost:8000/api/auth/login/', json={'username': 'load_test_user_0', 'password': 'testpass123'})
print(f"Login: {login.status_code}")
login_data = login.json()
# 토큰은 token.access에 있음
token = login_data.get('token', {}).get('access') or login_data.get('access')
print(f"Token: {token[:50] if token else 'None'}...")
headers = {'Authorization': f'Bearer {token}'}

# CB Force OPEN
print("\n=== CB Force OPEN ===")
r = s.post('http://localhost:8000/api/self-healing/control/', 
    json={'service_name': 'toss_payment', 'action': 'block', 'environment': 'test', 'reason': 'test'},
    headers=headers)
print(f"Status: {r.status_code}")
print(f"Response: {json.dumps(r.json(), indent=2, ensure_ascii=False)}")

# CB Force CLOSE
print("\n=== CB Force CLOSE ===")
r = s.post('http://localhost:8000/api/self-healing/control/', 
    json={'service_name': 'toss_payment', 'action': 'allow', 'environment': 'test', 'reason': 'test'},
    headers=headers)
print(f"Status: {r.status_code}")
print(f"Response: {json.dumps(r.json(), indent=2, ensure_ascii=False)}")

# DLQ Test Create
print("\n=== DLQ Test Create ===")
r = s.post('http://localhost:8000/api/self-healing/dlq/test/create/', 
    json={'domain': 'payment', 'failure_type': 'PG_TIMEOUT', 'entity_type': 'test', 'entity_id': 'test123', 'error_message': 'test'},
    headers=headers)
print(f"Status: {r.status_code}")
print(f"Response: {json.dumps(r.json(), indent=2, ensure_ascii=False)}")

# Emergency Mode Trigger
print("\n=== Emergency Mode Trigger ===")
r = s.post('http://localhost:8000/api/self-healing/emergency/trigger/', 
    json={'level': 'LEVEL_1', 'reason': 'test', 'duration_minutes': 1},
    headers=headers)
print(f"Status: {r.status_code}")
print(f"Response: {json.dumps(r.json(), indent=2, ensure_ascii=False)}")

# Emergency Mode Release
print("\n=== Emergency Mode Release ===")
r = s.post('http://localhost:8000/api/self-healing/emergency/release/', 
    json={'reason': 'test done'},
    headers=headers)
print(f"Status: {r.status_code}")
print(f"Response: {json.dumps(r.json(), indent=2, ensure_ascii=False)}")
