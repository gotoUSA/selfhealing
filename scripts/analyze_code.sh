#!/bin/bash
# ===========================================
# 코드 분석 도구 통합 실행 스크립트
# ===========================================
set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 리포트 디렉토리
REPORT_DIR="reports/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$REPORT_DIR"

echo -e "${BLUE}=================================="
echo "   코드 분석 도구 실행"
echo -e "==================================${NC}"
echo ""
echo -e "리포트 저장 위치: ${GREEN}$REPORT_DIR${NC}"
echo ""

# 1. 코드 통계
echo -e "${YELLOW}[1/7] 코드 통계 (pygount)...${NC}"
pygount --format=summary shopping/ --folders-to-skip="migrations,__pycache__" | tee "$REPORT_DIR/code_stats.txt"
echo ""

# 2. 복잡도 분석
echo -e "${YELLOW}[2/7] 복잡도 분석 (radon cc)...${NC}"
radon cc shopping -a -s --min B 2>/dev/null | tee "$REPORT_DIR/complexity.txt"
echo ""

# 3. 유지보수성 분석
echo -e "${YELLOW}[3/7] 유지보수성 분석 (radon mi)...${NC}"
radon mi shopping -s --min B 2>/dev/null | tee "$REPORT_DIR/maintainability.txt"
echo ""

# 4. 보안 분석
echo -e "${YELLOW}[4/7] 보안 분석 (bandit)...${NC}"
bandit -r shopping -x shopping/tests -f html -o "$REPORT_DIR/security.html" 2>/dev/null || true
bandit -r shopping -x shopping/tests -f txt 2>/dev/null | head -50
echo -e "   → ${GREEN}$REPORT_DIR/security.html${NC} 생성됨"
echo ""

# 5. 죽은 코드 탐지
echo -e "${YELLOW}[5/7] 죽은 코드 탐지 (vulture)...${NC}"
vulture shopping --min-confidence 90 2>/dev/null | tee "$REPORT_DIR/dead_code.txt" | head -20
echo ""

# 6. 의존성 충돌 검사
echo -e "${YELLOW}[6/7] 의존성 충돌 검사 (pipdeptree)...${NC}"
if pipdeptree --warn fail 2>&1 | grep -q "Warning"; then
    echo -e "${RED}⚠ 의존성 충돌 감지됨${NC}"
    pipdeptree --warn fail 2>&1 | grep "Warning" | tee "$REPORT_DIR/dep_conflicts.txt"
else
    echo -e "${GREEN}✓ 의존성 충돌 없음${NC}"
fi
echo ""

# 7. 의존성 보안 검사
echo -e "${YELLOW}[7/7] 의존성 보안 검사 (safety)...${NC}"
safety check --output text 2>/dev/null | tee "$REPORT_DIR/safety.txt" | head -30 || true
echo ""

echo -e "${BLUE}=================================="
echo -e "   분석 완료!"
echo -e "==================================${NC}"
echo ""
echo -e "리포트 위치: ${GREEN}$REPORT_DIR${NC}"
echo ""
echo "생성된 파일들:"
ls -la "$REPORT_DIR"
