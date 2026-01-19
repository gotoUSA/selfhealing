#!/usr/bin/env python
"""
Verify Global Anchors.

글로벌 앵커의 무결성을 검증하고 보고서를 생성합니다.

Usage:
    python scripts/verify_global_anchors.py
    python scripts/verify_global_anchors.py --date=2026-01-18
    python scripts/verify_global_anchors.py --days=7
    python scripts/verify_global_anchors.py --create-anchor

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def setup_environment():
    """환경 설정."""
    # Django 설정 (필요한 경우)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
    
    # Fail-Fast 비활성화 (검증 도구에서는 필요 없음)
    os.environ.setdefault("SELFHEALING_FAIL_FAST", "false")


def get_linker():
    """CrossClusterAuditLinker 인스턴스 가져오기."""
    from selfhealing.audit.integrity.cross_cluster_linker import (
        CrossClusterAuditLinker,
    )
    return CrossClusterAuditLinker()


def verify_date(linker, target_date: date) -> dict:
    """
    특정 날짜의 글로벌 앵커 검증.
    
    Args:
        linker: CrossClusterAuditLinker 인스턴스
        target_date: 검증할 날짜
        
    Returns:
        검증 결과
    """
    result = linker.verify_global_integrity(target_date)
    
    if result["valid"]:
        logger.info(f"✅ {target_date}: VALID (clusters: {result.get('clusters', [])})")
    else:
        error = result.get("error", "Unknown error")
        if error == "Global anchor not found":
            logger.warning(f"⚠️  {target_date}: NO ANCHOR")
        else:
            logger.error(f"❌ {target_date}: INVALID - {error}")
    
    return result


def verify_range(linker, days: int) -> List[dict]:
    """
    최근 N일간의 글로벌 앵커 검증.
    
    Args:
        linker: CrossClusterAuditLinker 인스턴스
        days: 검증할 일수
        
    Returns:
        검증 결과 목록
    """
    results = []
    today = date.today()
    
    for i in range(days):
        target = today - timedelta(days=i + 1)  # 오늘 제외
        result = verify_date(linker, target)
        result["date"] = target.isoformat()
        results.append(result)
    
    return results


def create_and_submit_anchor(linker, target_date: Optional[date] = None) -> bool:
    """
    로컬 앵커 생성 및 글로벌 제출.
    
    Args:
        linker: CrossClusterAuditLinker 인스턴스
        target_date: 대상 날짜 (기본: 어제)
        
    Returns:
        성공 여부
    """
    if target_date is None:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    
    logger.info(f"Creating local anchor for {target_date}...")
    
    anchor = linker.create_local_anchor(target_date)
    if not anchor:
        logger.error("Failed to create local anchor")
        return False
    
    logger.info(f"Local anchor created: {anchor.compute_anchor_hash()[:16]}...")
    logger.info(f"  - Cluster: {anchor.cluster_id}")
    logger.info(f"  - Sequence: {anchor.final_sequence}")
    logger.info(f"  - Entry count: {anchor.entry_count}")
    
    logger.info("Submitting to global storage...")
    if linker.submit_to_global(anchor):
        logger.info("✅ Successfully submitted to global storage")
        return True
    else:
        logger.error("❌ Failed to submit to global storage")
        return False


def list_anchors(linker, limit: int = 30) -> List[str]:
    """
    글로벌 앵커 목록 조회.
    
    Args:
        linker: CrossClusterAuditLinker 인스턴스
        limit: 최대 개수
        
    Returns:
        날짜 문자열 목록
    """
    dates = linker.list_global_anchors(limit)
    
    logger.info(f"Found {len(dates)} global anchors:")
    for d in dates:
        logger.info(f"  - {d}")
    
    return dates


def generate_report(results: List[dict]) -> str:
    """
    검증 결과 보고서 생성.
    
    Args:
        results: 검증 결과 목록
        
    Returns:
        보고서 문자열
    """
    total = len(results)
    valid = sum(1 for r in results if r.get("valid"))
    no_anchor = sum(1 for r in results if r.get("error") == "Global anchor not found")
    invalid = total - valid - no_anchor
    
    report = []
    report.append("=" * 60)
    report.append("Global Anchor Verification Report")
    report.append(f"Generated at: {datetime.now(timezone.utc).isoformat()}")
    report.append("=" * 60)
    report.append("")
    report.append("Summary:")
    report.append(f"  Total checked:  {total}")
    report.append(f"  Valid:          {valid} ({100*valid/total:.1f}%)" if total > 0 else "  Valid:          0")
    report.append(f"  No anchor:      {no_anchor}")
    report.append(f"  Invalid:        {invalid}")
    report.append("")
    
    if invalid > 0:
        report.append("⚠️  ATTENTION: Invalid anchors detected!")
        report.append("")
        report.append("Invalid dates:")
        for r in results:
            if not r.get("valid") and r.get("error") != "Global anchor not found":
                report.append(f"  - {r.get('date')}: {r.get('error')}")
    else:
        report.append("✅ All anchors are valid")
    
    report.append("")
    report.append("=" * 60)
    
    return "\n".join(report)


def main():
    parser = argparse.ArgumentParser(
        description="Verify global audit anchors"
    )
    parser.add_argument(
        "--date",
        help="Specific date to verify (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to verify (default: 7)"
    )
    parser.add_argument(
        "--create-anchor",
        action="store_true",
        help="Create and submit local anchor"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all global anchors"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )
    
    args = parser.parse_args()
    
    setup_environment()
    
    try:
        linker = get_linker()
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        sys.exit(1)
    
    if args.create_anchor:
        target = date.fromisoformat(args.date) if args.date else None
        success = create_and_submit_anchor(linker, target)
        sys.exit(0 if success else 1)
    
    if args.list:
        dates = list_anchors(linker)
        if args.json:
            print(json.dumps(dates, indent=2))
        sys.exit(0)
    
    # 검증 수행
    if args.date:
        target = date.fromisoformat(args.date)
        results = [verify_date(linker, target)]
        results[0]["date"] = target.isoformat()
    else:
        results = verify_range(linker, args.days)
    
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        report = generate_report(results)
        print(report)
    
    # 무효한 앵커가 있으면 exit code 1
    invalid = any(
        not r.get("valid") and r.get("error") != "Global anchor not found"
        for r in results
    )
    sys.exit(1 if invalid else 0)


if __name__ == "__main__":
    main()
