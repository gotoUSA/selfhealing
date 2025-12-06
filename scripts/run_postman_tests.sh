#!/bin/bash

# Postman 테스트를 Newman Docker로 실행하는 스크립트
# 사용법: ./run_postman_tests.sh [collection_path] [환경변수...]
#
# 주의사항:
# - production 환경에서는 테스트 실행 금지
# - Celery/Queue 기반 API는 딜레이 필수
# - 병렬 실행 금지 (POST API 충돌 방지)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ========================================
# Newman 실행 설정 (현업 권장 값)
# ========================================
# 요청 타임아웃: API 응답 대기 시간 (ms)
REQUEST_TIMEOUT=30000
# 요청 간 딜레이: Celery/Queue 처리 시간 확보 (ms)
REQUEST_DELAY=200
# 반복 간 딜레이: 테스트 세트 간 간격 (ms)
ITERATION_DELAY=500

# 기본값 설정
NETWORK="myproject_default"
BASE_URL="http://nginx/api"
ENV_FILE="/etc/newman/environments/local.json"

# 색상 정의
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Newman Docker Test Runner${NC}"
echo -e "${YELLOW}========================================${NC}"

# 사용법
usage() {
    echo ""
    echo "사용법: $0 [collection_path] [options]"
    echo ""
    echo "예시:"
    echo "  $0 setup                              # 테스트 데이터 초기화 (먼저 실행)"
    echo "  $0                                    # tier1 모든 테스트 실행"
    echo "  $0 tier1                              # tier1 모든 테스트 실행"
    echo "  $0 tier2                              # tier2 보안 테스트 실행"
    echo "  $0 tier3                              # tier3 여정 테스트 실행"
    echo "  $0 all                                # setup + tier1 + tier2 + tier3 전체 실행"
    echo "  $0 t1_cart_pricechanged               # 특정 테스트 실행"
    echo ""
    echo "옵션:"
    echo "  --list       사용 가능한 테스트 목록 표시"
    echo "  --env=ENV    환경 선택 (local, staging) - production 금지"
    echo "  --verbose    상세 출력"
    echo "  --skip-setup setup 단계 건너뛰기 (all 실행 시)"
    echo ""
    echo "환경 설정:"
    echo "  REQUEST_TIMEOUT=$REQUEST_TIMEOUT ms (요청 타임아웃)"
    echo "  REQUEST_DELAY=$REQUEST_DELAY ms (요청 간 딜레이)"
    echo ""
}

# 테스트 목록 표시
list_tests() {
    echo ""
    echo "사용 가능한 테스트 컬렉션:"
    echo ""
    echo "Setup (테스트 데이터 초기화):"
    find "$PROJECT_DIR/postman/collections/setup" -name "*.json" 2>/dev/null | while read f; do
        echo "  - $(basename "$f" .json)"
    done
    echo ""
    echo "Tier 1 (Money Integrity):"
    find "$PROJECT_DIR/postman/collections/tier1_money_integrity" -name "*.json" 2>/dev/null | while read f; do
        echo "  - $(basename "$f" .json)"
    done
    echo ""
    echo "Tier 2 (Security):"
    find "$PROJECT_DIR/postman/collections/tier2_security" -name "*.json" 2>/dev/null | while read f; do
        echo "  - $(basename "$f" .json)"
    done
    echo ""
    echo "Tier 3 (Journey):"
    find "$PROJECT_DIR/postman/collections/tier3_journey" -name "*.json" 2>/dev/null | while read f; do
        echo "  - $(basename "$f" .json)"
    done
    echo ""
}

# ========================================
# Setup 실행 함수 (테스트 데이터 초기화)
# ========================================
run_setup() {
    echo ""
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}  테스트 데이터 초기화 (Setup)${NC}"
    echo -e "${YELLOW}========================================${NC}"
    echo "  - verified_user: 인증 완료된 정상 사용자"
    echo "  - unverified_user: 미인증 사용자 (보안 테스트용)"
    echo "  - test_product: 테스트용 상품 정보"
    echo ""

    MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" \
        -v "$PROJECT_DIR/postman:/etc/newman" \
        postman/newman:alpine run "/etc/newman/collections/setup/test_data_setup.json" \
        -e "$ENV_FILE" \
        --env-var "base_url=$BASE_URL" \
        --timeout-request $REQUEST_TIMEOUT \
        --delay-request $REQUEST_DELAY \
        --reporters cli $VERBOSE \
        --export-environment "$ENV_FILE"

    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 테스트 데이터 초기화 완료${NC}"
        return 0
    else
        echo -e "${RED}✗ 테스트 데이터 초기화 실패${NC}"
        return 1
    fi
}

# 인자 처리
VERBOSE=""
SELECTED_ENV="local"
SKIP_SETUP=false

for arg in "$@"; do
    case $arg in
        --help|-h)
            usage
            exit 0
            ;;
        --list)
            list_tests
            exit 0
            ;;
        --env=*)
            SELECTED_ENV="${arg#*=}"
            ;;
        --verbose)
            VERBOSE="--verbose"
            ;;
        --skip-setup)
            SKIP_SETUP=true
            ;;
    esac
done

# ========================================
# Production 환경 보호 (중요!)
# ========================================
if [ "$SELECTED_ENV" == "production" ] || [ "$SELECTED_ENV" == "prod" ]; then
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}  ⛔ PRODUCTION 환경에서 테스트 실행 금지!${NC}"
    echo -e "${RED}========================================${NC}"
    echo ""
    echo "Production 환경에서 자동화 테스트를 실행하면:"
    echo "  - 실제 사용자 데이터 오염"
    echo "  - 결제/주문 데이터 혼란"
    echo "  - 보안 감사 로그 오염"
    echo ""
    echo "대신 사용하세요:"
    echo "  $0 --env=local    # 로컬 환경"
    echo "  $0 --env=staging  # 스테이징 환경"
    echo ""
    exit 1
fi

# 환경 파일 설정
ENV_FILE="/etc/newman/environments/${SELECTED_ENV}.json"
echo -e "${YELLOW}환경: ${SELECTED_ENV}${NC}"

# 컬렉션 경로 결정 (--로 시작하지 않는 첫 번째 인자)
COLLECTION_PATH=""
for arg in "$@"; do
    if [[ ! "$arg" == --* ]]; then
        COLLECTION_PATH="$arg"
        break
    fi
done

# ========================================
# Setup 실행 (테스트 데이터 초기화)
# ========================================
if [ "$COLLECTION_PATH" == "setup" ]; then
    run_setup
    exit $?
fi

# ========================================
# All 실행 (setup + tier1 + tier2 + tier3)
# ========================================
if [ "$COLLECTION_PATH" == "all" ]; then
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}  전체 테스트 실행 (Setup + Tier 1/2/3)${NC}"
    echo -e "${YELLOW}========================================${NC}"
    
    TOTAL_FAILED=0
    
    # 1. Setup 실행 (--skip-setup 옵션이 없을 때만)
    if [ "$SKIP_SETUP" == "false" ]; then
        run_setup || { echo -e "${RED}Setup 실패 - 테스트 중단${NC}"; exit 1; }
    else
        echo -e "${YELLOW}⏭️  Setup 건너뛰기 (--skip-setup)${NC}"
    fi
    
    # 2. Tier 1 실행 (실패 시 중단)
    echo ""
    $0 tier1 --env=$SELECTED_ENV $VERBOSE || { echo -e "${RED}Tier 1 실패 - 배포 중단${NC}"; exit 1; }
    
    # 3. Tier 2 실행 (실패 시 중단)
    echo ""
    $0 tier2 --env=$SELECTED_ENV $VERBOSE || { echo -e "${RED}Tier 2 실패 - 배포 중단${NC}"; exit 1; }
    
    # 4. Tier 3 실행 (실패해도 경고만)
    echo ""
    $0 tier3 --env=$SELECTED_ENV $VERBOSE || TOTAL_FAILED=1
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    if [ $TOTAL_FAILED -eq 0 ]; then
        echo -e "${GREEN}  전체 테스트 완료 - 배포 가능${NC}"
    else
        echo -e "${YELLOW}  Tier 3 일부 실패 - 배포 가능 (확인 필요)${NC}"
    fi
    echo -e "${GREEN}========================================${NC}"
    exit 0
fi

if [ -z "$COLLECTION_PATH" ] || [ "$COLLECTION_PATH" == "tier1" ]; then
    # Tier 1 모든 테스트 실행
    echo "Tier 1 (Money Integrity) 테스트 실행 중..."
    echo -e "${YELLOW}⚠️  Tier 1 실패 시 배포 중단 정책${NC}"

    COLLECTIONS=$(find "$PROJECT_DIR/postman/collections/tier1_money_integrity" -name "*.json" 2>/dev/null)
    FAILED=0

    for collection in $COLLECTIONS; do
        collection_name=$(basename "$collection" .json)
        echo ""
        echo -e "${YELLOW}>>> $collection_name 실행 중...${NC}"

        MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" \
            -v "$PROJECT_DIR/postman:/etc/newman" \
            postman/newman:alpine run "/etc/newman/collections/tier1_money_integrity/${collection_name}.json" \
            -e "$ENV_FILE" \
            --env-var "base_url=$BASE_URL" \
            --timeout-request $REQUEST_TIMEOUT \
            --delay-request $REQUEST_DELAY \
            --reporters cli $VERBOSE \
            && echo -e "${GREEN}✓ $collection_name 통과${NC}" \
            || { echo -e "${RED}✗ $collection_name 실패${NC}"; FAILED=1; }
    done
    
    if [ $FAILED -eq 1 ]; then
        echo -e "${RED}Tier 1 테스트 실패 - 배포 중단 필요${NC}"
        exit 1
    fi
    exit 0
fi

if [ "$COLLECTION_PATH" == "tier2" ]; then
    # Tier 2 보안 테스트 실행
    echo "Tier 2 (Security) 테스트 실행 중..."
    echo -e "${YELLOW}⚠️  Tier 2 실패 시 배포 중단 정책${NC}"

    COLLECTIONS=$(find "$PROJECT_DIR/postman/collections/tier2_security" -name "*.json" 2>/dev/null)
    FAILED=0

    for collection in $COLLECTIONS; do
        collection_name=$(basename "$collection" .json)
        echo ""
        echo -e "${YELLOW}>>> $collection_name 실행 중...${NC}"

        MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" \
            -v "$PROJECT_DIR/postman:/etc/newman" \
            postman/newman:alpine run "/etc/newman/collections/tier2_security/${collection_name}.json" \
            -e "$ENV_FILE" \
            --env-var "base_url=$BASE_URL" \
            --timeout-request $REQUEST_TIMEOUT \
            --delay-request $REQUEST_DELAY \
            --reporters cli $VERBOSE \
            && echo -e "${GREEN}✓ $collection_name 통과${NC}" \
            || { echo -e "${RED}✗ $collection_name 실패${NC}"; FAILED=1; }
    done
    
    if [ $FAILED -eq 1 ]; then
        echo -e "${RED}Tier 2 테스트 실패 - 배포 중단 필요${NC}"
        exit 1
    fi
    exit 0
fi

if [ "$COLLECTION_PATH" == "tier3" ]; then
    # Tier 3 여정 테스트 실행 (경고만, 배포 중단 X)
    echo "Tier 3 (Journey) 테스트 실행 중..."
    echo -e "${YELLOW}ℹ️  Tier 3 실패는 경고만 (배포 가능)${NC}"

    COLLECTIONS=$(find "$PROJECT_DIR/postman/collections/tier3_journey" -name "*.json" 2>/dev/null)
    FAILED=0

    for collection in $COLLECTIONS; do
        collection_name=$(basename "$collection" .json)
        echo ""
        echo -e "${YELLOW}>>> $collection_name 실행 중...${NC}"

        MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" \
            -v "$PROJECT_DIR/postman:/etc/newman" \
            postman/newman:alpine run "/etc/newman/collections/tier3_journey/${collection_name}.json" \
            -e "$ENV_FILE" \
            --env-var "base_url=$BASE_URL" \
            --timeout-request $REQUEST_TIMEOUT \
            --delay-request $REQUEST_DELAY \
            --reporters cli $VERBOSE \
            && echo -e "${GREEN}✓ $collection_name 통과${NC}" \
            || { echo -e "${YELLOW}⚠ $collection_name 실패 (경고)${NC}"; FAILED=1; }
    done
    
    if [ $FAILED -eq 1 ]; then
        echo -e "${YELLOW}Tier 3 일부 실패 - 배포는 가능하나 확인 필요${NC}"
    fi
    exit 0
fi

# 단일 컬렉션 실행
# 컬렉션 경로 자동 완성
if [[ ! "$COLLECTION_PATH" == *.json ]]; then
    # 파일 이름만 제공된 경우 검색
    FOUND=$(find "$PROJECT_DIR/postman/collections" -name "*${COLLECTION_PATH}*.json" 2>/dev/null | head -1)
    if [ -n "$FOUND" ]; then
        COLLECTION_PATH="$FOUND"
    else
        echo -e "${RED}오류: '$COLLECTION_PATH' 컬렉션을 찾을 수 없습니다.${NC}"
        echo "사용 가능한 테스트: $0 --list"
        exit 1
    fi
fi

# 절대 경로를 컨테이너 내부 경로로 변환
if [[ "$COLLECTION_PATH" == /* ]] || [[ "$COLLECTION_PATH" == *:* ]]; then
    # 절대 경로인 경우 상대 경로로 변환
    COLLECTION_RELATIVE="${COLLECTION_PATH#*postman/}"
    CONTAINER_PATH="/etc/newman/$COLLECTION_RELATIVE"
else
    CONTAINER_PATH="/etc/newman/$COLLECTION_PATH"
fi

echo "컬렉션: $CONTAINER_PATH"
echo "네트워크: $NETWORK"
echo "Base URL: $BASE_URL"
echo "Timeout: ${REQUEST_TIMEOUT}ms | Delay: ${REQUEST_DELAY}ms"
echo ""

# Newman 실행 (timeout, delay 설정 포함)
MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" \
    -v "$PROJECT_DIR/postman:/etc/newman" \
    postman/newman:alpine run "$CONTAINER_PATH" \
    -e "$ENV_FILE" \
    --env-var "base_url=$BASE_URL" \
    --timeout-request $REQUEST_TIMEOUT \
    --delay-request $REQUEST_DELAY \
    --reporters cli $VERBOSE

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  모든 테스트 통과!${NC}"
    echo -e "${GREEN}========================================${NC}"
else
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}  일부 테스트 실패${NC}"
    echo -e "${RED}========================================${NC}"
fi

exit $EXIT_CODE
