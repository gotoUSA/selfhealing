@echo off
chcp 65001 >nul
REM 포인트 부하 테스트 준비 스크립트 (Windows)
REM 이 스크립트는 부하 테스트에 필요한 데이터를 생성합니다.

echo ==========================================
echo 포인트 부하 테스트 준비
echo ==========================================
echo.

REM 1. 테스트 데이터 생성 (상품, 카테고리 등)
echo 1. 기본 테스트 데이터 생성 중...
python manage.py create_test_data --preset full
echo ✓ 완료
echo.

REM 2. 부하 테스트용 사용자 생성 (0-999, 총 1000명)
echo 2. 부하 테스트용 사용자 1000명 생성 중...
echo    - 사용자명: load_test_user_0 ~ load_test_user_999
echo    - 비밀번호: testpass123
echo    - 초기 포인트: 50,000원
echo.
python manage.py create_load_test_users --count 1000 --points 50000
echo ✓ 완료
echo.

REM 3. 준비 완료 메시지
echo ==========================================
echo ✓ 부하 테스트 준비 완료!
echo ==========================================
echo.
echo 부하 테스트 실행 방법:
echo.
echo 1. 개발 서버 실행:
echo    python manage.py runserver
echo.
echo 2. Locust 부하 테스트 실행 (새 터미널):
echo    locust -f shopping/tests/performance/point_concurrent_load_test.py --host=http://localhost:8000
echo.
echo 3. 웹 브라우저에서 http://localhost:8089 접속
echo.
echo 권장 설정:
echo    - Users: 100~1000명
echo    - Spawn rate: 10~50명/초
echo.
pause
