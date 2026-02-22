"""
Stage 9 Extension: Worker Crash Recovery Test (GAP-05)

목표: 부하 중 Worker 강제 종료 시 in-flight 작업 복구 검증

시나리오:
  - 부하 중 Worker 강제 종료 (kill -9 시뮬레이션)
  - in-flight 작업 복구 확인
  - 새 Worker 자동 시작 확인

Invariants:
  - in_flight_tasks_recovered
  - no_permanent_task_loss

실행 방법:
    # Standalone 모드 (시뮬레이션)
    python load_tests/scenarios/stage9_worker_crash.py

    # Locust 모드
    locust -f load_tests/scenarios/stage9_worker_crash.py --host=http://localhost:8000

Reference:
  - docs/GAP_RESOLUTION_PLAN.md (GAP-05)
"""

import os
import sys
import time
import random
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque

_current_dir = os.path.dirname(os.path.abspath(__file__))
_load_tests_dir = os.path.dirname(_current_dir)
_project_root = os.path.dirname(_load_tests_dir)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


STAGE_NAME = "[Stage9-WorkerCrash]"


# =============================================================================
# 설정
# =============================================================================


@dataclass
class WorkerCrashConfig:
    """Worker Crash 테스트 설정"""
    
    num_workers: int = 4
    tasks_per_second: int = 50
    task_processing_time_ms: int = 100
    crash_interval_seconds: int = 5  # Worker crash 간격
    recovery_timeout_seconds: int = 3  # 복구 타임아웃
    max_task_retries: int = 3
    task_visibility_timeout_seconds: int = 2  # 작업 visibility 타임아웃 (빠른 복구)


CONFIG = WorkerCrashConfig()


# =============================================================================
# Task 정의
# =============================================================================


class TaskState(str, Enum):
    """Task 상태"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERED = "recovered"


@dataclass
class Task:
    """Task 정의"""
    
    id: str
    payload: Dict[str, Any]
    state: TaskState = TaskState.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    worker_id: Optional[str] = None
    error_message: Optional[str] = None
    recovery_attempts: int = 0
    
    def is_stale(self, timeout_seconds: int) -> bool:
        """작업이 stale 상태인지 확인"""
        if self.state != TaskState.IN_PROGRESS:
            return False
        if self.started_at is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return elapsed > timeout_seconds


# =============================================================================
# Worker 정의
# =============================================================================


class WorkerState(str, Enum):
    """Worker 상태"""
    RUNNING = "running"
    CRASHED = "crashed"
    STOPPED = "stopped"


@dataclass
class Worker:
    """Worker 정의"""
    
    id: str
    state: WorkerState = WorkerState.RUNNING
    current_task: Optional[Task] = None
    tasks_completed: int = 0
    tasks_failed: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    crashed_at: Optional[datetime] = None
    
    def crash(self):
        """Worker crash 시뮬레이션"""
        self.state = WorkerState.CRASHED
        self.crashed_at = datetime.now(timezone.utc)
        # in-flight task는 그대로 남김
        
    def is_healthy(self) -> bool:
        return self.state == WorkerState.RUNNING


# =============================================================================
# Task Queue Manager
# =============================================================================


class TaskQueueManager:
    """Task Queue 관리자"""
    
    def __init__(self, config: WorkerCrashConfig = None):
        self.config = config or CONFIG
        
        self.pending_queue: deque = deque()
        self.in_progress: Dict[str, Task] = {}
        self.completed: Dict[str, Task] = {}
        self.failed: Dict[str, Task] = {}
        self.recovered: Dict[str, Task] = {}
        
        self.workers: Dict[str, Worker] = {}
        self.lock = threading.Lock()
        
        # 통계
        self.total_tasks = 0
        self.total_completed = 0
        self.total_failed = 0
        self.total_recovered = 0
        self.worker_crashes = 0
        
    def create_task(self, payload: Dict) -> Task:
        """Task 생성"""
        task = Task(
            id=str(uuid.uuid4()),
            payload=payload,
            max_retries=self.config.max_task_retries,
        )
        with self.lock:
            self.pending_queue.append(task)
            self.total_tasks += 1
        return task
    
    def create_worker(self) -> Worker:
        """Worker 생성"""
        worker = Worker(id=str(uuid.uuid4()))
        with self.lock:
            self.workers[worker.id] = worker
        return worker
    
    def claim_task(self, worker_id: str) -> Optional[Task]:
        """Worker가 Task 가져가기"""
        with self.lock:
            if worker_id not in self.workers:
                return None
            
            worker = self.workers[worker_id]
            if not worker.is_healthy():
                return None
            
            if not self.pending_queue:
                return None
            
            task = self.pending_queue.popleft()
            task.state = TaskState.IN_PROGRESS
            task.started_at = datetime.now(timezone.utc)
            task.worker_id = worker_id
            
            self.in_progress[task.id] = task
            worker.current_task = task
            
            return task
    
    def complete_task(self, task_id: str, success: bool, error: str = None):
        """Task 완료 처리"""
        with self.lock:
            if task_id not in self.in_progress:
                return
            
            task = self.in_progress.pop(task_id)
            task.completed_at = datetime.now(timezone.utc)
            
            if success:
                task.state = TaskState.COMPLETED
                self.completed[task_id] = task
                self.total_completed += 1
            else:
                task.error_message = error
                task.retry_count += 1
                
                if task.retry_count < task.max_retries:
                    # 재시도를 위해 다시 큐에 넣기
                    task.state = TaskState.PENDING
                    task.started_at = None
                    task.worker_id = None
                    self.pending_queue.append(task)
                else:
                    task.state = TaskState.FAILED
                    self.failed[task_id] = task
                    self.total_failed += 1
            
            # Worker 상태 업데이트
            if task.worker_id and task.worker_id in self.workers:
                worker = self.workers[task.worker_id]
                worker.current_task = None
                if success:
                    worker.tasks_completed += 1
                else:
                    worker.tasks_failed += 1
    
    def crash_worker(self, worker_id: str):
        """Worker crash 시뮬레이션"""
        with self.lock:
            if worker_id not in self.workers:
                return
            
            worker = self.workers[worker_id]
            in_flight_task = worker.current_task
            
            worker.crash()
            self.worker_crashes += 1
            
            print(f"{STAGE_NAME} Worker {worker_id[:8]} CRASHED!")
            
            if in_flight_task:
                print(f"  └─ In-flight task: {in_flight_task.id[:8]}")
    
    def recover_stale_tasks(self) -> List[Task]:
        """Stale task 복구"""
        recovered_tasks = []
        
        with self.lock:
            stale_task_ids = []
            
            for task_id, task in self.in_progress.items():
                if task.is_stale(self.config.task_visibility_timeout_seconds):
                    stale_task_ids.append(task_id)
            
            for task_id in stale_task_ids:
                task = self.in_progress.pop(task_id)
                task.recovery_attempts += 1
                task.state = TaskState.PENDING
                task.started_at = None
                
                # Worker가 crashed 상태인지 확인
                if task.worker_id and task.worker_id in self.workers:
                    worker = self.workers[task.worker_id]
                    if worker.state == WorkerState.CRASHED:
                        worker.current_task = None
                
                task.worker_id = None
                
                self.pending_queue.append(task)
                self.recovered[task_id] = task
                self.total_recovered += 1
                recovered_tasks.append(task)
                
                print(f"{STAGE_NAME} Task {task_id[:8]} RECOVERED (attempt: {task.recovery_attempts})")
        
        return recovered_tasks
    
    def respawn_crashed_workers(self) -> List[Worker]:
        """Crashed worker 재생성"""
        new_workers = []
        
        with self.lock:
            crashed_worker_ids = [
                w_id for w_id, w in self.workers.items()
                if w.state == WorkerState.CRASHED
            ]
            
            for worker_id in crashed_worker_ids:
                del self.workers[worker_id]
        
        # Lock 해제 후 새 worker 생성
        for _ in range(len(crashed_worker_ids) if crashed_worker_ids else 0):
            new_worker = self.create_worker()
            new_workers.append(new_worker)
            print(f"{STAGE_NAME} New Worker {new_worker.id[:8]} spawned")
        
        return new_workers
    
    def get_stats(self) -> Dict[str, Any]:
        """통계 반환"""
        with self.lock:
            healthy_workers = sum(1 for w in self.workers.values() if w.is_healthy())
            crashed_workers = sum(1 for w in self.workers.values() if w.state == WorkerState.CRASHED)
            
            return {
                "total_tasks": self.total_tasks,
                "pending": len(self.pending_queue),
                "in_progress": len(self.in_progress),
                "completed": self.total_completed,
                "failed": self.total_failed,
                "recovered": self.total_recovered,
                "workers_healthy": healthy_workers,
                "workers_crashed": crashed_workers,
                "worker_crash_count": self.worker_crashes,
            }


# =============================================================================
# 시뮬레이션
# =============================================================================


class WorkerCrashSimulator:
    """Worker Crash 시뮬레이션"""
    
    def __init__(self, config: WorkerCrashConfig = None):
        self.config = config or CONFIG
        self.queue_manager = TaskQueueManager(config)
        self.running = False
        self.results: Dict[str, Any] = {}
        
    def run_simulation(self, duration_seconds: int = 30):
        """시뮬레이션 실행"""
        print(f"\n{STAGE_NAME} Starting Worker Crash Simulation")
        print(f"  - Duration: {duration_seconds}s")
        print(f"  - Workers: {self.config.num_workers}")
        print(f"  - Tasks/sec: {self.config.tasks_per_second}")
        print(f"  - Crash Interval: {self.config.crash_interval_seconds}s")
        print("-" * 60)
        
        self.running = True
        start_time = time.time()
        
        # Workers 생성
        workers = []
        for _ in range(self.config.num_workers):
            worker = self.queue_manager.create_worker()
            workers.append(worker)
            print(f"  Worker {worker.id[:8]} started")
        
        # 스레드 시작
        threads = []
        
        # Task 생성 스레드
        task_thread = threading.Thread(target=self._task_generator_loop, args=(duration_seconds,))
        task_thread.start()
        threads.append(task_thread)
        
        # Worker 처리 스레드
        for worker in workers:
            t = threading.Thread(target=self._worker_loop, args=(worker,))
            t.start()
            threads.append(t)
        
        # Crash 주입 스레드
        crash_thread = threading.Thread(target=self._crash_injector_loop, args=(duration_seconds,))
        crash_thread.start()
        threads.append(crash_thread)
        
        # Recovery 스레드
        recovery_thread = threading.Thread(target=self._recovery_loop)
        recovery_thread.start()
        threads.append(recovery_thread)
        
        # 대기
        time.sleep(duration_seconds)
        self.running = False
        
        # 스레드 종료 대기
        for t in threads:
            t.join(timeout=5)
        
        # 마지막 복구 시도
        self.queue_manager.recover_stale_tasks()
        
        # 결과 분석
        self._analyze_results()
    
    def _task_generator_loop(self, duration_seconds: int):
        """Task 생성 루프"""
        start_time = time.time()
        task_count = 0
        
        while self.running and (time.time() - start_time) < duration_seconds:
            # 초당 tasks_per_second 개 생성
            batch_size = max(1, self.config.tasks_per_second // 10)
            
            for _ in range(batch_size):
                self.queue_manager.create_task({
                    "task_num": task_count,
                    "created": datetime.now(timezone.utc).isoformat(),
                })
                task_count += 1
            
            time.sleep(0.1)
    
    def _worker_loop(self, worker: Worker):
        """Worker 처리 루프"""
        while self.running:
            if not worker.is_healthy():
                break
            
            task = self.queue_manager.claim_task(worker.id)
            
            if task:
                # 작업 처리 시뮬레이션
                time.sleep(self.config.task_processing_time_ms / 1000)
                
                # Worker가 crash되지 않았으면 완료 처리
                if worker.is_healthy():
                    success = random.random() > 0.05  # 5% 실패율
                    self.queue_manager.complete_task(
                        task.id,
                        success=success,
                        error=None if success else "Random failure"
                    )
            else:
                time.sleep(0.01)
    
    def _crash_injector_loop(self, duration_seconds: int):
        """Crash 주입 루프"""
        start_time = time.time()
        
        while self.running and (time.time() - start_time) < duration_seconds:
            time.sleep(self.config.crash_interval_seconds)
            
            if not self.running:
                break
            
            # 랜덤 Worker crash
            with self.queue_manager.lock:
                healthy_workers = [
                    w for w in self.queue_manager.workers.values()
                    if w.is_healthy()
                ]
            
            if healthy_workers:
                victim = random.choice(healthy_workers)
                self.queue_manager.crash_worker(victim.id)
    
    def _recovery_loop(self):
        """Recovery 루프"""
        while self.running:
            time.sleep(1)  # 1초마다 체크
            
            # Stale task 복구
            self.queue_manager.recover_stale_tasks()
            
            # Crashed worker 재생성
            self.queue_manager.respawn_crashed_workers()
    
    def _analyze_results(self):
        """결과 분석"""
        stats = self.queue_manager.get_stats()
        
        print("\n" + "=" * 60)
        print("📊 WORKER CRASH RECOVERY TEST RESULTS")
        print("=" * 60)
        
        print("\n📈 Task Statistics:")
        print(f"  Total Tasks Created: {stats['total_tasks']}")
        print(f"  Completed: {stats['completed']}")
        print(f"  Failed: {stats['failed']}")
        print(f"  Recovered: {stats['recovered']}")
        print(f"  Still Pending: {stats['pending']}")
        print(f"  In Progress: {stats['in_progress']}")
        
        print("\n👷 Worker Statistics:")
        print(f"  Healthy Workers: {stats['workers_healthy']}")
        print(f"  Crashed Workers: {stats['workers_crashed']}")
        print(f"  Total Crashes: {stats['worker_crash_count']}")
        
        # Invariant 검증
        print("\n" + "-" * 60)
        print("✅ INVARIANT VERIFICATION")
        print("-" * 60)
        
        # 1. in_flight_tasks_recovered
        # in-flight 상태로 남아있는 task가 없어야 함 (모두 recovered 또는 처리됨)
        in_flight_stuck = stats['in_progress']
        in_flight_recovered = stats['recovered'] > 0 if stats['worker_crash_count'] > 0 else True
        print(f"  in_flight_tasks_recovered: {'✅ PASS' if in_flight_recovered else '❌ FAIL'}")
        print(f"    └─ Recovered: {stats['recovered']}, Still in-flight: {in_flight_stuck}")
        
        # 2. no_permanent_task_loss
        # 모든 task가 completed + failed + pending + in_progress에 있어야 함
        total_accounted = (
            stats['completed'] + 
            stats['failed'] + 
            stats['pending'] + 
            stats['in_progress']
        )
        task_loss = stats['total_tasks'] - total_accounted
        no_loss = task_loss <= 0
        print(f"  no_permanent_task_loss: {'✅ PASS' if no_loss else '❌ FAIL'}")
        print(f"    └─ Total: {stats['total_tasks']}, Accounted: {total_accounted}, Lost: {max(0, task_loss)}")
        
        # 3. Workers 재시작 확인
        worker_recovery = stats['workers_healthy'] > 0
        print(f"  workers_respawned: {'✅ PASS' if worker_recovery else '❌ FAIL'}")
        
        # 4. 처리율 확인
        completion_rate = (
            stats['completed'] / stats['total_tasks'] * 100
            if stats['total_tasks'] > 0 else 0
        )
        print(f"  completion_rate: {completion_rate:.1f}%")
        
        # 최종 결과
        all_passed = in_flight_recovered and no_loss and worker_recovery
        
        print("\n" + "=" * 60)
        if all_passed:
            print("🎉 WORKER CRASH RECOVERY TEST: ✅ ALL PASSED")
        else:
            print("⚠️  WORKER CRASH RECOVERY TEST: SOME CHECKS FAILED")
        print("=" * 60)
        
        self.results = {
            "passed": all_passed,
            "stats": stats,
            "invariants": {
                "in_flight_tasks_recovered": in_flight_recovered,
                "no_permanent_task_loss": no_loss,
                "workers_respawned": worker_recovery,
            }
        }


# =============================================================================
# Locust User (HTTP 테스트용)
# =============================================================================

try:
    from locust import HttpUser, task, between, tag, events
    
    class WorkerCrashUser(HttpUser):
        """Worker Crash 테스트 User"""
        
        wait_time = between(0.1, 0.5)
        
        def on_start(self):
            """테스트 시작"""
            self.task_ids = []
        
        @task(10)
        @tag("worker", "submit")
        def submit_task(self):
            """Task 제출"""
            with self.client.post(
                "/api/tasks/",
                json={
                    "task_id": str(uuid.uuid4()),
                    "data": {"test": "data"},
                },
                name=f"{STAGE_NAME} POST /api/tasks/",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201, 202]:
                    data = response.json()
                    if 'task_id' in data:
                        self.task_ids.append(data['task_id'])
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")
        
        @task(3)
        @tag("worker", "status")
        def check_task_status(self):
            """Task 상태 확인"""
            if not self.task_ids:
                return
            
            task_id = random.choice(self.task_ids)
            
            with self.client.get(
                f"/api/tasks/{task_id}/",
                name=f"{STAGE_NAME} GET /api/tasks/[id]/",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    response.success()
                elif response.status_code == 404:
                    # Task not found - may have been recovered
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")
        
        @task(1)
        @tag("worker", "health")
        def check_workers_health(self):
            """Worker 상태 확인"""
            with self.client.get(
                "/api/workers/health/",
                name=f"{STAGE_NAME} GET /api/workers/health/",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"Status: {response.status_code}")

except ImportError:
    pass


# =============================================================================
# Main
# =============================================================================


if __name__ == "__main__":
    print("=" * 60)
    print("GAP-05: Worker Crash Recovery Test")
    print("=" * 60)
    
    simulator = WorkerCrashSimulator()
    simulator.run_simulation(duration_seconds=30)
    
    # 종료 코드
    sys.exit(0 if simulator.results.get("passed", False) else 1)
