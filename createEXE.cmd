@echo off
setlocal

REM Папка, де лежить цей .cmd (із завершаючим \)
set "ROOT=%~dp0"

REM На всякий випадок працюємо з правильною робочою текою
pushd "%ROOT%"

REM Шлях до Python із вашого .venv
set "PY=%ROOT%.venv\Scripts\python.exe"

REM Збірка EXE
"%PY%" -m PyInstaller ^
  --noconsole --onefile ^
  --name MKRschedule_bot ^
  --hidden-import aiosqlite ^
  --icon "%ROOT%bot.ico" ^
  "%ROOT%app.py"

if errorlevel 1 (
  echo [ERROR] PyInstaller завершився з помилкою.
  popd
  exit /b 1
)

REM Цільова тека зі збіркою
set "DIST=%ROOT%dist"

REM Копіюємо help.md (якщо є)
if exist "%ROOT%help.md" (
  copy /Y "%ROOT%help.md" "%DIST%\" >nul
  echo [OK] Copied: help.md -> dist
) else (
  echo [WARN] Not finded help.md у "%ROOT%help.md"
)

REM Копіюємо .env (якщо є)
if exist "%ROOT%.env" (
  copy /Y "%ROOT%.env" "%DIST%\" >nul
  echo [OK] Copied: .env -> dist
) else (
  echo [WARN] Not finded .env у "%ROOT%.env"
)

REM Копіюємо bot.db (якщо є)
if exist "%ROOT%bot.db" (
  copy /Y "%ROOT%bot.db" "%DIST%\" >nul
  echo [OK] Copied: bot.db -> dist
) else (
  echo [WARN] Not finded bot.db у "%ROOT%bot.db"
)

echo [OK] Done: "%DIST%\MKRschedule_bot.exe"
popd
exit /b 0
