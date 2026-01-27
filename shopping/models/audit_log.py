"""
Audit Log Model - 감사 로그 저장.

136_EXCEPTION_HANDLER_6_ENHANCEMENTS.md Q3 보완 구현:
- audit_event_id Unique 제약으로 WAL 복구 시 중복 삽입 방지 (2차 방어)
- ON CONFLICT (audit_event_id) DO NOTHING 지원

Usage:
    from shopping.models.audit_log import AuditLog
    
    # 단일 삽입 (중복 무시)
    log, created = AuditLog.insert_ignore_conflict(
        audit_event_id="wal:123:pg_insert",
        action="AUTO_TUNING_ADJUSTMENT",
        timestamp=datetime.now(),
        ...
    )
    
    # 벌크 삽입 (PostgreSQL ON CONFLICT DO NOTHING)
    inserted, skipped = AuditLog.bulk_insert_ignore_conflict(entries)
"""

from selfhealing.adapters.django.models import AbstractAuditLog


class AuditLog(AbstractAuditLog):
    """
    Concrete Audit Log model for shopping application.
    
    Inherits from AbstractAuditLog:
    - audit_event_id: Unique (WAL 복구 중복 방지)
    - action, timestamp, actor_*, target_*
    - integrity_hash, previous_hash (해시 체인)
    - insert_ignore_conflict(), bulk_insert_ignore_conflict()
    """
    
    class Meta(AbstractAuditLog.Meta):
        abstract = False
        db_table = "audit_log"
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
