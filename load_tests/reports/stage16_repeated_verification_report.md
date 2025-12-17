# Stage 16 - REPEATED Lock Contention Statistical Verification

**테스트 일시**: 2025-12-17T04:44:47 ~ 04:46:32 (UTC)  
**테스트 목적**: Self-Healing 시스템이 반복적인 DB 락 경합을 일관되게 흡수하는지 검증

---

## A) LOCK ITERATION TABLE

| Iter | Lock Acquired | Lock Released | Hold (s) | Blocked Sessions |
|------|---------------|---------------|----------|------------------|
| 1 | 2025-12-17T04:44:47 | 2025-12-17T04:44:52 | 5.14 | **1** ⚡ |
| 2 | 2025-12-17T04:44:54 | 2025-12-17T04:44:59 | 5.12 | 0 |
| 3 | 2025-12-17T04:45:01 | 2025-12-17T04:45:06 | 5.15 | **1** ⚡ |
| 4 | 2025-12-17T04:45:08 | 2025-12-17T04:45:13 | 5.11 | 0 |
| 5 | 2025-12-17T04:45:15 | 2025-12-17T04:45:21 | 5.64 | 0 |
| 6 | 2025-12-17T04:45:23 | 2025-12-17T04:45:28 | 5.13 | 0 |
| 7 | 2025-12-17T04:45:30 | 2025-12-17T04:45:35 | 5.14 | 0 |
| 8 | 2025-12-17T04:45:37 | 2025-12-17T04:45:42 | 5.12 | 0 |
| 9 | 2025-12-17T04:45:44 | 2025-12-17T04:45:49 | 5.12 | 0 |
| 10 | 2025-12-17T04:45:51 | 2025-12-17T04:45:56 | 5.13 | 0 |
| 11 | 2025-12-17T04:45:58 | 2025-12-17T04:46:04 | 5.63 | 0 |
| 12 | 2025-12-17T04:46:06 | 2025-12-17T04:46:11 | 5.13 | 0 |
| 13 | 2025-12-17T04:46:13 | 2025-12-17T04:46:18 | 5.13 | 0 |
| 14 | 2025-12-17T04:46:20 | 2025-12-17T04:46:25 | 5.13 | 0 |
| 15 | 2025-12-17T04:46:27 | 2025-12-17T04:46:32 | 5.13 | 0 |

**Lock Target**: `shopping_product.id=397` (아이폰 15 Pro)

---

## B) AGGREGATED HEALING EVIDENCE

### Lock Contention Statistics

| Metric | Value |
|--------|-------|
| **Total Contention Windows** | 15 |
| **Successful Lock Iterations** | 15/15 (100%) |
| **Windows with Blocked Sessions** | 2 (13.3%) |
| **Total Blocking Events Observed** | 2 |

### DB Monitor Evidence (140초 모니터링)

| Metric | Value |
|--------|-------|
| max_blocked_sessions | 1 |
| max_lock_wait_sessions | 1 |
| total_blocking_events | 1 (DB monitor 관점) |

### Blocking Detail Captured

```json
{
  "blocked_pid": 463,
  "blocked_user": "shopping_user",
  "blocking_pid": 509,
  "blocking_user": "shopping_user",
  "blocked_query": "SELECT \"shopping_carts\"...",
  "blocking_query": "COMMIT",
  "wait_event_type": "Lock",
  "wait_event": "transactionid"
}
```

### Application-Level Response

| Metric | Value |
|--------|-------|
| Total Requests | 15,067 |
| RPS | 167.09 |
| Failure Rate | 24.03% (비즈니스 에러 포함) |
| **Lock Timeout (423)** | **0** |
| **Conflict (409)** | **0** |
| **Server Error (5xx)** | **0** |
| Retry-related responses | 0 |

### Response Time Analysis

| Percentile | Time |
|------------|------|
| P50 | 20ms |
| P95 | 98ms |
| P99 | 310ms |
| Max | 850ms |

### Latency Phase Analysis

| Phase | Avg Response | Requests |
|-------|--------------|----------|
| EARLY | 54ms | 4,386 |
| MID | 23ms | 5,297 |
| LATE | 29ms | 5,343 |
| **Growth Ratio** | **0.53x (감소)** | - |

---

## C) DATA INTEGRITY CHECK

| Check | Result |
|-------|--------|
| **Duplicate Successful Operations** | **0** ✅ |
| **Unique Idempotency Keys Used** | 9,946 |
| **Server Errors (500/502/503)** | **0** ✅ |
| **Max Consecutive Errors** | 6 (bounded) ✅ |
| **Unbounded Retry Loops** | **NONE** ✅ |

---

## D) FINAL VERDICT

### Evidence Summary

1. **15회 반복 락 성공**: 모든 반복에서 락 획득/해제 성공
2. **2회 블로킹 이벤트**: Iteration 1, 3에서 `blocked_sessions=1` 관측
3. **애플리케이션 회복력**: 타임아웃 0, 서버 에러 0, 중복 작업 0
4. **데이터 무결성**: 완벽하게 유지됨

### Contention Window Analysis

- 총 15회 × 5초 = **75초** 동안 락 유지
- 이 중 **2회(13.3%)**에서 실제 블로킹 발생
- 블로킹된 세션들은 모두 **타임아웃 없이 성공적으로 완료**
- 이는 PostgreSQL의 `lock_timeout=5000ms` 내에서 락 획득 성공을 의미

### Why Contention is Limited

1. **테이블 분리**: Cart 작업은 `shopping_cartitem` 테이블, Lock은 `shopping_product` 테이블
2. **주문 실패율**: Order Create가 빈 장바구니로 대부분 실패 → `select_for_update` 미도달
3. **빠른 트랜잭션**: Django 트랜잭션이 매우 빠르게 완료 (ms 단위)

---

## 🎯 FINAL VERDICT (ONE LINE)

> **"Lock contention rarely propagates due to design; Self-Healing minimally exercised"**

---

### 상세 설명

락 경합이 **실제로 발생**했지만 (Iteration 1, 3에서 `blocked_sessions=1`), 다음 이유로 Self-Healing이 **최소한으로만 활성화**되었습니다:

1. 대부분의 Locust 트래픽이 `shopping_product` 테이블을 직접 UPDATE하지 않음
2. Order Create가 비즈니스 검증(빈 장바구니)에서 실패하여 `select_for_update`에 도달하지 못함
3. 블로킹된 세션들이 5초 `lock_timeout` 내에서 빠르게 해결됨

**Self-Healing 시스템은 정상 작동**하고 있으나, 테스트 설계 상 락 경합이 애플리케이션 레벨까지 전파되지 않아 Self-Healing의 **전체 역량**은 검증되지 않았습니다.

---

*Report generated: 2025-12-17T04:47:00 UTC*
