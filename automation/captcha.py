import time

from utils.human import human_wait
from utils.progress import fmt_time


def wait_for_captcha_challenge_resolution(page, initial_wait=2.5):
    time.sleep(initial_wait)
    challenge_selectors = [
        'iframe[title*="recaptcha challenge"]',
        'iframe[title*="challenge"]',
        'iframe[src*="bframe"]',
    ]

    challenge_sel = None
    for sel in challenge_selectors:
        try:
            iframe_el = page.locator(sel)
            if iframe_el.count() > 0 and iframe_el.first.is_visible():
                challenge_sel = sel
                break
        except Exception:
            continue

    if not challenge_sel:
        return

    print("\n  ⚠ A CAPTCHA challenge (image puzzle) appeared.")
    print("  Please solve it manually in the browser window - the script will wait for you.")

    waited = 0
    while True:
        time.sleep(2)
        waited += 2
        try:
            iframe_el = page.locator(challenge_sel)
            still_visible = iframe_el.count() > 0 and iframe_el.first.is_visible()
        except Exception:
            still_visible = False
        if not still_visible:
            print(f"  ✓ CAPTCHA challenge resolved after {fmt_time(waited)}. Continuing...")
            return
        if waited % 10 == 0:
            print(f"\r  Still waiting for you to solve the CAPTCHA... ({fmt_time(waited)})", end="", flush=True)


def handle_captcha(page):
    recaptcha_selectors = [
        'iframe[title*="reCAPTCHA"]',
        'iframe[src*="recaptcha"]',
        'iframe[src*="google.com/recaptcha"]',
    ]

    for sel in recaptcha_selectors:
        try:
            frame_loc = page.frame_locator(sel)

            checkbox = frame_loc.locator('.recaptcha-checkbox-border')
            if checkbox.count() == 0:
                checkbox = frame_loc.locator('[role="checkbox"]')
            if checkbox.count() == 0:
                checkbox = frame_loc.locator('.rc-anchor-content')

            if checkbox.count() > 0 and checkbox.first.is_visible():
                print("  CAPTCHA detected, clicking...")
                checkbox.first.scroll_into_view_if_needed()
                human_wait(0.8, 1.8)
                checkbox.first.click()
                human_wait(2, 4)
                wait_for_captcha_challenge_resolution(page)
                return True
        except Exception as e:
            print(f"  (captcha check on '{sel}' failed: {e})")
            continue

    return False