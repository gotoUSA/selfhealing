# Self-Healing SaaS 분리 작업 계획서

> ⚠️ **문서 이전 안내**: 이 문서는 페이즈별로 분리되었습니다.
>
> 새 문서 위치: **[docs/self_healing/](./self_healing/README.md)**

---

## 문서 구조

| 문서 | 내용 | 상태 |
|------|------|------|
| **[README.md](./self_healing/README.md)** | 개요 및 인덱스 | 📌 메인 |
| [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md) | 도메인 순수성 감사 + Legacy Residue Purge | 📝 감사 완료 |
| [PHASE_1_IMPORT_GATE.md](./self_healing/PHASE_1_IMPORT_GATE.md) | Import Crash Gate 제거 | ⬜ 미시작 |
| [PHASE_2_TAXONOMY.md](./self_healing/PHASE_2_TAXONOMY.md) | Core Taxonomy 중립화 | ⬜ 미시작 |
| [PHASE_3_REPLAY.md](./self_healing/PHASE_3_REPLAY.md) | Replay/비즈니스 로직 격리 | ✅ 완료 |

---

> **목표**: Self-Healing을 쇼핑/결제/주문 도메인 없이도 import 가능하고 의미가 동일하게 유지되는 "코어 패키지"로 절단한다.
>
> **금지**: 기능 추가, 리팩토링 제안, 네이밍 개선, 구조 재설계, 책임/법률 언급
>
> **허용**: 오직 "도메인 의존 제거"를 위한 최소 변경

---

## 최종 합격 기준

| 기준 | 설명 | 검증 방법 |
|------|------|----------|
| **Empty Host Import PASS** | 쇼핑 앱/Django 없이도 패키지 import 가능 | `python -c "import selfhealing"` |
| **Replay Neutrality PASS** | 코어에 paid/cancelled/order/payment/webhook/toss 의미 판정 로직 없음 | grep 검색 0건 |
| **Taxonomy Neutrality PASS** | 코어 분류(enum/DTO/metrics)가 특정 비즈니스 단어로 고정되지 않음 | 코드 리뷰 |

---

## 작업 범위 요약

| Phase | 작업명 | 예상 파일 수 | 복잡도 | 상태 | 문서 |
|-------|--------|-------------|--------|------|------|
| **Phase 0.5** | 도메인 순수성 감사 + Legacy Purge | ~15개 | 고 | 📝 감사 완료 | [링크](./self_healing/PHASE_0_5_AUDIT.md) |
| **Phase 1** | Import Crash Gate 제거 | ~8개 | 중 | ⬜ 미시작 | [링크](./self_healing/PHASE_1_IMPORT_GATE.md) |
| **Phase 2** | Core Taxonomy 중립화 | ~6개 | 고 | ⬜ 미시작 | [링크](./self_healing/PHASE_2_TAXONOMY.md) |
| **Phase 3** | Replay/비즈니스 로직 격리 | ~3개 | 중 | ✅ 완료 | [링크](./self_healing/PHASE_3_REPLAY.md) |

---

## 2025-12-14 감사 결과 요약 (2차 전체 감사)

> **최종 판정**: ❌ **NO (조건부)** - 즉시 수정 필요 항목 다수 존재
>
> 상세 내용: **[PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md)** 참조

### 감사 요약

| 범주 | 항목 수 |
|------|--------|
| ❌ 즉시 수정 필요 | 24개 |
| ⚠️ 분리 위험 | 15개 |
| ✅ 분리 안전 | 5개 |

---

### ❌ 주요 발견 항목 (즉시 수정 필요)

| 범주 | 발견 항목 | 상세 문서 |
|------|----------|----------|
| **0.5-A** | Enum Alias (PAYMENT, WEBHOOK 등) 12개 | [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md#-05-a-legacy-enum-alias-즉시-제거-필요) |
| **0.5-B** | Property Accessor (order_id, payment_hours 등) 17개 | [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md#-05-b-legacy-property-accessor-즉시-제거-필요) |
| **0.5-C** | Helper 함수 (create_shopping_snapshot_data) 2개 | [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md#-05-c-legacy-helpersnapshot-함수-즉시-제거-필요) |
| **0.5-D** | Payment Interface (PaymentProviderInterface) 1개 파일 | [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md#-05-d-paymentpg-전용-인터페이스-격리-필요) |
| **0.5-E** | Default Vendor (toss, DOMAINS 상수) 5개 | [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md#-05-e-default-providervendor-값-즉시-제거-필요) |
| **Phase 1** | from shopping import (11개 파일) | [PHASE_1_IMPORT_GATE.md](./self_healing/PHASE_1_IMPORT_GATE.md) |
| **추가** | IdempotencyService 도메인 특화 메서드 9개 | [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md#-추가-발견-idempotencyservice-도메인-특화-즉시-제거-필요) |
| **추가** | SecurityViolationService 도메인 특화 6개 | [PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md#-추가-발견-securityviolationservice-도메인-특화-즉시-제거-필요) |

---

## 작업 로그

| 날짜 | Phase | 작업 내용 | 결과 |
|------|-------|----------|------|
| 2025-12-14 | 0.5 | 도메인 순수성 전체 감사 (2차) | ✅ 완료 - 24개 즉시 수정, 15개 위험 항목 발견 |
| 2025-12-14 | 0.5 | 문서 분리 (페이즈별) | ✅ 완료 |
| 2025-12-14 | 3 | ReplayHandler 코어에서 제거 | ✅ 완료 |
| 2025-12-14 | 3 | 비즈니스 판정 로직 제거 | ✅ 완료 |
| 2025-12-14 | 3 | shopping import/delay 제거 | ✅ 완료 |

---

## 최종 검증 스크립트

```bash
#!/bin/bash
# final_verification.sh

cd packages/selfhealing-python

echo "=== Self-Healing SaaS 분리 최종 검증 ==="

echo "1. Empty Host Import Test"
python -c "import selfhealing" && echo "   ✅ PASS" || echo "   ❌ FAIL"

echo "2. Shopping Import Check"
grep -rn --include="*.py" "from shopping\|import shopping" src/ | wc -l

echo "3. Taxonomy Neutrality Check"
grep -rn --include="*.py" "payment_hours\|point_hours" src/ | grep -v "#" | wc -l

echo "4. Replay Neutrality Check"  
grep -rn --include="*.py" "is_paid\|call_toss" src/ | wc -l
```

---

> 📌 **상세 내용은 분리된 문서를 참조하세요:**
> - [docs/self_healing/README.md](./self_healing/README.md)
> - [docs/self_healing/PHASE_0_5_AUDIT.md](./self_healing/PHASE_0_5_AUDIT.md)
> - [docs/self_healing/PHASE_1_IMPORT_GATE.md](./self_healing/PHASE_1_IMPORT_GATE.md)
> - [docs/self_healing/PHASE_2_TAXONOMY.md](./self_healing/PHASE_2_TAXONOMY.md)
> - [docs/self_healing/PHASE_3_REPLAY.md](./self_healing/PHASE_3_REPLAY.md)

---

*문서 생성일: 2025-12-14*
*마지막 수정: 2025-12-14 (문서 분리 완료)*

