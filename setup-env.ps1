# install.ps1  (ASCII-only)
# [UKR] Налаштування користувача: змініть значення нижче за потреби.
$Config = @{
    Recreate     = $false   # [UKR] Видалити існуючий .venv та створити заново
    OverwriteEnv = $false   # [UKR] Дозволити перезапис файлу .env
    OpenShell    = $true    # [UKR] ВІДКРИТИ НОВЕ ВІКНО З АКТИВОВАНИМ .venv ОДРАЗУ
}

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Write-Host "=== MKR setup: venv + requirements + .env ===`n"

# [UKR] Дозволяємо виконання скрипта лише в межах поточного процесу
try { Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force } catch {}

# [UKR] Пошук встановленого Python: спочатку 'py', потім 'python'/'python3'
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
    throw "Python not found. Install Python 3.11+ or add it to PATH."
}

$pyParts = Resolve-Python
$pyCmd   = $pyParts[0]
$pyArgs  = $pyParts[1..($pyParts.Length-1)]

# [UKR] Створення або перестворення .venv
$venvPath = Join-Path (Get-Location) ".venv"
if ($Config.Recreate -and (Test-Path $venvPath)) {
    Write-Host "Removing existing .venv..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvPath
}
if (-not (Test-Path $venvPath)) {
    Write-Host "Creating .venv..."
    & $pyCmd @pyArgs -m venv ".venv"
} else {
    Write-Host ".venv already exists - reusing it." -ForegroundColor Yellow
}

# [UKR] Активація віртуального середовища в межах цього скрипта
$activate = Join-Path $venvPath "Scripts\Activate.ps1"
if (-not (Test-Path $activate)) { throw "Activation script not found: $activate" }
. "$activate"
Write-Host "Virtual environment activated (current script scope)."

# [UKR] Оновлення pip та встановлення залежностей
$venvPython = Join-Path $venvPath "Scripts\python.exe"
Write-Host "Upgrading pip..."
& "$venvPython" -m pip install --upgrade pip

if (Test-Path "requirements.txt") {
    Write-Host "Installing dependencies from requirements.txt..."
    & "$venvPython" -m pip install -r "requirements.txt"
} else {
    Write-Host "requirements.txt not found - skipping dependencies installation." -ForegroundColor Yellow
}

# [UKR] Перейменування .env.example -> .env (з опцією перезапису)
if (Test-Path ".env.example") {
    if (Test-Path ".env") {
        if ($Config.OverwriteEnv) {
            Write-Host "Overwriting existing .env..." -ForegroundColor Yellow
            Remove-Item ".env" -Force
            Rename-Item ".env.example" ".env"
        } else {
            Write-Host ".env already exists - not overwriting (set OverwriteEnv = `$true to overwrite)." -ForegroundColor Yellow
        }
    } else {
        Rename-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example."
    }
} else {
    Write-Host ".env.example not found - skipping rename." -ForegroundColor Yellow
}

# [UKR] Одразу відкриваємо нове вікно з активованим .venv (за замовчуванням $true)
if ($Config.OpenShell) {
    $proj = (Resolve-Path ".").Path
    $cmd  = "Set-Location `"$proj`"; . `"$activate`""
    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$cmd
    Write-Host "Opened a new PowerShell window with the virtual environment activated."
} else {
    # [UKR] Альтернатива: можна дотсурсити цей файл, щоб активувати у поточній сесії:
    # . .\install.ps1
    Write-Host "Note: To keep activation in the current window, dot-source the script: . .\install.ps1" -ForegroundColor Yellow
}

Write-Host "`nDone." -ForegroundColor Green
