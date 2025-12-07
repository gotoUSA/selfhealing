# PowerShell Chaos Test Runner

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location "$ScriptDir\.."

Write-Host "🎲 Running Chaos Tests..." -ForegroundColor Yellow

$env:PYTHONUTF8 = "1"
$env:CHAOS_ENABLED = "true"
$env:CHAOS_PROBABILITY = "0.10"

python runners/run_stage.py --profile chaos @args
