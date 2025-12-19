# =============================================================================
# Makefile - 테스트 및 개발 명령어
# =============================================================================
# 사용법:
#   make test         - 전체 테스트 실행 (Docker 인프라 자동 시작)
#   make test-fast    - Mock 기반 빠른 테스트만 (DB 불필요)
#   make test-unit    - 단위 테스트만
#   make test-integration - 통합 테스트만 (DB 필요)
#   make clean        - Docker 컨테이너 정리
# =============================================================================

.PHONY: help test test-fast test-unit test-integration test-all clean docker-up docker-down

# 기본 타겟
help:
	@echo "사용 가능한 명령어:"
	@echo "  make test             - 전체 테스트 실행 (Docker 인프라 자동 시작)"
	@echo "  make test-fast        - Mock 기반 빠른 테스트만 (DB 불필요)"
	@echo "  make test-unit        - 단위 테스트만"
	@echo "  make test-integration - 통합 테스트만 (DB 필요)"
	@echo "  make clean            - Docker 컨테이너 정리"

# =============================================================================
# Docker 인프라 관리
# =============================================================================

docker-up:
	@echo "🐳 Docker 인프라 시작 중..."
	@docker run -d --name test-postgres \
		-e POSTGRES_DB=shopping_db \
		-e POSTGRES_USER=shopping_user \
		-e POSTGRES_PASSWORD=shopping_pass \
		-p 15432:5432 \
		postgres:15-alpine 2>/dev/null || true
	@docker run -d --name test-redis \
		-p 16379:6379 \
		redis:7-alpine 2>/dev/null || true
	@echo "⏳ DB 준비 대기 중..."
	@sleep 3
	@echo "✅ 인프라 준비 완료"

docker-down:
	@echo "🧹 Docker 컨테이너 정리 중..."
	@docker rm -f test-postgres test-redis 2>/dev/null || true
	@echo "✅ 정리 완료"

# =============================================================================
# 테스트 실행
# =============================================================================

# 빠른 테스트 (DB 불필요, Mock 기반)
test-fast:
	@echo "🚀 빠른 테스트 실행 (Mock 기반)..."
	pytest tests/self_healing/integration/ tests/self_healing/unit/ \
		--no-cov -q --tb=short -n auto

# 단위 테스트만
test-unit:
	@echo "🧪 단위 테스트 실행..."
	pytest tests/self_healing/unit/ --no-cov -q --tb=short -n auto

# 통합 테스트 (DB 필요)
test-integration: docker-up
	@echo "🔗 통합 테스트 실행..."
	TEST_DATABASE_HOST=localhost \
	TEST_DATABASE_PORT=15432 \
	TEST_DB_AVAILABLE=true \
	TEST_REDIS_AVAILABLE=true \
	pytest tests/ shopping/tests/ \
		--no-cov -q --tb=short \
		-p no:xdist \
		-o "addopts=" \
		--ignore=shopping/tests/schema \
		--ignore=shopping/tests/performance \
		-m "not load_test and not manual and not concurrency and not performance and not heavy and not slow"

# 전체 테스트 (추천)
test: docker-up
	@echo "🎯 전체 테스트 실행..."
	@echo ""
	@echo "📦 Step 1: Self-Healing 테스트..."
	TEST_DATABASE_HOST=localhost \
	TEST_DATABASE_PORT=15432 \
	TEST_DB_AVAILABLE=true \
	TEST_REDIS_AVAILABLE=true \
	pytest tests/self_healing/ \
		--no-cov -q --tb=short -n auto || exit 1
	@echo ""
	@echo "📦 Step 2: Shopping 테스트..."
	TEST_DATABASE_HOST=localhost \
	TEST_DATABASE_PORT=15432 \
	TEST_DB_AVAILABLE=true \
	pytest shopping/tests/ \
		--no-cov -q --tb=short \
		-p no:xdist \
		-o "addopts=" \
		--ignore=shopping/tests/schema \
		--ignore=shopping/tests/performance \
		-m "not load_test and not manual and not concurrency and not performance and not heavy and not slow" || exit 1
	@echo ""
	@echo "✅ 전체 테스트 완료!"

# 전체 테스트 + 정리
test-all: test docker-down
	@echo "🎉 테스트 완료 및 정리됨"

# 정리
clean: docker-down
	@echo "🧹 캐시 정리 중..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf htmlcov/ .coverage 2>/dev/null || true
	@echo "✅ 정리 완료"
