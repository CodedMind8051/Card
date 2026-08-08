import re
import sys
import json
import time
import random
import shutil
import socket
import zipfile
import argparse
import traceback
from pathlib import Path
from collections import deque
from playwright.sync_api import sync_playwright

from card_render import (
    TEMPLATE_PATH, PHOTO_BOX, TEMPLATES_DIR,
    build_default_layout, detect_face_crop_rect, render_card, load_active_template,
)

IMAGE_DIR = Path("image")
OUTPUT_DIR = Path("output")
CARD_DIR = OUTPUT_DIR / "cards"       # rendered card PNGs
RECORD_DIR = OUTPUT_DIR / "records"   # editable *_data.json sidecars
RETRY_DIR = Path("retry")
COMPLETED_DIR = Path("completed")
TEMP_DIR = Path("temp")
TEMP_SCREENSHOTS = ["03_after_upload_full.png", "03b_after_ai_mode.png", "04_response_full.png"]
LOG_FILE = TEMP_DIR / "run_log.txt"
HISTORY_DIR = Path("history")  # archived template snapshots (*.zip)

# ---- Resilience settings ----
JSON_RETRY_WAIT = 900
BAD_JSON_STREAK_LIMIT = 3
INTERNET_POLL_INTERVAL = 5
INTERNET_CHECK_HOST = "8.8.8.8"
INTERNET_CHECK_PORT = 53

NETWORK_ERROR_PATTERNS = [
    "ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED", "ERR_CONNECTION_",
    "ERR_NAME_NOT_RESOLVED", "ERR_TIMED_OUT", "ERR_ADDRESS_UNREACHABLE",
    "net::ERR", "NS_ERROR_", "getaddrinfo", "Timeout", "ECONNRESET",
]


class BadJSONResponseError(RuntimeError):
    pass


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


def goto_with_retry(page, url, **kwargs):
    while True:
        try:
            page.goto(url, **kwargs)
            return
        except Exception as e:
            if is_network_error(e) or not check_internet():
                print(f"\n  ⚠ Couldn't reach {url}: {short_error(e)}")
                wait_for_internet()
                continue
            raise


PROMPT = (
    "Extract all information from this image and return ONLY a valid JSON object "
    "with these keys: school_name, student_name, father_name, mother_name, class, "
    "section, roll_number, mobile_number, dob, address, student_photo_bbox. "
    "For student_photo_bbox, return a list of 4 decimal numbers between 0.0 and 1.0 "
    "representing [x_ratio, y_ratio, width_ratio, height_ratio] relative to the "
    "total image width and height. Example: [0.15, 0.20, 0.10, 0.15]. "
    "Missing fields=\"\". No extra text. And if information in hindi then convert into english.and if the address is too long just give the important part of the address. Return only the JSON object, nothing else."
)


def human_move(page, x, y, steps=25):
    box = page.viewport_size
    sx = random.randint(100, box["width"] - 100)
    sy = random.randint(100, box["height"] - 100)
    for i in range(steps):
        t = i / steps
        cx = sx + (x - sx) * t + random.uniform(-15, 15)
        cy = sy + (y - sy) * t + random.uniform(-15, 15)
        page.mouse.move(cx, cy)
        time.sleep(random.uniform(0.005, 0.02))
    page.mouse.move(x, y)


def human_click(page, locator):
    box = locator.bounding_box()
    if not box:
        locator.click()
        return
    x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
    y = box["y"] + box["height"] / 2 + random.uniform(-5, 5)
    human_move(page, x, y)
    time.sleep(random.uniform(0.2, 0.6))
    page.mouse.click(x, y)


def human_wait(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))


def extract_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def parse_bbox_ratios(bbox_data):
    if not bbox_data:
        return None
    if isinstance(bbox_data, list) and len(bbox_data) == 4:
        try:
            ratios = [float(v) for v in bbox_data]
            if all(0.0 <= r <= 1.0 for r in ratios):
                return ratios
        except (ValueError, TypeError):
            pass
    if isinstance(bbox_data, str):
        nums = re.findall(r'\d+\.\d+|\d+', bbox_data)
        if len(nums) >= 4:
            ratios = [float(n) for n in nums[:4]]
            if all(0.0 <= r <= 1.0 for r in ratios):
                return ratios
    return None


def find_ask_box_all_frames(page):
    ask_selectors = [
        'textarea[placeholder*="Ask"]',
        'input[placeholder*="Ask"]',
        'textarea[aria-label*="Ask"]',
        'div[contenteditable="true"][aria-label*="Ask"]',
        'div[contenteditable="true"]',
    ]
    frames_to_check = [page.main_frame] + page.frames
    for frame in frames_to_check:
        for sel in ask_selectors:
            try:
                loc = frame.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first, frame
            except Exception:
                pass
    return None, None


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


def find_ai_mode_button(page):
    ai_mode_selectors = [
        'div[role="tab"]:has-text("AI Mode")',
        'a[role="tab"]:has-text("AI Mode")',
        'button:has-text("AI Mode")',
        'a:has-text("AI Mode")',
        '[aria-label="AI Mode"]',
        '[aria-label*="AI Mode"]',
        'text=AI Mode',
    ]
    frames_to_check = [page.main_frame] + page.frames
    for frame in frames_to_check:
        for sel in ai_mode_selectors:
            try:
                loc = frame.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                pass
    return None


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


def sidecar_path_for(image_name: str) -> Path:
    return RECORD_DIR / f"{Path(image_name).stem}_data.json"


def save_sidecar(image_name: str, data: dict, layout: dict, source_file: str, output_file: str,
                 template_file: str = TEMPLATE_PATH):
    """Write the editable record that the browser editor (--edit) reads/writes."""
    record = {
        "image_name": image_name,
        "source_file": source_file,    # path to the ORIGINAL uploaded image (relative to project root)
        "output_file": output_file,    # path to the rendered card PNG
        "template_file": template_file,  # which template the card was rendered on
        "data": data,
        "layout": layout,
    }
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = sidecar_path_for(image_name)
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def fill_template(data: dict, image_name: str, source_image_path: Path, ai_ratios=None, enhance_photo=False):
    """Render the card for the first time and save an editable sidecar next to it.

    Uses the active template (if one was designed with `--new`), otherwise
    falls back to the original hard-coded template.png layout."""
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CARD_DIR / f"{Path(image_name).stem}_filled.png"

    active = load_active_template()
    if active:
        layout = dict(active["layout"])
        template_path = str(Path(active["template_file"]))
    else:
        layout = build_default_layout()
        template_path = TEMPLATE_PATH

    if not Path(template_path).exists():
        print(f"  Template not found at {template_path}, skipping fill step.")
        return None

    crop_rect = detect_face_crop_rect(source_image_path, ai_ratios=ai_ratios)
    if crop_rect:
        rotation = crop_rect.get("rotation") or 0
        layout["photo"]["crop"] = {k: crop_rect[k] for k in ("x", "y", "w", "h")}
        layout["photo"]["rotation"] = rotation
    else:
        layout["photo"]["crop"] = None

    im = render_card(data, layout, template_path=template_path, photo_source_path=str(source_image_path), enhance_photo=enhance_photo)
    im.save(output_path)
    print(f"  Filled card saved: {output_path}")
    if crop_rect is None:
        print("  No face detected in source image - photo left blank (adjust the crop in --edit).")

    # Source file will live under completed/ once the pass finishes moving it;
    # we record that expected final location so the editor can find it later.
    source_rel = str(Path("completed") / image_name)
    save_sidecar(image_name, data, layout, source_rel, str(output_path), template_file=template_path)
    return output_path


def process_single_image(page, image_path: Path, enhance_photo=False):
    image_path_str = str(image_path.resolve())
    print(f"\n--- Processing: {image_path.name} ---")

    print("  Resetting page state...")
    try:
        if page.url != "about:blank":
            goto_with_retry(page, "about:blank", wait_until="domcontentloaded", timeout=10000)
    except Exception:
        pass
    human_wait(0.5, 1)
    print("  Opening Google Images...")
    goto_with_retry(page, "https://images.google.com", wait_until="networkidle")
    human_wait(3, 5)

    for text in ["Accept all", "I agree", "Reject all"]:
        try:
            btn = page.get_by_role("button", name=text)
            if btn.count() > 0 and btn.first.is_visible():
                human_click(page, btn.first)
                human_wait(1, 2)
                break
        except Exception:
            pass

    handle_captcha(page)

    print("  Looking for Lens button...")
    lens = None
    selectors = [
        'div[aria-label="Search by image"]',
        'button[aria-label*="Lens"]',
        'button[aria-label*="Search by image"]',
        '[aria-label*="Lens"]',
        '[aria-label*="Search by image"]',
        'div[role="button"][aria-label*="Lens"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                lens = loc.first
                break
        except Exception:
            pass

    if lens is None:
        raise RuntimeError("Lens button not found")

    print("  Clicking Lens (human-style)...")
    human_click(page, lens)
    human_wait(1.5, 3)

    file_inputs = page.locator("input[type=file]")
    if file_inputs.count() == 0:
        raise RuntimeError("No upload input found")

    print(f"  Uploading: {image_path.name}")
    file_inputs.first.set_input_files(image_path_str)

    print("  Waiting for Lens results to load...")
    page.wait_for_timeout(8000)
    handle_captcha(page)
    page.screenshot(path=str(TEMP_DIR / "03_after_upload_full.png"), full_page=True)

    print("  Looking for 'AI Mode' tab...")
    ai_mode = find_ai_mode_button(page)

    if ai_mode is None:
        page.screenshot(path=str(TEMP_DIR / "ai_mode_not_found.png"), full_page=True)
        print(f"  Screenshot saved: {TEMP_DIR / 'ai_mode_not_found.png'}")
        raise RuntimeError("Could not find 'AI Mode' tab")

    print("  Clicking AI Mode (human-style)...")
    human_click(page, ai_mode)
    human_wait(1.5, 3)
    page.screenshot(path=str(TEMP_DIR / "03b_after_ai_mode.png"), full_page=True)

    print("  Looking for 'Ask anything' input...")
    ask_box, ask_frame = find_ask_box_all_frames(page)

    if ask_box is None:
        page.screenshot(path=str(TEMP_DIR / "ask_box_not_found.png"), full_page=True)
        print(f"  Screenshot saved: {TEMP_DIR / 'ask_box_not_found.png'}")
        raise RuntimeError("Could not find 'Ask anything' box")

    print("  Clicking ask box and typing prompt...")
    ask_box.scroll_into_view_if_needed()
    human_wait(0.5, 1)
    ask_box.click()
    human_wait(0.5, 1)

    page.wait_for_timeout(1000)
    ask_box.fill(PROMPT)
    human_wait(0.5, 1)
    ask_box.press("Enter")

    print("  Waiting for response...")
    time.sleep(2)
    body = wait_for_response_stable(page, max_wait=70, check_interval=1.0, stable_checks=3)
    page.screenshot(path=str(TEMP_DIR / "04_response_full.png"), full_page=True)

    data = extract_json(body)
    if data is None:
        log_detail(f"[{image_path.name}] Could not parse JSON. Raw page text:\n{body}")
        raise BadJSONResponseError(
            "Response wasn't valid JSON (full text saved to temp/run_log.txt)"
        )

    log_detail(f"[{image_path.name}] Extracted data: {json.dumps(data, ensure_ascii=False)}")
    summary_bits = [
        data.get("student_name") or "(no name)",
        f"Class {data.get('class')}" if data.get("class") else None,
        f"Sec {data.get('section')}" if data.get("section") else None,
        f"Roll {data.get('roll_number')}" if data.get("roll_number") else None,
    ]
    print("  ✓ Extracted: " + " | ".join(b for b in summary_bits if b))

    ai_ratios = parse_bbox_ratios(data.get("student_photo_bbox"))
    fill_template(data, image_path.name, image_path, ai_ratios=ai_ratios, enhance_photo=enhance_photo)
    return data


def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def show_progress(done, total, failed, label="Processing", start_time=None):
    pct = (done / total * 100) if total else 0
    bar_len = 30
    filled = int(bar_len * done / total) if total else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    elapsed = time.time() - start_time if start_time else 0
    eta = (elapsed / done * (total - done)) if done > 0 else 0
    print(f"\r  {label}: |{bar}| {pct:.1f}%  {done}/{total}  {fmt_time(elapsed)} elapsed  ~{fmt_time(eta)} left  {failed} retry", end="", flush=True)


def cleanup_temp_screenshots():
    for s in TEMP_SCREENSHOTS:
        p = TEMP_DIR / s
        if p.exists():
            p.unlink()


def process_image_pass(page, images, label, pass_start, enhance_photo=False):
    total = len(images)
    done = 0
    failed = 0
    retry_images = []
    queue = deque(images)

    consecutive_bad_json = 0
    bad_json_batch = []

    def flush_bad_json_batch_as_failures():
        nonlocal failed, done
        for p in bad_json_batch:
            failed += 1
            done += 1
            log_detail(f"[{p.name}] Bad-JSON streak broke without hitting {BAD_JSON_STREAK_LIMIT} in a row; treating as failed.")
            print(f"\n  ✗ Failed: {p.name} — bad/non-JSON response")
            retry_path = RETRY_DIR / p.name
            if p.exists():
                shutil.copy2(str(p), str(retry_path))
                retry_images.append(retry_path)
        bad_json_batch.clear()

    while queue:
        img_path = queue.popleft()
        img_start = time.time()
        finished = False
        while not finished:
            try:
                process_single_image(page, img_path, enhance_photo=enhance_photo)
                cleanup_temp_screenshots()
                shutil.move(str(img_path), str(COMPLETED_DIR / img_path.name))
                done += 1
                print(f"  ⏱ Done in {time.time() - img_start:.1f}s")
                finished = True
                if bad_json_batch:
                    consecutive_bad_json = 0
                    flush_bad_json_batch_as_failures()

            except BadJSONResponseError as e:
                consecutive_bad_json += 1
                bad_json_batch.append(img_path)
                cleanup_temp_screenshots()
                print(f"\n  ⚠ Non-JSON response for {img_path.name} "
                      f"({consecutive_bad_json}/{BAD_JSON_STREAK_LIMIT} in a row): {short_error(e)}")

                if consecutive_bad_json >= BAD_JSON_STREAK_LIMIT:
                    print(f"  {BAD_JSON_STREAK_LIMIT} bad responses in a row — "
                          f"waiting {fmt_time(JSON_RETRY_WAIT)} before trying them again...")
                    time.sleep(JSON_RETRY_WAIT)
                    consecutive_bad_json = 0
                    for p in reversed(bad_json_batch):
                        queue.appendleft(p)
                    bad_json_batch.clear()
                    print("  Resuming...")

                finished = True

            except Exception as e:
                if is_network_error(e) or not check_internet():
                    print(f"\n  ⚠ Network issue while processing {img_path.name}: {short_error(e)}")
                    log_detail(f"[{img_path.name}] NETWORK ERROR:\n{traceback.format_exc()}")
                    cleanup_temp_screenshots()
                    wait_for_internet()
                    print(f"  Resuming {img_path.name}...")
                    continue

                if bad_json_batch:
                    consecutive_bad_json = 0
                    flush_bad_json_batch_as_failures()

                failed += 1
                log_detail(f"[{img_path.name}] ERROR:\n{traceback.format_exc()}")
                print(f"\n  ✗ Failed: {img_path.name} — {short_error(e)}")
                print(f"    (full details in {LOG_FILE})")
                cleanup_temp_screenshots()
                retry_path = RETRY_DIR / img_path.name
                shutil.copy2(str(img_path), str(retry_path))
                retry_images.append(retry_path)
                done += 1
                finished = True

        if done == 1:
            per_image = time.time() - pass_start
            print(f"  → First image took {fmt_time(per_image)}. Estimated total: ~{fmt_time(per_image * total)}\n")
        show_progress(min(done, total), total, failed, label=label, start_time=pass_start)

    if bad_json_batch:
        flush_bad_json_batch_as_failures()

    print()
    return done, failed, retry_images


def scrape_main(enhance_photo=False):
    OUTPUT_DIR.mkdir(exist_ok=True)
    CARD_DIR.mkdir(exist_ok=True)
    RECORD_DIR.mkdir(exist_ok=True)
    RETRY_DIR.mkdir(exist_ok=True)
    COMPLETED_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    all_images = sorted(IMAGE_DIR.glob("*.[jJ][pP][gG]")) + sorted(IMAGE_DIR.glob("*.[jJ][pP][eE][gG]")) + sorted(IMAGE_DIR.glob("*.[pP][nN][gG]"))
    if not all_images:
        print(f"No images found in {IMAGE_DIR}")
        return

    with sync_playwright() as p:
        context = p.firefox.launch_persistent_context(
            user_data_dir="/home/coded_mind__/.mozilla/firefox/e0uaw6ea.default-esr",
            headless=False,
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        page = context.pages[0] if context.pages else context.new_page()

        total = len(all_images)
        print(f"Found {total} image(s) to process. Estimating time after the first one...\n")
        pass_start = time.time()

        done, failed, retry_images = process_image_pass(page, all_images, "Processing", pass_start, enhance_photo=enhance_photo)

        if retry_images:
            print(f"\n=== Restarting browser for retry of {len(retry_images)} image(s) ===")
            context.close()
            context = p.firefox.launch_persistent_context(
                user_data_dir="/home/coded_mind__/.mozilla/firefox/e0uaw6ea.default-esr",
                headless=False,
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            page = context.pages[0] if context.pages else context.new_page()

            retry_pass_start = time.time()
            retry_done, retry_failed, still_failed = process_image_pass(page, retry_images, "Retry", retry_pass_start, enhance_photo=enhance_photo)

            if still_failed:
                print(f"\n=== {len(still_failed)} image(s) still failed after retry ===")
                for f in still_failed:
                    print(f"  {f.name}")
                print("These remain in the retry folder.")
            else:
                print("\n=== All retries succeeded! ===")
        else:
            print("\n=== All images processed successfully! ===")

        input("\nPress ENTER to close browser...")
        context.close()


def archive_current_template(name: str) -> bool:
    """Mark the current template design as 'old' by snapshotting everything
    under templates/ (active_template.json + the *_template.png / *_design.json
    files) into a zip in history/. Returns True on success."""
    if not TEMPLATES_DIR.exists() or not any(TEMPLATES_DIR.iterdir()):
        print(f"  Nothing to archive — {TEMPLATES_DIR} is empty.")
        return False
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    target = HISTORY_DIR / f"{name}.zip"
    if target.exists():
        print(f"  {target} already exists. Pick a different name or delete it first.")
        return False
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(TEMPLATES_DIR.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(TEMPLATES_DIR)))
    print(f"  ✓ Archived current template -> {target}")
    return True


def restore_archive(name: str) -> bool:
    """Use an archived template: restore history/<name>.zip back into
    templates/ so it becomes the current working design again."""
    target = HISTORY_DIR / f"{name}.zip"
    if not target.exists():
        print(f"  ✗ No archived template named '{name}' (looked for {target}).")
        return False
    if TEMPLATES_DIR.exists():
        for p in TEMPLATES_DIR.iterdir():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target) as zf:
        zf.extractall(str(TEMPLATES_DIR))
    print(f"  ✓ Restored '{name}' as the current working template.")
    return True


def list_history() -> list[str]:
    if not HISTORY_DIR.exists():
        return []
    return sorted(p.name for p in HISTORY_DIR.glob("*.zip"))


def main():
    parser = argparse.ArgumentParser(description="Scrape + fill student ID cards, or edit them in the browser.")
    parser.add_argument("--edit", action="store_true", help="Launch the browser editor instead of scraping.")
    parser.add_argument("--new", action="store_true", help="Design a new template in the browser (asks for a template image path).")
    parser.add_argument("--template", help="Template image or folder for --new (otherwise you are asked interactively).")
    parser.add_argument("--port", type=int, default=5000, help="Port for the --edit / --new web server (default 5000).")
    parser.add_argument("--image-enhance", action="store_true", default=False,
                        help="Enhance the student photo (upscale, denoise, contrast/colour/sharpening) when rendering. "
                             "Off by default — the original photo is used as-is.")
    parser.add_argument("--mark-old", metavar="NAME",
                        help="Archive the current template design as a zip in history/ under NAME, then stop.")
    parser.add_argument("--given-name", metavar="NAME",
                        help="Restore the archived template NAME from history/ so it becomes the current working design.")
    parser.add_argument("--current", action="store_true", default=False,
                        help="Explicitly keep using the current working design (default behaviour; cancels --given-name).")
    args = parser.parse_args()

    if args.mark_old and args.given_name:
        print("Choose either --mark-old or --given-name, not both.")
        return
    if args.mark_old and args.current:
        print("--current doesn't make sense with --mark-old; continuing to archive anyway.")

    if args.mark_old:
        if archive_current_template(args.mark_old):
            print(f"\n  Archived designs in history/: {list_history()}")
        return

    if args.given_name and not args.current:
        if not restore_archive(args.given_name):
            return

    if args.edit and args.new:
        print("Choose one of --edit or --new, not both.")
        return
    if args.edit:
        from editor_app import run_editor
        run_editor(port=args.port)
    elif args.new:
        from designer import main as design_main
        import sys
        sys.argv = ["designer"]
        if args.template:
            sys.argv += [args.template]
        if args.port != 5000:
            sys.argv += ["--port", str(args.port)]
        design_main()
    else:
        scrape_main(enhance_photo=args.image_enhance)


if __name__ == "__main__":
    main()