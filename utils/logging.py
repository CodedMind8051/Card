import time

from constants.paths import TEMP_DIR, LOG_FILE
from constants.screenshots import TEMP_SCREENSHOTS


def log_detail(message: str):
    try:
        TEMP_DIR.mkdir(exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def short_error(e: Exception, max_len: int = 140) -> str:
    first_line = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return first_line if len(first_line) <= max_len else first_line[:max_len] + "…"


def cleanup_temp_screenshots():
    for s in TEMP_SCREENSHOTS:
        p = TEMP_DIR / s
        if p.exists():
            p.unlink()