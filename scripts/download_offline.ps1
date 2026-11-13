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
      frontend-dist/   رابط کاربری ساخته‌شده (سورس برنامه در مخزن می‌ماند)
      requirements-offline.txt، manifest.json، README-INSTALL.txt

    سپس در ریشه پروژه:
      powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1
      powershell -ExecutionPolicy Bypass -File scriptsun.ps1

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
    #: اختیاری: میرور دیگر (مثلاً https://mirrors.cloud.tencent.com/pypi/simple)
    [string]$IndexUrl = "",
    [int]$PipRetries = 4,
    #: اگر شبکه شما واقعاً به پروکسی نیاز دارد، این را بدهید تا پروکسی سیستم
    #: (از جمله پروکسی لوکالِ خاموش) دور زده نشود.
    [switch]$UseSystemProxy,
    #: پوشهٔ کش wheel ها که بین نسخه‌ها نگه داشته می‌شود (پیش‌فرض:
    #: %LOCALAPPDATA%\CameraSmart\wheel-cache). اگر پین‌ها تغییر نکرده باشند،
    #: دانلود کامل رد می‌شود و فقط از کش کپی می‌شود.
    [string]$WheelCache = "",
    #: کش را نادیده بگیر و همهٔ wheel ها را دوباره بررسی/دریافت کن
    [switch]$RefreshWheels,
    [switch]$NoWheelCache,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
# Logs are often redirected to a file; without this PowerShell writes them in
# the OEM code page and Persian text turns into "????".
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8
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
$distOut  = Join-Path $out "frontend-dist"

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
    "greenlet==3.5.6",
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
# نسخه torch با برچسب محلی همان ایندکس جفت می‌شود (۲٫۵٫۱+cu124 / +cu121 / +cpu)
$localTag = if ($CudaTag -eq "cpu") { "+cpu" } else { "+$CudaTag" }
$pinLines = @($pinLines | Where-Object { $_ -notmatch '^(pytest|ruff)' } | ForEach-Object {
    $_ -replace '^(torch(?:vision)?==[0-9.]+)\+cu[0-9]+$', "`$1$localTag"
})
$reqFile  = Join-Path $out "requirements-offline.txt"
$consFile = Join-Path $out "constraints-offline.txt"
if (-not $DryRun) {
    $header = @("# CameraSmart offline bundle ($CudaTag)", "# generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm')")
    Set-Content -Path $reqFile  -Value ($header + $pinLines) -Encoding ASCII
    Set-Content -Path $consFile -Value ($header + $pinLines) -Encoding ASCII
}
Write-Ok "$($pinLines.Count) بسته پین‌شده"

# ---------------------------------------------------------------------------
# کش wheel ها بین نسخه‌ها
# ---------------------------------------------------------------------------
# هر بروزرسانی معمولاً فقط چند پین را عوض می‌کند، ولی دانلود دوبارهٔ ۳٫۴ گیگابایت
# wheel وقت زیادی می‌برد. یک امضای SHA-256 از فهرست پین‌ها + برچسب CUDA + ایندکس
# نگه می‌داریم: اگر امضا همان باشد و کش پر باشد، مرحلهٔ دانلود کامل رد می‌شود.
Write-Step "کش wheel ها بین نسخه‌ها"
$cacheDir = if ($WheelCache) {
    [System.IO.Path]::GetFullPath($WheelCache)
} elseif ($NoWheelCache) {
    ""
} else {
    Join-Path $env:LOCALAPPDATA "CameraSmart\wheel-cache"
}
$signatureText = (($pinLines -join "`n") + "|cuda=" + $CudaTag + "|index=" + $IndexUrl + "|py=3.11")
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $signature = ([System.BitConverter]::ToString(
        $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($signatureText))
    ) -replace '-', '').ToLower()
} finally {
    $sha256.Dispose()
}
$stampFile = if ($cacheDir) { Join-Path $cacheDir "pins-$signature.ok" } else { $null }
$cachedWheels = @()
if ($cacheDir -and (Test-Path $cacheDir)) {
    $cachedWheels = @(Get-ChildItem $cacheDir -Filter *.whl -ErrorAction SilentlyContinue)
}
$cacheHit = [bool](
    $stampFile -and (Test-Path $stampFile) -and -not $RefreshWheels -and $cachedWheels.Count -gt 0
)
if (-not $cacheDir) {
    Write-Warn "کش wheel غیرفعال است (NoWheelCache)"
} elseif ($cacheHit) {
    Write-Ok "کش هم‌راستا است ($($cachedWheels.Count) wheel) — دانلود پایتون رد می‌شود"
} elseif (Test-Path $stampFile) {
    Write-Warn "امضای پین‌ها یکی است ولی کش خالی است؛ دانلود انجام می‌شود"
} else {
    Write-Warn "کش برای این نسخهٔ پین‌ها وجود ندارد؛ این بار دانلود لازم است"
}
Write-Host "       کش: $(if ($cacheDir) { $cacheDir } else { '—' })" -ForegroundColor DarkGray

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

# ---------------------------------------------------------------------------
# پروکسی سیستم
# ---------------------------------------------------------------------------
# Windows registry often points at a local proxy client (e.g. 127.0.0.1:10808).
# When that client is not running, pip fails with ProxyError/10061 and reports
# "No matching distribution found" for every package - which looks like a bad
# version pin but is really a dead proxy. A loopback proxy is bypassed unless
# the operator explicitly asks for it.
$bypassSystemProxy = $false
if (-not $UseSystemProxy) {
    try {
        $inet = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
        if ($inet.ProxyEnable -eq 1 -and $inet.ProxyServer -match '^(https?://)?(127\.0\.0\.1|localhost|\[::1\])(:|$)') {
            $bypassSystemProxy = $true
            Write-Warn "پروکسی سیستم یک آدرس لوکال است ($($inet.ProxyServer))؛ برای دانلود دور زده می‌شود."
        }
    } catch { }
}

# ``--resume-retries`` only exists in pip >= 24.2; older pips abort with
# "no such option", which looks like a network failure. Probe once.
$supportsResume = $false
try {
    $pipHelp = (& $pyExe @pyArgs -m pip download --help 2>$null) -join "`n"
    $supportsResume = $pipHelp -match '--resume-retries'
} catch { }

function Invoke-PipDownload {
    <#
        pip download با چند تلاش. میرورهای عمومی (و پروکسی) گاهی اتصال را
        وسط دریافت نیمه‌کاره قطع می‌کنند و pip با آن که retry داخلی دارد،
        گاهی کل دستور را شکست می‌دهد؛ اجرای دوباره از صفر معمولاً موفق است
        و wheel های گرفته‌شده دوباره دانلود نمی‌شوند.
    #>
    param([string[]]$PipArgs)
    $attempts = [Math]::Max(1, $PipRetries)
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        $arguments = @($pyArgs) + @(
            "-m", "pip", "download", "--dest", $wheels,
            "--only-binary=:all:", "--python-version", "3.11", "--platform", "win_amd64",
            "--implementation", "cp", "--retries", "5", "--timeout", "60",
            "--disable-pip-version-check"
        )
        # A 2.4 GB wheel over a flaky link is the usual reason this step
        # "hangs": without resume every reset restarts the download from zero.
        if ($supportsResume) { $arguments += @("--resume-retries", "20") }
        if ($IndexUrl) { $arguments += @("--index-url", $IndexUrl) }
        $arguments += $PipArgs
        $savedNoProxy = $env:NO_PROXY
        if ($bypassSystemProxy) { $env:NO_PROXY = "*"; $env:no_proxy = "*" }
        try {
            & $pyExe @arguments
        } finally {
            $env:NO_PROXY = $savedNoProxy
            if ($null -eq $savedNoProxy) { Remove-Item Env:no_proxy -ErrorAction SilentlyContinue }
        }
        if ($LASTEXITCODE -eq 0) { return $true }
        if ($attempt -lt $attempts) {
            Write-Warn "تلاش $attempt از $attempts ناموفق بود؛ ۵ ثانیه صبر و تلاش دوباره"
            Start-Sleep -Seconds 5
        }
    }
    return $false
}

function Save-RemoteFile {
    <#
        دریافت یک فایل بزرگ با curl و ادامهٔ دریافت نیمه‌کاره (-C -).
        pip برای هر قطعی از صفر شروع می‌کند و wheel ۲٫۴ گیگابایتی torch روی
        اتصال ناپایدار هیچ‌وقت کامل نمی‌شود؛ curl فایل .part را نگه می‌دارد و
        از همان‌جا ادامه می‌دهد.
    #>
    param([string]$Url, [string]$Destination, [int]$Attempts = 8)
    if (Test-Path $Destination) { return $true }
    $partial = "$Destination.part"
    $expected = 0
    try {
        $head = (& curl.exe -sIL --max-time 30 $Url 2>$null) -join "`n"
        $match = [regex]::Match($head, '(?im)^content-length:\s*(\d+)')
        if ($match.Success) { $expected = [int64]$match.Groups[1].Value }
    } catch { }
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        & curl.exe -L --fail --retry 5 --retry-all-errors --retry-delay 2 `
            --connect-timeout 30 -C - -o $partial $Url
        $code = $LASTEXITCODE
        if (Test-Path $partial) {
            $size = (Get-Item $partial).Length
            $complete = ($expected -gt 0 -and $size -eq $expected) -or ($expected -eq 0 -and $code -eq 0)
            if ($complete) {
                Move-Item $partial $Destination -Force
                return $true
            }
        }
        Write-Warn "قطعی در دریافت $(Split-Path $Destination -Leaf)؛ ادامه از $([math]::Round($(if (Test-Path $partial) { (Get-Item $partial).Length } else { 0 }) / 1MB)) مگابایت (تلاش $attempt/$Attempts)"
        Start-Sleep -Seconds 3
    }
    return $false
}

# If the cache already matches the pinned set, the bundle is filled from it and
# every network step below is skipped entirely.
if ($cacheHit) {
    New-Item -ItemType Directory -Force -Path $wheels | Out-Null
    $seedArgs = @($cacheDir, $wheels, "*.whl", "/NJH", "/NJS", "/NDL", "/NFL", "/NP", "/R:1", "/W:1")
    & robocopy @seedArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "کپی wheel ها از کش ناموفق بود (کد $LASTEXITCODE)." }
    Write-Ok "wheel ها از کش در بسته کپی شدند"
}

if (-not $cacheHit -and -not (Invoke-PipDownload -PipArgs @("pip", "setuptools", "wheel", "--quiet"))) {
    throw "دانلود pip/setuptools ناموفق بود."
}

# torch/torchvision از ایندکس pyTorch گرفته می‌شوند. قبلاً با --extra-index-url
# در همان دستور pip بود؛ ولی pip برای *هر* بسته به آن ایندکس هم سر می‌زد و یک
# قطعی در آن میزبان کل دانلود را می‌شکست. اکنون جدا و با curl قابل ادامه است.
$torchPins = @($pinLines | Where-Object { $_ -match '^(torch|torchvision)==' })
$otherPins = @($pinLines | Where-Object { $_ -notmatch '^(torch|torchvision)==' })
$downloadReq = Join-Path $out "requirements-download.txt"
Set-Content -Path $downloadReq -Value (@("# torch/torchvision are fetched separately (resumable curl)") + $otherPins) -Encoding ASCII

foreach ($pin in $(if ($cacheHit) { @() } else { $torchPins })) {
    $name, $version = $pin -split '==' | Select-Object -First 2
    $fileName = "$name-$version-cp311-cp311-win_amd64.whl"
    $target = Join-Path $wheels $fileName
    if (Test-Path $target) { Write-Ok "$fileName از قبل موجود است"; continue }
    $url = "$torchIndex/$name-$([uri]::EscapeDataString($version))-cp311-cp311-win_amd64.whl"
    Write-Host "  ... دانلود $fileName (چند صد مگابایت؛ با curl و قابل ادامه)"
    if (-not (Save-RemoteFile -Url $url -Destination $target)) {
        throw "دانلود $fileName ناموفق بود. اتصال را بررسی کنید یا فایل را دستی در wheels\ بگذارید."
    }
    Write-Ok "$fileName دانلود شد ($([math]::Round((Get-Item $target).Length / 1MB)) مگابایت)"
}

# -c constraints-offline.txt pins torch/torchvision to the tag already fetched
# above. Without it pip pulls whatever torch the dependencies ask for (e.g.
# torch-2.15.0) and the bundle would contain two competing torch wheels.
if (-not $cacheHit -and -not (Invoke-PipDownload -PipArgs @("-r", $downloadReq, "-c", $consFile, "--find-links", $wheels))) {
    Write-Warn "دانلود بسته‌ها شکست خورد؛ اتصال میرور ناپایدار است."
    Write-Warn "با میرور دیگری امتحان کنید، مثلاً:"
    Write-Warn "  -IndexUrl https://mirrors.cloud.tencent.com/pypi/simple"
    Write-Warn "  -IndexUrl https://mirrors.aliyun.com/pypi/simple"
    throw "دانلود بسته‌های اصلی ناموفق بود (خروجی بالا را ببینید)."
}

$wheelCount = (Get-ChildItem $wheels -Filter *.whl).Count
Write-Ok "$wheelCount فایل wheel در بسته موجود است"

# Remember this exact pin set so the next release can skip the download.
if (-not $cacheHit -and $cacheDir -and -not $DryRun) {
    if ($wheelCount -lt 20) {
        Write-Warn "کش wheel ذخیره نشد: تعداد wheel ها کم است ($wheelCount)"
    } else {
        New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
        $saveArgs = @($wheels, $cacheDir, "*.whl", "/NJH", "/NJS", "/NDL", "/NFL", "/NP", "/R:1", "/W:1")
        & robocopy @saveArgs | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Write-Warn "کپی wheel ها به کش ناموفق بود (کد $LASTEXITCODE)"
        } else {
            Set-Content -Path $stampFile -Value "$signature`ncuda=$CudaTag`nwheels=$wheelCount" -Encoding ASCII
            Write-Ok "کش wheel برای بروزرسانی‌های بعدی ذخیره شد: $cacheDir"
        }
    }
}

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

Write-Step "گردآوری رابط کاربری ساخته‌شده"
# بستهٔ آفلاین فقط چیزهایی را دارد که باید دانلود یا ساخته شوند (wheel، وزن مدل،
# ffmpeg و dist رابط کاربری). سورس برنامه در مخزن می‌ماند و نصب هم در همان‌جا
# انجام می‌شود، پس دیگر کپی سورس لازم نیست.
$distCandidates = @()
if ($FrontendDist) { $distCandidates += $FrontendDist }
if ($repoRoot) { $distCandidates += (Join-Path $repoRoot "frontend\dist") }
$dist = $distCandidates | Where-Object { $_ -and (Test-Path (Join-Path $_ "index.html")) } | Select-Object -First 1
if (-not $dist) {
    throw "dist رابط کاربری پیدا نشد. اول در پوشه frontend دستورهای «npm ci» و «npm run build» را اجرا کنید (یا -FrontendDist بدهید)."
}
if (Test-Path $distOut) { Remove-Item $distOut -Recurse -Force -ErrorAction SilentlyContinue }
$started = Get-Date
Write-Host "  ... کپی dist رابط کاربری"
$distArgs = @($dist, $distOut, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
& robocopy @distArgs | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy نتوانست dist رابط کاربری را کپی کند (کد $LASTEXITCODE)." }
$seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
$distFiles = (Get-ChildItem $distOut -Recurse -File | Measure-Object).Count
Write-Ok "رابط کاربری در frontend-dist/ آماده شد ($distFiles فایل، $seconds ثانیه)"

# ---------------------------------------------------------------------------
Write-Step "نوشتن اسکریپت‌ها، راهنما و فهرست بسته"
foreach ($name in @("install_offline.ps1", "run.ps1")) {
    $source = if ($repoRoot) { Join-Path (Join-Path $repoRoot "scripts") $name } else { Join-Path $scriptDir $name }
    if (Test-Path $source) {
        Copy-Item $source $out -Force
        Write-Ok "$name در ریشه بسته قرار گرفت"
    } else {
        throw "$name پیدا نشد؛ بسته بدون اسکریپت نصب/اجرا قابل استفاده نیست."
    }
}

@"
CameraSmart — بستهٔ پیش‌نیازها و راهنمای نصب آفلاین
===================================================

این پوشه فقط چیزهایی است که باید دانلود شوند: wheel های پایتون، وزن‌های مدل،
ffmpeg/ffprobe و رابط کاربری ساخته‌شده. سورس برنامه اینجا نیست و در مخزن خودش
می‌ماند.

گام ۱ — نصب پیش‌نیازها (فقط یک بار؛ اینترنت لازم ندارد)
-------------------------------------------------------
در ریشهٔ پروژه (پوشه‌ای که backend و frontend در آن است):

   powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1

نصب می‌کند: محیط مجازی در backend\.venv، بسته‌های پایتون از wheels همین پوشه،
وزن مدل‌ها در backend\data\models، ffmpeg در backend\bin، رابط کاربری در
frontend\dist و فایل backend\.env (اگر از قبل نباشد).
هر چیزی که از قبل نصب شده باشد دوباره نصب نمی‌شود؛ اسکریپت گزارش می‌دهد.

گزینه‌های مفید:  -BundleDir مسیر دیگر همین پوشه   -Port 8080

گام ۲ — اجرای برنامه
--------------------
   powershell -ExecutionPolicy Bypass -File scripts\run.ps1

سپس در مرورگر: http://127.0.0.1:8000
در اولین بازدید، رمز ادمین را دو بار تعیین کنید.

پیش‌نیازها
----------
- ویندوز ۶۴ بیتی
- Python 3.11 (فقط برای نصب؛ از python.org با گزینه Add to PATH)
- Node.js لازم نیست (رابط کاربری از پیش ساخته شده است)
- درایور NVIDIA 525 یا بالاتر برای تشخیص روی GPU (بدون آن هم CPU کار می‌کند)

نصب همیشه از فایل‌های همین پوشه انجام می‌شود، نه از اینترنت.
"@ | Set-Content -Path (Join-Path $out "README-INSTALL.txt") -Encoding UTF8
Write-Ok "README-INSTALL.txt نوشته شد"

$versions = @{}
foreach ($line in $pinLines) {
    if ($line -match '^([^=]+)==(.+)$') { $versions[$Matches[1].Trim().ToLower()] = $Matches[2].Trim() }
}
$modelInfo = @()
foreach ($file in @(Get-ChildItem $modelsOut -Filter *.pth -ErrorAction SilentlyContinue)) {
    $modelInfo += [ordered]@{
        name = $file.Name
        mb   = [math]::Round($file.Length / 1MB)
        md5  = (Get-FileHash $file.FullName -Algorithm MD5).Hash.ToLower()
    }
}
$wheelBytes = (Get-ChildItem $wheels -Filter *.whl | Measure-Object Length -Sum).Sum
$distOk = Test-Path (Join-Path $distOut "index.html")

# Version stamp: the installer compares the database revision in the bundle with
# the one recorded in the database, so an old bundle can never silently run
# against a newer schema (and vice versa).
$appVersion = "1.0.0"
$dbRevision = ""
$gitSha = ""
if ($repoRoot) {
    $configFile = Join-Path $repoRoot "backend\app\core\config.py"
    if (Test-Path $configFile) {
        $configText = Get-Content $configFile -Raw
        if ($configText -match 'version:\s*str\s*=\s*"([^"]+)"') { $appVersion = $Matches[1] }
    }
    $versionsDir = Join-Path $repoRoot "backend\alembic\versions"
    if (Test-Path $versionsDir) {
        $revisions = @{}
        $downRevisions = @{}
        foreach ($file in (Get-ChildItem $versionsDir -Filter *.py -ErrorAction SilentlyContinue)) {
            $text = Get-Content $file.FullName -Raw
            $revMatch = [regex]::Match($text, '(?m)^revision:\s*str\s*=\s*"([^"]+)"')
            if ($revMatch.Success) { $revisions[$revMatch.Groups[1].Value] = $true }
            $downMatch = [regex]::Match($text, '(?m)^down_revision:\s*[^=]+=\s*"([^"]+)"')
            if ($downMatch.Success) { $downRevisions[$downMatch.Groups[1].Value] = $true }
        }
        $head = @($revisions.Keys | Where-Object { -not $downRevisions.ContainsKey($_) } | Sort-Object)
        if ($head.Count -gt 0) { $dbRevision = $head[-1] }
    }
    try {
        $gitSha = (& git -C $repoRoot rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -ne 0) { $gitSha = "" }
    } catch { $gitSha = "" }
}

@{
    generated_at    = (Get-Date).ToUniversalTime().ToString("o")
    app_version     = $appVersion
    git_sha         = ("" + $gitSha).Trim()
    db_revision     = $dbRevision
    wheel_cache     = $cacheDir
    wheel_cache_hit = [bool]$cacheHit
    platform        = "windows-x64"
    python_target   = "3.11"
    cuda_tag        = $CudaTag
    source_location = "repository (not bundled)"
    wheel_count     = $wheelCount
    wheel_mb        = [math]::Round($wheelBytes / 1MB)
    has_frontend    = $distOk
    has_ffmpeg      = (Test-Path (Join-Path $binOut "ffmpeg.exe"))
    models          = $modelInfo
    versions        = $versions
} | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $out "manifest.json") -Encoding UTF8
Write-Ok "manifest.json نوشته شد"

Write-Step "پایان"
$sizeGb = [math]::Round((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1GB, 2)
Write-Host "بستهٔ آفلاین آماده است: $out" -ForegroundColor Green
Write-Host "حجم کل: $sizeGb گیگابایت — wheel: $wheelCount — مدل: $($modelInfo.Count)" -ForegroundColor Green
Write-Host "نصب:  powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1" -ForegroundColor Cyan
Write-Host "اجرا:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1" -ForegroundColor Cyan
