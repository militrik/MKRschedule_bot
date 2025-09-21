#Requires -Version 5.1
<#
.SYNOPSIS
  Збірка самостійного EXE для MKRschedule_bot за допомогою PyInstaller
.DESCRIPTION
  Використовує Python з віртуального середовища .venv\Scripts\python.exe
  та копіює до dist допоміжні файли (help.md, .env, bot.db), якщо вони існують.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Папка, де лежить цей .ps1
$ROOT = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Path $PSCommandPath -Parent }
# На всякий випадок гарантуємо абсолютний шлях
$ROOT = [System.IO.Path]::GetFullPath($ROOT)
Write-Host "[INFO] ROOT: $ROOT"

# Шлях до Python із вашого .venv
$PY = Join-Path -Path $ROOT -ChildPath '.venv\Scripts\python.exe'

# Перехід у робочу теку, як у pushd/popd
Push-Location $ROOT
try {
    if (-not (Test-Path -LiteralPath $PY)) {
        Write-Host "[ERROR] Not found Python in venv: $PY"
        exit 1
    }

    # Збірка EXE через PyInstaller (аналог рядків із ^ у .cmd)
    $pyArgs = @(
        '-m', 'PyInstaller',
        '--noconsole', '--onefile',
        '--name', 'MKRschedule_bot',
        '--hidden-import', 'aiosqlite',
        '--icon', (Join-Path $ROOT 'bot.ico'),
        (Join-Path $ROOT 'app.py')
    )

    & $PY @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] PyInstaller завершився з помилкою. Код: $LASTEXITCODE"
        exit $LASTEXITCODE
    }

    # Цільова тека зі збіркою
    $DIST = Join-Path $ROOT 'dist'

    # Копіюємо help.md (якщо є)
    $src = Join-Path $ROOT 'help.md'
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination $DIST -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Copied: help.md -> dist"
    } else {
        Write-Host "[WARN] Not finded help.md у `"$src`""
    }

    # Копіюємо .env (якщо є)
    $src = Join-Path $ROOT '.env'
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination $DIST -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Copied: .env -> dist"
    } else {
        Write-Host "[WARN] Not finded .env у `"$src`""
    }

    # Копіюємо bot.db (якщо є)
    $src = Join-Path $ROOT 'bot.db'
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination $DIST -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Copied: bot.db -> dist"
    } else {
        Write-Host "[WARN] Not finded bot.db у `"$src`""
    }

    Write-Host "[OK] Done: `"$DIST\MKRschedule_bot.exe`""
    exit 0
}
finally {
    Pop-Location
}
