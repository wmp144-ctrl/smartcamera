<#
.SYNOPSIS
    نصب آفلاین CameraSmart روی ویندوز ۶۴ بیتی (بدون هیچ دسترسی به اینترنت).

.DESCRIPTION
    از بسته‌ای که با scripts\download_offline.ps1 ساخته شده استفاده می‌کند:

      1. ساخت محیط مجازی backend\.venv و نصب wheel های محلی
      2. کپی وزن‌های مدل به backend\data\models
      3. کپی رابط کاربری ساخته‌شده به frontend\dist
      4. ساخت جداول پایگاه داده (Alembic) و بررسی سلامت سامانه

.PARAMETER Port
    پورتی که سرور روی آن اجرا می‌شود (پیش‌فرض 8000)

.PARAMETER Start
    پس از نصب، سرور را در همین پنجره اجرا کن

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1 -Start
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$PythonExe = "py -3.11",
    [switch]$Start
)

$ErrorActionPreference = "Stop"

$root     = Split-Path -Parent $PSScriptRoot
$backend  = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$offline  = Join-Path $root "offline"
$wheels   = Join-Path $offline "wheels"

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }

Write-Step "بررسی بسته آفلاین"
if (-not (Test-Path $wheels)) {
    throw "پوشه offline\wheels پیدا نشد. ابتدا روی ماشین متصل scripts\download_offline.ps1 را اجرا کنید."
}
$wheelCount = (Get-ChildItem $wheels -Filter *.whl).Count
if ($wheelCount -eq 0) { throw "هیچ فایل wheel ای در offline\wheels وجود ندارد." }
Write-Ok "$wheelCount فایل wheel آماده است"
if (Test-Path (Join-Path $offline "manifest.json")) {
    Write-Host (Get-Content (Join-Path $offline "manifest.json") -Raw)
}

Write-Step "ساخت محیط مجازی"
$venvDir = Join-Path $backend ".venv"
if (-not (Test-Path (Join-Path $venvDir "Scripts\python.exe"))) {
    Invoke-Expression "$PythonExe -m venv `"$venvDir`""
}
$py = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $py)) { throw "ساخت محیط مجازی ناموفق بود (Python 3.11 لازم است)." }
Write-Ok "محیط مجازی آماده است: $venvDir"

Write-Step "نصب بسته‌ها از فایل‌های محلی (بدون اینترنت)"
& $py -m pip install --no-index --find-links $wheels --upgrade pip
& $py -m pip install --no-index --find-links $wheels -r (Join-Path $backend "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Warn "نصب کامل ناموفق بود؛ تلاش برای نصب فقط بسته‌های پایه (بدون هوش مصنوعی)"
    & $py -m pip install --no-index --find-links $wheels -r (Join-Path $backend "requirements-core.txt")
    if ($LASTEXITCODE -ne 0) { throw "نصب بسته‌های پایه هم ناموفق بود" }
    Write-Warn "سامانه بدون RF-DETR اجرا می‌شود (تشخیص اشیا غیرفعال می‌ماند)."
} else {
    Write-Ok "همه بسته‌ها نصب شدند"
}

Write-Step "نصب وزن‌های مدل"
$modelsTarget = Join-Path $backend "data\models"
New-Item -ItemType Directory -Force -Path $modelsTarget | Out-Null
$modelsSource = Join-Path $offline "models"
if (Test-Path $modelsSource) {
    Copy-Item -Path (Join-Path $modelsSource "*") -Destination $modelsTarget -Force
    Write-Ok "وزن‌ها کپی شدند: $modelsTarget"
} else {
    $existing = (Get-ChildItem $modelsTarget -Filter *.pth -ErrorAction SilentlyContinue).Count
    Write-Warn "وزن مدلی در بسته نبود؛ $existing فایل در data\models موجود است."
}

Write-Step "نصب رابط کاربری"
$distTarget = Join-Path $frontend "dist"
$distSource = Join-Path $offline "frontend\dist"
if (Test-Path (Join-Path $distSource "index.html")) {
    if (Test-Path $distTarget) { Remove-Item $distTarget -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $distTarget) | Out-Null
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
    Write-Ok "backend\.env از نمونه ساخته شد (قابل ویرایش)"
} else {
    Write-Ok "backend\.env از قبل وجود دارد"
}

Write-Step "ساخت جداول پایگاه داده"
Push-Location $backend
try {
    & $py -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "اجرای Alembic ناموفق بود؛ از ساخت خودکار جداول هنگام شروع استفاده می‌شود."
    } else {
        Write-Ok "جداول آماده هستند"
    }

    Write-Step "بررسی سلامت سامانه"
    & $py -c @"
import platform, sys
try:
    import fastapi, sqlalchemy, pydantic
    print('  [ok] FastAPI %s / SQLAlchemy %s / Pydantic %s' % (fastapi.__version__, sqlalchemy.__version__, pydantic.VERSION))
except Exception as exc:
    print('  [!]  بسته پایه نصب نیست:', exc)
try:
    import torch
    print('  [ok] PyTorch %s — CUDA available: %s' % (torch.__version__, torch.cuda.is_available()))
except Exception:
    print('  [!]  PyTorch نصب نیست (تشخیص اشیا غیرفعال خواهد بود)')
try:
    import rfdetr
    print('  [ok] RF-DETR نصب است')
except Exception:
    print('  [!]  rfdetr نصب نیست')
print('  [i]  Python', platform.python_version(), '|', platform.machine())
"@
} finally {
    Pop-Location
}

Write-Step "پایان نصب"
Write-Host "برای اجرای سامانه:" -ForegroundColor Green
Write-Host "  cd backend"
Write-Host "  .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port $Port"
Write-Host "سپس در مرورگر: http://127.0.0.1:$Port" -ForegroundColor Green

if ($Start) {
    Write-Step "اجرای سرور"
    Push-Location $backend
    try {
        & $py -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    } finally {
        Pop-Location
    }
}
