import time

from network.checks import check_internet, wait_for_internet


def wait_for_response_stable(page, max_wait=30, check_interval=1.0, stable_checks=3):
    start = time.time()
    last_text = ""
    stable_count = 0
    while time.time() - start < max_wait:
        if not check_internet():
            wait_for_internet()
            start = time.time()
            last_text = ""
            stable_count = 0
            continue
        try:
            current_text = page.locator("body").inner_text()
        except Exception:
            current_text = ""
        if current_text == last_text and current_text.strip():
            stable_count += 1
            if stable_count >= stable_checks:
                return current_text
        else:
            stable_count = 0
        last_text = current_text
        time.sleep(check_interval)
    return last_text