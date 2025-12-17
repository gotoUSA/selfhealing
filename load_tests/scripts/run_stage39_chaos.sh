#!/bin/bash
# Stage 39: Docker Chaos Test Runner
# 실제 Docker 컨테이너를 죽이고 재시작하면서 Self-Healing 테스트

set -e

echo "=============================================="
echo " Stage 39: Docker Container Chaos Tests"
echo "=============================================="
echo ""

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 함수 정의
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 프로젝트 디렉토리 확인
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Docker 확인
if ! command -v docker &> /dev/null; then
    log_error "Docker가 설치되어 있지 않습니다."
    exit 1
fi

if ! docker info &> /dev/null; then
    log_error "Docker 데몬이 실행되고 있지 않습니다."
    exit 1
fi

# 도움말
show_help() {
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  start     - 서비스 시작 (빌드 포함)"
    echo "  stop      - 서비스 중지"
    echo "  test      - Chaos 테스트 실행"
    echo "  logs      - 서비스 로그 확인"
    echo "  status    - 컨테이너 상태 확인"
    echo "  clean     - 모든 리소스 정리"
    echo "  all       - 전체 실행 (start → test → logs)"
    echo ""
}

# 서비스 시작
start_services() {
    log_info "Stage 39 서비스를 시작합니다..."
    docker-compose -f docker-compose.stage39.yml up -d --build

    log_info "서비스가 준비될 때까지 대기 중..."
    sleep 10

    # Health check
    MAX_RETRIES=30
    RETRY_COUNT=0

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s http://localhost:8000/api/health/ > /dev/null 2>&1; then
            log_success "웹 서비스가 준비되었습니다!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo -n "."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log_warning "서비스가 준비되지 않았을 수 있습니다. 로그를 확인하세요."
    fi

    echo ""
    status_services
}

# 서비스 중지
stop_services() {
    log_info "Stage 39 서비스를 중지합니다..."
    docker-compose -f docker-compose.stage39.yml down
    log_success "서비스가 중지되었습니다."
}

# Chaos 테스트 실행
run_tests() {
    log_info "Docker Chaos 테스트를 실행합니다..."
    echo ""

    # chaos-runner 컨테이너로 테스트 실행
    docker-compose -f docker-compose.stage39.yml run --rm chaos-runner

    log_success "테스트가 완료되었습니다!"
}

# 로그 확인
show_logs() {
    log_info "서비스 로그 (Ctrl+C로 종료)..."
    docker-compose -f docker-compose.stage39.yml logs -f web celery_worker
}

# 상태 확인
status_services() {
    log_info "컨테이너 상태:"
    echo ""
    docker-compose -f docker-compose.stage39.yml ps
}

# 정리
clean_all() {
    log_info "모든 리소스를 정리합니다..."
    docker-compose -f docker-compose.stage39.yml down -v --remove-orphans
    log_success "정리 완료!"
}

# 전체 실행
run_all() {
    start_services
    echo ""
    log_info "5초 후 테스트를 시작합니다..."
    sleep 5
    run_tests
}

# 메인
case "${1:-help}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    test)
        run_tests
        ;;
    logs)
        show_logs
        ;;
    status)
        status_services
        ;;
    clean)
        clean_all
        ;;
    all)
        run_all
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "알 수 없는 명령어: $1"
        show_help
        exit 1
        ;;
esac
