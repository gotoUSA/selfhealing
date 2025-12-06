#!/bin/bash

# Postman 테스트를 Newman Docker로 실행하는 스크립트
# 사용법: ./run_postman_tests.sh [collection_path] [환경변수...]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

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
    echo "  $0                                    # tier1 모든 테스트 실행"
    echo "  $0 tier1                              # tier1 모든 테스트 실행"
    echo "  $0 t1_cart_pricechanged               # 특정 테스트 실행"
    echo "  $0 collections/tier1_money_integrity/t1_cart_pricechanged_orderblock.json"
    echo ""
    echo "옵션:"
    echo "  --list    사용 가능한 테스트 목록 표시"
    echo ""
}

# 테스트 목록 표시
list_tests() {
    echo ""
    echo "사용 가능한 테스트 컬렉션:"
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

# 인자 처리
if [ "$1" == "--help" ] || [ "$1" == "-h" ]; then
    usage
    exit 0
fi

if [ "$1" == "--list" ]; then
    list_tests
    exit 0
fi

# 컬렉션 경로 결정
COLLECTION_PATH="$1"

if [ -z "$COLLECTION_PATH" ] || [ "$COLLECTION_PATH" == "tier1" ]; then
    # Tier 1 모든 테스트 실행
    echo "Tier 1 (Money Integrity) 테스트 실행 중..."
    
    COLLECTIONS=$(find "$PROJECT_DIR/postman/collections/tier1_money_integrity" -name "*.json" 2>/dev/null)
    
    for collection in $COLLECTIONS; do
        collection_name=$(basename "$collection" .json)
        echo ""
        echo -e "${YELLOW}>>> $collection_name 실행 중...${NC}"
        
        MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" \
            -v "$PROJECT_DIR/postman:/etc/newman" \
            postman/newman:alpine run "/etc/newman/collections/tier1_money_integrity/${collection_name}.json" \
            -e "$ENV_FILE" \
            --env-var "base_url=$BASE_URL" \
            --reporters cli \
            && echo -e "${GREEN}✓ $collection_name 통과${NC}" \
            || echo -e "${RED}✗ $collection_name 실패${NC}"
    done
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
echo ""

# Newman 실행
MSYS_NO_PATHCONV=1 docker run --rm --network "$NETWORK" \
    -v "$PROJECT_DIR/postman:/etc/newman" \
    postman/newman:alpine run "$CONTAINER_PATH" \
    -e "$ENV_FILE" \
    --env-var "base_url=$BASE_URL" \
    --reporters cli

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
