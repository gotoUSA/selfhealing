#!/usr/bin/env python3
"""
Visibility-Focused Self-Healing Verification - Product Stock Lock Holder

CHOKE POINT: Product.stock 필드 (shopping_product.stock)

이 스크립트는 모든 성공적인 주문 플로우가 반드시 통과해야 하는
Product.stock 필드에 대해 외부 락을 반복적으로 생성합니다.

왜 Product.stock이 최적의 choke point인가?
1. 모든 주문에서 재고 차감 시 SELECT FOR UPDATE로 락 획득
2. 여러 사용자가 동일 상품을 주문하면 반드시 경합 발생
3. 비즈니스 검증(장바구니, 포인트 등) 이후에 접근
4. 조기 실패로 우회 불가능

동작:
- N회 반복 (N ≥ 10)
- 각 반복: 상품 행 락 획득 → 8-10초 유지 → ROLLBACK → 1-2초 대기
- Locust 부하 테스트와 동시 실행 시 락 경합 발생

환경변수:
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
    TARGET_PRODUCT_ID  - 락을 잡을 상품 ID (0 = 자동 탐색)
    LOCK_ITERATIONS    - 락 반복 횟수 (default: 10)
    LOCK_HOLD_MIN      - 락 유지 최소 시간 초 (default: 8)
    LOCK_HOLD_MAX      - 락 유지 최대 시간 초 (default: 10)
    COOLDOWN_MIN       - 쿨다운 최소 시간 초 (default: 1)
    COOLDOWN_MAX       - 쿨다운 최대 시간 초 (default: 2)
    STARTUP_DELAY      - 시작 전 대기 시간 초 (default: 5)
"""

import os
import sys
import time
import random
import logging
from datetime import datetime
from typing import Optional, Tuple

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


class LockHolderConfig:
    """환경변수 기반 설정"""
    
    def __init__(self):
        self.host = os.environ.get('PGHOST', 'db')
        self.port = int(os.environ.get('PGPORT', '5432'))
        self.database = os.environ.get('PGDATABASE', 'shopping_db')
        self.user = os.environ.get('PGUSER', 'shopping_user')
        self.password = os.environ.get('PGPASSWORD', 'shopping_pass')
        
        self.product_table = 'shopping_product'
        self.target_product_id = int(os.environ.get('TARGET_PRODUCT_ID', '0'))
        self.lock_iterations = int(os.environ.get('LOCK_ITERATIONS', '10'))
        self.lock_hold_min = int(os.environ.get('LOCK_HOLD_MIN', '8'))
        self.lock_hold_max = int(os.environ.get('LOCK_HOLD_MAX', '10'))
        self.cooldown_min = int(os.environ.get('COOLDOWN_MIN', '1'))
        self.cooldown_max = int(os.environ.get('COOLDOWN_MAX', '2'))
        self.startup_delay = int(os.environ.get('STARTUP_DELAY', '5'))


class LockWindow:
    """단일 락 윈도우 정보"""
    
    def __init__(self, iteration: int):
        self.iteration = iteration
        self.started_at: Optional[datetime] = None
        self.released_at: Optional[datetime] = None
        self.hold_duration: float = 0.0
        self.cooldown_duration: float = 0.0
        self.success: bool = False
        self.error: Optional[str] = None


class ProductStockLockHolder:
    """Product.stock 필드에 대한 반복적 락 홀더"""
    
    def __init__(self, config: LockHolderConfig):
        self.config = config
        self.lock_windows: list[LockWindow] = []
        self.target_product_id: Optional[int] = None
        self.target_product_name: Optional[str] = None
        
    def wait_for_db_ready(self, max_retries: int = 30, retry_interval: int = 2) -> bool:
        """DB가 준비될 때까지 대기"""
        logger.info(f"Waiting for database at {self.config.host}:{self.config.port}...")
        
        for attempt in range(max_retries):
            try:
                conn = psycopg2.connect(
                    host=self.config.host,
                    port=self.config.port,
                    database=self.config.database,
                    user=self.config.user,
                    password=self.config.password,
                    connect_timeout=5
                )
                conn.close()
                logger.info("Database is ready!")
                return True
            except psycopg2.OperationalError as e:
                logger.warning(f"Attempt {attempt + 1}/{max_retries}: Database not ready - {e}")
                time.sleep(retry_interval)
        
        return False
    
    def discover_target_product(self, cursor) -> Tuple[int, str]:
        """
        락을 잡을 대상 상품 탐색
        
        조건:
        1. 재고가 있는 활성 상품
        2. 가장 인기 있는 (낮은 ID = 테스트에서 먼저 선택될) 상품
        """
        if self.config.target_product_id > 0:
            cursor.execute(
                f"SELECT id, name, stock FROM {self.config.product_table} WHERE id = %s",
                (self.config.target_product_id,)
            )
            row = cursor.fetchone()
            if row:
                logger.info(f"Using specified product: id={row[0]}, name={row[1]}, stock={row[2]}")
                return row[0], row[1]
            else:
                logger.error(f"Target product ID {self.config.target_product_id} not found!")
                sys.exit(1)
        
        # 자동 탐색: 재고가 있고 활성화된 상품 중 첫 번째
        cursor.execute(f"""
            SELECT id, name, stock 
            FROM {self.config.product_table} 
            WHERE is_active = TRUE
              AND stock > 0
            ORDER BY id
            LIMIT 1
        """)
        row = cursor.fetchone()
        
        if row:
            logger.info(f"AUTO_DISCOVER: Found product - id={row[0]}, name={row[1]}, stock={row[2]}")
            return row[0], row[1]
        
        logger.error("AUTO_DISCOVER: No active products with stock found!")
        sys.exit(1)
    
    def hold_lock_single_iteration(self, iteration: int) -> LockWindow:
        """단일 락 윈도우 실행"""
        window = LockWindow(iteration)
        hold_seconds = random.randint(self.config.lock_hold_min, self.config.lock_hold_max)
        
        conn = None
        cursor = None
        
        try:
            # 새 연결 생성
            conn = psycopg2.connect(
                host=self.config.host,
                port=self.config.port,
                database=self.config.database,
                user=self.config.user,
                password=self.config.password
            )
            conn.autocommit = False
            cursor = conn.cursor()
            
            # 락 타임아웃 무제한
            cursor.execute("SET lock_timeout = '0'")
            
            # 최초 반복에서만 대상 상품 탐색
            if self.target_product_id is None:
                self.target_product_id, self.target_product_name = self.discover_target_product(cursor)
                conn.rollback()  # 탐색 트랜잭션 종료
            
            # SELECT FOR UPDATE로 락 획득
            logger.info(f"[Iteration {iteration}/{self.config.lock_iterations}] Acquiring lock on product id={self.target_product_id}...")
            
            lock_query = f"""
                SELECT id, name, stock 
                FROM {self.config.product_table} 
                WHERE id = %s 
                FOR UPDATE
            """
            cursor.execute(lock_query, (self.target_product_id,))
            row = cursor.fetchone()
            
            window.started_at = datetime.now()
            
            if not row:
                window.error = f"Product id={self.target_product_id} not found"
                logger.error(f"[Iteration {iteration}] {window.error}")
                return window
            
            logger.info("=" * 70)
            logger.info(f"🔒 [Iteration {iteration}] LOCK ACQUIRED at {window.started_at.isoformat()}")
            logger.info(f"   Product: id={row[0]}, name={row[1]}, stock={row[2]}")
            logger.info(f"   Hold duration: {hold_seconds} seconds")
            logger.info("   ⚠️  Any order attempting to purchase this product will be BLOCKED")
            logger.info("=" * 70)
            
            # 락 유지
            elapsed = 0
            log_interval = 2
            
            while elapsed < hold_seconds:
                remaining = hold_seconds - elapsed
                sleep_time = min(log_interval, remaining)
                time.sleep(sleep_time)
                elapsed += sleep_time
                
                if elapsed < hold_seconds:
                    logger.info(f"   [Iteration {iteration}] Lock held for {elapsed}s, {remaining - sleep_time}s remaining...")
            
            window.hold_duration = hold_seconds
            window.success = True
            
        except psycopg2.Error as e:
            window.error = str(e)
            logger.error(f"[Iteration {iteration}] Database error: {e}")
            
        finally:
            window.released_at = datetime.now()
            
            if conn:
                try:
                    conn.rollback()
                    logger.info("=" * 70)
                    logger.info(f"🔓 [Iteration {iteration}] LOCK RELEASED at {window.released_at.isoformat()}")
                    if window.started_at:
                        actual_duration = (window.released_at - window.started_at).total_seconds()
                        logger.info(f"   Actual lock duration: {actual_duration:.2f} seconds")
                    logger.info("=" * 70)
                except Exception as e:
                    logger.error(f"[Iteration {iteration}] Rollback error: {e}")
                finally:
                    if cursor:
                        cursor.close()
                    conn.close()
        
        return window
    
    def run(self) -> list[LockWindow]:
        """전체 락 홀더 실행"""
        logger.info("=" * 70)
        logger.info("VISIBILITY-FOCUSED SELF-HEALING VERIFICATION")
        logger.info("CHOKE POINT: Product.stock (shopping_product)")
        logger.info("=" * 70)
        logger.info(f"Target iterations: {self.config.lock_iterations}")
        logger.info(f"Lock hold time: {self.config.lock_hold_min}-{self.config.lock_hold_max} seconds")
        logger.info(f"Cooldown time: {self.config.cooldown_min}-{self.config.cooldown_max} seconds")
        logger.info("=" * 70)
        
        # 시작 대기
        if self.config.startup_delay > 0:
            logger.info(f"Waiting {self.config.startup_delay} seconds before starting...")
            time.sleep(self.config.startup_delay)
        
        # DB 준비 대기
        if not self.wait_for_db_ready():
            logger.error("Failed to connect to database. Exiting.")
            sys.exit(1)
        
        # 반복 실행
        for iteration in range(1, self.config.lock_iterations + 1):
            window = self.hold_lock_single_iteration(iteration)
            self.lock_windows.append(window)
            
            # 쿨다운 (마지막 반복 제외)
            if iteration < self.config.lock_iterations:
                cooldown = random.randint(self.config.cooldown_min, self.config.cooldown_max)
                window.cooldown_duration = cooldown
                logger.info(f"[Iteration {iteration}] Cooldown: {cooldown} seconds...")
                time.sleep(cooldown)
        
        return self.lock_windows
    
    def print_summary(self):
        """실행 결과 요약 출력"""
        logger.info("")
        logger.info("=" * 70)
        logger.info("LOCK HOLDER EXECUTION SUMMARY")
        logger.info("=" * 70)
        
        successful = sum(1 for w in self.lock_windows if w.success)
        failed = len(self.lock_windows) - successful
        total_lock_time = sum(w.hold_duration for w in self.lock_windows)
        
        logger.info(f"Target Product: id={self.target_product_id}, name={self.target_product_name}")
        logger.info(f"Total iterations: {len(self.lock_windows)}")
        logger.info(f"Successful locks: {successful}")
        logger.info(f"Failed locks: {failed}")
        logger.info(f"Total lock time: {total_lock_time:.2f} seconds")
        
        if self.lock_windows:
            first = self.lock_windows[0]
            last = self.lock_windows[-1]
            if first.started_at and last.released_at:
                total_duration = (last.released_at - first.started_at).total_seconds()
                logger.info(f"Total test duration: {total_duration:.2f} seconds")
        
        logger.info("")
        logger.info("Lock Window Timeline:")
        for w in self.lock_windows:
            status = "✅" if w.success else "❌"
            started = w.started_at.strftime("%H:%M:%S") if w.started_at else "N/A"
            released = w.released_at.strftime("%H:%M:%S") if w.released_at else "N/A"
            logger.info(f"  {status} Iteration {w.iteration}: {started} → {released} ({w.hold_duration:.1f}s)")
        
        logger.info("=" * 70)
        
        # 환경변수로 타겟 상품 ID 출력 (Locust와 동기화용)
        logger.info("\n📌 To force Locust to target this product:")
        logger.info(f"   export LOCK_TARGET_PRODUCT_ID={self.target_product_id}")


def main():
    """메인 엔트리 포인트"""
    config = LockHolderConfig()
    holder = ProductStockLockHolder(config)
    
    try:
        holder.run()
        holder.print_summary()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        holder.print_summary()
    except Exception as e:
        logger.error(f"Lock holder failed: {e}")
        sys.exit(1)
    
    logger.info("Lock holder exiting normally.")


if __name__ == "__main__":
    main()
