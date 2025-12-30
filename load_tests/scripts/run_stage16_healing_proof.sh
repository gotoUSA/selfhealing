#!/bin/bash
# =============================================================================
# Stage 16 v6.0.0: HEALING PROOF - 테스트 실행 스크립트
# =============================================================================
#
# 사용법:
#   ./load_tests/scripts/run_stage16_healing_proof.sh
#
# 옵션:
#   --local     : 로컬 환경에서 실행 (Docker 없이)
#   --docker    : Docker Compose로 실행 (기본값)
#   --cleanup   : 테스트 후 Docker 정리
#
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RESULTS_DIR="$PROJECT_ROOT/load_tests/results/stage16"

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 로고 출력
echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  🔥 Stage 16 v6.0.0: HEALING PROOF                              ║"
echo "║  Complete Self-Healing Verification Test                         ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 옵션 파싱
MODE="docker"
CLEANUP=false

for arg in "$@"; do
    case $arg in
        --local)
            MODE="local"
            shift
            ;;
        --docker)
            MODE="docker"
            shift
            ;;
        --cleanup)
            CLEANUP=true
            shift
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            exit 1
            ;;
    esac
done

echo -e "${YELLOW}Mode: $MODE${NC}"
echo -e "${YELLOW}Project Root: $PROJECT_ROOT${NC}"
echo -e "${YELLOW}Results Dir: $RESULTS_DIR${NC}"
echo ""

# 결과 디렉토리 생성
mkdir -p "$RESULTS_DIR"

if [ "$MODE" = "docker" ]; then
    echo -e "${BLUE}🐳 Starting Docker Compose environment...${NC}"
    
    cd "$PROJECT_ROOT"
    
    # 기존 컨테이너 정리
    docker-compose -f docker-compose.stage16.yml down --remove-orphans 2>/dev/null || true
    
    # 빌드 및 시작
    echo -e "${YELLOW}Building containers...${NC}"
    docker-compose -f docker-compose.stage16.yml build --quiet
    
    echo -e "${YELLOW}Starting services (db, redis, web, celery-worker)...${NC}"
    docker-compose -f docker-compose.stage16.yml up -d db redis web celery-worker
    
    # 헬스 체크 대기
    echo -e "${YELLOW}Waiting for services to be healthy...${NC}"
    sleep 10
    
    for i in {1..30}; do
        if docker-compose -f docker-compose.stage16.yml exec -T web curl -sf http://localhost:8000/api/self-healing/health/ > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Web service is healthy!${NC}"
            break
        fi
        echo "Waiting for web service... ($i/30)"
        sleep 2
    done
    
    # 테스트 실행
    echo -e "${BLUE}🔥 Running Healing Proof v6 Test...${NC}"
    echo ""
    
    docker-compose -f docker-compose.stage16.yml run --rm test-healing-proof
    
    # 결과 확인
    echo ""
    echo -e "${BLUE}📊 Test Results:${NC}"
    echo "----------------------------------------"
    
    LATEST_REPORT=$(ls -t "$RESULTS_DIR"/stage16_healing_proof_v6_*.md 2>/dev/null | head -1)
    if [ -n "$LATEST_REPORT" ]; then
        cat "$LATEST_REPORT"
    else
        echo -e "${YELLOW}No report file found. Check container logs.${NC}"
    fi
    
    # 정리
    if [ "$CLEANUP" = true ]; then
        echo ""
        echo -e "${YELLOW}Cleaning up Docker resources...${NC}"
        docker-compose -f docker-compose.stage16.yml down -v
        echo -e "${GREEN}✓ Cleanup complete!${NC}"
    else
        echo ""
        echo -e "${YELLOW}To cleanup: docker-compose -f docker-compose.stage16.yml down -v${NC}"
    fi

else
    # 로컬 모드
    echo -e "${BLUE}🖥️ Running in local mode...${NC}"
    
    cd "$PROJECT_ROOT"
    
    # 가상환경 활성화 체크
    if [ -z "$VIRTUAL_ENV" ]; then
        echo -e "${YELLOW}Warning: No virtual environment detected. Consider activating one.${NC}"
    fi
    
    # 의존성 설치
    pip install requests -q
    
    # 테스트 실행
    echo -e "${BLUE}🔥 Running Healing Proof v6 Test...${NC}"
    echo ""
    
    python load_tests/scenarios/chaos/stage16_healing_proof_v6.py \
        --base-url http://localhost:8000 \
        --output-dir "$RESULTS_DIR" \
        --calm-duration 30 \
        --burst-duration 10 \
        --burst-concurrent 100
    
    # 결과 확인
    echo ""
    echo -e "${BLUE}📊 Test Results:${NC}"
    echo "----------------------------------------"
    
    LATEST_REPORT=$(ls -t "$RESULTS_DIR"/stage16_healing_proof_v6_*.md 2>/dev/null | head -1)
    if [ -n "$LATEST_REPORT" ]; then
        cat "$LATEST_REPORT"
    fi
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Stage 16 v6.0.0 HEALING PROOF Test Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════${NC}"
