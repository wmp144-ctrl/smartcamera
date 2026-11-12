<#
.SYNOPSIS
    آماده‌سازی بسته نصب آفلاین CameraSmart (ویندوز ۶۴ بیتی).

.DESCRIPTION
    این اسکریپت روی یک ماشین *متصل به اینترنت* اجرا می‌شود و همه چیزهایی که
    برای نصب روی ماشین *آفلاین* لازم است را در پوشه offline/ جمع می‌کند:

      1. همه بسته‌های پایتون (سخت‌افزاری + هوش مصنوعی/CUDA) به صورت wheel
      2. وزن‌های مدل RF-DETR در data/models
      3. ساخت رابط کاربری (frontend\dist) برای سرو شدن توسط همان سرور

    خروجی نهایی را می‌توانید روی فلش یا شبکه به ماشین مقصد ببرید و سپس
    scripts\install_offline.ps1 را اجرا کنید.

.PARAMETER CudaTag
    نسخه CUDA داخل wheel های PyTorch: cu124 (پیش‌فرض) یا cu121 یا cpu

.PARAMETER SkipFrontend
    ساخت رابط کاربری را رد کن (اگر dist از قبل آماده است)

.PARAMETER SkipModels
    دانلود وزن‌های مدل را رد کن

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\download_offline.ps1
#>
[CmdletBinding()]
param(
    [ValidateSet("cu124", "cu121", "cpu")]
    [string]$CudaTag = "cu124",
    [string]$PythonExe = "py -3.11",
    [switch]$SkipFrontend,
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$root        = Split-Path -Parent $PSScriptRoot
$backend     = Join-Path $root "backend"
$frontend    = Join-Path $root "frontend"
$offline     = Join-Path $root "offline"
$wheels      = Join-Path $offline "wheels"
$modelsDir   = Join-Path $backend "data\models"
$supervision = Join-Path $offline "models"

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }

Write-Step "بررسی پیش‌نیازها"
foreach ($tool in @("ffmpeg", "ffprobe")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Warn "$tool در PATH پیدا نشد. برای اجرای سامانه، ffmpeg روی ماشین مقصد لازم است."
    } else {
        Write-Ok "$tool پیدا شد"
    }
}

New-Item -ItemType Directory -Force -Path $wheels, $modelsDir, $offline | Out-Null

# ---------------------------------------------------------------------------
Write-Step "ساخت محیط موقت دانلود"
$venvDir = Join-Path $offline ".download-venv"
if (-not (Test-Path $venvDir)) {
    Invoke-Expression "$PythonExe -m venv `"$venvDir`""
}
$py = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $py)) { throw "ساخت محیط مجازی با Python 3.11 ناموفق بود. آیا Python 3.11 نصب است؟" }
& $py -m pip install --upgrade pip | Out-Null
Write-Ok "محیط آماده است"

# ---------------------------------------------------------------------------
Write-Step "دانلود بسته‌های پایه (بدون GPU)"
& $py -m pip download -r (Join-Path $backend "requirements-core.txt") -d $wheels
if ($LASTEXITCODE -ne 0) { throw "دانلود بسته‌های پایه ناموفق بود" }
Write-Ok "بسته‌های پایه دانلود شد"

# ---------------------------------------------------------------------------
Write-Step "دانلود PyTorch / TorchVision ($CudaTag)"
$torchIndex = if ($CudaTag -eq "cpu") { "https://download.pytorch.org/whl/cpu" } else { "https://download.pytorch.org/whl/$CudaTag" }
# نسخه‌ها بر اساس ماتریس سازگاری اسناد (Torch 2.5.1 <-> TorchVision 0.20.1)
& $py -m pip download torch==2.5.1 torchvision==0.20.1 -d $wheels --index-url $torchIndex
if ($LASTEXITCODE -ne 0) { throw "دانلود PyTorch ناموفق بود" }
Write-Ok "PyTorch و TorchVision دانلود شد"

# ---------------------------------------------------------------------------
Write-Step "دانلود ONNX Runtime، OpenCV، RF-DETR و بقیه"
$aiExtras = @(
    "onnxruntime-gpu==1.20.1",
    "opencv-python==4.10.0.84",
    "scipy>=1.11",
    "rfdetr>=1.2.0",
    "supervision>=0.22",
    "pynvml>=11.5",
    "transformers>=4.44",
    "numpy>=1.26.0,<2.0"
)
& $py -m pip download @aiExtras -d $wheels
if ($LASTEXITCODE -ne 0) {
    Write-Warn "دانلود یکی از بسته‌های هوش مصنوعی ناموفق بود. جزئیات خطای بالا را بررسی کنید."
} else {
    Write-Ok "بسته‌های هوش مصنوعی دانلود شدند"
}

# ---------------------------------------------------------------------------
Write-Step "دانلود وزن‌های مدل RF-DETR"
if ($SkipModels) {
    Write-Warn "طبق درخواست رد شد"
} else {
    $weights = @(
        @{ name = "rf-detr-nano.pth";   url = "https://github.com/roboflow/rf-detr/releases/download/v1.2.0/rf-detr-nano.pth" },
        @{ name = "rf-detr-small.pth";  url = "https://github.com/roboflow/rf-detr/releases/download/v1.2.0/rf-detr-small.pth" },
        @{ name = "rf-detr-base.pth";   url = "https://github.com/roboflow/rf-detr/releases/download/v1.2.0/rf-detr-base.pth" },
        @{ name = "rf-detr-medium.pth"; url = "https://github.com/roboflow/rf-detr/releases/download/v1.2.0/rf-detr-medium.pth" },
        @{ name = "rf-detr-large.pth";  url = "https://github.com/roboflow/rf-detr/releases/download/v1.2.0/rf-detr-large.pth" }
    )
    foreach ($w in $weights) {
        $target = Join-Path $modelsDir $w.name
        if (Test-Path $target) { Write-Ok "$($w.name) از قبل موجود است"; continue }
        try {
            Invoke-WebRequest -Uri $w.url -OutFile $target -TimeoutSec 1800
            Write-Ok "$($w.name) دانلود شد"
        } catch {
            Write-Warn "دانلود $($w.name) ناموفق بود: $($_.Exception.Message)"
            Write-Warn "اگر RF-DETR را از pip نصب کرده‌اید، کتابخانه خودش وزن‌ها را در حافظه پنهان نگه می‌دارد."
        }
    }
    # وزن‌های دانلودشده همراه بسته منتقل می‌شوند
    New-Item -ItemType Directory -Force -Path $supervision | Out-Null
    Copy-Item -Path (Join-Path $modelsDir "*") -Destination $supervision -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
Write-Step "ساخت رابط کاربری (frontend\dist)"
if ($SkipFrontend) {
    Write-Warn "طبق درخواست رد شد"
} elseif (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Warn "npm پیدا نشد؛ مراحل زیر را روی ماشین متصل اجرا کنید: npm ci ; npm run build"
} else {
    Push-Location $frontend
    try {
        if (-not (Test-Path (Join-Path $frontend "node_modules"))) { npm ci }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "ساخت رابط کاربری ناموفق بود" }
        Write-Ok "رابط کاربری ساخته شد"
    } finally {
        Pop-Location
    }
    $dist = Join-Path $frontend "dist"
    if (Test-Path $dist) {
        New-Item -ItemType Directory -Force -Path (Join-Path $offline "frontend") | Out-Null
        Copy-Item -Path $dist -Destination (Join-Path $offline "frontend\dist") -Recurse -Force
        Write-Ok "dist در بسته آفلاین کپی شد"
    }
}

# ---------------------------------------------------------------------------
Write-Step "نوشتن فهرست نسخه‌ها"
$manifest = Join-Path $offline "manifest.json"
$pythonVersion = (& $py --version) -join " "
@{
    generated_at  = (Get-Date).ToUniversalTime().ToString("o")
    timezone      = "UTC"
    python        = $pythonVersion
    cuda_tag      = $CudaTag
    platform      = "windows-x64"
    wheel_count   = (Get-ChildItem $wheels -Filter *.whl -ErrorAction SilentlyContinue).Count
    has_frontend  = (Test-Path (Join-Path $offline "frontend\dist\index.html"))
    models        = (Get-ChildItem $supervision -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
} | ConvertTo-Json -Depth 4 | Set-Content -Path $manifest -Encoding UTF8
Write-Ok "manifest.json نوشته شد"

Write-Step "پایان"
Write-Host "بسته آفلاین آماده است: $offline" -ForegroundColor Green
Write-Host "کل حجم: $(([math]::Round((Get-ChildItem $offline -Recurse -File | Measure-Object Length -Sum).Sum / 1GB, 2))) گیگابایت"
Write-Host "این پوشه را به ماشین مقصد منتقل کنید و اجرا کنید:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1"
