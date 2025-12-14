# Infrastructure Features

pyproject.toml - Black 포매터 설정
pyproject.toml - Ruff 린터 설정
pyproject.toml - isort 설정
pyproject.toml - pytest 설정
pyproject.toml - coverage 설정
pyproject.toml - mutmut 설정
pyproject.toml - mypy 설정

setup.cfg - flake8 설정
setup.cfg - isort 설정 (백업)

Dockerfile - Python 3.12-slim 베이스 이미지 설정
Dockerfile - 작업 디렉토리 /code 설정
Dockerfile - apt-get update 시스템 패키지 업데이트
Dockerfile - apt 시스템 패키지 설치 (gcc, postgresql-client, git, libev-dev, libevent-dev, libffi-dev, python3-dev, graphviz)
Dockerfile - apt 캐시 정리 (rm -rf /var/lib/apt/lists/*)
Dockerfile - requirements.txt 복사
Dockerfile - requirements-dev.txt 복사
Dockerfile - pip 업그레이드
Dockerfile - Python 패키지 설치 (requirements-dev.txt, --no-cache-dir)
Dockerfile - 프로젝트 파일 복사
Dockerfile - PYTHONDONTWRITEBYTECODE 환경변수 설정
Dockerfile - PYTHONUNBUFFERED 환경변수 설정
Dockerfile - 포트 8000 노출

docker-compose.yml - PostgreSQL 15-alpine 데이터베이스 서비스 (db)
docker-compose.yml - postgres_data 볼륨 마운트
docker-compose.yml - PostgreSQL 환경변수 설정 (POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD)
docker-compose.yml - PostgreSQL 포트 5432:5432 노출
docker-compose.yml - PostgreSQL healthcheck (pg_isready)
docker-compose.yml - PostgreSQL healthcheck interval 5s
docker-compose.yml - PostgreSQL healthcheck timeout 5s
docker-compose.yml - PostgreSQL healthcheck retries 5
docker-compose.yml - Redis 7-alpine 서비스 (Celery 브로커 및 캐시)
docker-compose.yml - Redis healthcheck (redis-cli ping)
docker-compose.yml - Redis healthcheck interval 5s
docker-compose.yml - Redis healthcheck timeout 3s
docker-compose.yml - Redis healthcheck retries 5
docker-compose.yml - Django 웹 애플리케이션 서비스 (web)
docker-compose.yml - web 서비스 build 설정
docker-compose.yml - collectstatic 자동 실행
docker-compose.yml - Gunicorn WSGI 서버 (workers=4, threads=4, gthread worker-class)
docker-compose.yml - Gunicorn timeout 60 설정
docker-compose.yml - Gunicorn access-logfile 설정
docker-compose.yml - Gunicorn error-logfile 설정
docker-compose.yml - web 서비스 코드 볼륨 마운트 (.:/code)
docker-compose.yml - web 서비스 static_volume 볼륨 마운트
docker-compose.yml - web 서비스 media_volume 볼륨 마운트
docker-compose.yml - web 서비스 expose 8000
docker-compose.yml - web 서비스 .env 환경파일 로드
docker-compose.yml - web 서비스 db, redis 의존성 설정 (service_healthy condition)
docker-compose.yml - Nginx alpine 리버스 프록시 서비스
docker-compose.yml - Nginx nginx.conf 설정파일 마운트
docker-compose.yml - Nginx static_volume 마운트
docker-compose.yml - Nginx media_volume 마운트
docker-compose.yml - Nginx 포트 8000:80 노출
docker-compose.yml - Nginx web 서비스 의존성 설정
docker-compose.yml - Celery Worker 서비스 (celery_worker)
docker-compose.yml - Celery Worker build 설정
docker-compose.yml - Celery 큐 설정 (default, payment_critical, order_processing, external_api, points, notifications)
docker-compose.yml - Celery concurrency=8 설정
docker-compose.yml - Celery prefetch-multiplier=1 설정
docker-compose.yml - Celery Worker 코드 볼륨 마운트 (.:/code)
docker-compose.yml - Celery Worker .env 환경파일 로드
docker-compose.yml - Celery Worker db, redis 의존성 설정 (service_healthy condition)
docker-compose.yml - Celery Beat 서비스 (주기적 작업 스케줄러)
docker-compose.yml - Celery Beat build 설정
docker-compose.yml - DatabaseScheduler 사용 (django_celery_beat)
docker-compose.yml - Celery Beat 코드 볼륨 마운트 (.:/code)
docker-compose.yml - Celery Beat .env 환경파일 로드
docker-compose.yml - Celery Beat db, redis 의존성 설정 (service_healthy condition)
docker-compose.yml - Flower 모니터링 서비스 (Celery 모니터링)
docker-compose.yml - Flower build 설정
docker-compose.yml - Flower 코드 볼륨 마운트 (.:/code)
docker-compose.yml - Flower .env 환경파일 로드
docker-compose.yml - Flower 포트 5555 노출
docker-compose.yml - Flower redis, celery_worker 의존성 설정
docker-compose.yml - postgres_data 볼륨 정의
docker-compose.yml - static_volume 볼륨 정의
docker-compose.yml - media_volume 볼륨 정의

nginx/nginx.conf - upstream django_app 정의
nginx/nginx.conf - upstream server web:8000 설정
nginx/nginx.conf - upstream keepalive 16 설정
nginx/nginx.conf - 서버 포트 80 리스닝
nginx/nginx.conf - server_name localhost 설정
nginx/nginx.conf - client_max_body_size 20M 설정
nginx/nginx.conf - Gzip 압축 활성화
nginx/nginx.conf - gzip_vary 설정
nginx/nginx.conf - gzip_min_length 1024 설정
nginx/nginx.conf - gzip_comp_level 5 설정
nginx/nginx.conf - gzip_types 설정 (text/plain, text/css, text/javascript, application/javascript, application/json, application/xml, image/svg+xml)
nginx/nginx.conf - gzip_proxied any 설정
nginx/nginx.conf - proxy_buffer_size 16k 설정
nginx/nginx.conf - proxy_buffers 8 16k 설정
nginx/nginx.conf - proxy_busy_buffers_size 32k 설정
nginx/nginx.conf - /static/ 정적 파일 서빙 (alias /code/staticfiles/)
nginx/nginx.conf - 정적 파일 브라우저 캐싱 (expires 30d)
nginx/nginx.conf - 정적 파일 Cache-Control public immutable 설정
nginx/nginx.conf - gzip_static 사전 압축 파일 사용
nginx/nginx.conf - 정적 파일 access_log off
nginx/nginx.conf - 정적 파일 tcp_nodelay 최적화
nginx/nginx.conf - 정적 파일 sendfile 최적화
nginx/nginx.conf - /media/ 미디어 파일 서빙 (alias /code/media/)
nginx/nginx.conf - 미디어 파일 캐싱 (expires 7d)
nginx/nginx.conf - 미디어 파일 Cache-Control public 설정
nginx/nginx.conf - 미디어 파일 access_log off
nginx/nginx.conf - 미디어 파일 sendfile 최적화
nginx/nginx.conf - / 루트 Django 프록시 설정 (proxy_pass http://django_app)
nginx/nginx.conf - X-Forwarded-For 헤더 설정
nginx/nginx.conf - X-Forwarded-Proto 헤더 설정
nginx/nginx.conf - Host 헤더 설정
nginx/nginx.conf - X-Real-IP 헤더 설정
nginx/nginx.conf - proxy_redirect off 설정
nginx/nginx.conf - HTTP/1.1 keepalive 지원 (proxy_http_version 1.1)
nginx/nginx.conf - proxy_set_header Connection "" 설정
nginx/nginx.conf - proxy_connect_timeout 3s 설정
nginx/nginx.conf - proxy_send_timeout 30s 설정
nginx/nginx.conf - proxy_read_timeout 30s 설정
nginx/nginx.conf - /api/ 엔드포인트 프록시 설정
nginx/nginx.conf - API 응답 캐시 비활성화 (no-store, no-cache, must-revalidate)
nginx/nginx.conf - /health/ 헬스 체크 엔드포인트 (return 200 OK)
nginx/nginx.conf - /health/ access_log off
nginx/nginx.conf - /health/ Content-Type text/plain 설정
nginx/nginx.conf - 숨김 파일 접근 차단 (location ~ /\.)
nginx/nginx.conf - 숨김 파일 deny all 설정
nginx/nginx.conf - 숨김 파일 log_not_found off
nginx/nginx.conf - /favicon.ico 404 로깅 방지
nginx/nginx.conf - /favicon.ico try_files $uri =204
nginx/nginx.conf - /robots.txt 로깅 방지

check-code-quality.sh - 코드 품질 검사 스크립트
check-code-quality.sh - flake8 린트 검사 실행
check-code-quality.sh - black 포맷 검사 실행
check-code-quality.sh - isort 임포트 정렬 검사 실행
check-code-quality.sh - mypy 타입 검사 실행

scripts/analyze_code.sh - set -e 에러 시 스크립트 종료
scripts/analyze_code.sh - 색상 정의 (RED, GREEN, YELLOW, BLUE, NC)
scripts/analyze_code.sh - 리포트 디렉토리 생성 (reports/YYYYMMDD_HHMMSS)
scripts/analyze_code.sh - pygount 코드 통계 실행
scripts/analyze_code.sh - pygount --folders-to-skip 설정
scripts/analyze_code.sh - radon cc 복잡도 분석 실행
scripts/analyze_code.sh - radon cc --min B 설정
scripts/analyze_code.sh - radon mi 유지보수성 분석 실행
scripts/analyze_code.sh - radon mi --min B 설정
scripts/analyze_code.sh - bandit 보안 분석 실행
scripts/analyze_code.sh - bandit HTML 리포트 생성 (-f html)
scripts/analyze_code.sh - bandit 테스트 디렉토리 제외 (-x shopping/tests)
scripts/analyze_code.sh - vulture 죽은 코드 탐지 실행
scripts/analyze_code.sh - vulture --min-confidence 90 설정
scripts/analyze_code.sh - pipdeptree 의존성 충돌 검사 실행
scripts/analyze_code.sh - pipdeptree --warn fail 설정
scripts/analyze_code.sh - safety 의존성 보안 검사 실행
scripts/analyze_code.sh - tee 명령어로 파일 저장 및 출력
scripts/analyze_code.sh - ls -la 생성된 파일 목록 출력

scripts/mutation_test.sh - set -e 에러 시 스크립트 종료
scripts/mutation_test.sh - 색상 정의 (RED, GREEN, YELLOW, BLUE, NC)
scripts/mutation_test.sh - Docker 컨테이너 상태 확인 (docker-compose ps)
scripts/mutation_test.sh - mutmut 캐시 정리 (rm -f .mutmut-cache)
scripts/mutation_test.sh - 대화형 테스트 선택 메뉴 (read -p)
scripts/mutation_test.sh - 전체 서비스 mutation testing 실행
scripts/mutation_test.sh - 결제 서비스 mutation testing 실행
scripts/mutation_test.sh - 포인트 서비스 mutation testing 실행
scripts/mutation_test.sh - 장바구니 서비스 mutation testing 실행
scripts/mutation_test.sh - 주문 서비스 mutation testing 실행
scripts/mutation_test.sh - PYTHONIOENCODING=utf-8 환경변수 설정
scripts/mutation_test.sh - mutmut --paths-to-mutate 옵션
scripts/mutation_test.sh - mutmut --runner 옵션
scripts/mutation_test.sh - mutmut results 결과 요약 출력
scripts/mutation_test.sh - mutmut html 리포트 생성

scripts/prepare_load_test.sh - create_test_data 관리 명령어 실행 (--preset full)
scripts/prepare_load_test.sh - create_load_test_users 관리 명령어 실행 (--count 1000 --points 50000)
scripts/prepare_load_test.sh - 부하 테스트 실행 방법 안내 출력
scripts/prepare_load_test.sh - Locust 실행 명령어 안내

scripts/prepare_load_test.bat - UTF-8 코드페이지 설정 (chcp 65001)
scripts/prepare_load_test.bat - create_test_data 관리 명령어 실행 (--preset full)
scripts/prepare_load_test.bat - create_load_test_users 관리 명령어 실행 (--count 1000 --points 50000)
scripts/prepare_load_test.bat - 부하 테스트 실행 방법 안내 출력
scripts/prepare_load_test.bat - Locust 실행 명령어 안내
scripts/prepare_load_test.bat - pause 명령어 (사용자 입력 대기)

scripts/migration/update_payment_tests.sh - sed 기반 테스트 파일 상태 코드 일괄 변경 (HTTP 200 → HTTP 202)
scripts/migration/update_payment_tests.sh - test_payment_confirm.py 수정
scripts/migration/update_payment_tests.sh - test_payment_validation.py 수정
scripts/migration/update_payment_tests.sh - test_payment_points.py 수정
scripts/migration/update_payment_tests.sh - test_order_payment.py 수정
scripts/migration/update_payment_tests.sh - test_payment_security.py 수정

scripts/migration/update_test_responses.py - update_test_file 함수 (테스트 파일 업데이트)
scripts/migration/update_test_responses.py - regex 기반 points_earned 응답 검증 제거
scripts/migration/update_test_responses.py - full_points_payment_no_earn 케이스 수정
scripts/migration/update_test_responses.py - 비동기 결제 테스트 응답 구조 변경 적용
scripts/migration/update_test_responses.py - 테스트 파일 일괄 처리 메인 로직

.env.example - DJANGO_SECRET_KEY 환경변수
.env.example - DJANGO_DEBUG 환경변수
.env.example - DJANGO_ALLOWED_HOSTS 환경변수
.env.example - DATABASE_ENGINE 환경변수
.env.example - DATABASE_NAME 환경변수
.env.example - DATABASE_USER 환경변수
.env.example - DATABASE_PASSWORD 환경변수
.env.example - DATABASE_HOST 환경변수
.env.example - DATABASE_PORT 환경변수
.env.example - REDIS_URL 환경변수
.env.example - CELERY_BROKER_URL 환경변수
.env.example - CELERY_RESULT_BACKEND 환경변수
.env.example - TOSS_CLIENT_KEY 환경변수
.env.example - TOSS_SECRET_KEY 환경변수
.env.example - TOSS_WEBHOOK_SECRET 환경변수
.env.example - TOSS_BASE_URL 환경변수
.env.example - FRONTEND_URL 환경변수
.env.example - EMAIL_BACKEND 환경변수
.env.example - EMAIL_HOST 환경변수
.env.example - EMAIL_PORT 환경변수
.env.example - EMAIL_USE_TLS 환경변수
.env.example - EMAIL_HOST_USER 환경변수
.env.example - EMAIL_HOST_PASSWORD 환경변수
.env.example - DEFAULT_FROM_EMAIL 환경변수
.env.example - TEST_USER_PASSWORD 환경변수
.env.example - TEST_ADMIN_PASSWORD 환경변수
.env.example - TEST_DATA_PRESET 환경변수
.env.example - GOOGLE_CLIENT_ID 환경변수
.env.example - GOOGLE_CLIENT_SECRET 환경변수
.env.example - KAKAO_REST_API_KEY 환경변수
.env.example - KAKAO_CLIENT_SECRET 환경변수
.env.example - NAVER_CLIENT_ID 환경변수
.env.example - NAVER_CLIENT_SECRET 환경변수
.env.example - SOCIAL_LOGIN_REDIRECT_URI 환경변수
.env.example - ENCRYPTION_KEY 환경변수

.github/workflows/django-ci.yml - name: Django CI 워크플로우 이름
.github/workflows/django-ci.yml - push 트리거 (main, master, develop 브랜치)
.github/workflows/django-ci.yml - pull_request 트리거 (main, master, develop 브랜치)
.github/workflows/django-ci.yml - runs-on: ubuntu-latest
.github/workflows/django-ci.yml - PostgreSQL 15 서비스 컨테이너
.github/workflows/django-ci.yml - PostgreSQL 환경변수 설정 (POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD)
.github/workflows/django-ci.yml - PostgreSQL 포트 5432:5432 매핑
.github/workflows/django-ci.yml - PostgreSQL healthcheck 설정 (pg_isready)
.github/workflows/django-ci.yml - PostgreSQL healthcheck interval 10s
.github/workflows/django-ci.yml - PostgreSQL healthcheck timeout 5s
.github/workflows/django-ci.yml - PostgreSQL healthcheck retries 5
.github/workflows/django-ci.yml - Redis 7-alpine 서비스 컨테이너
.github/workflows/django-ci.yml - Redis 포트 6379:6379 매핑
.github/workflows/django-ci.yml - Redis healthcheck 설정 (redis-cli ping)
.github/workflows/django-ci.yml - Redis healthcheck interval 10s
.github/workflows/django-ci.yml - Redis healthcheck timeout 5s
.github/workflows/django-ci.yml - Redis healthcheck retries 5
.github/workflows/django-ci.yml - DJANGO_SECRET_KEY GitHub Secrets 참조
.github/workflows/django-ci.yml - ENCRYPTION_KEY GitHub Secrets 참조
.github/workflows/django-ci.yml - DJANGO_DEBUG 환경변수 (False)
.github/workflows/django-ci.yml - DATABASE_ENGINE 환경변수
.github/workflows/django-ci.yml - DATABASE_NAME 환경변수
.github/workflows/django-ci.yml - DATABASE_USER 환경변수
.github/workflows/django-ci.yml - DATABASE_PASSWORD 환경변수
.github/workflows/django-ci.yml - DATABASE_HOST 환경변수
.github/workflows/django-ci.yml - DATABASE_PORT 환경변수
.github/workflows/django-ci.yml - REDIS_URL 환경변수
.github/workflows/django-ci.yml - CELERY_BROKER_URL 환경변수
.github/workflows/django-ci.yml - CELERY_RESULT_BACKEND 환경변수
.github/workflows/django-ci.yml - TOSS_CLIENT_KEY 환경변수
.github/workflows/django-ci.yml - TOSS_SECRET_KEY 환경변수
.github/workflows/django-ci.yml - TOSS_WEBHOOK_SECRET 환경변수
.github/workflows/django-ci.yml - actions/checkout@v4 코드 체크아웃
.github/workflows/django-ci.yml - actions/setup-python@v4 Python 3.12 설정
.github/workflows/django-ci.yml - pip 캐싱 활성화 (cache: 'pip')
.github/workflows/django-ci.yml - pip 업그레이드 (pip install --upgrade pip)
.github/workflows/django-ci.yml - requirements.txt 의존성 설치
.github/workflows/django-ci.yml - requirements-dev.txt 의존성 설치
.github/workflows/django-ci.yml - Django migrate 마이그레이션 실행 (--noinput)
.github/workflows/django-ci.yml - coverage 패키지 설치
.github/workflows/django-ci.yml - pytest-cov 패키지 설치
.github/workflows/django-ci.yml - nproc CPU 정보 출력
.github/workflows/django-ci.yml - pytest-xdist 버전 출력
.github/workflows/django-ci.yml - pytest 테스트 실행
.github/workflows/django-ci.yml - pytest --reuse-db 데이터베이스 재사용
.github/workflows/django-ci.yml - pytest --cov=shopping 커버리지 측정
.github/workflows/django-ci.yml - pytest --cov-report= 옵션
.github/workflows/django-ci.yml - pytest -vv verbose 출력
.github/workflows/django-ci.yml - pytest --dist loadscope 병렬 테스트 실행
.github/workflows/django-ci.yml - coverage combine 워커별 커버리지 병합
.github/workflows/django-ci.yml - coverage report 커버리지 리포트 생성
.github/workflows/django-ci.yml - coverage report --skip-covered 옵션
.github/workflows/django-ci.yml - coverage xml XML 커버리지 리포트 생성
.github/workflows/django-ci.yml - flake8 패키지 설치
.github/workflows/django-ci.yml - flake8 코드 스타일 체크
.github/workflows/django-ci.yml - flake8 --count 옵션
.github/workflows/django-ci.yml - flake8 에러 체크 (--select=E9,F63,F7,F82)
.github/workflows/django-ci.yml - flake8 --show-source 옵션
.github/workflows/django-ci.yml - flake8 --statistics 옵션
.github/workflows/django-ci.yml - flake8 --exit-zero 옵션
.github/workflows/django-ci.yml - flake8 복잡도 체크 (--max-complexity=10)
.github/workflows/django-ci.yml - flake8 라인 길이 체크 (--max-line-length=127)
.github/workflows/django-ci.yml - continue-on-error: true (flake8 실패해도 계속 진행)
