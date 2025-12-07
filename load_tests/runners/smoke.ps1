# PowerShell Smoke Test Runner

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\.."

Write-Host "🔥 Running Smoke Test..." -ForegroundColor Green

$env:PYTHONUTF8 = "1"
python runners/run_stage.py stage0_smoke @args
