#!/bin/bash
# ===========================================
# Mutation Testing 실행 스크립트
# 돈 관련 서비스 (결제, 포인트, 주문 등)
# ===========================================
set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=================================="
echo "   Mutation Testing (돈 관련 서비스)"
echo -e "==================================${NC}"
echo ""

# Docker 상태 확인
echo -e "${YELLOW}Docker 상태 확인...${NC}"
if ! docker-compose ps db 2>/dev/null | grep -q "Up"; then
    echo -e "${RED}⚠ PostgreSQL 컨테이너가 실행 중이지 않습니다.${NC}"
    echo "다음 명령어로 시작하세요: docker-compose up -d db redis"
    exit 1
fi
echo -e "${GREEN}✓ PostgreSQL 실행 중${NC}"
echo ""

# 대상 파일 표시
echo -e "${YELLOW}대상 파일:${NC}"
echo "  - shopping/services/payment_service.py (결제)"
echo "  - shopping/services/point_service.py (포인트)"
echo "  - shopping/services/cart_service.py (장바구니)"
echo "  - shopping/services/order_service.py (주문)"
echo "  - shopping/services/return_service.py (반품/환불)"
echo "  - shopping/services/toss_webhook_service.py (토스 웹훅)"
echo ""

# 이전 캐시 정리
echo -e "${YELLOW}이전 캐시 정리...${NC}"
rm -f .mutmut-cache 2>/dev/null || true
echo ""

# 선택 메뉴
echo -e "${BLUE}실행할 테스트를 선택하세요:${NC}"
echo "  1) 전체 실행 (시간 오래 걸림)"
echo "  2) 결제 서비스만"
echo "  3) 포인트 서비스만"
echo "  4) 장바구니 서비스만"
echo "  5) 주문 서비스만"
echo ""
read -p "선택 (1-5): " choice

case $choice in
    1)
        echo -e "${YELLOW}전체 서비스 Mutation Testing 시작...${NC}"
        PYTHONIOENCODING=utf-8 mutmut run
        ;;
    2)
        echo -e "${YELLOW}결제 서비스 Mutation Testing 시작...${NC}"
        PYTHONIOENCODING=utf-8 mutmut run \
            --paths-to-mutate=shopping/services/payment_service.py \
            --runner="python -m pytest shopping/tests/unit/services/test_payment_service.py shopping/tests/unit/test_edge_cases_payment.py -x --tb=short -q -c pytest_mutmut.ini"
        ;;
    3)
        echo -e "${YELLOW}포인트 서비스 Mutation Testing 시작...${NC}"
        PYTHONIOENCODING=utf-8 mutmut run \
            --paths-to-mutate=shopping/services/point_service.py \
            --runner="python -m pytest shopping/tests/unit/services/test_point_service_basic.py shopping/tests/unit/services/test_point_service_fifo.py shopping/tests/unit/test_edge_cases_point.py -x --tb=short -q -c pytest_mutmut.ini"
        ;;
    4)
        echo -e "${YELLOW}장바구니 서비스 Mutation Testing 시작...${NC}"
        PYTHONIOENCODING=utf-8 mutmut run \
            --paths-to-mutate=shopping/services/cart_service.py \
            --runner="python -m pytest shopping/tests/unit/services/test_cart_service.py shopping/tests/unit/test_edge_cases_cart_stock.py -x --tb=short -q -c pytest_mutmut.ini"
        ;;
    5)
        echo -e "${YELLOW}주문 서비스 Mutation Testing 시작...${NC}"
        PYTHONIOENCODING=utf-8 mutmut run \
            --paths-to-mutate=shopping/services/order_service.py \
            --runner="python -m pytest shopping/tests/unit/services/test_order_service.py -x --tb=short -q -c pytest_mutmut.ini"
        ;;
    *)
        echo -e "${RED}잘못된 선택입니다.${NC}"
        exit 1
        ;;
esac

# 결과 출력
echo ""
echo -e "${BLUE}=================================="
echo "   결과 요약"
echo -e "==================================${NC}"
mutmut results

# HTML 리포트 생성
echo ""
echo -e "${YELLOW}HTML 리포트 생성 중...${NC}"
mutmut html 2>/dev/null || true
echo -e "${GREEN}✓ HTML 리포트: html/index.html${NC}"

echo ""
echo -e "${BLUE}=================================="
echo "   완료!"
echo -e "==================================${NC}"
echo ""
echo "생존한 뮤턴트 상세 보기: mutmut show <id>"
echo "특정 뮤턴트 적용 테스트: mutmut apply <id>"
