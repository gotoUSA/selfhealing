"""Celery 수동 재시도(self.retry) 공통 정책

Celery 는 retry_backoff / retry_backoff_max / retry_jitter 를 autoretry_for 자동 재시도에서만 쓴다.
손으로 부르는 self.retry(exc=e) 는 countdown 을 넘기지 않으면 default_retry_delay(180초 고정)를 쓰므로,
데코레이터에 적은 백오프가 실제로 적용되도록 countdown 을 여기서 계산해 넘긴다.
"""

from __future__ import annotations

from celery import Task
from celery.utils.time import get_exponential_backoff_interval


def retry_countdown(task: Task) -> int:
    """태스크의 retry_backoff / retry_backoff_max / retry_jitter 로 다음 재시도까지의 대기(초)를 계산한다

    retry_backoff 가 꺼져 있으면 default_retry_delay 를 그대로 쓴다 (Celery 기본 동작과 같음).
    """
    if not task.retry_backoff:
        return task.default_retry_delay
    return get_exponential_backoff_interval(
        factor=int(task.retry_backoff),
        retries=task.request.retries,
        maximum=task.retry_backoff_max,
        full_jitter=task.retry_jitter,
    )


def retry_with_backoff(task: Task, exc: Exception):
    """백오프 countdown 으로 재시도한다. 호출자는 raise 한다: raise retry_with_backoff(self, e)

    재시도 여유가 없으면 Celery 가 exc 를 그대로 다시 던진다 (MaxRetriesExceededError 가 아님).
    """
    return task.retry(exc=exc, countdown=retry_countdown(task))


def retry_unless_exhausted(task: Task, exc: Exception) -> None:
    """재시도 여유가 있으면 백오프 countdown 으로 재시도한다(Retry 발생). 소진됐으면 그냥 돌아온다

    Celery 의 retry(exc=...) 는 소진 시 MaxRetriesExceededError 가 아니라 exc 를 그대로 다시 던진다.
    그래서 'except MaxRetriesExceededError' 로는 소진 처리를 잡을 수 없다 — 부르기 전에 횟수로 판단한다.
    호출자는 이 함수가 돌아오면 소진 처리(롤백·알림)를 하고 예외를 올린다.
    """
    if task.request.retries < task.max_retries:
        raise task.retry(exc=exc, countdown=retry_countdown(task))
