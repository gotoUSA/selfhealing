# Stage 16: DB Lock / Deadlock Recovery Test

📅 **Generated**: 2025-12-30 17:44:11  
🏷️ **Stage**: stage16  
📝 **Schema Version**: 1.0.0

---

## 📋 Executive Summary

| Metric | Value |
|--------|-------|
| Test Duration | 180s |
| Max Users | 300 |
| Min Users | 150 |
| Total Requests | 3,926 |
| Total Errors | 1,128 |
| Error Rate | 28.73% |
| Throughput | 21.78 RPS |



## ⏱️ Response Time (ms)

| Percentile | Value |
|------------|-------|
| Average | 55.44 |
| Min | 0.00 |
| Max | 1791.13 |
| P50 | 43.64 |
| P95 | 126.69 |
| P99 | 706.43 |


## 🎯 Result

**✅ PASSED**

## 🔌 Circuit Breaker Analysis

| Metric | Value |
|--------|-------|
| Open Count | 0 |
| Close Count | 0 |
| Half-Open Count | 0 |
| Recovery Latency | N/A |
| Services Affected | None |


## 🚨 Emergency Mode Analysis

| Metric | Value |
|--------|-------|
| Triggered Count | 0 |
| Released Count | 0 |
| Max Level Reached | LEVEL_0 |


## 💰 Error Budget Analysis

| Metric | Value |
|--------|-------|
| Initial Remaining | N/A |
| Min Remaining | N/A |
| Exhausted | ❌ No |
| Recovered | ❌ No |


## 📥 DLQ (Dead Letter Queue) Analysis

| Metric | Value |
|--------|-------|
| Max Count | 0 |
| Replay Success | 0 |
| Replay Fail | 0 |


## 💥 Chaos & Kill Switch Analysis

### Chaos Injection
| Metric | Value |
|--------|-------|
| Failures Injected | 0 |
| CB Triggers | 0 |
| Recovery Triggers | 0 |

### Kill Switch
| Metric | Value |
|--------|-------|
| Activated Count | 0 |
| Deactivated Count | 0 |
| Targets | None |


## 🔄 Recovery Analysis

| Metric | Value |
|--------|-------|
| Recovery Latency | 180.3s |
| SLA (< 2min) | ✅ PASSED |

