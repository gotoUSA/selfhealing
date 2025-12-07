# PowerShell Full Cycle Runner

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\.."

Write-Host "🔥 Running Full Test Suite..." -ForegroundColor Green

$env:PYTHONUTF8 = "1"
python runners/run_stage.py --profile full @args
