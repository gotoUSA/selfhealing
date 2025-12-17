#!/usr/bin/env python3
"""
Stage 16 - REPEATED Row Lock Holder for Statistical Verification

반복적으로 락을 획득/해제하여 Self-Healing 시스템의 일관된 대응을 검증합니다.

Usage:
    python repeated_lock_holder.py

Environment Variables:
    PGHOST              - PostgreSQL 호스트 (default: db)
    PGPORT              - PostgreSQL 포트 (default: 5432)
    PGDATABASE          - 데이터베이스 이름 (default: shopping_db)
    PGUSER              - 데이터베이스 사용자 (default: shopping_user)
    PGPASSWORD          - 데이터베이스 비밀번호 (default: shopping_pass)
    PRODUCT_TABLE       - 상품 테이블 이름 (default: shopping_product)
    TARGET_PRODUCT_ID   - 락을 잡을 상품 ID (0=auto-discover)
    LOCK_ITERATIONS     - 반복 횟수 (default: 15)
    LOCK_HOLD_SECONDS   - 각 락 유지 시간 (default: 5)
    COOLDOWN_SECONDS    - 락 해제 후 대기 시간 (default: 2)
    STARTUP_DELAY_SECONDS - 시작 전 대기 시간 (default: 10)
"""

import os
import sys
import time
import json
import logging
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Optional

try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 not installed. pip install psycopg2-binary")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


@dataclass
class LockIterationResult:
    """단일 락 반복 결과"""
    iteration: int
    lock_acquired_at: Optional[str] = None
    lock_released_at: Optional[str] = None
    hold_duration_seconds: float = 0.0
    success: bool = False
    error: Optional[str] = None
    blocked_sessions_observed: int = 0
    lock_wait_sessions_observed: int = 0


@dataclass
class RepeatedLockResults:
    """반복 락 테스트 전체 결과"""
    target_product_id: int = 0
    target_product_name: str = ""
    total_iterations: int = 0
    successful_iterations: int = 0
    failed_iterations: int = 0
    iterations: List[LockIterationResult] = field(default_factory=list)
    test_start_time: Optional[str] = None
    test_end_time: Optional[str] = None
    total_blocked_events: int = 0


def get_env_config():
    """환경 변수에서 설정 로드"""
    return {
        'host': os.environ.get('PGHOST', 'db'),
        'port': int(os.environ.get('PGPORT', '5432')),
        'database': os.environ.get('PGDATABASE', 'shopping_db'),
        'user': os.environ.get('PGUSER', 'shopping_user'),
        'password': os.environ.get('PGPASSWORD', 'shopping_pass'),
        'product_table': os.environ.get('PRODUCT_TABLE', 'shopping_product'),
        'target_product_id': int(os.environ.get('TARGET_PRODUCT_ID', '0')),
        'lock_iterations': int(os.environ.get('LOCK_ITERATIONS', '15')),
        'lock_hold_seconds': int(os.environ.get('LOCK_HOLD_SECONDS', '5')),
        'cooldown_seconds': int(os.environ.get('COOLDOWN_SECONDS', '2')),
        'startup_delay_seconds': int(os.environ.get('STARTUP_DELAY_SECONDS', '10')),
    }


def wait_for_db_ready(config, max_retries=30, retry_interval=2):
    """DB 준비 대기"""
    logger.info(f"Waiting for database at {config['host']}:{config['port']}...")
    
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                host=config['host'],
                port=config['port'],
                database=config['database'],
                user=config['user'],
                password=config['password'],
                connect_timeout=5
            )
            conn.close()
            logger.info("Database is ready!")
            return True
        except psycopg2.OperationalError as e:
            logger.warning(f"Attempt {attempt + 1}/{max_retries}: {e}")
            time.sleep(retry_interval)
    
    logger.error("Database failed to become ready")
    return False


def discover_target_product(config) -> tuple:
    """첫 번째 상품 ID 자동 탐색"""
    conn = psycopg2.connect(
        host=config['host'],
        port=config['port'],
        database=config['database'],
        user=config['user'],
        password=config['password']
    )
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, name FROM {config['product_table']} ORDER BY id LIMIT 1")
        row = cursor.fetchone()
        if row:
            return row[0], row[1]
        return None, None
    finally:
        conn.close()


def validate_target_exists(config, target_id: int) -> tuple:
    """대상 상품 존재 확인"""
    conn = psycopg2.connect(
        host=config['host'],
        port=config['port'],
        database=config['database'],
        user=config['user'],
        password=config['password']
    )
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, name FROM {config['product_table']} WHERE id = %s", (target_id,))
        row = cursor.fetchone()
        if row:
            return row[0], row[1]
        return None, None
    finally:
        conn.close()


def check_blocked_sessions(config) -> tuple:
    """현재 블로킹된 세션 수 확인"""
    conn = psycopg2.connect(
        host=config['host'],
        port=config['port'],
        database=config['database'],
        user=config['user'],
        password=config['password']
    )
    try:
        cursor = conn.cursor()
        
        # blocked_sessions: 다른 세션의 락을 대기 중인 세션 수
        cursor.execute("""
            SELECT COUNT(*) 
            FROM pg_stat_activity 
            WHERE wait_event_type = 'Lock' 
              AND state = 'active'
              AND pid != pg_backend_pid()
        """)
        blocked = cursor.fetchone()[0]
        
        # lock_wait: 락 대기 중인 세션 수 (pg_locks 기준)
        cursor.execute("""
            SELECT COUNT(*) 
            FROM pg_locks 
            WHERE NOT granted
        """)
        lock_wait = cursor.fetchone()[0]
        
        return blocked, lock_wait
    finally:
        conn.close()


def execute_single_lock_iteration(config, iteration: int, target_id: int) -> LockIterationResult:
    """단일 락 반복 실행"""
    result = LockIterationResult(iteration=iteration)
    hold_seconds = config['lock_hold_seconds']
    
    conn = None
    try:
        conn = psycopg2.connect(
            host=config['host'],
            port=config['port'],
            database=config['database'],
            user=config['user'],
            password=config['password']
        )
        conn.autocommit = False
        cursor = conn.cursor()
        
        # 락 타임아웃 무제한
        cursor.execute("SET lock_timeout = '0'")
        
        # SELECT FOR UPDATE 실행
        cursor.execute(
            f"SELECT id, name FROM {config['product_table']} WHERE id = %s FOR UPDATE",
            (target_id,)
        )
        
        row = cursor.fetchone()
        if not row:
            result.error = f"Row id={target_id} not found"
            return result
        
        result.lock_acquired_at = datetime.now().isoformat()
        logger.info(f"  [Iter {iteration:02d}] 🔒 LOCK ACQUIRED at {result.lock_acquired_at}")
        
        # 락 유지 중 blocked sessions 샘플링
        max_blocked = 0
        max_lock_wait = 0
        sample_count = hold_seconds * 2  # 0.5초 간격 샘플링
        
        for i in range(sample_count):
            time.sleep(0.5)
            blocked, lock_wait = check_blocked_sessions(config)
            max_blocked = max(max_blocked, blocked)
            max_lock_wait = max(max_lock_wait, lock_wait)
            
            if blocked > 0 or lock_wait > 0:
                logger.info(f"  [Iter {iteration:02d}]    ⚡ Contention detected: blocked={blocked}, lock_wait={lock_wait}")
        
        result.blocked_sessions_observed = max_blocked
        result.lock_wait_sessions_observed = max_lock_wait
        
        # 롤백으로 락 해제
        conn.rollback()
        result.lock_released_at = datetime.now().isoformat()
        result.success = True
        
        # 락 유지 시간 계산
        acquired = datetime.fromisoformat(result.lock_acquired_at)
        released = datetime.fromisoformat(result.lock_released_at)
        result.hold_duration_seconds = (released - acquired).total_seconds()
        
        logger.info(f"  [Iter {iteration:02d}] 🔓 LOCK RELEASED at {result.lock_released_at} (held {result.hold_duration_seconds:.2f}s, blocked_max={max_blocked})")
        
    except psycopg2.Error as e:
        result.error = str(e)
        logger.error(f"  [Iter {iteration:02d}] ❌ FAILED: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass
    
    return result


def run_repeated_lock_test(config) -> RepeatedLockResults:
    """반복 락 테스트 실행"""
    results = RepeatedLockResults()
    results.test_start_time = datetime.now().isoformat()
    
    product_table = config['product_table']
    target_id = config['target_product_id']
    iterations = config['lock_iterations']
    hold_seconds = config['lock_hold_seconds']
    cooldown_seconds = config['cooldown_seconds']
    
    # 1. TARGET_PRODUCT_ID 결정
    if target_id == 0:
        logger.info("AUTO_DISCOVER: Finding first available product ID...")
        target_id, product_name = discover_target_product(config)
        if not target_id:
            logger.error("AUTO_DISCOVER: No products found!")
            sys.exit(1)
        logger.info(f"AUTO_DISCOVER: Found product id={target_id}, name={product_name}")
    else:
        target_id, product_name = validate_target_exists(config, target_id)
        if not target_id:
            logger.error(f"FAIL: Target product id={config['target_product_id']} not found!")
            sys.exit(1)
    
    results.target_product_id = target_id
    results.target_product_name = product_name or ""
    results.total_iterations = iterations
    
    logger.info("=" * 70)
    logger.info("STAGE 16 - REPEATED LOCK HOLDER FOR STATISTICAL VERIFICATION")
    logger.info("=" * 70)
    logger.info(f"Target table: {product_table}")
    logger.info(f"Target product ID: {target_id}")
    logger.info(f"Target product name: {product_name}")
    logger.info(f"Iterations: {iterations}")
    logger.info(f"Lock hold per iteration: {hold_seconds} seconds")
    logger.info(f"Cooldown between iterations: {cooldown_seconds} seconds")
    logger.info(f"Total test duration: ~{iterations * (hold_seconds + cooldown_seconds)} seconds")
    logger.info("=" * 70)
    
    # 2. 반복 실행
    for i in range(1, iterations + 1):
        logger.info(f"\n--- ITERATION {i}/{iterations} ---")
        
        result = execute_single_lock_iteration(config, i, target_id)
        results.iterations.append(result)
        
        if result.success:
            results.successful_iterations += 1
            if result.blocked_sessions_observed > 0:
                results.total_blocked_events += 1
        else:
            results.failed_iterations += 1
            logger.error(f"ITERATION {i} FAILED - ABORTING TEST")
            break
        
        # 쿨다운 (마지막 반복 제외)
        if i < iterations:
            logger.info(f"  Cooldown: {cooldown_seconds}s...")
            time.sleep(cooldown_seconds)
    
    results.test_end_time = datetime.now().isoformat()
    return results


def print_final_report(results: RepeatedLockResults):
    """최종 보고서 출력"""
    print("\n")
    print("=" * 80)
    print("REPEATED LOCK TEST - FINAL REPORT")
    print("=" * 80)
    
    # A) Lock Iteration Table
    print("\nA) LOCK ITERATION TABLE")
    print("-" * 80)
    print(f"{'Iter':>4} | {'Lock Acquired':^26} | {'Lock Released':^26} | {'Blocked':>7}")
    print("-" * 80)
    
    for it in results.iterations:
        acquired = it.lock_acquired_at[:19] if it.lock_acquired_at else "N/A"
        released = it.lock_released_at[:19] if it.lock_released_at else "N/A"
        blocked = it.blocked_sessions_observed
        status = "✓" if it.success else "✗"
        print(f"{it.iteration:>4} | {acquired:^26} | {released:^26} | {blocked:>7} {status}")
    
    print("-" * 80)
    
    # B) Aggregated Evidence
    print("\nB) AGGREGATED EVIDENCE")
    print("-" * 80)
    windows_with_blocked = sum(1 for it in results.iterations if it.blocked_sessions_observed > 0)
    print(f"   Total contention windows:        {results.total_iterations}")
    print(f"   Successful iterations:           {results.successful_iterations}")
    print(f"   Failed iterations:               {results.failed_iterations}")
    print(f"   Windows with blocked sessions:   {windows_with_blocked}")
    print(f"   Total blocked events observed:   {results.total_blocked_events}")
    
    # C) Summary
    print("\nC) TARGET VALIDATION")
    print("-" * 80)
    print(f"   Target Product ID:   {results.target_product_id}")
    print(f"   Target Product Name: {results.target_product_name}")
    print(f"   Test Start:          {results.test_start_time}")
    print(f"   Test End:            {results.test_end_time}")
    
    print("\n" + "=" * 80)
    
    # JSON 출력 (파싱용)
    print("\n--- LOCK_RESULTS_JSON_START ---")
    output = {
        "target_product_id": results.target_product_id,
        "target_product_name": results.target_product_name,
        "total_iterations": results.total_iterations,
        "successful_iterations": results.successful_iterations,
        "failed_iterations": results.failed_iterations,
        "windows_with_blocked_sessions": windows_with_blocked,
        "total_blocked_events": results.total_blocked_events,
        "test_start_time": results.test_start_time,
        "test_end_time": results.test_end_time,
        "iterations": [asdict(it) for it in results.iterations]
    }
    print(json.dumps(output, indent=2))
    print("--- LOCK_RESULTS_JSON_END ---")


def main():
    config = get_env_config()
    
    # 시작 전 대기
    startup_delay = config['startup_delay_seconds']
    if startup_delay > 0:
        logger.info(f"Waiting {startup_delay} seconds before starting...")
        time.sleep(startup_delay)
    
    # DB 준비 대기
    if not wait_for_db_ready(config):
        logger.error("Failed to connect to database")
        sys.exit(1)
    
    # 반복 락 테스트 실행
    results = run_repeated_lock_test(config)
    
    # 최종 보고서 출력
    print_final_report(results)
    
    # 실패 시 종료 코드 1
    if results.failed_iterations > 0:
        sys.exit(1)
    
    logger.info("Repeated lock holder completed successfully.")


if __name__ == "__main__":
    main()
