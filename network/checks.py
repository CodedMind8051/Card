import socket
import time

from constants.resilience import (
    INTERNET_POLL_INTERVAL,
    INTERNET_CHECK_HOST,
    INTERNET_CHECK_PORT,
    NETWORK_ERROR_PATTERNS,
)
from utils.progress import fmt_time


def is_network_error(e: Exception) -> bool:
    s = str(e)
    return any(p.lower() in s.lower() for p in NETWORK_ERROR_PATTERNS)


def check_internet(timeout: float = 3.0) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((INTERNET_CHECK_HOST, INTERNET_CHECK_PORT))
        s.close()
        return True
    except OSError:
        return False


def wait_for_internet():
    if check_internet():
        return
    print("\n  ⚠ Internet connection lost. Waiting for it to come back...")
    waited = 0
    while not check_internet():
        time.sleep(INTERNET_POLL_INTERVAL)
        waited += INTERNET_POLL_INTERVAL
        print(f"\r  Still offline... ({fmt_time(waited)} so far)", end="", flush=True)
    print(f"\n  ✓ Internet reconnected after {fmt_time(waited)}. Resuming...")