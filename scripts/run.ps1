<#
.SYNOPSIS
    اجرای CameraSmart پس از نصب.

.DESCRIPTION
    فقط سرور را بالا می‌آورد؛ هیچ چیزی نصب یا کپی نمی‌کند. پیش‌نیازها باید قبلاً
    با scripts\install_offline.ps1 نصب شده باشند.

.PARAMETER Port
    پورت سرور (پیش‌فرض: مقدار CAMERASMART_PORT در backend\.env یا 8000)

.PARAMETER HostAddr
    آدرس گوش‌دادن (پیش‌فرض: مقدار CAMERASMART_HOST در backend\.env یا 127.0.0.1)

.PARAMETER Lan
    گوش‌دادن روی 0.0.0.0 تا دستگاه‌های شبکه هم وصل شوند

.PARAMETER Reload
    حالت توسعه: با هر تغییر کد، سرور خودش دوباره بارگذاری می‌شود

.PARAMETER OpenBrowser
    باز کردن آدرس برنامه در مرورگر پس از شروع

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -Port 8080 -OpenBrowser
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$HostAddr = "",
    [switch]$Lan,
    [switch]$Reload,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

function Fail($text) {
    Write-Host "`n[!] $text" -ForegroundColor Red
    exit 1
}

$scriptDir = $PSScriptRoot
$root = Split-Path -Parent $scriptDir
if (-not (Test-Path (Join-Path $root "backend\app\main.py"))) {
    $root = (Get-Location).Path
    if (-not (Test-Path (Join-Path $root "backend\app\main.py"))) {
        Fail "ریشهٔ پروژه پیدا نشد. این اسکریپت را از ریشهٔ پروژه اجرا کنید."
    }
}
$backend = Join-Path $root "backend"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$envFile = Join-Path $backend ".env"

# ---------------------------------------------------------------------------
# بررسی نصب (بدون تغییر دادن چیزی)
# ---------------------------------------------------------------------------
if (-not (Test-Path $py)) {
    Fail "محیط مجازی پیدا نشد: backend\.venv`nاول نصب را اجرا کنید:  powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1"
}
& $py -c "import fastapi, uvicorn, sqlalchemy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail "بسته‌های پایتون کامل نیستند.`nنصب را اجرا کنید:  powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1"
}

$envPort = 8000
$envHost = "127.0.0.1"
if (Test-Path $envFile) {
    $lines = Get-Content $envFile
    $portLine = ($lines | Select-String '^CAMERASMART_PORT=' | Select-Object -First 1)
    $hostLine = ($lines | Select-String '^CAMERASMART_HOST=' | Select-Object -First 1)
    if ($portLine) {
        $parsed = 0
        if ([int]::TryParse(($portLine -replace '^CAMERASMART_PORT=', '').Trim(), [ref]$parsed) -and $parsed -gt 0) { $envPort = $parsed }
    }
    if ($hostLine) {
        $value = ($hostLine -replace '^CAMERASMART_HOST=', '').Trim()
        if ($value) { $envHost = $value }
    }
}

$listenHost = if ($Lan) { "0.0.0.0" } elseif ($HostAddr) { $HostAddr } else { $envHost }
$listenPort = if ($Port -gt 0) { $Port } else { $envPort }
$browseHost = if ($listenHost -eq "0.0.0.0") { "127.0.0.1" } else { $listenHost }
$url = "http://${browseHost}:${listenPort}"

# ffmpeg: همراه بسته یا در PATH
$ffmpegEnv = Join-Path $backend "bin\ffmpeg.exe"
if (-not (Test-Path $ffmpegEnv) -and -not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "[!] ffmpeg پیدا نشد؛ استریم دوربین ممکن است کار نکند. نصب را دوباره اجرا کنید." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# اجرا
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "CameraSmart" -ForegroundColor White
Write-Host "  پوشه پروژه: $root"
Write-Host "  آدرس:       $url" -ForegroundColor Green
if ($listenHost -eq "0.0.0.0") {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
        Select-Object -First 1 -ExpandProperty IPAddress)
    if ($lanIp) { Write-Host "  از شبکه:    http://${lanIp}:${listenPort}" -ForegroundColor Green }
}
Write-Host "  برای توقف:  Ctrl+C" -ForegroundColor DarkGray
Write-Host ""

if ($OpenBrowser) {
    Start-Process $url | Out-Null
}

Push-Location $backend
try {
    $uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $listenHost, "--port", "$listenPort")
    if ($Reload) { $uvicornArgs += "--reload" }
    & $py @uvicornArgs
} finally {
    Pop-Location
}
