"""
Postmortem Record Model

장애 사후 분석(Post-mortem) 데이터의 영구 저장소.
서버 재시작 시에도 데이터가 유지되며, 복잡한 쿼리와 장기 분석이 가능합니다.
"""

from __future__ import annotations

from selfhealing.adapters.django.models import AbstractPostmortemRecord


class PostmortemRecord(AbstractPostmortemRecord):
    """
    장애 사후 분석 영구 저장 모델.

    기존 In-Memory 저장소(_healing_incidents)의 한계를 해결:
    - 서버 재시작 시 데이터 손실 방지
    - 다중 워커 간 데이터 일관성 보장
    - 장기 보존 (90일~1년)
    - 복잡한 쿼리 지원 (기간별, 서비스별, 지속시간별)
    """

    class Meta(AbstractPostmortemRecord.Meta):
        abstract = False
        db_table = "selfhealing_postmortem"
        verbose_name = "Postmortem Record"
        verbose_name_plural = "Postmortem Records"
