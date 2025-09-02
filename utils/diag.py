from __future__ import annotations
import os
from pathlib import Path
from datetime import datetime
from typing import Union

# Увімкнути логування HTTP/HTML:
#   Windows (cmd):   set DEBUG_HTTP=1
#   PowerShell:      $env:DEBUG_HTTP="1"
#   Linux/macOS:     export DEBUG_HTTP=1
ENABLED = 0 #os.getenv("DEBUG_HTTP", "0") == "1"

def log(msg: str) -> None:
    """Лаконічний друк у консоль (лише коли ввімкнено)."""
    if ENABLED:
        print(f"[DEBUG] {msg}")

def write_blob(name: str, content: Union[str, bytes]) -> Path | None:
    """
    Зберегти контент у logs/<timestamp>_<name>.html.
    Повертає шлях до файлу або None, якщо логування вимкнене.
    """
    if not ENABLED:
        return None

    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_"))
    suffix = ".html"
    p = logs_dir / f"{ts}_{safe}{suffix}"

    if isinstance(content, (bytes, bytearray)):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")

    print(f"[DEBUG] saved: {p}")
    return p
