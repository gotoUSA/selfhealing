#!/usr/bin/env python3
"""
Stage 16 - Database Lock Monitor

PostgreSQL의 락 상태를 실시간으로 모니터링하고 기록하는 스크립트.
pg_stat_activity와 pg_locks를 조회하여 락 경합 증거를 수집합니다.

Usage:
    python monitor_db_locks.py

Environment Variables:
    PGHOST          - PostgreSQL 호스트 (default: db)
    PGPORT          - PostgreSQL 포트 (default: 5432)
    PGDATABASE      - 데이터베이스 이름 (default: shopping_db)
    PGUSER          - 데이터베이스 사용자 (default: shopping_user)
    PGPASSWORD      - 데이터베이스 비밀번호 (default: shopping_pass)
    MONITOR_DURATION_SECONDS - 모니터링 시간 (default: 60)
    MONITOR_INTERVAL_SECONDS - 조회 간격 (default: 2)
"""

import os
import sys
import time
import json
import logging
from datetime import datetime

try:
    import psycopg2
    import psycopg2.extras
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
        'duration': int(os.environ.get('MONITOR_DURATION_SECONDS', '60')),
        'interval': int(os.environ.get('MONITOR_INTERVAL_SECONDS', '2')),
    }


def get_lock_stats(cursor):
    """현재 락 통계 조회"""
    # 블로킹된 세션 수
    cursor.execute("""
        SELECT count(*) 
        FROM pg_locks 
        WHERE NOT granted
    """)
    blocked_count = cursor.fetchone()[0]
    
    # Lock 대기 중인 세션
    cursor.execute("""
        SELECT count(*) 
        FROM pg_stat_activity 
        WHERE wait_event_type = 'Lock'
    """)
    lock_wait_count = cursor.fetchone()[0]
    
    # 활성 세션 수
    cursor.execute("""
        SELECT count(*) 
        FROM pg_stat_activity 
        WHERE state = 'active' AND pid != pg_backend_pid()
    """)
    active_count = cursor.fetchone()[0]
    
    # idle in transaction 세션 수
    cursor.execute("""
        SELECT count(*) 
        FROM pg_stat_activity 
        WHERE state = 'idle in transaction'
    """)
    idle_tx_count = cursor.fetchone()[0]
    
    return {
        'blocked_sessions': blocked_count,
        'lock_waiting_sessions': lock_wait_count,
        'active_sessions': active_count,
        'idle_in_transaction': idle_tx_count,
    }


def get_blocking_details(cursor):
    """블로킹 세션 상세 정보"""
    cursor.execute("""
        SELECT 
            blocked_locks.pid AS blocked_pid,
            blocked_activity.usename AS blocked_user,
            blocking_locks.pid AS blocking_pid,
            blocking_activity.usename AS blocking_user,
            blocked_activity.query AS blocked_query,
            blocking_activity.query AS blocking_query,
            blocked_activity.wait_event_type,
            blocked_activity.wait_event
        FROM pg_locks blocked_locks
        JOIN pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
        JOIN pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
            AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
            AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
            AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
            AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
            AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
            AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
            AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
            AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
            AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
            AND blocking_locks.pid != blocked_locks.pid
        JOIN pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
        WHERE NOT blocked_locks.granted
        LIMIT 10
    """)
    
    rows = cursor.fetchall()
    details = []
    for row in rows:
        details.append({
            'blocked_pid': row[0],
            'blocked_user': row[1],
            'blocking_pid': row[2],
            'blocking_user': row[3],
            'blocked_query': row[4][:100] if row[4] else None,
            'blocking_query': row[5][:100] if row[5] else None,
            'wait_event_type': row[6],
            'wait_event': row[7],
        })
    return details


def get_row_locks_on_table(cursor, table_name='shopping_product'):
    """특정 테이블의 행 락 정보"""
    cursor.execute("""
        SELECT 
            l.pid,
            l.locktype,
            l.mode,
            l.granted,
            a.state,
            a.query,
            a.wait_event_type,
            a.wait_event
        FROM pg_locks l
        JOIN pg_class c ON c.oid = l.relation
        JOIN pg_stat_activity a ON a.pid = l.pid
        WHERE c.relname = %s
          AND l.locktype = 'tuple'
        LIMIT 20
    """, (table_name,))
    
    rows = cursor.fetchall()
    locks = []
    for row in rows:
        locks.append({
            'pid': row[0],
            'locktype': row[1],
            'mode': row[2],
            'granted': row[3],
            'state': row[4],
            'query': row[5][:80] if row[5] else None,
            'wait_event_type': row[6],
            'wait_event': row[7],
        })
    return locks


def monitor_locks(config):
    """락 상태 모니터링 메인 함수"""
    duration = config['duration']
    interval = config['interval']
    
    logger.info("=" * 60)
    logger.info("STAGE 16 - DATABASE LOCK MONITOR")
    logger.info("=" * 60)
    logger.info(f"Monitoring duration: {duration} seconds")
    logger.info(f"Check interval: {interval} seconds")
    logger.info("=" * 60)
    
    # 모니터링 결과 저장
    results = {
        'start_time': datetime.now().isoformat(),
        'samples': [],
        'max_blocked_sessions': 0,
        'max_lock_wait_sessions': 0,
        'total_blocking_events': 0,
        'blocking_details_captured': [],
    }
    
    try:
        conn = psycopg2.connect(
            host=config['host'],
            port=config['port'],
            database=config['database'],
            user=config['user'],
            password=config['password']
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        logger.info("Connected to database. Starting monitoring...")
        logger.info("-" * 60)
        
        start_time = time.time()
        sample_count = 0
        
        while time.time() - start_time < duration:
            timestamp = datetime.now().isoformat()
            
            # 락 통계 수집
            stats = get_lock_stats(cursor)
            
            sample = {
                'timestamp': timestamp,
                'elapsed_seconds': round(time.time() - start_time, 1),
                **stats
            }
            results['samples'].append(sample)
            
            # 최대값 갱신
            results['max_blocked_sessions'] = max(results['max_blocked_sessions'], stats['blocked_sessions'])
            results['max_lock_wait_sessions'] = max(results['max_lock_wait_sessions'], stats['lock_waiting_sessions'])
            
            # 블로킹이 발생하면 상세 정보 기록
            if stats['blocked_sessions'] > 0 or stats['lock_waiting_sessions'] > 0:
                results['total_blocking_events'] += 1
                
                # 상세 정보 캡처
                blocking_details = get_blocking_details(cursor)
                if blocking_details:
                    results['blocking_details_captured'].extend(blocking_details)
                    logger.info(f"🔴 BLOCKING DETECTED at {timestamp}")
                    logger.info(f"   Blocked: {stats['blocked_sessions']}, Lock Wait: {stats['lock_waiting_sessions']}")
                    for detail in blocking_details[:2]:  # 최대 2개만 로깅
                        logger.info(f"   - PID {detail['blocked_pid']} blocked by PID {detail['blocking_pid']}")
                        logger.info(f"     Wait: {detail['wait_event_type']}/{detail['wait_event']}")
                
                # 행 락 정보
                row_locks = get_row_locks_on_table(cursor)
                if row_locks:
                    for lock in row_locks[:3]:
                        logger.info(f"   Row lock: PID={lock['pid']}, mode={lock['mode']}, granted={lock['granted']}")
            else:
                # 정상 상태는 간단히 표시
                if sample_count % 5 == 0:  # 10초마다 (5 * 2초)
                    logger.info(f"📊 [{sample['elapsed_seconds']}s] Active: {stats['active_sessions']}, IdleTx: {stats['idle_in_transaction']}, Blocked: {stats['blocked_sessions']}")
            
            sample_count += 1
            time.sleep(interval)
        
        results['end_time'] = datetime.now().isoformat()
        
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        results['error'] = str(e)
    finally:
        if conn:
            conn.close()
    
    # 결과 요약 출력
    logger.info("=" * 60)
    logger.info("MONITORING COMPLETE - SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total samples: {len(results['samples'])}")
    logger.info(f"Max blocked sessions: {results['max_blocked_sessions']}")
    logger.info(f"Max lock wait sessions: {results['max_lock_wait_sessions']}")
    logger.info(f"Total blocking events: {results['total_blocking_events']}")
    logger.info(f"Blocking details captured: {len(results['blocking_details_captured'])}")
    
    # 결론 출력
    logger.info("=" * 60)
    if results['max_blocked_sessions'] > 0 or results['max_lock_wait_sessions'] > 0:
        logger.info("✅ DB-LEVEL EVIDENCE: Lock contention WAS observed")
        logger.info(f"   Peak blocked sessions: {results['max_blocked_sessions']}")
        logger.info(f"   Peak lock wait sessions: {results['max_lock_wait_sessions']}")
    else:
        logger.info("⚠️ DB-LEVEL EVIDENCE: Lock contention was NOT observed")
        logger.info("   No blocked sessions or lock waits detected during monitoring")
    logger.info("=" * 60)
    
    # JSON 결과 출력
    print("\n--- MONITOR_RESULTS_JSON_START ---")
    print(json.dumps(results, indent=2, default=str))
    print("--- MONITOR_RESULTS_JSON_END ---")
    
    return results


def main():
    """메인 함수"""
    config = get_env_config()
    
    # DB 연결 대기
    logger.info(f"Waiting for database at {config['host']}:{config['port']}...")
    max_retries = 30
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
            break
        except psycopg2.OperationalError:
            time.sleep(2)
    else:
        logger.error("Could not connect to database")
        sys.exit(1)
    
    # 모니터링 시작
    monitor_locks(config)
    
    logger.info("Lock monitor exiting normally.")


if __name__ == '__main__':
    main()
