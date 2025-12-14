# Self-Healing SaaS 분리 작업 문서

> **목표**: Self-Healing을 쇼핑/결제/주문 도메인 없이도 import 가능하고 의미가 동일하게 유지되는 "코어 패키지"로 절단한다.
>
> **금지**: 기능 추가, 리팩토링 제안, 네이밍 개선, 구조 재설계, 책임/법률 언급
>
> **허용**: 오직 "도메인 의존 제거"를 위한 최소 변경

---

## 문서 구조

| 문서 | 내용 | 상태 |
|------|------|------|
| [PHASE_0_5_AUDIT.md](./PHASE_0_5_AUDIT.md) | 도메인 순수성 감사 결과 및 Legacy Residue Purge 계획 | 📝 감사 완료 |
| [PHASE_1_IMPORT_GATE.md](./PHASE_1_IMPORT_GATE.md) | Import Crash Gate 제거 (즉사 포인트) | ⬜ 미시작 |
| [PHASE_2_TAXONOMY.md](./PHASE_2_TAXONOMY.md) | Core Taxonomy 중립화 | ⬜ 미시작 |
| [PHASE_3_REPLAY.md](./PHASE_3_REPLAY.md) | Replay/비즈니스 로직 격리 | ✅ 완료 |

---

## 최종 합격 기준

| 기준 | 설명 | 검증 방법 |
|------|------|----------|
| **Empty Host Import PASS** | 쇼핑 앱/Django 없이도 패키지 import 가능 | `python -c "import selfhealing"` |
| **Replay Neutrality PASS** | 코어에 paid/cancelled/order/payment/webhook/toss 의미 판정 로직 없음 | grep 검색 0건 |
| **Taxonomy Neutrality PASS** | 코어 분류(enum/DTO/metrics)가 특정 비즈니스 단어로 고정되지 않음 | 코드 리뷰 |

---

## 작업 범위 요약

| Phase | 작업명 | 예상 파일 수 | 복잡도 | 상태 |
|-------|--------|-------------|--------|------|
| **Phase 0.5** | 도메인 순수성 감사 + Legacy Residue Purge | ~18개 | 고 | 📝 감사 완료 |
| **Phase 1** | Import Crash Gate 제거 | ~12개 | 중 | ⬜ 미시작 |
| **Phase 2** | Core Taxonomy 중립화 | ~6개 | 고 | ⬜ 미시작 |
| **Phase 3** | Replay/비즈니스 로직 격리 | ~3개 | 중 | ✅ 완료 |

---

## 실행 순서 및 배치

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 0.5: Legacy Domain Residue Purge                     │
│  ├─ 0.5-A: Enum Alias 완전 제거                             │
│  ├─ 0.5-B: Legacy Property Accessor 제거                    │
│  ├─ 0.5-C: Legacy Helper/Snapshot 함수 제거                 │
│  ├─ 0.5-D: Payment/PG 전용 인터페이스 격리                  │
│  └─ 0.5-E: Default Provider/Vendor 값 제거                  │
│                                                             │
│  예상 소요: 2시간 | 위험도: 중                              │
├─────────────────────────────────────────────────────────────┤
│  Phase 1: Import Crash Gate 제거                            │
│  ├─ 1-1: shopping import 제거                               │
│  ├─ 1-2: Django 직접 의존 제거                              │
│  └─ 1-3: 코어→어댑터 역방향 import 없음 확인                │
│                                                             │
│  예상 소요: 30분 | 위험도: 낮음                             │
│  ✅ 체크: python -c "import selfhealing"                    │
├─────────────────────────────────────────────────────────────┤
│  Phase 2: Core Taxonomy 중립화                              │
│  ├─ 2-1: ViolationType 중립화                               │
│  ├─ 2-2: FailedOperationDomain 중립화                       │
│  ├─ 2-3: DTO 필드 일반화                                    │
│  ├─ 2-4: Metrics DOMAINS 동적화                             │
│  └─ 2-5: SLA 필드명 중립화                                  │
│                                                             │
│  예상 소요: 1시간 | 위험도: 중간                            │
├─────────────────────────────────────────────────────────────┤
│  Phase 3: Replay/비즈니스 로직 격리 (완료)                  │
│  ├─ 3-1: ReplayHandler 어댑터 이동                          │
│  ├─ 3-2: 비즈니스 판정 로직 제거                            │
│  └─ 3-3: PG/API 호출 제거                                   │
│                                                             │
│  ✅ 완료                                                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 최종 검증 스크립트

```bash
#!/bin/bash
# final_verification.sh

echo "=== Self-Healing SaaS 분리 최종 검증 ==="

cd packages/selfhealing-python

echo ""
echo "1. Empty Host Import Test"
python -c "import selfhealing" 2>&1
if [ $? -eq 0 ]; then
    echo "   ✅ PASS"
else
    echo "   ❌ FAIL"
fi

echo ""
echo "2. Shopping Import Check"
COUNT=$(grep -rn --include="*.py" "from shopping\|import shopping" src/ | wc -l)
if [ "$COUNT" -eq 0 ]; then
    echo "   ✅ PASS (shopping import: 0건)"
else
    echo "   ❌ FAIL (shopping import: ${COUNT}건)"
fi

echo ""
echo "3. Taxonomy Neutrality Check"
COUNT=$(grep -rn --include="*.py" "payment_hours\|point_hours\|inventory_hours" src/ | grep -v "#" | wc -l)
if [ "$COUNT" -eq 0 ]; then
    echo "   ✅ PASS (도메인 특화 필드: 0건)"
else
    echo "   ❌ FAIL (도메인 특화 필드: ${COUNT}건)"
fi

echo ""
echo "4. Replay Neutrality Check"
COUNT=$(grep -rn --include="*.py" "is_paid\|\.status.*==.*cancelled\|call_toss" src/ | wc -l)
if [ "$COUNT" -eq 0 ]; then
    echo "   ✅ PASS (비즈니스 로직: 0건)"
else
    echo "   ❌ FAIL (비즈니스 로직: ${COUNT}건)"
fi

echo ""
echo "=== 검증 완료 ==="
```

---

## 작업 로그

| 날짜 | Phase | 작업 내용 | 결과 |
|------|-------|----------|------|
| 2025-12-14 | 0.5 | 도메인 순수성 전체 감사 (2차) | ✅ 완료 - 24개 즉시 수정, 15개 위험 항목 발견 |
| 2025-12-14 | 0.5 | Legacy Residue Purge 계획 수립 | ✅ 완료 |
| 2025-12-14 | 3 | ReplayHandler 코어에서 제거 | ✅ 완료 |
| 2025-12-14 | 3 | 비즈니스 판정 로직 제거 | ✅ 완료 |
| 2025-12-14 | 3 | shopping import/delay 제거 | ✅ 완료 |

---

*문서 생성일: 2025-12-14*
*마지막 수정: 2025-12-14*
