#!/usr/bin/env python
"""DNA Analyzer 테스트 스크립트"""
import os
import sys

# 프로젝트 루트 경로 추가
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, project_root)
os.chdir(project_root)

from dna_analyzer import DNAAnalyzer


def test_result_based_recommendations():
    """테스트 결과 기반 모듈 추천"""
    analyzer = DNAAnalyzer()
    
    # 가상 테스트 결과 (에러 패턴 기반 추천)
    mock_test_output = '''
FAILED test_order_payment - TimeoutError: Connection timed out after 30s
FAILED test_webhook_delivery - ConnectionError: Max retries exceeded with url
ERROR test_concurrent_orders - IntegrityError: duplicate key value violates unique constraint
ERROR test_payment_callback - Error 429: Too Many Requests
FAILED test_cache_update - redis.exceptions.ConnectionError: Connection refused
FAILED test_api_health - Service Unavailable (503)
ERROR test_bulk_operation - MemoryError: unable to allocate array
    '''
    
    print('📊 테스트 결과 기반 모듈 추천')
    print('=' * 60)
    print()
    print('테스트 출력 (예시):')
    for line in mock_test_output.strip().split('\n'):
        if line.strip():
            print(f'  {line.strip()}')
    print()
    print('=' * 60)
    print('분석 결과:')
    print()
    
    # 먼저 Stage 파일 분석하여 기본 result 생성
    result = analyzer.analyze_stage_file('load_tests/scenarios/integration/stage14_dlq_api_test.py')
    
    # 테스트 결과 기반 추가 분석
    result = analyzer.analyze_test_results(result, mock_test_output)
    
    # 추가된 추천사항 출력
    if not result.recommendations:
        print('  추천사항 없음')
    else:
        for rec in result.recommendations:
            priority = rec.priority if isinstance(rec.priority, str) else rec.priority.value
            icon = '🔴' if priority == 'HIGH' else '🟡' if priority == 'MEDIUM' else '🟢'
            print(f'  {icon} [{priority}] {rec.module_name}: {rec.reason}')


def test_gap_detection():
    """Gap 분석 테스트 - 새 기능이 필요한 영역 감지"""
    analyzer = DNAAnalyzer()
    
    print()
    print()
    print('🔍 Gap 분석 (새 기능 필요 영역)')
    print('=' * 60)
    
    # 가상 테스트 결과 - 기존 모듈로 해결 안 되는 패턴
    mock_output = '''
ERROR: race condition detected in concurrent order processing
FAILED: compensating transaction failed during rollback
WARNING: queue full - producer faster than consumer
CRITICAL: resource exhausted - connection pool depleted
ERROR: saga orchestration failure - compensation stuck
    '''
    
    print('테스트 출력 (Gap 관련 예시):')
    for line in mock_output.strip().split('\n'):
        if line.strip():
            print(f'  {line.strip()}')
    print()
    
    # 먼저 Stage 파일 분석
    result = analyzer.analyze_stage_file('load_tests/scenarios/integration/stage14_dlq_api_test.py')
    
    # Gap 분석
    result = analyzer.detect_gaps(result, mock_output)
    
    if not result.gaps:
        print('  Gap 없음 - 기존 모듈로 커버 가능')
    else:
        print('발견된 Gap:')
        for gap in result.gaps:
            print(f'  ⚠️ {gap}')
        
        print()
        print('새 기능 추천:')
        for rec in result.recommendations:
            rec_type = rec.type if isinstance(rec.type, str) else rec.type.value
            if rec_type == 'new_feature':
                print(f'  💡 {rec.module_name}: {rec.reason}')


def test_summary_report():
    """전체 Stage 분석 요약 리포트"""
    import os
    
    print()
    print()
    print('📈 전체 Stage DNA 분석 요약')
    print('=' * 60)
    
    analyzer = DNAAnalyzer()
    base_dir = 'load_tests/scenarios'
    
    total_stages = 0
    with_dna = 0
    without_dna = 0
    total_over = 0
    total_under = 0
    
    for root, dirs, files in os.walk(base_dir):
        for f in files:
            if f.startswith('stage') and f.endswith('.py'):
                total_stages += 1
                path = os.path.join(root, f)
                try:
                    result = analyzer.analyze_stage_file(path)
                    if result.declared_modules:
                        with_dna += 1
                    else:
                        without_dna += 1
                    total_over += len(result.over_engineered)
                    total_under += len(result.under_declared)
                except:
                    pass
    
    print(f'  총 Stage 수: {total_stages}')
    print(f'  DNA 선언 있음: {with_dna} ({100*with_dna/total_stages:.1f}%)')
    print(f'  DNA 선언 없음: {without_dna} ({100*without_dna/total_stages:.1f}%)')
    print()
    print(f'  오버엔지니어링 사례: {total_over}개')
    print(f'  Under-declaration 사례: {total_under}개')
    print()
    
    # DNA 정확도 점수
    if total_stages > 0:
        accuracy = 100 * (1 - (total_over + total_under) / (total_stages * 3))  # 3 = 평균 모듈 수
        print(f'  📊 DNA 정확도 점수: {max(0, accuracy):.1f}%')


if __name__ == '__main__':
    test_result_based_recommendations()
    test_gap_detection()
    test_summary_report()
