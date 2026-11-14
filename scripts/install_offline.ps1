<#
.SYNOPSIS
    نصب پیش‌نیازهای CameraSmart از بستهٔ آفلاین — بدون اینترنت و بدون اجرای برنامه.

.DESCRIPTION
    این اسکریپت فقط «نصب» می‌کند: محیط مجازی، بسته‌های پایتون از wheel های محلی،
    وزن مدل‌ها، ffmpeg/ffprobe، رابط کاربری ساخته‌شده و فایل تنظیمات. سورس برنامه
    سر جای خودش در مخزن می‌ماند و جابه‌جا نمی‌شود.

    هر چیزی که از قبل نصب شده باشد دوباره نصب نمی‌شود؛ فقط کمبودها اضافه می‌شوند.

    پیش‌نیاز بسته: scripts\download_offline.ps1 قبلاً اجرا شده باشد (پوشهٔ offline
    با wheels/، models/، bin/ و frontend-dist/).

.PARAMETER BundleDir
    مسیر پوشهٔ بستهٔ آفلاین (پیش‌فرض: offline\ در ریشهٔ پروژه)

.PARAMETER Port
    پورت ذخیره‌شده در .env (پیش‌فرض 8000؛ .env موجود دست‌نخورده می‌ماند مگر این را بدهید)

.PARAMETER Lan
    گوش‌دادن روی 0.0.0.0 + قانون فایروال (Administrator) — در .env هم نوشته می‌شود

.PARAMETER Autostart
    اجرای خودکار برنامه در ورود کاربر (Scheduled Task؛ Administrator)

.PARAMETER Repair
    حتی اگر همه‌چیز نصب باشد، بسته‌ها را دوباره از wheel های محلی نصب کن

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_offline.ps1 -BundleDir D:\offline -Port 8080
#>
[CmdletBinding()]
param(
    [string]$BundleDir = "",
    [int]$Port = 0,
    [string]$PythonExe = "",
    #: پوشهٔ داده (پایگاه‌داده، تصاویر، مدل‌ها) بیرون از پوشهٔ برنامه، تا بروزرسانی
    #: فقط فایل‌های برنامه را عوض کند و به داده دست نزند.
    [string]$DataDir = "",
    [switch]$Lan,
    [switch]$Autostart,
    [switch]$Repair
)

$ErrorActionPreference = "Stop"
# پیکربندی‌های استاندارد ویندوز/نصب‌کننده‌ها ممکن است پیام‌های فارسی را با
# code page سیستم بنویسند و نتیجه «????» شود.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [System.Text.Encoding]::UTF8
# The embedded Python snippets print Persian; without this they die on cp1252
# whenever the output is redirected to a file.
$env:PYTHONIOENCODING = "utf-8"

function Write-Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Write-Ok($text)   { Write-Host "  [ok] $text" -ForegroundColor Green }
function Write-Skip($text) { Write-Host "  [--] $text" -ForegroundColor DarkGray }
function Write-Warn($text) { Write-Host "  [!]  $text" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# ۰) محل پروژه و بسته
# ---------------------------------------------------------------------------
$scriptDir = $PSScriptRoot
$root = Split-Path -Parent $scriptDir
if (-not (Test-Path (Join-Path $root "backend\app\main.py"))) {
    # اجرا از داخل خود بسته: پوشهٔ بسته کنار scripts نیست، پس ریشه از مسیر جاری
    # گرفته می‌شود تا کاربر مجبور نباشد مسیر بدهد.
    $root = (Get-Location).Path
    if (-not (Test-Path (Join-Path $root "backend\app\main.py"))) {
        throw "ریشهٔ پروژه پیدا نشد. این اسکریپت را از ریشهٔ پروژه (پوشه‌ای که backend و frontend در آن است) اجرا کنید."
    }
}
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"

if (-not $BundleDir) {
    $default = Join-Path $root "offline"
    $BundleDir = if (Test-Path (Join-Path $default "wheels")) { $default } elseif (Test-Path (Join-Path $scriptDir "wheels")) { $scriptDir } else { $default }
}
$resolved = Resolve-Path $BundleDir -ErrorAction SilentlyContinue
$bundle = if ($resolved) { $resolved.Path } else { $null }
$hostAddr = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }

Write-Host "CameraSmart — نصب پیش‌نیازها" -ForegroundColor White
Write-Host "  پروژه: $root"
Write-Host "  بستهٔ آفلاین: $BundleDir"

# ---------------------------------------------------------------------------
# ۱) بازبینی بسته (هیچ دانلودی انجام نمی‌شود)
# ---------------------------------------------------------------------------
Write-Step "۱/۷ بازبینی بستهٔ آفلاین"
if (-not $bundle -or -not (Test-Path (Join-Path $bundle "wheels"))) {
    throw "پوشهٔ بسته پیدا نشد: $BundleDir`nاول روی سیستم آنلاین اجرا کنید:  powershell -ExecutionPolicy Bypass -File scripts\download_offline.ps1"
}
$wheels = Join-Path $bundle "wheels"
$models = Join-Path $bundle "models"
$binSrc = Join-Path $bundle "bin"
$distSrc = Join-Path $bundle "frontend-dist"
$reqFile = Join-Path $bundle "requirements-offline.txt"
$consFile = Join-Path $bundle "constraints-offline.txt"

$problems = @()
$wheelCount = (Get-ChildItem $wheels -Filter *.whl -ErrorAction SilentlyContinue).Count
if ($wheelCount -eq 0) { $problems += "هیچ wheel ای در $wheels نیست" }
if (-not (Test-Path $reqFile)) { $problems += "requirements-offline.txt نیست" }
$modelFiles = @(Get-ChildItem $models -Filter *.pth -ErrorAction SilentlyContinue)
if ($modelFiles.Count -eq 0) { $problems += "هیچ وزن مدلی در $models نیست" }
if (-not (Test-Path (Join-Path $binSrc "ffmpeg.exe"))) { $problems += "ffmpeg.exe در bin نیست" }
if (-not (Test-Path (Join-Path $distSrc "index.html"))) { $problems += "رابط کاربری ساخته‌شده در frontend-dist نیست" }
if ($problems.Count -gt 0) {
    Write-Warn "بسته ناقص است:"
    $problems | ForEach-Object { Write-Host "        - $_" -ForegroundColor Yellow }
    throw "بستهٔ آفلاین کامل نیست. ابتدا scripts\download_offline.ps1 را (روی سیستم متصل) اجرا کنید."
}
Write-Ok "$wheelCount فایل wheel"
Write-Ok "وزن مدل: $($modelFiles.Name -join ', ')"
Write-Ok "ffmpeg / رابط کاربری / فهرست نسخه‌ها موجود است"
$bundleVersion = ""
$bundleDbRevision = ""
if (Test-Path (Join-Path $bundle "manifest.json")) {
    $manifest = Get-Content (Join-Path $bundle "manifest.json") -Raw | ConvertFrom-Json
    Write-Host ("       ساخته‌شده: {0} — CUDA: {1}" -f $manifest.generated_at, $manifest.cuda_tag)
    $bundleVersion = "$($manifest.app_version)"
    $bundleDbRevision = "$($manifest.db_revision)"
}
# A code-only release zip carries its version in version.json at the app root.
$versionFile = Join-Path $root "version.json"
if (Test-Path $versionFile) {
    $localVersion = Get-Content $versionFile -Raw | ConvertFrom-Json
    if ($localVersion.app_version) { $bundleVersion = "$($localVersion.app_version)" }
    if ($localVersion.db_revision) { $bundleDbRevision = "$($localVersion.db_revision)" }
    Write-Host ("       نسخهٔ برنامه: {0} — نسخهٔ پایگاه‌داده: {1}" -f $bundleVersion, $bundleDbRevision)
} elseif ($bundleVersion) {
    Write-Host ("       نسخهٔ برنامه: {0} — نسخهٔ پایگاه‌داده: {1}" -f $bundleVersion, $bundleDbRevision)
}

# ---------------------------------------------------------------------------
# ۲) Python 3.11
# ---------------------------------------------------------------------------
Write-Step "۲/۷ پیدا کردن Python 3.11"
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

$venvDir = Join-Path $backend ".venv"
$py = Join-Path $venvDir "Scripts\python.exe"
$venvReady = $false
if (Test-Path $py) {
    $venvVersion = & $py -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($venvVersion -and $venvVersion.Trim() -eq "3.11") { $venvReady = $true }
}

if ($venvReady) {
    Write-Skip "محیط مجازی 3.11 از قبل آماده است: $venvDir"
} else {
    $python = Find-Python311 -Candidates @($PythonExe, "py -3.11", "py", "python", "python3")
    if (-not $python) {
        throw "Python 3.11 پیدا نشد. آن را از python.org نصب کنید (گزینهٔ Add python.exe to PATH) و دوباره اجرا کنید."
    }
    Write-Ok "Python 3.11: $($python.exe) $($python.args -join ' ')"
    if (Test-Path $venvDir) { Remove-Item $venvDir -Recurse -Force -ErrorAction SilentlyContinue }
    & $python.exe @($python.args) -m venv $venvDir
    if (-not (Test-Path $py)) { throw "ساخت محیط مجازی ناموفق بود." }
    Write-Ok "محیط مجازی ساخته شد: $venvDir"
}

# ---------------------------------------------------------------------------
# ۳) بسته‌های پایتون — فقط کمبودها، فقط از wheel های محلی
# ---------------------------------------------------------------------------
Write-Step "۳/۷ بررسی بسته‌های پایتون"
$checkScript = @'
import importlib.metadata as md
import sys

pins = {}
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, want = line.split("==", 1)
        pins[name.strip()] = want.strip()

# پکیج منسوخ pynvml فقط یک redirector به nvidia-ml-py است (هر دو ماژول pynvml
# را نصب می‌کنند)؛ در بسته‌های قدیمی‌تر پین شده، پس حاضربودن جایگزین کافی است.
ALIASES = {"pynvml": "nvidia-ml-py"}

missing = []
for name, want in pins.items():
    have = None
    replaced = False
    candidates = [name] + ([ALIASES[name]] if name in ALIASES else [])
    for candidate in candidates:
        try:
            have = md.version(candidate)
        except md.PackageNotFoundError:
            continue
        replaced = candidate != name
        break
    if have is None:
        missing.append("%s %s" % (name, want))
        continue
    if replaced:
        continue
    if "+" in want:
        ok = have == want
    else:
        ok = have.split("+")[0] == want.split("+")[0]
    if not ok:
        missing.append("%s (نصب: %s / لازم: %s)" % (name, have, want))

print(len(pins))
print(len(missing))
for item in missing:
    print(item)
'@
$checkFile = Join-Path $env:TEMP "camerasmart-check-pins.py"
Set-Content -Path $checkFile -Value $checkScript -Encoding UTF8
$checkOut = & $py $checkFile $reqFile
$lines = @($checkOut)
$pinTotal = [int]$lines[0]
$missingCount = [int]$lines[1]

$needsInstall = $Repair -or ($missingCount -gt 0)
if (-not $needsInstall) {
    Write-Skip "همهٔ $pinTotal بستهٔ پین‌شده از قبل نصب هستند (نصب دوباره انجام نشد)"
} else {
    if ($missingCount -gt 0) {
        Write-Host "       $missingCount بسته نصب نیست؛ از wheel های محلی نصب می‌شود:"
        $missingLines = @($lines | Select-Object -Skip 2)
        $missingLines | Select-Object -First 12 | ForEach-Object { Write-Host "         - $_" -ForegroundColor DarkGray }
    }
    if ($Repair) { Write-Host "       حالت -Repair: نصب دوبارهٔ بسته‌ها" -ForegroundColor DarkGray }
    & $py -m pip install --no-index --disable-pip-version-check --find-links $wheels --upgrade pip setuptools wheel --quiet
    $constraintsArgs = @()
    if (Test-Path $consFile) { $constraintsArgs = @("-c", $consFile) }
    & $py -m pip install --no-index --find-links $wheels -r $reqFile @constraintsArgs
    if ($LASTEXITCODE -ne 0) {
        throw "نصب بسته‌ها از بستهٔ محلی ناموفق بود (خروجی بالا را ببینید). چیزی از اینترنت دانلود نمی‌شود؛ اگر wheel ای کم است بسته را دوباره بسازید."
    }
    Write-Ok "بسته‌های پایتون نصب شدند"
}

# The old ``pynvml`` distribution is a redirector shim to ``nvidia-ml-py``: it
# ships no real code and prints a FutureWarning on every start. Bundles before
# 1.0.20261114 installed it, so drop it from an existing environment. This runs
# even when nothing needed installing, which is exactly the update case.
$sitePackages = (@(& $py -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") -join "").Trim()
if ($sitePackages -and (Test-Path $sitePackages)) {
    $shim = @(Get-ChildItem -Path $sitePackages -Filter "pynvml-*.dist-info" -Directory -ErrorAction SilentlyContinue)
    $real = @(Get-ChildItem -Path $sitePackages -Filter "nvidia_ml_py-*.dist-info" -Directory -ErrorAction SilentlyContinue)
    if ($shim.Count -gt 0 -and $real.Count -gt 0) {
        & $py -m pip uninstall --yes --disable-pip-version-check pynvml | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "بستهٔ منسوخ pynvml حذف شد (NVML از nvidia-ml-py خوانده می‌شود)"
        } else {
            Write-Warn "حذف بستهٔ منسوخ pynvml انجام نشد؛ فقط یک هشدار بی‌خطر در لاگ می‌ماند"
        }
    }
}

# ---------------------------------------------------------------------------
# ۴) وزن مدل‌ها و ffmpeg
# ---------------------------------------------------------------------------
Write-Step "۴/۷ وزن مدل‌ها و ffmpeg"
$modelsTarget = Join-Path $backend "data\models"
New-Item -ItemType Directory -Force -Path $modelsTarget | Out-Null
foreach ($file in $modelFiles) {
    $target = Join-Path $modelsTarget $file.Name
    if ((Test-Path $target) -and ((Get-Item $target).Length -eq $file.Length)) {
        Write-Skip "$($file.Name) از قبل موجود است"
        continue
    }
    Copy-Item $file.FullName $target -Force
    Write-Ok "$($file.Name) → backend\data\models"
}

$binTarget = Join-Path $backend "bin"
New-Item -ItemType Directory -Force -Path $binTarget | Out-Null
$ffmpegTarget = $null
foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    $source = Join-Path $binSrc $tool
    $target = Join-Path $binTarget $tool
    if (Test-Path $target) {
        if ($tool -eq "ffmpeg.exe") { $ffmpegTarget = $target }
        Write-Skip "$tool از قبل موجود است"
        continue
    }
    if (Test-Path $source) {
        Copy-Item $source $target -Force
        if ($tool -eq "ffmpeg.exe") { $ffmpegTarget = $target }
        Write-Ok "$tool → backend\bin"
    } else {
        Write-Warn "$tool در بسته نبود"
    }
}
if (-not $ffmpegTarget -and (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Skip "ffmpeg در PATH موجود است"
}

# ---------------------------------------------------------------------------
# ۵) رابط کاربری و فایل تنظیمات
# ---------------------------------------------------------------------------
Write-Step "۵/۷ رابط کاربری و تنظیمات"
$distTarget = Join-Path $frontend "dist"
$distCurrent = Join-Path $distTarget "index.html"
$distSource = Join-Path $distSrc "index.html"
$sameDist = $false
if (Test-Path $distCurrent) {
    $sameDist = (Get-FileHash $distCurrent -Algorithm SHA256).Hash -eq (Get-FileHash $distSource -Algorithm SHA256).Hash
}
if ($sameDist) {
    Write-Skip "رابط کاربری از قبل به‌روز است: frontend\dist"
} else {
    if (Test-Path $distTarget) { Remove-Item $distTarget -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $frontend | Out-Null
    Copy-Item -Path $distSrc -Destination $distTarget -Recurse -Force
    Write-Ok "رابط کاربری در frontend\dist نصب شد"
}

$envFile = Join-Path $backend ".env"
if (-not (Test-Path $envFile)) {
    if (-not (Test-Path (Join-Path $backend ".env.example"))) {
        throw "backend\.env.example پیدا نشد؛ نمی‌توان فایل تنظیمات را ساخت."
    }
    Copy-Item (Join-Path $backend ".env.example") $envFile
    $effectivePort = if ($Port -gt 0) { $Port } else { 8000 }
    $extra = @(
        "",
        "# --- نوشته‌شده توسط نصب آفلاین ---",
        "CAMERASMART_HOST=$hostAddr",
        "CAMERASMART_PORT=$effectivePort"
    )
    if ($ffmpegTarget) {
        $ffmpegPosix = $ffmpegTarget -replace '\\', '/'
        $extra += "CAMERASMART_FFMPEG_BIN=$ffmpegPosix"
        $extra += "CAMERASMART_FFPROBE_BIN=$($ffmpegPosix -replace 'ffmpeg\.exe$', 'ffprobe.exe')"
    }
    Add-Content -Path $envFile -Encoding UTF8 -Value $extra
    Write-Ok "backend\.env ساخته شد (پورت $effectivePort)"
} else {
    $existing = Get-Content $envFile
    $currentPort = ($existing | Select-String '^CAMERASMART_PORT=' | Select-Object -First 1) -replace '^CAMERASMART_PORT=', ''
    if ($Port -gt 0 -and $currentPort -and $currentPort.Trim() -ne "$Port") {
        $updated = $existing -replace '^CAMERASMART_PORT=.*$', "CAMERASMART_PORT=$Port"
        Set-Content -Path $envFile -Value $updated -Encoding UTF8
        Write-Ok "پورت .env به $Port تغییر کرد"
    } else {
        Write-Skip "backend\.env از قبل وجود دارد (دست‌نخورده ماند، پورت $(if ($currentPort) { $currentPort.Trim() } else { '8000' }))"
    }
    if ($Lan) {
        $updated = (Get-Content $envFile) -replace '^CAMERASMART_HOST=.*$', "CAMERASMART_HOST=0.0.0.0"
        Set-Content -Path $envFile -Value $updated -Encoding UTF8
        Write-Ok "CAMERASMART_HOST در .env روی 0.0.0.0 تنظیم شد"
    }
}

# ---------------------------------------------------------------------------
# پوشهٔ داده بیرون از پوشهٔ برنامه (اختیاری ولی پیشنهادی)
# ---------------------------------------------------------------------------
if ($DataDir) {
    $dataPath = [System.IO.Path]::GetFullPath($DataDir)
    New-Item -ItemType Directory -Force -Path $dataPath | Out-Null
    $dataPosix = $dataPath -replace '\\', '/'
    $envLines = @(Get-Content $envFile)
    if ($envLines | Select-String '^CAMERASMART_DATA_DIR=') {
        $envLines = @($envLines -replace '^CAMERASMART_DATA_DIR=.*$', "CAMERASMART_DATA_DIR=$dataPosix")
    } else {
        $envLines += @("", "# --- پوشهٔ داده خارج از پوشهٔ برنامه (بروزرسانی به داده دست نمی‌زند) ---", "CAMERASMART_DATA_DIR=$dataPosix")
    }
    Set-Content -Path $envFile -Value $envLines -Encoding UTF8
    Write-Ok "پوشهٔ داده در .env تنظیم شد: $dataPath"
}

# ---------------------------------------------------------------------------
# ۶) پایگاه داده
# ---------------------------------------------------------------------------
Write-Step "۶/۷ ساخت/به‌روزرسانی پایگاه داده"
Push-Location $backend
try {
    # An existing database may have been created by the app itself
    # (``auto_create_tables``) and therefore have no alembic revision recorded.
    # Upgrading such a database fails with "table ... already exists"; stamping
    # the current head first makes the following upgrade a no-op.
    $dbStateFile = Join-Path $env:TEMP "camerasmart-db-state.py"
    Set-Content -Path $dbStateFile -Encoding UTF8 -Value @'
import sqlite3
import sys

try:
    db = sqlite3.connect(sys.argv[1])
except sqlite3.Error:
    print("absent")
    raise SystemExit(0)

tables = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
if "cameras" not in tables:
    print("empty")
elif "alembic_version" not in tables:
    print("unversioned")
else:
    print("unversioned" if not db.execute("select count(*) from alembic_version").fetchone()[0] else "versioned")
'@
    $dbState = (& $py $dbStateFile (Join-Path $backend "data\camerasmart.db") | Select-Object -Last 1)
    if ($dbState -eq "unversioned") {
        Write-Host "       پایگاه داده از قبل ساخته شده ولی نسخه‌اش ثبت نشده؛ ثبت نسخه (alembic stamp head)" -ForegroundColor DarkGray
        & $py -m alembic stamp head
    }
    & $py -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "alembic upgrade head ناموفق بود (خروجی بالا)."
    }
    Write-Ok "جداول پایگاه داده آماده هستند"

    # Version compatibility: the bundle lists the schema revision it expects. If
    # the database ends up on a different revision, the code and the data do not
    # belong together and running the app would fail at the first query.
    if ($bundleDbRevision) {
        # Do not redirect stderr here: PowerShell 5.1 turns a native command's
        # stderr into a terminating error when it is redirected and the script
        # runs with ``$ErrorActionPreference = Stop``.
        $currentRevision = (& $py -m alembic current | Out-String)
        if ($currentRevision -notmatch [regex]::Escape($bundleDbRevision)) {
            throw "ناسازگاری نسخه: بسته نسخهٔ پایگاه‌داده «$bundleDbRevision» را انتظار دارد ولی پایگاه‌داده روی نسخهٔ دیگری است.`nخروجی alembic current:`n$currentRevision`nبستهٔ سازگار با دادهٔ فعلی را نصب کنید."
        }
        Write-Ok "نسخهٔ پایگاه‌داده با بسته هم‌خوان است ($bundleDbRevision)"
    }

    Write-Step "۷/۷ بررسی سلامت نصب"
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
    if ($LASTEXITCODE -ne 0) { Write-Warn "بررسی سلامت هشدار داد؛ خروجی بالا را ببینید." }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
if ($Autostart) {
    Write-Step "اجرای خودکار در ورود"
    $taskName = "CameraSmart"
    $runner = Join-Path $scriptDir "run.ps1"
    schtasks /Query /TN $taskName > $null 2>&1
    if ($LASTEXITCODE -eq 0) { schtasks /Delete /TN $taskName /F | Out-Null }
    $action = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`""
    schtasks /Create /TN $taskName /SC ONLOGON /TR $action /RL HIGHEST /F | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok "وظیفهٔ زمان‌بندی «$taskName» ثبت شد" }
    else { Write-Warn "ثبت اجرای خودکار ناموفق بود (PowerShell را Administrator اجرا کنید)." }
}

if ($Lan) {
    Write-Step "دسترسی از شبکه"
    try {
        New-NetFirewallRule -DisplayName "CameraSmart 8000" -Direction Inbound -Protocol TCP `
            -LocalPort 8000 -Action Allow -ErrorAction Stop | Out-Null
        Write-Ok "قانون فایروال برای پورت 8000 اضافه شد"
    } catch {
        Write-Warn "افزودن قانون فایروال ممکن نشد (Administrator لازم است): $($_.Exception.Message)"
    }
}

Write-Step "پایان نصب"
Write-Host "نصب از بستهٔ محلی انجام شد؛ هیچ درخواستی به اینترنت نرفت." -ForegroundColor Green
Write-Host "(موارد نصب‌شده و مواردی که از قبل بودند در خروجی بالا با ok و -- مشخص شده‌اند.)" -ForegroundColor DarkGray
Write-Host "برای اجرا:" -ForegroundColor White
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\run.ps1" -ForegroundColor Cyan
Write-Host "سپس در مرورگر: http://127.0.0.1:$(if ($Port -gt 0) { $Port } else { 8000 })" -ForegroundColor Cyan
Write-Host "در اولین بازدید، رمز ادمین را دو بار تعیین کنید." -ForegroundColor Yellow
