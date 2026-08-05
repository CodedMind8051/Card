import json
import time
from pathlib import Path

from constants.paths import TEMP_DIR
from constants.photo import PHOTO_BOX
from constants.prompts import PROMPT
from exceptions import BadJSONResponseError
from utils.logging import log_detail
from utils.parsing import extract_json, parse_bbox_ratios
from utils.human import human_wait, human_click
from network.navigation import goto_with_retry
from automation.captcha import handle_captcha
from automation.ask_box import find_ask_box_all_frames
from automation.ai_mode import find_ai_mode_button
from automation.response import wait_for_response_stable
from image_processing.detection import extract_student_photo_smart
from image_processing.template import fill_template


def process_single_image(page, image_path: Path):
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

    print("  Extracting student photo...")
    target_aspect = PHOTO_BOX[2] / PHOTO_BOX[3]

    ai_ratios = parse_bbox_ratios(data.get("student_photo_bbox"))

    student_photo = extract_student_photo_smart(image_path, target_aspect, ai_ratios=ai_ratios)

    if student_photo is None:
        print("  AI region failed or no face found. Falling back to full-image scan...")
        student_photo = extract_student_photo_smart(image_path, target_aspect, ai_ratios=None)

    if student_photo is not None:
        student_photo.save(TEMP_DIR / "student_photo_debug.png")
        print(f"  Photo successfully cropped and oriented ({student_photo.size[0]}x{student_photo.size[1]}px).")
    else:
        print("  No face detected in source image.")

    fill_template(data, image_path.name, student_photo)
    return data