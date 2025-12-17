#!/usr/bin/env python3
"""
Stage 16 - External Row Lock Holder

PostgreSQL에 연결하여 특정 행의 락을 잡고 유지하는 헬퍼 스크립트.
Self-Healing 시스템의 락 경합 처리 능력을 테스트하기 위해 사용됩니다.

Usage:
    python hold_row_lock.py

Environment Variables:
    PGHOST          - PostgreSQL 호스트 (default: db)
    PGPORT          - PostgreSQL 포트 (default: 5432)
    PGDATABASE      - 데이터베이스 이름 (default: shopping_db)
    PGUSER          - 데이터베이스 사용자 (default: shopping_user)
    PGPASSWORD      - 데이터베이스 비밀번호 (default: shopping_pass)
    PRODUCT_TABLE   - 상품 테이블 이름 (default: shopping_product)
    TARGET_PRODUCT_ID - 락을 잡을 상품 ID (default: 1)
    LOCK_HOLD_SECONDS - 락 유지 시간 (default: 30)
    STARTUP_DELAY_SECONDS - 시작 전 대기 시간 (default: 5)
"""

import os
import sys
import time
import logging
from datetime import datetime

# psycopg2 import
try:
    import psycopg2
except ImportError:
    print("ERROR: psycopg2 is not installed. Install it with: pip install psycopg2-binary")
    sys.exit(1)

# Logging 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def get_env_config():
    """환경 변수에서 설정 로드"""
    return {
        'host': os.environ.get('PGHOST', 'db'),
        'port': int(os.environ.get('PGPORT', '5432')),
        'database': os.environ.get('PGDATABASE', 'shopping_db'),
        'user': os.environ.get('PGUSER', 'shopping_user'),
        'password': os.environ.get('PGPASSWORD', 'shopping_pass'),
        'product_table': os.environ.get('PRODUCT_TABLE', 'shopping_product'),
        'target_product_id': int(os.environ.get('TARGET_PRODUCT_ID', '1')),
        'lock_hold_seconds': int(os.environ.get('LOCK_HOLD_SECONDS', '30')),
        'startup_delay_seconds': int(os.environ.get('STARTUP_DELAY_SECONDS', '5')),
    }


def wait_for_db_ready(config, max_retries=30, retry_interval=2):
    """DB가 준비될 때까지 대기"""
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
            logger.warning(f"Attempt {attempt + 1}/{max_retries}: Database not ready yet - {e}")
            time.sleep(retry_interval)
    
    logger.error("Database failed to become ready within the timeout period")
    return False


def hold_row_lock(config):
    """
    지정된 행의 락을 잡고 유지합니다.
    
    이 함수는:
    1. 트랜잭션 시작
    2. SELECT ... FOR UPDATE로 행 락 획득
    3. 지정된 시간 동안 락 유지 (커밋하지 않음)
    4. 롤백 후 종료
    """
    product_table = config['product_table']
    target_id = config['target_product_id']
    hold_seconds = config['lock_hold_seconds']
    
    logger.info("=" * 60)
    logger.info("STAGE 16 - EXTERNAL ROW LOCK HOLDER")
    logger.info("=" * 60)
    logger.info(f"Target table: {product_table}")
    logger.info(f"Target product ID: {target_id}")
    logger.info(f"Lock hold duration: {hold_seconds} seconds")
    logger.info("=" * 60)
    
    conn = None
    cursor = None
    lock_acquired_at = None
    lock_released_at = None
    
    try:
        # 데이터베이스 연결
        logger.info("Connecting to database...")
        conn = psycopg2.connect(
            host=config['host'],
            port=config['port'],
            database=config['database'],
            user=config['user'],
            password=config['password']
        )
        
        # 자동 커밋 비활성화 (트랜잭션 모드)
        conn.autocommit = False
        cursor = conn.cursor()
        
        # 락 타임아웃 설정 (이 세션은 무제한 대기)
        cursor.execute("SET lock_timeout = '0'")
        
        # 트랜잭션 격리 수준 설정
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        
        # AUTO_DISCOVER: 지정된 ID가 없거나 0이면 자동으로 첫 번째 상품 찾기
        if target_id == 0:
            logger.info("AUTO_DISCOVER: Finding first available product ID...")
            cursor.execute(f"SELECT id, name FROM {product_table} ORDER BY id LIMIT 1")
            first_row = cursor.fetchone()
            if first_row:
                target_id = first_row[0]
                logger.info(f"AUTO_DISCOVER: Found product id={target_id}, name={first_row[1]}")
            else:
                logger.error("AUTO_DISCOVER: No products found in table!")
                sys.exit(1)
        
        logger.info(f"Acquiring exclusive lock on {product_table} (id={target_id})...")
        
        # SELECT FOR UPDATE로 행 락 획득
        lock_query = f"SELECT id, name FROM {product_table} WHERE id = %s FOR UPDATE"
        cursor.execute(lock_query, (target_id,))
        
        row = cursor.fetchone()
        lock_acquired_at = datetime.now()
        
        if row:
            logger.info("=" * 60)
            logger.info(f"🔒 LOCK ACQUIRED at {lock_acquired_at.isoformat()}")
            logger.info(f"   Locked row: id={row[0]}, name={row[1]}")
            logger.info("=" * 60)
        else:
            # FAIL FAST: 행이 존재하지 않으면 즉시 실패
            logger.error("=" * 60)
            logger.error(f"❌ FAIL FAST: No row found with id={target_id}")
            logger.error("   Cannot proceed with lock contention test on non-existent row.")
            logger.error("   Please provide a valid TARGET_PRODUCT_ID.")
            logger.error("=" * 60)
            if conn:
                conn.rollback()
            sys.exit(1)
        
        # 락 유지 (진행 상황 로깅)
        logger.info(f"Holding lock for {hold_seconds} seconds...")
        logger.info("Other transactions trying to UPDATE/DELETE this row will be blocked.")
        
        elapsed = 0
        log_interval = 5  # 5초마다 로깅
        
        while elapsed < hold_seconds:
            remaining = hold_seconds - elapsed
            if remaining > 0:
                sleep_time = min(log_interval, remaining)
                time.sleep(sleep_time)
                elapsed += sleep_time
                
                if elapsed < hold_seconds:
                    logger.info(f"   Lock held for {elapsed}s, {remaining - sleep_time}s remaining...")
        
        logger.info("Lock hold duration completed.")
        
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        raise
    
    finally:
        # 롤백하여 락 해제
        lock_released_at = datetime.now()
        
        if conn:
            try:
                conn.rollback()
                logger.info("=" * 60)
                logger.info(f"🔓 LOCK RELEASED at {lock_released_at.isoformat()}")
                if lock_acquired_at:
                    duration = (lock_released_at - lock_acquired_at).total_seconds()
                    logger.info(f"   Total lock duration: {duration:.2f} seconds")
                logger.info("=" * 60)
            except Exception as e:
                logger.error(f"Error during rollback: {e}")
            finally:
                if cursor:
                    cursor.close()
                conn.close()
                logger.info("Database connection closed.")
    
    return lock_acquired_at, lock_released_at


def main():
    """메인 엔트리 포인트"""
    config = get_env_config()
    
    # 시작 전 대기 (다른 서비스가 시작될 시간 확보)
    startup_delay = config['startup_delay_seconds']
    if startup_delay > 0:
        logger.info(f"Waiting {startup_delay} seconds before starting (STARTUP_DELAY_SECONDS)...")
        time.sleep(startup_delay)
    
    # DB 준비 대기
    if not wait_for_db_ready(config):
        logger.error("Failed to connect to database. Exiting.")
        sys.exit(1)
    
    try:
        # 락 획득 및 유지
        lock_acquired_at, lock_released_at = hold_row_lock(config)
        
        # 결과 요약
        logger.info("")
        logger.info("=" * 60)
        logger.info("LOCK HOLDER SESSION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"lock_acquired_at: {lock_acquired_at.isoformat() if lock_acquired_at else 'N/A'}")
        logger.info(f"lock_released_at: {lock_released_at.isoformat() if lock_released_at else 'N/A'}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Lock holder failed with error: {e}")
        sys.exit(1)
    
    logger.info("Lock holder exiting normally.")


if __name__ == "__main__":
    main()
