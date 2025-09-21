# setup-venv.ps1
[CmdletBinding()]
param(
    [switch]$Recreate,       # Видалити та створити .venv заново
    [switch]$OverwriteEnv,   # Дозволити перезапис .env
    [switch]$OpenShell       # Відкрити нове вікно PowerShell з активованим .venv
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Write-Host "=== MKR setup: venv + requirements + .env ===`n"

# 1) Дозволимо активацію тільки в межах цього процеса (безпечний режим)
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}

function Resolve-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($ver in @('-3.13','-3.12','-3.11','-3')) {
            try {
                $p = & py $ver -c "import sys;print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $p) { return @('py', $ver) }
            } catch {}
        }
    }
    foreach ($cmd in @('python','python3')) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) { return @($cmd) }
    }
    throw "Python не знайдено. Встановіть Python 3.11+ або додайте його до PATH."
}

$pyParts = Resolve-Python
$pyCmd   = $pyParts[0]
$pyArgs  = $pyParts[1..($pyParts.Length-1)]

# 2) Створення/перестворення .venv
$venvPath = Join-Path (Get-Location) ".venv"
if ($Recreate -and (Test-Path $venvPath)) {
    Write-Host "Видаляю існуючий .venv..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvPath
}
if (-not (Test-Path $venvPath)) {
    Write-Host "Створюю .venv..."
    & $pyCmd @pyArgs -m venv ".venv"
} else {
    Write-Host ".venv вже існує — використовую наявний." -ForegroundColor Yellow
}

# 3) Активація в межах скрипта
$activate = Join-Path $venvPath "Scripts\Activate.ps1"
if (-not (Test-Path $activate)) { throw "Не знайшов $activate" }
. "$activate"
Write-Host "Активовано .venv (у межах цього скрипта)."

# 4) Оновлення pip і встановлення залежностей
$venvPython = Join-Path $venvPath "Scripts\python.exe"
Write-Host "Оновлюю pip..."
& "$venvPython" -m pip install --upgrade pip

if (Test-Path "requirements.txt") {
    Write-Host "Встановлюю залежності з requirements.txt..."
    & "$venvPython" -m pip install -r "requirements.txt"
} else {
    Write-Host "Файл requirements.txt не знайдено — пропускаю крок встановлення." -ForegroundColor Yellow
}

# 5) Перейменування .env.example → .env
if (Test-Path ".env.example") {
    if (Test-Path ".env") {
        if ($OverwriteEnv) {
            Write-Host "Перезаписую існуючий .env..." -ForegroundColor Yellow
            Remove-Item ".env" -Force
            Rename-Item ".env.example" ".env"
        } else {
            Write-Host ".env вже існує — не перезаписую (додайте -OverwriteEnv для перезапису)." -ForegroundColor Yellow
        }
    } else {
        Rename-Item ".env.example" ".env"
        Write-Host "Створено .env з .env.example"
    }
} else {
    Write-Host ".env.example не знайдено — пропускаю перейменування." -ForegroundColor Yellow
}

# 6) Опційно — відкрити нове вікно з активованим .venv
if ($OpenShell) {
    $proj = (Resolve-Path ".").Path
    $cmd  = "Set-Location `"$proj`"; . `"$activate`""
    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$cmd
    Write-Host "Відкрив нове вікно PowerShell з активованим .venv."
}

Write-Host "`nГотово. Щоб активувати .venv у поточній сесії пізніше: `n    .\.venv\Scripts\Activate.ps1" -ForegroundColor Green
