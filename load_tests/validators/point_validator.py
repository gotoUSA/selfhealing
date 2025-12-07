"""
포인트 검증기 - Point Validator

결제 전후 포인트 정합성 검증
"""

from typing import Optional, Dict, Any


class PointValidator:
    """포인트 정합성 검증"""

    def __init__(self, client, stage_name: str = ""):
        """
        Args:
            client: Locust HttpUser client
            stage_name: 메트릭 prefix용 Stage 이름
        """
        self.client = client
        self.stage_name = stage_name
        self._point_snapshots: Dict[int, int] = {}

    def get_points(self, user_id: int) -> Optional[int]:
        """
        현재 포인트 조회

        Note: 실제 API 엔드포인트에 맞게 수정 필요
        """
        request_name = f"{self.stage_name} [Validator] GET User Points".strip()

        # TODO: 실제 포인트 조회 API 엔드포인트로 변경
        response = self.client.get(
            f"/api/users/{user_id}/points/",
            name=request_name,
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("points", data.get("balance", 0))
        return None

    def snapshot_points(self, user_id: int) -> Optional[int]:
        """포인트 스냅샷 저장"""
        points = self.get_points(user_id)
        if points is not None:
            self._point_snapshots[user_id] = points
        return points

    def get_snapshot(self, user_id: int) -> Optional[int]:
        """저장된 스냅샷 조회"""
        return self._point_snapshots.get(user_id)

    def clear_snapshots(self):
        """스냅샷 초기화"""
        self._point_snapshots.clear()

    def validate_earn(
        self,
        user_id: int,
        before: int,
        earned: int,
        after: int,
    ) -> Dict[str, Any]:
        """
        포인트 적립 검증

        Args:
            user_id: 사용자 ID
            before: 적립 전 포인트
            earned: 적립 포인트
            after: 적립 후 포인트

        Returns:
            검증 결과 딕셔너리
        """
        expected = before + earned

        result = {
            "user_id": user_id,
            "before": before,
            "earned": earned,
            "after": after,
            "expected": expected,
            "valid": after == expected,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(f"Point earn mismatch: expected={expected}, actual={after}")

        return result

    def validate_use(
        self,
        user_id: int,
        before: int,
        used: int,
        after: int,
    ) -> Dict[str, Any]:
        """
        포인트 사용 검증

        Args:
            user_id: 사용자 ID
            before: 사용 전 포인트
            used: 사용 포인트
            after: 사용 후 포인트

        Returns:
            검증 결과 딕셔너리
        """
        expected = before - used

        result = {
            "user_id": user_id,
            "before": before,
            "used": used,
            "after": after,
            "expected": expected,
            "valid": after == expected and after >= 0,
            "errors": [],
        }

        if after < 0:
            result["valid"] = False
            result["errors"].append(f"Negative points: {after}")

        if after != expected:
            result["valid"] = False
            result["errors"].append(f"Point use mismatch: expected={expected}, actual={after}")

        return result

    def validate_rollback(
        self,
        user_id: int,
        before: int,
        after: int,
    ) -> Dict[str, Any]:
        """
        롤백 후 포인트 복구 검증

        Args:
            user_id: 사용자 ID
            before: 롤백 전 포인트 (원래 포인트)
            after: 롤백 후 포인트

        Returns:
            검증 결과 딕셔너리
        """
        result = {
            "user_id": user_id,
            "before": before,
            "after": after,
            "valid": before == after,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(f"Point rollback failed: before={before}, after={after}")

        return result

    def validate_point_change(
        self,
        user_id: int,
        expected_change: int,
    ) -> Dict[str, Any]:
        """
        스냅샷 대비 포인트 변화 검증

        Args:
            user_id: 사용자 ID
            expected_change: 예상 변화량 (음수: 감소, 양수: 증가)

        Returns:
            검증 결과 딕셔너리
        """
        before = self._point_snapshots.get(user_id)
        if before is None:
            return {
                "user_id": user_id,
                "valid": False,
                "errors": ["No snapshot found"],
            }

        after = self.get_points(user_id)
        if after is None:
            return {
                "user_id": user_id,
                "valid": False,
                "errors": ["Failed to get current points"],
            }

        expected_after = before + expected_change

        result = {
            "user_id": user_id,
            "before": before,
            "after": after,
            "expected_change": expected_change,
            "expected_after": expected_after,
            "actual_change": after - before,
            "valid": after == expected_after,
            "errors": [],
        }

        if not result["valid"]:
            result["errors"].append(f"Point change mismatch: expected={expected_change}, " f"actual={result['actual_change']}")

        return result
