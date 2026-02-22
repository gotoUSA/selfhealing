"""
Chaos Engineering API Client.

킬스위치, 안전 체크, 스케줄, 카오스 실험 관련 API.
"""

from typing import Any, Dict, Optional

from .base import BaseClient


class ChaosClient:
    """Chaos Engineering API 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
    
    # =========================================================================
    # Kill Switch API (Admin)
    # =========================================================================
    
    def get_kill_switch_status(self) -> Dict[str, Any]:
        """
        킬스위치 상태 조회.
        
        GET /kill-switch/status/
        """
        response = self.client.api_get("kill-switch/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def activate_kill_switch(
        self,
        target: str,
        reason: str = "Manual activation",
    ) -> Dict[str, Any]:
        """
        킬스위치 활성화.
        
        POST /kill-switch/activate/
        
        Args:
            target: 비활성화할 대상 (feature name, service name 등)
            reason: 활성화 사유
        """
        response = self.client.api_post(
            "kill-switch/activate/",
            json={"target": target, "reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def deactivate_kill_switch(
        self,
        target: str,
        reason: str = "Manual deactivation",
    ) -> Dict[str, Any]:
        """
        킬스위치 비활성화.
        
        POST /kill-switch/deactivate/
        """
        response = self.client.api_post(
            "kill-switch/deactivate/",
            json={"target": target, "reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def list_kill_switches(self) -> Dict[str, Any]:
        """
        모든 킬스위치 목록 조회.
        
        GET /kill-switch/list/
        """
        response = self.client.api_get("kill-switch/list/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Safety Check API (Operator)
    # =========================================================================
    
    def run_safety_check(self) -> Dict[str, Any]:
        """
        안전 체크 실행.
        
        POST /safety-check/run/
        """
        response = self.client.api_post("safety-check/run/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_safety_status(self) -> Dict[str, Any]:
        """
        안전 상태 조회.
        
        GET /safety-check/status/
        """
        response = self.client.api_get("safety-check/status/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_safety_history(self, limit: int = 50) -> Dict[str, Any]:
        """
        안전 체크 이력 조회.
        
        GET /safety-check/history/
        """
        response = self.client.api_get("safety-check/history/", params={"limit": limit})
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Chaos Schedule API (Admin)
    # =========================================================================
    
    def list_schedules(self) -> Dict[str, Any]:
        """
        카오스 스케줄 목록 조회.
        
        GET /chaos/schedules/
        """
        response = self.client.api_get("chaos/schedules/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_schedule(self, schedule_id: str) -> Dict[str, Any]:
        """
        스케줄 상세 조회.
        
        GET /chaos/schedules/{schedule_id}/
        """
        response = self.client.api_get(f"chaos/schedules/{schedule_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def create_schedule(
        self,
        name: str,
        experiment_type: str,
        cron_expression: str,
        config: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ) -> Dict[str, Any]:
        """
        카오스 스케줄 생성.
        
        POST /chaos/schedules/
        """
        data = {
            "name": name,
            "experiment_type": experiment_type,
            "cron_expression": cron_expression,
            "enabled": enabled,
        }
        if config:
            data["config"] = config
        
        response = self.client.api_post("chaos/schedules/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def update_schedule(
        self,
        schedule_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        스케줄 수정.
        
        PATCH /chaos/schedules/{schedule_id}/
        """
        response = self.client.api_patch(f"chaos/schedules/{schedule_id}/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def delete_schedule(self, schedule_id: str) -> Dict[str, Any]:
        """
        스케줄 삭제.
        
        DELETE /chaos/schedules/{schedule_id}/
        """
        response = self.client.api_delete(f"chaos/schedules/{schedule_id}/")
        if response.status_code in (200, 204):
            return {"status": "deleted"}
        return {"status": "error", "status_code": response.status_code}
    
    def toggle_schedule(self, schedule_id: str, enabled: bool) -> Dict[str, Any]:
        """
        스케줄 활성화/비활성화.
        
        POST /chaos/schedules/{schedule_id}/toggle/
        """
        response = self.client.api_post(
            f"chaos/schedules/{schedule_id}/toggle/",
            json={"enabled": enabled},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Chaos Experiment API (XTest)
    # =========================================================================
    
    def list_experiments(self) -> Dict[str, Any]:
        """
        실험 목록 조회.
        
        GET /chaos/experiments/
        """
        response = self.client.api_get("chaos/experiments/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_experiment(self, experiment_id: str) -> Dict[str, Any]:
        """
        실험 상세 조회.
        
        GET /chaos/experiments/{experiment_id}/
        """
        response = self.client.api_get(f"chaos/experiments/{experiment_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_run_experiment(
        self,
        experiment_type: str,
        target: Optional[str] = None,
        duration_seconds: int = 60,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        XTest 모드에서 카오스 실험 실행.
        
        POST /xtest/chaos/run/
        
        Args:
            experiment_type: 실험 유형 (latency, failure, resource-exhaustion 등)
            target: 대상 컴포넌트
            duration_seconds: 실험 지속 시간
            config: 추가 설정
        """
        data = {
            "experiment_type": experiment_type,
            "duration_seconds": duration_seconds,
        }
        if target:
            data["target"] = target
        if config:
            data["config"] = config
        
        response = self.client.xtest_post("chaos/run/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_stop_experiment(self, experiment_id: str) -> Dict[str, Any]:
        """
        XTest 모드에서 실험 중지.
        
        POST /xtest/chaos/stop/
        """
        response = self.client.xtest_post(
            "chaos/stop/",
            json={"experiment_id": experiment_id},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_inject_latency(
        self,
        target: str,
        latency_ms: int = 500,
        duration_seconds: int = 60,
    ) -> Dict[str, Any]:
        """
        XTest 모드에서 지연 주입.
        
        POST /xtest/chaos/inject-latency/
        """
        response = self.client.xtest_post(
            "chaos/inject-latency/",
            json={
                "target": target,
                "latency_ms": latency_ms,
                "duration_seconds": duration_seconds,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_inject_failure(
        self,
        target: str,
        failure_rate: float = 0.5,
        duration_seconds: int = 60,
    ) -> Dict[str, Any]:
        """
        XTest 모드에서 실패 주입.
        
        POST /xtest/chaos/inject-failure/
        """
        response = self.client.xtest_post(
            "chaos/inject-failure/",
            json={
                "target": target,
                "failure_rate": failure_rate,
                "duration_seconds": duration_seconds,
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def xtest_reset_all(self) -> Dict[str, Any]:
        """
        XTest 모드에서 모든 카오스 실험 리셋.
        
        POST /xtest/chaos/reset/
        """
        response = self.client.xtest_post("chaos/reset/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def is_kill_switch_active(self, target: str) -> bool:
        """특정 킬스위치 활성화 여부."""
        status = self.get_kill_switch_status()
        active_switches = status.get("active_switches", [])
        return target in active_switches
    
    def is_safe_to_deploy(self) -> bool:
        """배포 안전 여부."""
        status = self.get_safety_status()
        return status.get("is_safe", False)
    
    # =========================================================================
    # Chaos Config API (신규 추가 - Gap 분석 기반)
    # Reference: selfhealing/api/django/urls.py
    # =========================================================================
    
    def get_safety_guard_config(self) -> Dict[str, Any]:
        """
        안전장치 설정 조회.
        
        GET /chaos/config/safety-guard/
        """
        response = self.client.api_get("chaos/config/safety-guard/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_safety_guard_config(self, **kwargs) -> Dict[str, Any]:
        """
        안전장치 설정 변경.
        
        POST /chaos/config/safety-guard/
        """
        response = self.client.api_post("chaos/config/safety-guard/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_blast_radius_policy(self) -> Dict[str, Any]:
        """
        폭발반경 정책 조회.
        
        GET /chaos/config/blast-radius/
        """
        response = self.client.api_get("chaos/config/blast-radius/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_blast_radius_policy(self, **kwargs) -> Dict[str, Any]:
        """
        폭발반경 정책 설정.
        
        POST /chaos/config/blast-radius/
        """
        response = self.client.api_post("chaos/config/blast-radius/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_scheduler_config(self) -> Dict[str, Any]:
        """
        스케줄러 설정 조회.
        
        GET /chaos/config/scheduler/
        """
        response = self.client.api_get("chaos/config/scheduler/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_scheduler_config(self, **kwargs) -> Dict[str, Any]:
        """
        스케줄러 설정 변경.
        
        POST /chaos/config/scheduler/
        """
        response = self.client.api_post("chaos/config/scheduler/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_report_config(self) -> Dict[str, Any]:
        """
        리포트 설정 조회.
        
        GET /chaos/config/reports/
        """
        response = self.client.api_get("chaos/config/reports/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_report_config(self, **kwargs) -> Dict[str, Any]:
        """
        리포트 설정 변경.
        
        POST /chaos/config/reports/
        """
        response = self.client.api_post("chaos/config/reports/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_stop_conditions_config(self) -> Dict[str, Any]:
        """
        정지 조건 설정 조회.
        
        GET /chaos/config/stop-conditions/
        """
        response = self.client.api_get("chaos/config/stop-conditions/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_stop_conditions_config(self, **kwargs) -> Dict[str, Any]:
        """
        정지 조건 설정 변경.
        
        POST /chaos/config/stop-conditions/
        """
        response = self.client.api_post("chaos/config/stop-conditions/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_ttl_config(self) -> Dict[str, Any]:
        """
        TTL 설정 조회.
        
        GET /chaos/config/ttl/
        """
        response = self.client.api_get("chaos/config/ttl/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_ttl_config(self, **kwargs) -> Dict[str, Any]:
        """
        TTL 설정 변경.
        
        POST /chaos/config/ttl/
        """
        response = self.client.api_post("chaos/config/ttl/", json=kwargs)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_dry_run_config(self) -> Dict[str, Any]:
        """
        드라이런 설정 조회.
        
        GET /chaos/config/dry-run/
        """
        response = self.client.api_get("chaos/config/dry-run/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_dry_run_config(self, enabled: bool = True) -> Dict[str, Any]:
        """
        드라이런 설정 변경.
        
        POST /chaos/config/dry-run/
        """
        response = self.client.api_post("chaos/config/dry-run/", json={"enabled": enabled})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Chaos Schedule Approval & Execute API (신규 추가)
    # =========================================================================
    
    def approve_schedule(
        self,
        schedule_id: str,
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        스케줄 승인.
        
        POST /chaos/schedules/{schedule_id}/approve/
        """
        data = {}
        if comment:
            data["comment"] = comment
        
        response = self.client.api_post(f"chaos/schedules/{schedule_id}/approve/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def execute_schedule(self, schedule_id: str) -> Dict[str, Any]:
        """
        스케줄 즉시 실행.
        
        POST /chaos/schedules/{schedule_id}/execute/
        """
        response = self.client.api_post(f"chaos/schedules/{schedule_id}/execute/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Chaos Kill Switch API (Self-Healing 전용)
    # =========================================================================
    
    def get_chaos_kill_switch(self) -> Dict[str, Any]:
        """
        카오스 킬스위치 상태 조회.
        
        GET /chaos/kill-switch/
        """
        response = self.client.api_get("chaos/kill-switch/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def set_chaos_kill_switch(self, enabled: bool, reason: str = "") -> Dict[str, Any]:
        """
        카오스 킬스위치 설정.
        
        POST /chaos/kill-switch/
        """
        response = self.client.api_post(
            "chaos/kill-switch/",
            json={"enabled": enabled, "reason": reason},
        )
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def kill_all_chaos(self, reason: str = "Emergency stop") -> Dict[str, Any]:
        """
        모든 카오스 실험 즉시 중지.
        
        POST /chaos/control/kill-all/
        """
        response = self.client.api_post("chaos/control/kill-all/", json={"reason": reason})
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Chaos Safety & Blast Radius Check API (신규 추가)
    # =========================================================================
    
    def chaos_safety_check(self) -> Dict[str, Any]:
        """
        카오스 안전 체크.
        
        GET /chaos/safety-check/
        POST /chaos/safety-check/
        """
        response = self.client.api_get("chaos/safety-check/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def run_chaos_safety_check(self) -> Dict[str, Any]:
        """
        카오스 안전 체크 실행.
        
        POST /chaos/safety-check/
        """
        response = self.client.api_post("chaos/safety-check/")
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def check_blast_radius(
        self,
        experiment_type: Optional[str] = None,
        target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        폭발반경 체크.
        
        POST /chaos/blast-radius/check/
        """
        data = {}
        if experiment_type:
            data["experiment_type"] = experiment_type
        if target:
            data["target"] = target
        
        response = self.client.api_post("chaos/blast-radius/check/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    # =========================================================================
    # Chaos Reports API (신규 추가)
    # =========================================================================
    
    def list_chaos_reports(
        self,
        limit: int = 50,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        카오스 리포트 목록 조회.
        
        GET /chaos/reports/
        """
        params = {"limit": limit}
        if status:
            params["status"] = status
        
        response = self.client.api_get("chaos/reports/", params=params)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_chaos_report(self, report_id: str) -> Dict[str, Any]:
        """
        카오스 리포트 상세 조회.
        
        GET /chaos/reports/{report_id}/
        """
        response = self.client.api_get(f"chaos/reports/{report_id}/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def generate_chaos_report(
        self,
        experiment_id: Optional[str] = None,
        schedule_id: Optional[str] = None,
        include_metrics: bool = True,
    ) -> Dict[str, Any]:
        """
        카오스 리포트 생성.
        
        POST /chaos/reports/generate/
        """
        data = {"include_metrics": include_metrics}
        if experiment_id:
            data["experiment_id"] = experiment_id
        if schedule_id:
            data["schedule_id"] = schedule_id
        
        response = self.client.api_post("chaos/reports/generate/", json=data)
        if response.status_code in (200, 201):
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def get_grade_history(self, limit: int = 50) -> Dict[str, Any]:
        """
        카오스 등급 이력 조회.
        
        GET /chaos/reports/grades/
        """
        response = self.client.api_get("chaos/reports/grades/", params={"limit": limit})
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
    
    def list_pending_approvals(self) -> Dict[str, Any]:
        """
        대기 중인 승인 목록 조회.
        
        GET /chaos/pending-approvals/
        """
        response = self.client.api_get("chaos/pending-approvals/")
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "status_code": response.status_code}
