# ============================================================
# scripts/run_with_allure.ps1
# 一键跑测试并生成 Allure HTML 报告
#
# 用法：
#   .\scripts\run_with_allure.ps1                 # 跑全部用例
#   .\scripts\run_with_allure.ps1 -Open           # 跑完自动打开报告
#   .\scripts\run_with_allure.ps1 -Env uat        # 指定环境
#   .\scripts\run_with_allure.ps1 -PytestArgs "testcases/ecommerce/user_login.yaml -k login"
# ============================================================
param(
    [string]$Env = "test",
    [switch]$Open,
    [string]$PytestArgs = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ResultsDir = Join-Path $Root "reports\allure-results"
$ReportDir  = Join-Path $Root "reports\allure-report"

# ---------- 解析 allure 命令行（优先全局，回退本地） ----------
function Resolve-Allure {
    $global = Get-Command allure -ErrorAction SilentlyContinue
    if ($global) { return $global.Source }

    $local = Join-Path $Root ".tools\node_modules\.bin\allure.cmd"
    if (Test-Path $local) { return $local }

    return $null
}

$AllureBin = Resolve-Allure
if (-not $AllureBin) {
    Write-Host "[!] 未找到 allure 命令行。" -ForegroundColor Yellow
    Write-Host "    请先安装 Allure（二选一）：" -ForegroundColor Yellow
    Write-Host "      1) 全局安装后确保 allure 在 PATH 中" -ForegroundColor Yellow
    Write-Host "      2) npm install allure-commandline --prefix .tools --no-save --cache ./.npm-cache" -ForegroundColor Yellow
    exit 2
}

# ---------- 1. 清理旧结果，但保留历史趋势 ----------
# Allure 历史趋势靠“上次生成的 history 回灌到 results”实现，
# 因此清空 results 之前，先把上一份报告的 history 暂存出来。
$PrevHistory = Join-Path $Root (Join-Path "reports" "allure-history-cache")
if (Test-Path $PrevHistory) { Remove-Item $PrevHistory -Recurse -Force -ErrorAction SilentlyContinue }
$LastHistory = Join-Path $ReportDir "history"
if (Test-Path $LastHistory) {
    Copy-Item $LastHistory $PrevHistory -Recurse -Force -ErrorAction SilentlyContinue
}

if (Test-Path $ResultsDir) {
    Remove-Item "$ResultsDir\*" -Recurse -Force -ErrorAction SilentlyContinue
}

# 把历史回灌到 results/history，供本次前端的趋势图叠加
if (Test-Path $PrevHistory) {
    Copy-Item $PrevHistory (Join-Path $ResultsDir "history") -Recurse -Force -ErrorAction SilentlyContinue
}

# ---------- 2. 跑测试（pytest.ini 已带 --alluredir） ----------
$env:API_TEST_ENV = $Env
Write-Host "[1/3] 执行测试 (env=$Env)..." -ForegroundColor Cyan
$pyArgs = @("-m", "pytest") + ($PytestArgs -split "\s+" | Where-Object { $_ })
& python @pyArgs
$exitCode = $LASTEXITCODE

# ---------- 3. 生成 HTML 报告 ----------
Write-Host "[2/3] 生成 Allure 报告 (使用: $AllureBin)..." -ForegroundColor Cyan
& $AllureBin generate $ResultsDir -o $ReportDir --clean | Out-Null

$index = Join-Path $ReportDir "index.html"
if (Test-Path $index) {
    Write-Host "[3/3] 报告已生成: $index" -ForegroundColor Green
    if ($Open) { Start-Process $index }
} else {
    Write-Host "[!] 报告生成失败" -ForegroundColor Red
}

exit $exitCode
