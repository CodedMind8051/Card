import re
import json
import time
import random
import shutil
import traceback
import cv2
import numpy as np
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw, ImageFont

IMAGE_DIR = Path("image")
OUTPUT_DIR = Path("output")
RETRY_DIR = Path("retry")
COMPLETED_DIR = Path("completed")
TEMP_DIR = Path("temp")
TEMP_SCREENSHOTS = ["03_after_upload_full.png", "03b_after_ai_mode.png", "04_response_full.png"]
LOG_FILE = TEMP_DIR / "run_log.txt"


def log_detail(message: str):
    """Write full details (extracted JSON, raw AI text, full error text) to a
    log file instead of cluttering the console. Console stays short/clean."""
    try:
        TEMP_DIR.mkdir(exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass  # logging must never break the actual run


def short_error(e: Exception, max_len: int = 140) -> str:
    """First line of an exception message, truncated - Playwright errors in
    particular can include multi-line 'Call log:' dumps that are useful in
    the log file but too noisy for the console."""
    first_line = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return first_line if len(first_line) <= max_len else first_line[:max_len] + "…"

PROMPT = (
    "Extract all information from this image and return ONLY a valid JSON object "
    "with these keys: school_name, student_name, father_name, mother_name, class, "
    "section, roll_number, mobile_number, dob, address, student_photo_bbox. "
    "For student_photo_bbox, return a list of 4 decimal numbers between 0.0 and 1.0 "
    "representing [x_ratio, y_ratio, width_ratio, height_ratio] relative to the "
    "total image width and height. Example: [0.15, 0.20, 0.10, 0.15]. "
    "Missing fields=\"\". No extra text."
)

TEMPLATE_PATH = "template.png"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

VALUE_COLOR = (30, 40, 210)
ACCENT_COLOR = (237, 0, 0)

FIELD_FONT_SIZE = 34
NAME_FONT_SIZE = 80
SECTION_FONT_SIZE = 42

FIELD_POSITIONS = {
    "father_name":   (450, 978),
    "mother_name":   (450, 1030),
    "dob":           (450, 1083),
    "class":         (450, 1135),
    "roll_number":   (450, 1188),
    "mobile_number": (450, 1240),
    "address":       (450, 1278),
}

NAME_CENTER = (530, 890)
SECTION_POSITION = (130, 600)

# ---- Student photo placement on the OUTPUT TEMPLATE ----
PHOTO_BOX = (394, 435, 300, 360)

# How rounded the pasted photo's corners should be, in pixels.
PHOTO_CORNER_RADIUS = 14

# ---- TIGHTER Face-detection crop padding (relative to detected FACE) ----
PHOTO_PAD_SIDE = 0.35
PHOTO_PAD_TOP = 0.45
PHOTO_PAD_BOTTOM = 0.65

_FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_ROTATIONS = [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE]


ROTATION_CONFIDENCE_MARGIN = 1.4


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


def handle_captcha(page):
    """
    Check for a reCAPTCHA checkbox iframe and click it if present.

    Uses page.frame_locator() instead of locator().content_frame() — the
    latter is a property in Playwright's Python API, and calling it with
    () raises a TypeError that was previously being silently swallowed by
    a bare `except: pass`, making CAPTCHA handling fail invisibly. This
    version fails loudly (prints a message) instead of hiding the problem.
    """
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
                checkbox.first.click()  # frame_locator handles coordinate translation correctly
                human_wait(2, 4)
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


def extract_student_photo_smart(image_path: Path, target_aspect: float, ai_ratios=None):
    """
    PERFECT HYBRID EXTRACTION:
    1. Uses AI ratios to find the Region of Interest (ROI).
    2. Runs face detection ONLY inside the ROI to find the exact face and correct rotation.
    3. Applies tight padding to the FACE (not the vast AI box).
    4. Relies purely on the 4-way rotation loop for orientation, preventing random flips.
    """
    img = cv2.imread(str(image_path))
    if img is None:
        return None
        
    h_img, w_img = img.shape[:2]
    
   
    if ai_ratios:
        x = int(ai_ratios[0] * w_img)
        y = int(ai_ratios[1] * h_img)
        w = int(ai_ratios[2] * w_img)
        h = int(ai_ratios[3] * h_img)
   
        x, y = max(0, x), max(0, y)
        w, h = max(1, min(w, w_img - x)), max(1, min(h, h_img - y))
        roi = img[y:y+h, x:x+w]
    else:
        roi = img
        
    results_by_code = {}  
    for code in _ROTATIONS:
        rot = cv2.rotate(roi, code) if code is not None else roi
        gray = cv2.cvtColor(rot, cv2.COLOR_BGR2GRAY)
        faces = _FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        if len(faces) > 0:
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            results_by_code[code] = (fw * fh, (fx, fy, fw, fh), rot)

    if not results_by_code:
        return None  

    baseline_area = results_by_code[None][0] if None in results_by_code else 0
    chosen_code = None if None in results_by_code else max(results_by_code, key=lambda c: results_by_code[c][0])

    for code, (area, box, rot) in results_by_code.items():
        if code is None:
            continue
        if area > baseline_area * ROTATION_CONFIDENCE_MARGIN and area > results_by_code[chosen_code][0]:
            chosen_code = code

    best_area, best_box, best_rot_roi = results_by_code[chosen_code]
    fx, fy, fw, fh = best_box
    
    pad_side = fw * PHOTO_PAD_SIDE
    pad_top = fh * PHOTO_PAD_TOP
    pad_bottom = fh * PHOTO_PAD_BOTTOM
    
    crop_h = fh + pad_top + pad_bottom
    crop_w = crop_h * target_aspect if target_aspect else fw + 2 * pad_side
    
    face_cx = fx + fw / 2.0
    x0 = face_cx - crop_w / 2.0
    y0 = fy - pad_top
    x1 = x0 + crop_w
    y1 = y0 + crop_h
    
    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = min(best_rot_roi.shape[1], int(x1))
    y1 = min(best_rot_roi.shape[0], int(y1))
    
    if x1 - x0 < crop_w: x0 = max(0, x1 - int(crop_w))
    if y1 - y0 < crop_h: y0 = max(0, y1 - int(crop_h))
    
    crop = best_rot_roi[y0:y1, x0:x1]
    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    pil_crop = Image.fromarray(crop_rgb)
        
    return pil_crop


def enhance_photo(img: Image.Image) -> Image.Image:
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    h, w = arr.shape[:2]
    if max(h, w) < 700:
        scale = 700 / max(h, w)
        arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    arr = cv2.fastNlMeansDenoisingColored(arr, None, h=7, hColor=7, templateWindowSize=7, searchWindowSize=21)
    lab = cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    arr = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(arr, (0, 0), sigmaX=2)
    arr = cv2.addWeighted(arr, 1.5, blurred, -0.5, 0)
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def fit_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale + 0.5), int(src_h * scale + 0.5)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def add_rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255
    )
    img.putalpha(mask)
    return img


def draw_fitted_text(draw, text, x, y, max_width, max_height, font_path, font_size, color):
    lines = text.split("\n")
    while font_size > 10:
        font = ImageFont.truetype(font_path, font_size)
        bbox = font.getbbox("Ay")
        line_height = bbox[3] - bbox[1] + 4
        wrapped = []
        for line in lines:
            words = line.split()
            current = ""
            for word in words:
                test = (current + " " + word).strip()
                test_bbox = font.getbbox(test)
                if test_bbox[2] - test_bbox[0] <= max_width:
                    current = test
                else:
                    if current:
                        wrapped.append(current)
                    current = word
            if current:
                wrapped.append(current)
        total_height = len(wrapped) * line_height
        if total_height <= max_height:
            for i, wline in enumerate(wrapped):
                draw.text((x, y + i * line_height), wline, font=font, fill=color, anchor="la")
            return
        font_size -= 2


def fill_template(data: dict, image_name: str, student_photo: Image.Image = None):
    output_path = OUTPUT_DIR / f"{Path(image_name).stem}_filled.png"
    if not Path(TEMPLATE_PATH).exists():
        print(f"  Template not found at {TEMPLATE_PATH}, skipping fill step.")
        return None
    im = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(im)

    if student_photo is not None:
        px, py, pw, ph = PHOTO_BOX
        enhanced_photo = enhance_photo(student_photo)
        fitted_photo = fit_cover(enhanced_photo, pw, ph)
        rounded_photo = add_rounded_corners(fitted_photo, radius=PHOTO_CORNER_RADIUS)
        im.paste(rounded_photo, (px, py), rounded_photo)
        print(f"  Student photo enhanced and pasted at {PHOTO_BOX} (rounded corners r={PHOTO_CORNER_RADIUS})")
    else:
        print("  No student photo detected - skipping photo paste.")

    field_font = ImageFont.truetype(FONT_BOLD, FIELD_FONT_SIZE)
    name_font = ImageFont.truetype(FONT_BOLD, NAME_FONT_SIZE)
    section_font = ImageFont.truetype(FONT_BOLD, SECTION_FONT_SIZE)
    student_name = str(data.get("student_name", "") or "").upper()
    if student_name:
        draw.text(NAME_CENTER, student_name, font=name_font, fill=ACCENT_COLOR, anchor="mm")

    section = str(data.get("section", "") or "").strip()
    if section:
        section_text = f"Sec- {section}"
        draw.text(SECTION_POSITION, section_text, font=section_font, fill=ACCENT_COLOR, anchor="lm")

    for key, (x, y) in FIELD_POSITIONS.items():
        value = str(data.get(key, "") or "")
        if key == "class":
            if not value:
                continue
            draw.text((x, y), value, font=field_font, fill=VALUE_COLOR, anchor="lm")
        else:
            if not value:
                continue
            if key == "address":
                avail_width = im.width - x - 30
                avail_height = im.height - y - 20
                draw_fitted_text(draw, value, x, y, avail_width, avail_height, FONT_BOLD, FIELD_FONT_SIZE, VALUE_COLOR)
            else:
                draw.text((x, y), value, font=field_font, fill=VALUE_COLOR, anchor="lm")
    im.save(output_path)
    print(f"  Filled card saved: {output_path}")
    return output_path


def process_single_image(page, image_path: Path):
    image_path_str = str(image_path.resolve())
    print(f"\n--- Processing: {image_path.name} ---")

    print("  Resetting page state...")
    try:
        if page.url != "about:blank":
            page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
    except Exception:
        pass
    human_wait(0.5, 1)
    print("  Opening Google Images...")
    page.goto("https://images.google.com", wait_until="networkidle")
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
        raise RuntimeError("Could not parse JSON from AI response (full text saved to temp/run_log.txt)")

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
    remaining = total - done - failed
    elapsed = time.time() - start_time if start_time else 0
    eta = (elapsed / done * (total - done)) if done > 0 else 0
    print(f"\r  {label}: |{bar}| {pct:.1f}%  {done}/{total}  {fmt_time(elapsed)} elapsed  ~{fmt_time(eta)} left  {failed} retry", end="", flush=True)


def cleanup_temp_screenshots():
    for s in TEMP_SCREENSHOTS:
        p = TEMP_DIR / s
        if p.exists():
            p.unlink()


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
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
        retry_images = []
        done = 0
        failed = 0
        for img_path in all_images:
            img_start = time.time()
            try:
                process_single_image(page, img_path)
                cleanup_temp_screenshots()
                shutil.move(str(img_path), str(COMPLETED_DIR / img_path.name))
                done += 1
                print(f"  ⏱ Done in {time.time() - img_start:.1f}s")
            except Exception as e:
                failed += 1
                log_detail(f"[{img_path.name}] ERROR:\n{traceback.format_exc()}")
                print(f"\n  ✗ Failed: {img_path.name} — {short_error(e)}")
                print(f"    (full details in {LOG_FILE})")
                cleanup_temp_screenshots()
                retry_path = RETRY_DIR / img_path.name
                shutil.copy2(str(img_path), str(retry_path))
                retry_images.append(retry_path)
                done += 1
            if done == 1:
                per_image = time.time() - pass_start
                print(f"  → First image took {fmt_time(per_image)}. Estimated total: ~{fmt_time(per_image * total)}\n")
            show_progress(done, total, failed, start_time=pass_start)
        print()

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

            retry_total = len(retry_images)
            retry_pass_start = time.time()
            retry_done = 0
            retry_failed = 0
            still_failed = []
            for img_path in retry_images:
                img_start = time.time()
                try:
                    process_single_image(page, img_path)
                    cleanup_temp_screenshots()
                    shutil.move(str(img_path), str(COMPLETED_DIR / img_path.name))
                    retry_done += 1
                    print(f"  ⏱ Done in {time.time() - img_start:.1f}s")
                except Exception as e:
                    retry_failed += 1
                    log_detail(f"[{img_path.name}] RETRY ERROR:\n{traceback.format_exc()}")
                    print(f"\n  ✗ Retry failed: {img_path.name} — {short_error(e)}")
                    print(f"    (full details in {LOG_FILE})")
                    cleanup_temp_screenshots()
                    still_failed.append(img_path)
                    retry_done += 1
                show_progress(retry_done, retry_total, retry_failed, "Retry", start_time=retry_pass_start)
            print()

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


if __name__ == "__main__":
    main()