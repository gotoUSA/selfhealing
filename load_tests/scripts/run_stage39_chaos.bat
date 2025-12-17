@echo off
REM Stage 39: Docker Chaos Test Runner (Windows)
REM 실제 Docker 컨테이너를 죽이고 재시작하면서 Self-Healing 테스트

setlocal enabledelayedexpansion

echo ==============================================
echo  Stage 39: Docker Container Chaos Tests
echo ==============================================
echo.

REM 프로젝트 디렉토리로 이동
cd /d "%~dp0.."

REM Docker 확인
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker가 실행되고 있지 않습니다.
    exit /b 1
)

if "%1"=="" goto help
if "%1"=="help" goto help
if "%1"=="--help" goto help
if "%1"=="-h" goto help
if "%1"=="start" goto start
if "%1"=="stop" goto stop
if "%1"=="test" goto test
if "%1"=="logs" goto logs
if "%1"=="status" goto status
if "%1"=="clean" goto clean
if "%1"=="all" goto all

echo [ERROR] 알 수 없는 명령어: %1
goto help

:help
echo Usage: %~nx0 [command]
echo.
echo Commands:
echo   start     - 서비스 시작 (빌드 포함)
echo   stop      - 서비스 중지
echo   test      - Chaos 테스트 실행
echo   logs      - 서비스 로그 확인
echo   status    - 컨테이너 상태 확인
echo   clean     - 모든 리소스 정리
echo   all       - 전체 실행 (start -^> test -^> logs)
echo.
goto end

:start
echo [INFO] Stage 39 서비스를 시작합니다...
docker-compose -f docker-compose.stage39.yml up -d --build

echo [INFO] 서비스가 준비될 때까지 대기 중...
timeout /t 15 /nobreak >nul

REM Health check
set RETRY_COUNT=0
:healthcheck
if !RETRY_COUNT! GEQ 30 goto healthcheck_timeout
curl -s http://localhost:8000/api/health/ >nul 2>&1
if not errorlevel 1 (
    echo [SUCCESS] 웹 서비스가 준비되었습니다!
    goto status
)
set /a RETRY_COUNT+=1
echo|set /p="."
timeout /t 2 /nobreak >nul
goto healthcheck

:healthcheck_timeout
echo.
echo [WARNING] 서비스가 준비되지 않았을 수 있습니다. 로그를 확인하세요.
goto status

:stop
echo [INFO] Stage 39 서비스를 중지합니다...
docker-compose -f docker-compose.stage39.yml down
echo [SUCCESS] 서비스가 중지되었습니다.
goto end

:test
echo [INFO] Docker Chaos 테스트를 실행합니다...
echo.
docker-compose -f docker-compose.stage39.yml run --rm chaos-runner
echo [SUCCESS] 테스트가 완료되었습니다!
goto end

:logs
echo [INFO] 서비스 로그 (Ctrl+C로 종료)...
docker-compose -f docker-compose.stage39.yml logs -f web celery_worker
goto end

:status
echo [INFO] 컨테이너 상태:
echo.
docker-compose -f docker-compose.stage39.yml ps
goto end

:clean
echo [INFO] 모든 리소스를 정리합니다...
docker-compose -f docker-compose.stage39.yml down -v --remove-orphans
echo [SUCCESS] 정리 완료!
goto end

:all
call :start
echo.
echo [INFO] 5초 후 테스트를 시작합니다...
timeout /t 5 /nobreak >nul
call :test
goto end

:end
endlocal
