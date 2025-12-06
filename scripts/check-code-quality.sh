#!/bin/bash
# 코드 품질 자동 검증 스크립트
# Claude가 생성한 코드를 검증하는데 사용하세요!

echo "🔍 코드 품질 검사 시작..."
echo ""

# 1. 사용 안 하는 import 제거
echo "📦 1/5: 불필요한 import 제거 중..."
autoflake --remove-all-unused-imports --remove-unused-variables --in-place --recursive shopping/

# 2. import 정렬
echo "📑 2/5: import 정렬 중..."
isort shopping/

# 3. 코드 포맷팅
echo "✨ 3/5: 코드 포맷팅 중..."
black shopping/

# 4. 코드 스타일 검사
echo "🔎 4/5: 코드 스타일 검사 중..."
flake8 shopping/

# 5. 타입 체크 (mypy 설치 시)
if command -v mypy &> /dev/null; then
    echo "🔍 5/5: 타입 체크 중..."
    mypy shopping/ --ignore-missing-imports
else
    echo "⏭️  5/5: mypy 미설치 (선택사항)"
fi

echo ""
echo "✅ 코드 품질 검사 완료!"