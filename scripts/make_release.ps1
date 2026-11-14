<#
.SYNOPSIS
    ساخت بستهٔ نسخه‌دار CameraSmart برای نصب/بروزرسانی آفلاین.

.DESCRIPTION
    یک پوشه و (به‌صورت پیش‌فرض) یک فایل zip می‌سازد که روی سیستم مقصد فقط باید
    باز شود و مراحل نصب/اجرا اجرا شوند — بدون Git، بدون Node و بدون اینترنت.

    محتوای بسته:
      backend/            کد برنامه (بدون .venv، data، bin، .env و *.db)
      frontend/dist/      رابط کاربری از پیش ساخته‌شده
      scripts/            install_offline.ps1 و run.ps1 و بقیهٔ اسکریپت‌ها
      offline/            پیش‌نیازها (wheel، مدل، ffmpeg، dist) — مگر با -CodeOnly
      version.json        نسخهٔ برنامه، کامیت، نسخهٔ پایگاه‌داده، زمان ساخت
      README.md

.PARAMETER Version
    نسخهٔ بسته؛ پیش‌فرض: 1.0.<تاریخ امروز به شکل yyyyMMdd>

.PARAMETER OutDir
    محل خروجی (پیش‌فرض: release/ در ریشهٔ پروژه)

.PARAMETER BundleDir
    پوشهٔ پیش‌نیازهای آفلاین (پیش‌فرض: offline/)

.PARAMETER CodeOnly
    فقط کد برنامه + dist ساخته‌شده (برای بروزرسانی سیستم‌هایی که پیش‌نیازها را
    از قبل نصب کرده‌اند؛ حجم چند ده مگابایت به‌جای چند گیگابایت)

.PARAMETER BuildFrontend
    اجرای npm ci / npm run build حتی اگر frontend/dist موجود باشد

.PARAMETER Flat
    محتویات بسته در ریشهٔ zip قرار می‌گیرد (بدون پوشهٔ CameraSmart-<version>).
    برای بروزرسانی یک نصب موجود: فایل zip را در همان پوشهٔ برنامه «Extract Here»
    کنید تا فایل‌ها جایگزین شوند و پوشهٔ تو در تو ساخته نشود.

.PARAMETER NoZip
    فقط پوشهٔ نسخه ساخته شود، zip نشود

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -CodeOnly -Version 1.0.20261120

.EXAMPLE
    # بستهٔ بروزرسانی برای یک نصب آفلاین موجود (فایل مسطح، بدون پیش‌نیازها)
    powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1 -CodeOnly -Flat
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$OutDir = "",
    [string]$BundleDir = "",
    [switch]$CodeOnly,
    [switch]$BuildFrontend,
    [switch]$Flat,
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }

$scriptDir = $PSScriptRoot
$root = Split-Path -Parent $scriptDir
if (-not (Test-Path (Join-Path $root "backend\app\main.py"))) {
    throw "ریشهٔ پروژه پیدا نشد. اسکریپت را از پوشهٔ scripts پروژه اجرا کنید."
}

$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$out = if ($OutDir) { [System.IO.Path]::GetFullPath($OutDir) } else { Join-Path $root "release" }
$bundle = if ($BundleDir) { [System.IO.Path]::GetFullPath($BundleDir) } else { Join-Path $root "offline" }
if (-not $Version) { $Version = "1.0." + (Get-Date -Format "yyyyMMdd") }
$stage = Join-Path $out "CameraSmart-$Version"

Write-Host "CameraSmart — ساخت بستهٔ نسخه‌دار $Version" -ForegroundColor White
Write-Host "  خروجی: $out"
Write-Host "  پیش‌نیازها: $(if ($CodeOnly) { 'همراه نیست (CodeOnly)' } else { $bundle })"

# ---------------------------------------------------------------------------
Write-Step "۱/۵ بررسی رابط کاربری"
$dist = Join-Path $frontend "dist"
$distIndex = Join-Path $dist "index.html"
if ($BuildFrontend -or -not (Test-Path $distIndex)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "frontend\dist موجود نیست و npm هم پیدا نشد. روی سیستم متصل «npm ci && npm run build» را در پوشهٔ frontend اجرا کنید، یا -FrontendDist بدهید."
    }
    Push-Location $frontend
    try {
        if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
            Write-Host "  ... نصب وابستگی‌های فرانت (npm ci)"
            & npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci ناموفق بود." }
        }
        Write-Host "  ... ساخت رابط کاربری (npm run build)"
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build ناموفق بود." }
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path $distIndex)) { throw "رابط کاربری ساخته نشده است: $distIndex" }
Write-Ok "رابط کاربری آماده است: $dist"

# ---------------------------------------------------------------------------
Write-Step "۲/۵ نسخه و نسخهٔ پایگاه‌داده"
$appVersion = "1.0.0"
$configFile = Join-Path $backend "app\core\config.py"
if (Test-Path $configFile) {
    $configText = Get-Content $configFile -Raw
    if ($configText -match 'version:\s*str\s*=\s*"([^"]+)"') { $appVersion = $Matches[1] }
}
$dbRevision = ""
$versionsDir = Join-Path $backend "alembic\versions"
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
$gitSha = ""
try {
    $gitSha = (& git -C $root rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) { $gitSha = "" }
} catch { $gitSha = "" }
$gitSha = ("" + $gitSha).Trim()
Write-Ok "برنامه $appVersion — پایگاه‌داده $dbRevision — کامیت $(if ($gitSha) { $gitSha } else { '—' })"

# ---------------------------------------------------------------------------
Write-Step "۳/۵ کپی فایل‌ها"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

function Copy-Tree($source, $target, [string[]]$excludeDirs = @(), [string[]]$excludeFiles = @()) {
    if (-not (Test-Path $source)) { throw "مسیر کپی پیدا نشد: $source" }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    $copyArgs = @($source, $target, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
    if ($excludeDirs.Count -gt 0) { $copyArgs += "/XD"; $copyArgs += $excludeDirs }
    if ($excludeFiles.Count -gt 0) { $copyArgs += "/XF"; $copyArgs += $excludeFiles }
    & robocopy @copyArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "کپی ناموفق بود ($source → $target، کد $LASTEXITCODE)" }
}

# Application code only: the virtualenv, the database, stored images, downloaded
# models and the local .env must never travel inside a release.
Copy-Tree $backend (Join-Path $stage "backend") @(".venv", "data", "bin", "__pycache__", ".pytest_cache", ".ruff_cache") @(".env", "*.db", "*.db-journal", "*.pyc")
New-Item -ItemType Directory -Force -Path (Join-Path $stage "frontend") | Out-Null
Copy-Tree $dist (Join-Path $stage "frontend\dist")

New-Item -ItemType Directory -Force -Path (Join-Path $stage "scripts") | Out-Null
foreach ($file in (Get-ChildItem $scriptDir -Filter *.ps1)) {
    Copy-Item $file.FullName (Join-Path $stage "scripts") -Force
}
if (Test-Path (Join-Path $root "README.md")) { Copy-Item (Join-Path $root "README.md") $stage -Force }
Write-Ok "کد برنامه، رابط کاربری و اسکریپت‌ها کپی شدند"

if (-not $CodeOnly) {
    if (-not (Test-Path (Join-Path $bundle "manifest.json"))) {
        throw "پوشهٔ پیش‌نیازها ($bundle) آماده نیست. اول «scripts\download_offline.ps1» را اجرا کنید یا از -CodeOnly استفاده کنید."
    }
    Copy-Tree $bundle (Join-Path $stage "offline")
    Write-Ok "پیش‌نیازها (wheel/مدل/ffmpeg/رابط کاربری) اضافه شدند"
} else {
    # حتی یک بروزرسانی «فقط کد» باید فهرست پین‌ها را با خود ببرد: بدون آن،
    # نصب‌کننده روی سیستم مقصد پین‌های قدیمی (مثل pynvml منسوخ) را می‌خواند.
    # فقط فایل‌های متنی/متادیتا کپی می‌شوند؛ wheel و مدل سنگین در نصب موجودند.
    $meta = @("manifest.json", "requirements-offline.txt", "constraints-offline.txt", "README-INSTALL.txt")
    $copied = @()
    foreach ($name in $meta) {
        $source = Join-Path $bundle $name
        if (Test-Path $source) {
            New-Item -ItemType Directory -Force -Path (Join-Path $stage "offline") | Out-Null
            Copy-Item $source (Join-Path $stage "offline\$name") -Force
            $copied += $name
        }
    }
    if ($copied.Count -gt 0) {
        Write-Ok "فهرست پیش‌نیازها به‌روز شد: $($copied -join ', ')"
    } else {
        Write-Warn "offline/ پیدا نشد؛ فهرست پین‌ها در بسته نیست (wheel ها از نصب موجود استفاده می‌شوند)"
    }
}

# ---------------------------------------------------------------------------
Write-Step "۴/۵ نوشتن version.json"
@{
    app_version   = $appVersion
    package       = $Version
    git_sha       = $gitSha
    db_revision   = $dbRevision
    python_target = "3.11"
    platform      = "windows-x64"
    built_at      = (Get-Date).ToUniversalTime().ToString("o")
    includes_prerequisites = (-not $CodeOnly)
} | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $stage "version.json") -Encoding UTF8
Write-Ok "version.json نوشته شد"

# ---------------------------------------------------------------------------
Write-Step "۵/۵ بسته‌بندی"
$folderMb = [math]::Round((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Ok "پوشهٔ نسخه: $stage ($folderMb مگابایت)"
$zipPath = ""
if (-not $NoZip) {
    $zipName = if ($Flat) { "CameraSmart-$Version-update.zip" } else { "CameraSmart-$Version.zip" }
    $zipPath = Join-Path $out $zipName
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Write-Host "  ... فشرده‌سازی (چند دقیقه برای بستهٔ کامل)"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stage, $zipPath, [System.IO.Compression.CompressionLevel]::Fastest, (-not $Flat)
    )
    $zipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Ok "فایل zip آماده است: $zipPath ($zipMb مگابایت)"
}

Write-Step "پایان"
Write-Host "روی سیستم مقصد:" -ForegroundColor White
if ($zipPath -and $Flat) {
    Write-Host "  1) فایل $([System.IO.Path]::GetFileName($zipPath)) را داخل همان پوشهٔ برنامه «Extract Here» کنید (محتویات جایگزین می‌شوند)"
} elseif ($zipPath) {
    Write-Host "  1) فایل $([System.IO.Path]::GetFileName($zipPath)) را در مسیر برنامه باز کنید"
}
Write-Host "  2) powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1$(if ($CodeOnly) { '' } else { '' })"
Write-Host "  3) powershell -ExecutionPolicy Bypass -File scripts\run.ps1"
Write-Host "داده (پایگاه‌داده/تصاویر) بیرون از پوشهٔ برنامه می‌ماند: -DataDir در نصب." -ForegroundColor DarkGray
