#!/usr/bin/env python
"""
Cross-platform Locust Stage Runner

Stage 기반 부하 테스트 실행기

Usage:
    # 단일 Stage 실행
    python run_stage.py stage0_smoke
    python run_stage.py stage1_happy --host http://staging:8000

    # 프로파일 실행
    python run_stage.py --profile quick
    python run_stage.py --profile full --host http://staging:8000

    # 스케일링 테스트
    python run_stage.py stage1_happy --scaling

    # 사용 가능한 Stage/프로파일 목록
    python run_stage.py --list
"""

import argparse
import subprocess
import sys
import os
import time
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    print("PyYAML not installed. Installing...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml"])
    import yaml


# 경로 설정
RUNNER_DIR = Path(__file__).parent
LOAD_TESTS_DIR = RUNNER_DIR.parent
PROJECT_ROOT = LOAD_TESTS_DIR.parent
CONFIG_PATH = RUNNER_DIR / "config.yaml"
REPORTS_DIR = LOAD_TESTS_DIR / "reports"


def load_config() -> dict:
    """설정 파일 로드"""
    if not CONFIG_PATH.exists():
        print(f"Error: Config file not found: {CONFIG_PATH}")
        sys.exit(1)

    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_reports_dir():
    """리포트 디렉토리 생성"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def build_locust_command(
    stage_config: dict,
    host: str,
    headless: bool = True,
    report_name: str = None,
) -> list:
    """Locust 실행 명령어 생성"""

    file_path = LOAD_TESTS_DIR / stage_config["file"]

    cmd = [
        sys.executable,
        "-m",
        "locust",
        "-f",
        str(file_path),
        "--host",
        host,
        "--users",
        str(stage_config["users"]),
        "--spawn-rate",
        str(stage_config["spawn_rate"]),
        "--run-time",
        stage_config["duration"],
    ]

    if headless:
        cmd.append("--headless")

    if report_name:
        ensure_reports_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = REPORTS_DIR / f"{report_name}_{timestamp}.html"
        cmd.extend(["--html", str(report_path)])

    return cmd


def is_running_in_docker() -> bool:
    """Docker 컨테이너 내부에서 실행 중인지 확인"""
    # 1. /.dockerenv 파일 존재 확인
    if Path("/.dockerenv").exists():
        return True
    # 2. /proc/1/cgroup에서 docker 문자열 확인
    try:
        with open("/proc/1/cgroup", "r") as f:
            return "docker" in f.read()
    except (FileNotFoundError, PermissionError):
        pass
    return False


def run_stage(
    stage_name: str,
    config: dict,
    host: str = None,
    headless: bool = True,
    env_override: dict = None,
) -> int:
    """단일 Stage 실행"""

    stage = config["stages"].get(stage_name)
    if not stage:
        print(f"Error: Unknown stage: {stage_name}")
        print(f"Available stages: {', '.join(config['stages'].keys())}")
        return 1

    # 호스트 결정: Docker 환경이면 docker_host 사용
    if host:
        pass  # 명시적으로 지정된 경우 그대로 사용
    elif is_running_in_docker():
        host = config["defaults"].get("docker_host", "http://web:8000")
    else:
        host = config["defaults"]["host"]

    # 환경변수 설정
    env = os.environ.copy()
    if stage.get("env"):
        env.update(stage["env"])
    if env_override:
        env.update(env_override)

    # Windows UTF-8 설정
    env["PYTHONUTF8"] = "1"

    # 명령어 생성
    cmd = build_locust_command(
        stage,
        host,
        headless,
        report_name=stage_name,
    )

    print("\n" + "=" * 60)
    print(f"🚀 Running: {stage_name}")
    print(f"   Description: {stage.get('description', 'N/A')}")
    print(f"   Users: {stage['users']}, Spawn Rate: {stage['spawn_rate']}")
    print(f"   Duration: {stage['duration']}")
    print(f"   Host: {host}")
    print("=" * 60)
    print(f"Command: {' '.join(cmd)}\n")

    # 실행
    start_time = time.time()
    result = subprocess.run(cmd, env=env)
    elapsed = time.time() - start_time

    print(f"\n⏱️  {stage_name} completed in {elapsed:.1f}s")

    return result.returncode


def run_profile(
    profile_name: str,
    config: dict,
    host: str = None,
    headless: bool = True,
) -> int:
    """프로파일 (여러 Stage 순차 실행)"""

    profile = config["profiles"].get(profile_name)
    if not profile:
        print(f"Error: Unknown profile: {profile_name}")
        print(f"Available profiles: {', '.join(config['profiles'].keys())}")
        return 1

    stages = profile["stages"]

    print("\n" + "=" * 60)
    print(f"📋 Profile: {profile_name}")
    print(f"   Description: {profile.get('description', 'N/A')}")
    print(f"   Stages: {', '.join(stages)}")
    print("=" * 60)

    total_start = time.time()
    failed_stages = []

    for i, stage_name in enumerate(stages, 1):
        print(f"\n[{i}/{len(stages)}] Running {stage_name}...")

        returncode = run_stage(stage_name, config, host, headless)

        if returncode != 0:
            failed_stages.append(stage_name)

            # required stage 실패 시 중단
            stage = config["stages"].get(stage_name, {})
            if stage.get("required", False):
                print(f"\n❌ Required stage '{stage_name}' failed. Aborting.")
                break

    total_elapsed = time.time() - total_start

    print("\n" + "=" * 60)
    print(f"📊 Profile '{profile_name}' Summary")
    print("=" * 60)
    print(f"Total Duration: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"Stages Run: {len(stages)}")

    if failed_stages:
        print(f"Failed Stages: {', '.join(failed_stages)}")
        return 1
    else:
        print("All stages passed! ✅")
        return 0


def run_scaling_test(
    stage_name: str,
    config: dict,
    host: str = None,
    headless: bool = True,
) -> int:
    """스케일링 테스트 (여러 user level로 실행)"""

    scaling_config = config.get("scaling", {}).get(stage_name)
    if not scaling_config:
        print(f"Error: No scaling config for: {stage_name}")
        return 1

    stage = config["stages"].get(stage_name)
    if not stage:
        print(f"Error: Unknown stage: {stage_name}")
        return 1

    user_levels = scaling_config.get("user_levels", [50, 100, 200])
    duration = scaling_config.get("duration_per_level", "2m")

    print("\n" + "=" * 60)
    print(f"📈 Scaling Test: {stage_name}")
    print(f"   User Levels: {user_levels}")
    print(f"   Duration per Level: {duration}")
    print("=" * 60)

    for users in user_levels:
        # 임시로 users 수정
        modified_stage = stage.copy()
        modified_stage["users"] = users
        modified_stage["duration"] = duration

        print(f"\n--- Testing with {users} users ---")

        returncode = run_stage(
            f"{stage_name}_u{users}",
            {"stages": {f"{stage_name}_u{users}": modified_stage}, "defaults": config["defaults"]},
            host,
            headless,
        )

        if returncode != 0:
            print(f"Scaling test failed at {users} users")
            return returncode

    print("\n✅ Scaling test completed!")
    return 0


def list_stages_and_profiles(config: dict):
    """사용 가능한 Stage/프로파일 목록 출력"""

    print("\n📋 Available Stages:")
    print("-" * 60)
    for name, stage in config["stages"].items():
        tags = ", ".join(stage.get("tags", []))
        desc = stage.get("description", "")
        required = " [REQUIRED]" if stage.get("required") else ""
        optional = " [OPTIONAL]" if stage.get("optional") else ""
        print(f"  {name:<20} {desc}{required}{optional}")
        print(f"    Users: {stage['users']}, Duration: {stage['duration']}, Tags: {tags}")

    print("\n📋 Available Profiles:")
    print("-" * 60)
    for name, profile in config["profiles"].items():
        desc = profile.get("description", "")
        stages = ", ".join(profile["stages"])
        print(f"  {name:<15} {desc}")
        print(f"    Stages: {stages}")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Locust Stage Runner - Payment Load & Chaos Test Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_stage.py stage0_smoke
  python run_stage.py --profile quick
  python run_stage.py stage1_happy --scaling
  python run_stage.py --list
        """,
    )

    parser.add_argument(
        "stage",
        nargs="?",
        help="Stage name to run (e.g., stage0_smoke, stage1_happy)",
    )

    parser.add_argument(
        "--profile",
        "-p",
        help="Run a predefined profile (e.g., quick, full, chaos)",
    )

    parser.add_argument(
        "--host",
        "-H",
        help="Override host URL (default: http://localhost:8000)",
    )

    parser.add_argument(
        "--scaling",
        "-s",
        action="store_true",
        help="Run scaling test with multiple user levels",
    )

    parser.add_argument(
        "--web",
        "-w",
        action="store_true",
        help="Run with web UI (not headless)",
    )

    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List available stages and profiles",
    )

    parser.add_argument(
        "--docker",
        action="store_true",
        help="Use Docker host (http://web:8000)",
    )

    args = parser.parse_args()

    config = load_config()

    if args.list:
        list_stages_and_profiles(config)
        return 0

    # 호스트 결정
    host = args.host
    if args.docker:
        host = config["defaults"].get("docker_host", "http://web:8000")

    headless = not args.web

    if args.profile:
        return run_profile(args.profile, config, host, headless)

    if args.stage:
        if args.scaling:
            return run_scaling_test(args.stage, config, host, headless)
        else:
            return run_stage(args.stage, config, host, headless)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
