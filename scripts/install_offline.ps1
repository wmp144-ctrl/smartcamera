<#
.SYNOPSIS
    نصب کامل CameraSmart روی ویندوز ۱۱ بدون هیچ دسترسی به اینترنت.

.DESCRIPTION
    بسته‌ای که scripts\download_offline.ps1 می‌سازد را نصب می‌کند:

      1. محیط مجازی و نصب همه wheel های محلی (pip، پایه و GPU)
      2. وزن‌های RF-DETR و ffmpeg/ffprobe همراه بسته
      3. رابط کاربری ساخته‌شده
      4. backend\.env با مسیرهای محلی
      5. جداول پایگاه داده و بررسی سلامت (FastAPI / PyTorch / CUDA / RF-DETR)
      6. start_camerasmart.bat و (اختیاری) اجرای خودکار در ورود

    دو چیدمان پشتیبانی می‌شود:
      • بسته کامل: این فایل کنار wheels/، models/، app/ و frontend/ باشد
      • مخزن پروژه: این فایل در scripts/ و بسته در offline/ باشد

.PARAMETER Port
    پورت سرور (پیش‌فرض 8000)

.PARAMETER Lan
    گوش‌دادن روی 0.0.0.0 برای دسترسی از شبکه + قانون فایروال (Administrator)

.PARAMETER Autostart
    اجرای خودکار در ورود کاربر (Scheduled Task؛ Administrator)

.PARAMETER Start
    اجرای سرور بلافاصله پس از نصب

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_offline.ps1 -Start
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$PythonExe = "",
    [switch]$Lan,
    [switch]$Autostart,
    [switch]$Start
)

$ErrorActionPreference = "Stop"

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# چیدمان: بسته کامل یا مخزن پروژه
# ---------------------------------------------------------------------------
$scriptDir = $PSScriptRoot
$bundle = $null
if (Test-Path (Join-Path $scriptDir "wheels")) {
    $bundle = $scriptDir
} elseif (Test-Path (Join-Path (Split-Path -Parent $scriptDir) "wheels")) {
    $bundle = Split-Path -Parent $scriptDir
}
if (-not $bundle) { throw "پوشه wheels پیدا نشد. این فایل باید کنار بسته آفلاین (یا در scripts\ کنار offline\) اجرا شود." }

$offline = $bundle
$wheels  = Join-Path $offline "wheels"
$appRoot = if (Test-Path (Join-Path $offline "app\backend")) { Join-Path $offline "app" } else { Split-Path -Parent $scriptDir }
$backend  = Join-Path $appRoot "backend"
$frontend = Join-Path $appRoot "frontend"
$hostAddr = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }

function Find-Python311 {
    param([string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        if (-not $candidate) { continue }
        $parts = $candidate -split '\s+'
        $exe = $parts[0]
        $extra = @()
        if ($parts.Count -gt 1) { $extra = @($parts[1..($parts.Count - 1)]) }
        elseif ($exe -eq "py") { $extra = @("-3.11") }
        try {
            $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($version -and $version.Trim() -eq "3.11") { return @{ exe = $exe; args = $extra } }
        } catch { }
    }
    return $null
}

Write-Step "بررسی بسته و پیش‌نیازها"
$wheelCount = (Get-ChildItem $wheels -Filter *.whl -ErrorAction SilentlyContinue).Count
if ($wheelCount -eq 0) { throw "هیچ فایل wheel ای در $wheels نیست." }
Write-Ok "$wheelCount فایل wheel آماده است"
if (Test-Path (Join-Path $offline "manifest.json")) {
    $manifest = Get-Content (Join-Path $offline "manifest.json") -Raw | ConvertFrom-Json
    Write-Host ("  ساخته‌شده: {0} | CUDA: {1} | مدل‌ها: {2}" -f $manifest.generated_at, $manifest.cuda_tag, (($manifest.models | Where-Object { $_ }) -join ", "))
}

$python = Find-Python311 -Candidates @($PythonExe, "py -3.11", "py", "python", "python3")
if (-not $python) { throw "Python 3.11 پیدا نشد. آن را از python.org نصب کنید (گزینه Add to PATH) و دوباره اجرا کنید." }
Write-Ok "Python 3.11: $($python.exe) $($python.args -join ' ')"

Write-Step "ساخت محیط مجازی"
$venvDir = Join-Path $backend ".venv"
$py = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $py)) {
    & $python.exe @($python.args) -m venv $venvDir
}
if (-not (Test-Path $py)) { throw "ساخت محیط مجازی ناموفق بود." }
Write-Ok "محیط مجازی: $venvDir"

Write-Step "نصب بسته‌ها (کاملاً محلی، بدون اینترنت)"
& $py -m pip install --no-index --find-links $wheels --upgrade pip setuptools wheel --quiet
$requirements = Join-Path $offline "requirements-offline.txt"
if (-not (Test-Path $requirements)) { $requirements = Join-Path $backend "requirements.txt" }
$constraintsArgs = @()
if (Test-Path (Join-Path $backend "constraints.txt")) {
    $constraintsArgs = @("-c", (Join-Path $backend "constraints.txt"))
}
& $py -m pip install --no-index --find-links $wheels -r $requirements @constraintsArgs
if ($LASTEXITCODE -ne 0) {
    Write-Warn "نصب کامل ناموفق بود؛ تلاش برای نصب فقط بسته‌های پایه (بدون هوش مصنوعی)"
    & $py -m pip install --no-index --find-links $wheels -r (Join-Path $backend "requirements-core.txt") @constraintsArgs
    if ($LASTEXITCODE -ne 0) { throw "نصب بسته‌های پایه هم ناموفق بود. خروجی بالا را بررسی کنید." }
    Write-Warn "تشخیص اشیا غیرفعال می‌ماند (torch/rfdetr نصب نشد)."
} else {
    Write-Ok "همه بسته‌ها نصب شدند"
}

Write-Step "نصب وزن‌های مدل"
$modelsTarget = Join-Path $backend "data\models"
New-Item -ItemType Directory -Force -Path $modelsTarget | Out-Null
$modelFiles = @(Get-ChildItem (Join-Path $offline "models") -Filter *.pth -ErrorAction SilentlyContinue)
if ($modelFiles.Count -gt 0) {
    foreach ($file in $modelFiles) {
        Copy-Item $file.FullName $modelsTarget -Force
        Write-Ok "$($file.Name) → data\models"
    }
} else {
    Write-Warn "وزن مدلی در بسته نبود؛ در اولین اجرا مدل یا از پیش‌فرض استفاده می‌کند یا تلاش به دانلود می‌کند."
}

Write-Step "نصب ffmpeg / ffprobe"
$bin = Join-Path $backend "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$ffmpegTarget = $null
foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    $src = Join-Path $offline "bin\$tool"
    if (Test-Path $src) {
        Copy-Item $src $bin -Force
        if ($tool -eq "ffmpeg.exe") { $ffmpegTarget = Join-Path $bin $tool }
        Write-Ok "$tool نصب شد"
    } elseif (Get-Command ($tool -replace '\.exe$', '') -ErrorAction SilentlyContinue) {
        Write-Ok "$tool در PATH موجود است"
    } else {
        Write-Warn "$tool نه در بسته بود و نه در PATH؛ استریم RTSP از OpenCV استفاده می‌کند (کمتر پایدار)."
    }
}

Write-Step "نصب رابط کاربری"
$distSource = Join-Path $offline "frontend\dist"
$distTarget = Join-Path $frontend "dist"
if (Test-Path (Join-Path $distSource "index.html")) {
    if (Test-Path $distTarget) { Remove-Item $distTarget -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $frontend | Out-Null
    Copy-Item -Path $distSource -Destination $distTarget -Recurse -Force
    Write-Ok "رابط کاربری نصب شد: $distTarget"
} elseif (Test-Path (Join-Path $distTarget "index.html")) {
    Write-Ok "رابط کاربری از قبل موجود است"
} else {
    Write-Warn "رابط کاربری پیدا نشد؛ سرور فقط API را سرو می‌کند."
}

Write-Step "فایل تنظیمات"
$envFile = Join-Path $backend ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item -Path (Join-Path $backend ".env.example") -Destination $envFile
    Add-Content -Path $envFile -Encoding UTF8 -Value @(
        "",
        "# --- مقادیر نوشته‌شده توسط نصب آفلاین ---",
        "CAMERASMART_HOST=$hostAddr",
        "CAMERASMART_PORT=$Port"
    )
    if ($ffmpegTarget) {
        $ffmpegPosix = $ffmpegTarget -replace '\\', '/'
        Add-Content -Path $envFile -Encoding UTF8 -Value @(
            "CAMERASMART_FFMPEG_BIN=$ffmpegPosix",
            "CAMERASMART_FFPROBE_BIN=$($ffmpegPosix -replace 'ffmpeg\.exe$', 'ffprobe.exe')"
        )
    }
    Write-Ok "backend\.env ساخته شد"
} else {
    Write-Ok "backend\.env از قبل وجود دارد (دست‌نخورده ماند)"
}

Write-Step "ساخت پایگاه داده"
Push-Location $backend
try {
    & $py -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Alembic ناموفق بود؛ هنگام شروع، جداول به‌صورت خودکار ساخته می‌شوند."
    } else {
        Write-Ok "جداول آماده هستند"
    }

    Write-Step "بررسی سلامت نصب"
    & $py -c @"
import platform
ok = True
try:
    import fastapi, sqlalchemy, pydantic
    print('  [ok] FastAPI %s / SQLAlchemy %s / Pydantic %s' % (fastapi.__version__, sqlalchemy.__version__, pydantic.VERSION))
except Exception as exc:
    ok = False
    print('  [!]  بسته پایه نصب نیست:', exc)
try:
    import torch
    print('  [ok] PyTorch %s - CUDA: %s' % (torch.__version__, torch.cuda.is_available()))
except Exception:
    ok = False
    print('  [!]  PyTorch نصب نیست (تشخیص اشیا غیرفعال می‌ماند)')
try:
    import rfdetr, supervision
    print('  [ok] RF-DETR و Supervision نصب هستند')
except Exception as exc:
    print('  [!]  rfdetr/supervision ناقص است:', exc)
print('  [i]  Python %s | %s' % (platform.python_version(), platform.machine()))
raise SystemExit(0 if ok else 1)
"@
} finally {
    Pop-Location
}

Write-Step "میان‌بر اجرا"
$startBat = Join-Path $appRoot "start_camerasmart.bat"
@(
    "@echo off",
    "chcp 65001 >nul",
    "cd /d `"%~dp0backend`"",
    "echo CameraSmart - http://$hostAddr`:$Port",
    ".venv\Scripts\python -m uvicorn app.main:app --host $hostAddr --port $Port"
) | Set-Content -Path $startBat -Encoding ASCII
Write-Ok "start_camerasmart.bat ساخته شد: $startBat"

if ($Autostart) {
    Write-Step "اجرای خودکار در ورود"
    $taskName = "CameraSmart"
    schtasks /Query /TN $taskName > $null 2>&1
    if ($LASTEXITCODE -eq 0) { schtasks /Delete /TN $taskName /F | Out-Null }
    schtasks /Create /TN $taskName /SC ONLOGON /TR "`"$startBat`"" /RL HIGHEST /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "وظیفه زمان‌بندی «$taskName» ثبت شد"
    } else {
        Write-Warn "ثبت اجرای خودکار ناموفق بود (PowerShell را Administrator اجرا کنید)."
    }
}

if ($Lan) {
    Write-Step "دسترسی از شبکه"
    try {
        New-NetFirewallRule -DisplayName "CameraSmart $Port" -Direction Inbound -Protocol TCP `
            -LocalPort $Port -Action Allow -ErrorAction Stop | Out-Null
        Write-Ok "قانون فایروال برای پورت $Port اضافه شد"
    } catch {
        Write-Warn "افزودن قانون فایروال ممکن نشد (Administrator لازم است): $($_.Exception.Message)"
    }
}

Write-Step "پایان نصب"
Write-Host "اجرا با دوبار کلیک روی start_camerasmart.bat" -ForegroundColor Green
Write-Host "سپس در مرورگر: http://$(if ($Lan) { '<IP-این-سیستم>' } else { '127.0.0.1' }):$Port" -ForegroundColor Green
Write-Host "در اولین بازدید، رمز ادمین را دو بار وارد کنید." -ForegroundColor Yellow

if ($Start) {
    Write-Step "اجرای سرور"
    Push-Location $backend
    try { & $py -m uvicorn app.main:app --host $hostAddr --port $Port } finally { Pop-Location }
}
