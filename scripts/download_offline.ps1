<#
.SYNOPSIS
    ساخت بسته نصب کاملاً آفلاین CameraSmart — قابل اجرا روی هر سیستم ویندوزی متصل به اینترنت.

.DESCRIPTION
    این اسکریپت مستقل است: فقط به Python 3.8+ (برای pip) و اینترنت نیاز دارد؛ نه Node.js،
    نه مخزن گیت و نه مدل‌های از پیش دانلودشده. همه‌چیز را در پوشه offline/ کنار خودش می‌سازد:

      wheels/          همه wheel های cp311/win_amd64 + pip + setuptools
      models/          وزن‌های RF-DETR (small و base به‌صورت پیش‌فرض، با بررسی MD5)
      bin/             ffmpeg.exe و ffprobe.exe (نسخه ویندوز)
      frontend/dist/   رابط کاربری (اگر کنار اسکریپت باشد کپی می‌شود)
      app/             سورس برنامه + اسکریپت نصب، تا ماشین مقصد فقط به همین پوشه نیاز داشته باشد
      requirements-offline.txt، manifest.json، README-INSTALL.txt

    سپس کل پوشه offline/ را به ماشین آفلاین ببرید و آنجا اجرا کنید:
      powershell -ExecutionPolicy Bypass -File offline\install_offline.ps1 -Start

.PARAMETER OutDir
    مسیر خروجی (پیش‌فرض: offline/ کنار اسکریپت)

.PARAMETER CudaTag
    cu124 (پیش‌فرض و تأییدشده)، cu121 یا cpu

.PARAMETER Models
    مدل‌هایی که وزنشان دانلود شود: nano, small, base  (پیش‌فرض small,base)

.PARAMETER PythonExe
    مسیر پایتونِ روی همین سیستم برای اجرای pip (پیش‌فرض: python)

.PARAMETER ModelsFrom
    پوشه‌ای که فایل‌های rf-detr-*.pth در آن است (پیش‌فرض: %USERPROFILE%\.roboflow\models).
    اگر دانلود وزن‌ها در شبکه شما مسدود است، فایل‌ها را از سیستمی که دارند کپی کنید.

.PARAMETER FrontendDist
    مسیر dist ساخته‌شده رابط کاربری (پیش‌فرض: frontend\dist کنار مخزن، اگر باشد)

.PARAMETER DryRun
    فقط بررسی دسترسی به منابع و نمایش حجم؛ چیزی دانلود نمی‌شود

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File download_offline.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File download_offline.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [ValidateSet("cu124", "cu121", "cpu")]
    [string]$CudaTag = "cu124",
    [string[]]$Models = @("small", "base"),
    [string]$PythonExe = "python",
    [string]$ModelsFrom = "",
    [string]$FrontendDist = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---------------------------------------------------------------------------
# محل اسکریپت و مخزن (اگر کنار پروژه باشد، از فایل‌های آن استفاده می‌شود)
# ---------------------------------------------------------------------------
$scriptDir = $PSScriptRoot
$repoRoot = $null
foreach ($candidate in @($scriptDir, (Split-Path -Parent $scriptDir))) {
    if ($candidate -and (Test-Path (Join-Path $candidate "backend\requirements-core.txt"))) {
        $repoRoot = $candidate
        break
    }
}
$out = if ($OutDir) { [System.IO.Path]::GetFullPath($OutDir) } else { Join-Path $scriptDir "offline" }
$wheels   = Join-Path $out "wheels"
$modelsOut = Join-Path $out "models"
$binOut   = Join-Path $out "bin"
$appOut   = Join-Path $out "app"

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# نسخه‌های پین‌شده (همان ماتریس تأییدشده backend\constraints.txt)
# ---------------------------------------------------------------------------
$embeddedPins = @(
    "fastapi==0.142.2",
    "starlette==1.7.0",
    "uvicorn==0.54.0",
    "websockets==17.2",
    "python-multipart==0.0.32",
    "orjson==3.12.0",
    "pydantic==2.13.5",
    "pydantic-settings==2.15.0",
    "SQLAlchemy==2.1.3",
    "alembic==1.20.0",
    "aiosqlite==0.22.1",
    "asyncpg==0.31.0",
    "httpx==0.28.1",
    "anyio==4.15.1",
    "psutil==7.2.2",
    "python-dotenv==1.2.4",
    "tzdata==2026.5",
    "pillow==12.3.0",
    "numpy==1.26.4",
    "torch==2.5.1+cu124",
    "torchvision==0.20.1+cu124",
    "onnxruntime-gpu==1.30.0",
    "opencv-python==4.11.0.86",
    "scipy==1.17.1",
    "rfdetr==1.11.2",
    "supervision==0.30.7",
    "pynvml==13.0.1",
    "transformers==5.18.0"
)

# دو میزبان: repo.roboflow.com (میزبان رسمی جدید) و GCS قدیمی. اگر هر دو در
# شبکه شما مسدود باشند، از -ModelsFrom یا حافظه پنهان همین سیستم استفاده می‌شود.
$modelAssets = @{
    nano  = @{ file = "rf-detr-nano.pth";
               urls = @("https://storage.googleapis.com/rfdetr/nano_coco/checkpoint_best_regular.pth", "https://repo.roboflow.com/rfdetr/rf-detr-nano.pth");
               md5 = "fb6504cce7fbdc783f7a46991f07639f" }
    small = @{ file = "rf-detr-small.pth";
               urls = @("https://storage.googleapis.com/rfdetr/small_coco/checkpoint_best_regular.pth", "https://repo.roboflow.com/rfdetr/rf-detr-small.pth");
               md5 = "fb37061c1af7bace359c91b723a8d5c1" }
    base  = @{ file = "rf-detr-base.pth";
               urls = @("https://storage.googleapis.com/rfdetr/rf-detr-base-coco.pth", "https://repo.roboflow.com/rfdetr/rf-detr-base-coco.pth");
               md5 = "b4d3ce46099eaed50626ede388caf979" }
}

Write-Step "بررسی پیش‌نیازها"
$pyExe = $null
$pyArgs = @()
foreach ($candidate in @($PythonExe, "python", "py")) {
    if (-not $candidate) { continue }
    $parts = $candidate -split '\s+'
    $exe = $parts[0]
    $extra = @()
    if ($parts.Count -gt 1) { $extra = @($parts[1..($parts.Count - 1)]) }
    elseif ($exe -eq "py") { $extra = @("-3") }
    try {
        $version = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($version -and [version]$version.Trim() -ge [version]"3.8") {
            $pyExe = $exe
            $pyArgs = $extra
            break
        }
    } catch { }
}
if (-not $pyExe) { throw "Python پیدا نشد. برای اجرای pip به Python 3.8+ نیاز است (نسخه ماشین مقصد مهم نیست)." }
Write-Ok "Python (برای pip): $((& $pyExe @pyArgs --version 2>&1) -join ' ')"
if ($repoRoot) { Write-Ok "مخزن پروژه: $repoRoot" } else { Write-Warn "مخزن پروژه پیدا نشد؛ از نسخه‌های داخلی اسکریپت استفاده می‌شود." }

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $wheels, $modelsOut, $binOut | Out-Null
}

# ---------------------------------------------------------------------------
Write-Step "آماده‌سازی فهرست نسخه‌ها"
$pinLines = if ($repoRoot -and (Test-Path (Join-Path $repoRoot "backend\constraints.txt"))) {
    Get-Content (Join-Path $repoRoot "backend\constraints.txt") |
        Where-Object { $_ -match '^[A-Za-z0-9_.\-]+==' }
} else {
    $embeddedPins
}
$pinLines = @($pinLines | Where-Object { $_ -notmatch '^(pytest|ruff)' })
if ($CudaTag -ne "cu124") {
    $pinLines = @($pinLines | Where-Object { $_ -notmatch '^torch(vision)?==' })
}
$reqFile = Join-Path $out "requirements-offline.txt"
if (-not $DryRun) {
    Set-Content -Path $reqFile -Value (@("# CameraSmart offline bundle ($CudaTag)", "# generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm')") + $pinLines) -Encoding ASCII
}
Write-Ok "$($pinLines.Count) بسته پین‌شده"

if ($DryRun) {
    Write-Step "بررسی دسترسی به منابع (DryRun)"
    $torchIndex = if ($CudaTag -eq "cpu") { "https://download.pytorch.org/whl/cpu" } else { "https://download.pytorch.org/whl/$CudaTag" }
    foreach ($url in @("https://pypi.org/simple/", $torchIndex, "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip")) {
        try {
            $r = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 30
            Write-Ok "$url → $($r.StatusCode)"
        } catch { Write-Warn "$url → $($_.Exception.Message)" }
    }
    foreach ($name in $Models) {
        $asset = $modelAssets[$name]
        if (-not $asset) { Write-Warn "مدل ناشناخته: $name"; continue }
        $cached = Join-Path (Join-Path $env:USERPROFILE ".roboflow\models") $asset.file
        if ((Test-Path $cached) -or ($ModelsFrom -and (Test-Path (Join-Path $ModelsFrom $asset.file)))) {
            Write-Ok "$($asset.file) → در همین سیستم موجود است (کپی می‌شود)"
            continue
        }
        $reachable = $false
        foreach ($url in $asset.urls) {
            try {
                $r = Invoke-WebRequest -Uri $url -Method Head -UseBasicParsing -TimeoutSec 30
                Write-Ok "$($asset.file) → $($r.StatusCode) ($([math]::Round([int64]$r.Headers['Content-Length'] / 1MB)) MB)"
                $reachable = $true
                break
            } catch { Write-Warn "$url → $($_.Exception.Message)" }
        }
        if (-not $reachable) {
            Write-Warn "$($asset.file): هیچ میزبانی قابل دسترس نبود. فایل را از سیستم دیگری با -ModelsFrom کپی کنید."
        }
    }
    Write-Step "DryRun تمام شد (هیچ فایلی دانلود نشد)"
    return
}

# ---------------------------------------------------------------------------
Write-Step "دانلود wheel های پایتون (cp311 / win_amd64)"
$torchIndex = if ($CudaTag -eq "cpu") { "https://download.pytorch.org/whl/cpu" } else { "https://download.pytorch.org/whl/$CudaTag" }
$pipCommon = @(
    "-m", "pip", "download", "--dest", $wheels,
    "--only-binary=:all:", "--python-version", "3.11", "--platform", "win_amd64",
    "--implementation", "cp"
)
& $pyExe @pyArgs @pipCommon pip setuptools wheel --quiet
if ($LASTEXITCODE -ne 0) { throw "دانلود pip/setuptools ناموفق بود." }

& $pyExe @pyArgs @pipCommon -r $reqFile --extra-index-url $torchIndex
if ($LASTEXITCODE -ne 0) { throw "دانلود بسته‌های اصلی ناموفق بود (خروجی بالا را ببینید)." }

if ($CudaTag -ne "cu124") {
    Write-Step "دانلود PyTorch برای $CudaTag"
    & $pyExe @pyArgs @pipCommon torch torchvision --index-url $torchIndex
    if ($LASTEXITCODE -ne 0) { throw "دانلود PyTorch ناموفق بود." }
}
$wheelCount = (Get-ChildItem $wheels -Filter *.whl).Count
Write-Ok "$wheelCount فایل wheel دانلود شد"

# ---------------------------------------------------------------------------
Write-Step "دانلود وزن‌های مدل"
foreach ($name in $Models) {
    $asset = $modelAssets[$name]
    if (-not $asset) { Write-Warn "مدل ناشناخته: $name"; continue }
    $target = Join-Path $modelsOut $asset.file
    if ((Test-Path $target) -and ((Get-FileHash $target -Algorithm MD5).Hash -eq $asset.md5.ToUpper())) {
        Write-Ok "$($asset.file) از قبل موجود و سالم است"
        continue
    }
    $sources = @()
    if ($ModelsFrom) { $sources += (Join-Path $ModelsFrom $asset.file) }
    $sources += (Join-Path (Join-Path $env:USERPROFILE ".roboflow\models") $asset.file)
    $copied = $false
    foreach ($source in $sources) {
        if (-not (Test-Path $source)) { continue }
        Copy-Item $source $target -Force
        if ((Get-FileHash $target -Algorithm MD5).Hash -eq $asset.md5.ToUpper()) {
            Write-Ok "$($asset.file) از $source کپی شد"
            $copied = $true
            break
        }
        Remove-Item $target -Force
    }
    if ($copied) { continue }

    $tmp = "$target.download"
    foreach ($url in $asset.urls) {
        try {
            Write-Host "  ... دانلود $($asset.file) از $url"
            Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 3600
            $hash = (Get-FileHash $tmp -Algorithm MD5).Hash
            if ($hash -ne $asset.md5.ToUpper()) { throw "MD5 نامعتبر است ($hash)" }
            Move-Item $tmp $target -Force
            Write-Ok "$($asset.file) دانلود شد ($([math]::Round((Get-Item $target).Length / 1MB)) MB)"
            $copied = $true
            break
        } catch {
            if (Test-Path $tmp) { Remove-Item $tmp -Force }
            Write-Warn "دانلود از $url ناموفق بود: $($_.Exception.Message)"
        }
    }
    if (-not $copied) {
        Write-Warn "$($asset.file) در بسته نیست؛ اگر شبکه شما این میزبان‌ها را مسدود می‌کند،"
        Write-Warn "فایل‌های .pth را از سیستم دیگری با -ModelsFrom <پوشه> کپی کنید."
    }
}

# ---------------------------------------------------------------------------
Write-Step "دانلود ffmpeg / ffprobe"
if ((Test-Path (Join-Path $binOut "ffmpeg.exe")) -and (Test-Path (Join-Path $binOut "ffprobe.exe"))) {
    Write-Ok "از قبل موجود است"
} else {
    $copied = $false
    $local = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($local) {
        $shimFile = "$($local.Source).shim"
        if (Test-Path $shimFile) {
            $real = (Get-Content $shimFile | Select-String '^path\s*=\s*(.+)$').Matches.Groups[1].Value.Trim()
            $dir = Split-Path -Parent $real
            if ((Test-Path (Join-Path $dir "ffmpeg.exe")) -and ((Get-Item (Join-Path $dir "ffmpeg.exe")).Length -gt 5MB)) {
                Copy-Item (Join-Path $dir "ffmpeg.exe") $binOut -Force
                if (Test-Path (Join-Path $dir "ffprobe.exe")) { Copy-Item (Join-Path $dir "ffprobe.exe") $binOut -Force }
                $copied = $true
            }
        }
    }
    if (-not $copied) {
        $zip = Join-Path $out "ffmpeg-essentials.zip"
        try {
            Write-Host "  ... دانلود ffmpeg برای ویندوز (~۱۰۰ مگابایت)"
            Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip -UseBasicParsing -TimeoutSec 3600
            $tmp = Join-Path $out ".ffmpeg-extract"
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            $ff = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
            $fp = Get-ChildItem $tmp -Recurse -Filter ffprobe.exe | Select-Object -First 1
            Copy-Item $ff.FullName $binOut -Force
            if ($fp) { Copy-Item $fp.FullName $binOut -Force }
            Remove-Item $tmp -Recurse -Force
            Remove-Item $zip -Force
            $copied = $true
        } catch {
            Write-Warn "دانلود ffmpeg ناموفق بود: $($_.Exception.Message)"
        }
    }
    if ($copied) { Write-Ok "ffmpeg/ffprobe در bin/ آماده شد" }
}

# ---------------------------------------------------------------------------
Write-Step "گردآوری سورس برنامه و رابط کاربری"
if (Test-Path $appOut) { Remove-Item $appOut -Recurse -Force }
New-Item -ItemType Directory -Force -Path $appOut | Out-Null

if ($repoRoot) {
    $skip = @(".venv", "data", "__pycache__", ".pytest_cache", ".ruff_cache", ".env", "node_modules")
    foreach ($section in @("backend", "scripts")) {
        $src = Join-Path $repoRoot $section
        if (-not (Test-Path $src)) { continue }
        $dst = Join-Path $appOut $section
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        Get-ChildItem $src -Force | Where-Object { $skip -notcontains $_.Name } | ForEach-Object {
            Copy-Item $_.FullName -Destination $dst -Recurse -Force
        }
    }
    foreach ($file in @("README.md", ".gitignore", ".gitattributes")) {
        $src = Join-Path $repoRoot $file
        if (Test-Path $src) { Copy-Item $src $appOut -Force }
    }
    Get-ChildItem $appOut -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Write-Ok "سورس برنامه در app/ کپی شد"

    $distCandidates = @()
    if ($FrontendDist) { $distCandidates += $FrontendDist }
    $distCandidates += (Join-Path $repoRoot "frontend\dist")
    $dist = $distCandidates | Where-Object { $_ -and (Test-Path (Join-Path $_ "index.html")) } | Select-Object -First 1
    if ($dist) {
        New-Item -ItemType Directory -Force -Path (Join-Path $out "frontend") | Out-Null
        $distOut = Join-Path $out "frontend\dist"
        if (Test-Path $distOut) { Remove-Item $distOut -Recurse -Force }
        Copy-Item $dist $distOut -Recurse -Force
        Write-Ok "رابط کاربری ساخته‌شده در frontend/dist کپی شد"
    } else {
        Write-Warn "dist رابط کاربری پیدا نشد؛ روی ماشین مقصد فقط API بالا می‌آید."
        Write-Warn "راه‌حل: پوشه frontend را با Node بسازید (npm ci; npm run build) و دوباره اجرا کنید."
    }
} else {
    Write-Warn "سورس برنامه کنار اسکریپت نبود؛ app/ ساخته نشد."
    Write-Warn "این اسکریپت را داخل پوشه پروژه (کنار backend و frontend) اجرا کنید تا بسته کامل شود."
}

# ---------------------------------------------------------------------------
Write-Step "نوشتن راهنما و فهرست بسته"
$installSrc = if ($repoRoot) { Join-Path $repoRoot "scripts\install_offline.ps1" } else { Join-Path $scriptDir "install_offline.ps1" }
if (Test-Path $installSrc) {
    Copy-Item $installSrc $out -Force
    Write-Ok "install_offline.ps1 در ریشه بسته قرار گرفت"
}

@"
CameraSmart — راهنمای نصب آفلاین
=================================

۱) کل این پوشه (offline) را با فلش یا شبکه به سیستم آفلاین منتقل کنید.
۲) روی سیستم آفلاین، پنجره PowerShell را در همین پوشه باز کنید و اجرا کنید:

   powershell -ExecutionPolicy Bypass -File install_offline.ps1

   گزینه‌های مفید:
     -Port 8080     پورت دیگر
     -Lan           دسترسی از دستگاه‌های دیگر شبکه + قانون فایروال (Administrator)
     -Autostart     اجرای خودکار در ورود کاربر (Administrator)
     -Start         اجرای سرور بلافاصله پس از نصب

۳) پس از نصب، فایل start_camerasmart.bat را اجرا کنید و در مرورگر باز کنید:
   http://127.0.0.1:8000

۴) در اولین بازدید، رمز ادمین را دو بار وارد کنید.

پیش‌نیاز سیستم آفلاین: ویندوز ۶۴ بیتی و Python 3.11.
Node.js لازم نیست (رابط کاربری از پیش ساخته شده) و ffmpeg همراه بسته است.
"@ | Set-Content -Path (Join-Path $out "README-INSTALL.txt") -Encoding UTF8

$versions = @{}
foreach ($line in $pinLines) {
    if ($line -match '^([^=]+)==(.+)$') { $versions[$Matches[1].Trim().ToLower()] = $Matches[2].Trim() }
}
@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    platform     = "windows-x64"
    python_target = "3.11"
    cuda_tag     = $CudaTag
    wheel_count  = $wheelCount
    has_frontend = (Test-Path (Join-Path $out "frontend\dist\index.html"))
    has_ffmpeg   = (Test-Path (Join-Path $binOut "ffmpeg.exe"))
    has_source   = (Test-Path (Join-Path $appOut "backend\app\main.py"))
    models       = @(Get-ChildItem $modelsOut -Filter *.pth -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name)
    versions     = $versions
} | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $out "manifest.json") -Encoding UTF8
Write-Ok "manifest.json نوشته شد"

Write-Step "پایان"
$sizeGb = [math]::Round((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1GB, 2)
Write-Host "بسته آفلاین آماده است: $out" -ForegroundColor Green
Write-Host "حجم کل: $sizeGb گیگابایت — wheel: $wheelCount" -ForegroundColor Green
Write-Host "روی سیستم آفلاین:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File install_offline.ps1"
