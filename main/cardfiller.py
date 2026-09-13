import re
import sys
import json
import time
import os
import random
import shutil
import socket
import zipfile
import argparse
import traceback
import mimetypes
from pathlib import Path
from collections import deque

from card_render import (
    TEMPLATE_PATH, PHOTO_BOX, TEMPLATES_DIR,
    build_default_layout, detect_face_crop_rect, render_card, load_active_template,
)

IMAGE_DIR = Path("image")
STUDENT_IMAGE_DIR = Path("student_images")
OUTPUT_DIR = Path("output")
CARD_DIR = OUTPUT_DIR / "cards"       # rendered card PNGs
RECORD_DIR = OUTPUT_DIR / "records"   # editable *_data.json sidecars
RETRY_DIR = Path("retry")
RETRY_FORM_DIR = RETRY_DIR / "form"
RETRY_STUDENT_DIR = RETRY_DIR / "student"
COMPLETED_DIR = Path("completed")
COMPLETED_FORM_DIR = COMPLETED_DIR / "form"
COMPLETED_STUDENT_DIR = COMPLETED_DIR / "student"
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

# ---- Gemini settings (free tier) - FIXED Sep 2026 ----
# ACTUAL free tier (Sep 2026): gemini-flash-latest maps to 3.8-flash, limit 20 RPD
# per project per model — NOT 15 RPM. The 15 RPM / 1000 RPD comment was outdated.
# gemini-2.5-flash-lite / 2.0-flash now 404 with google-genai 0.3.0; keep as fallback only.
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
GEMINI_RPM = 5  # throttle to avoid 429 even though daily cap is the real limit
_GEMINI_MIN_INTERVAL = 60.0 / GEMINI_RPM + 1.0
_last_gemini_call = 0.0
_gemini_client = None

# ---- Groq fallback (free tier) - 30 RPM / 14.4K OTPM free ----
# Vision model with JSON mode: qwen/qwen3.6-27b supports image+JSON
# NOTE: free tier OTPM = 1000 (on_demand). Must keep max_tokens low and images small.
GROQ_MODEL = "qwen/qwen3.6-27b"
GROQ_RPM = 10  # safer than 30; OTPM is the real bottleneck
_GROQ_MIN_INTERVAL = 60.0 / GROQ_RPM + 0.5
_last_groq_call = 0.0
_groq_client = None

DAILY_QUOTA_PATTERNS = ["GenerateRequestsPerDay", "PerDayPerProject", "quotaValue.*20", "limit: 20"]

# ---- Stop signal for UI ----
_stop_requested = False
def request_stop():
    global _stop_requested
    _stop_requested = True
def clear_stop():
    global _stop_requested
    _stop_requested = False
def should_stop() -> bool:
    # also check temp file signal for cross-process stop
    if _stop_requested:
        return True
    try:
        if (TEMP_DIR / "stop_requested").exists():
            return True
    except Exception:
        pass
    return False


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


PROMPT = (
    "Extract all information from this image and return ONLY a valid JSON object "
    "with these keys: school_name, student_name, father_name, mother_name, class, "
    "section, roll_number, mobile_number, dob, address, student_photo_bbox. "
    "For student_photo_bbox, return a list of 4 decimal numbers between 0.0 and 1.0 "
    "representing [x_ratio, y_ratio, width_ratio, height_ratio] relative to the "
    "total image width and height. Example: [0.15, 0.20, 0.10, 0.15]. "
    "Missing fields=\"\". No extra text. And if information in hindi then convert into english.and if the address is too long just give the important part of the address. Return only the JSON object, nothing else."
)


def get_gemini_client():
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_GENAI_API_KEY")
    if not api_key:
        # also try reading from temp/gemini_key.txt for convenience
        key_file = Path("temp/gemini_key.txt")
        if key_file.exists():
            api_key = key_file.read_text(encoding="utf-8").strip()
    if not api_key:
        # try config_store (premium UI)
        try:
            import config_store as _cs
            api_key = _cs.get_active_key_value("gemini")
        except ModuleNotFoundError:
            try:
                from main import config_store as _cs
                api_key = _cs.get_active_key_value("gemini")
            except Exception:
                pass
        except Exception:
            pass
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not set.\n"
            "  1. Open Premium UI at http://127.0.0.1:5000 -> API Keys -> Add Gemini key\n"
            "  2. Or get free key at https://aistudio.google.com/app/apikey\n"
            "  3. Set it: export GEMINI_API_KEY='your_key' or create temp/gemini_key.txt"
        )
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=api_key)
    except ImportError:
        raise RuntimeError(
            "google-genai not installed. Run: pip install google-genai\n"
            "  (or pip install -r requirements.txt)"
        )
    return _gemini_client


def throttle_gemini():
    global _last_gemini_call
    now = time.time()
    elapsed = now - _last_gemini_call
    if elapsed < _GEMINI_MIN_INTERVAL:
        wait = _GEMINI_MIN_INTERVAL - elapsed
        print(f"  ⏳ Gemini rate limit ({GEMINI_RPM} RPM) - waiting {wait:.1f}s...")
        time.sleep(wait)
    _last_gemini_call = time.time()


def is_daily_quota_error(msg: str) -> bool:
    return any(p.lower() in msg.lower() for p in DAILY_QUOTA_PATTERNS) or ("perday" in msg.lower() and "20" in msg)

def downscale_image_for_api(image_path: Path, max_dim: int = 1024, quality: int = 80) -> tuple[bytes, str]:
    """Resize image so longest side <= max_dim and return (jpeg_bytes, mime). Saves API quota/tokens and avoids Groq 429 OTPM."""
    from PIL import Image
    import io
    try:
        im = Image.open(image_path)
        if im.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != "RGB":
            im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_dim / max(w, h))
        if scale < 1.0:
            new_w, new_h = int(w * scale), int(h * scale)
            im = im.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        # fallback to original bytes
        mime, _ = mimetypes.guess_type(str(image_path))
        if mime is None:
            mime = "image/jpeg"
        return image_path.read_bytes(), mime


def get_groq_client():
    global _groq_client
    if _groq_client is not None:
        return _groq_client
    api_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY")
    if not api_key:
        key_file = Path("temp/groq_key.txt")
        if key_file.exists():
            api_key = key_file.read_text(encoding="utf-8").strip()
    if not api_key:
        try:
            import config_store as _cs
            api_key = _cs.get_active_key_value("groq")
        except ModuleNotFoundError:
            try:
                from main import config_store as _cs
                api_key = _cs.get_active_key_value("groq")
            except Exception:
                pass
        except Exception:
            pass
    if not api_key:
        return None
    try:
        from groq import Groq
        _groq_client = Groq(api_key=api_key)
    except ImportError:
        print("  ⚠ groq not installed (pip install groq) - skipping Groq fallback")
        return None
    return _groq_client


def throttle_groq():
    global _last_groq_call
    now = time.time()
    elapsed = now - _last_groq_call
    if elapsed < _GROQ_MIN_INTERVAL:
        wait = _GROQ_MIN_INTERVAL - elapsed
        print(f"  ⏳ Groq rate limit ({GROQ_RPM} RPM) - waiting {wait:.1f}s...")
        time.sleep(wait)
    _last_groq_call = time.time()


def call_groq_image(image_path: Path) -> str:
    """Fallback: send image + PROMPT to Groq vision model. Downscales image + uses low max_tokens to stay under 1000 OTPM free limit."""
    import base64
    client = get_groq_client()
    if client is None:
        raise RuntimeError("GROQ_API_KEY not set. Get free key at https://console.groq.com/keys")
    throttle_groq()
    img_bytes, mime = downscale_image_for_api(image_path, max_dim=1024, quality=80)
    b64 = base64.b64encode(img_bytes).decode("utf-8")
    if not check_internet():
        wait_for_internet()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        response_format={"type": "json_object"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            ]
        }],
        temperature=0.1,
        max_tokens=600,  # must stay <1000 OTPM free tier; 2000 triggers 429 (see run_log 2026-09-13)
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise BadJSONResponseError("Empty response from Groq")
    return text


def call_gemini_image(image_path: Path) -> str:
    """Send image + PROMPT to Gemini and return raw text. Handles 404 fallback + 429/503 retry + throttle."""
    from google.genai import types

    throttle_gemini()

    # Downscale to ~1280px to cut quota cost and latency (originals are 4000x3000 ~3MB)
    image_bytes, mime = downscale_image_for_api(image_path, max_dim=1280, quality=85)
    client = get_gemini_client()

    models_to_try = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]
    max_retries = 3  # reduced - fallback to Groq instead of long wait

    for attempt in range(max_retries):
        # try each model until one succeeds
        last_error = None
        for model_name in models_to_try:
            try:
                if not check_internet():
                    wait_for_internet()
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime),
                        PROMPT,
                    ],
                )
                text = (response.text or "").strip()
                if not text:
                    raise BadJSONResponseError("Empty response from Gemini")
                # remember working model for next calls
                if model_name != GEMINI_MODEL:
                    globals()["GEMINI_MODEL"] = model_name
                    print(f"  ✓ Using model {model_name}")
                return text
            except BadJSONResponseError:
                raise
            except Exception as e:
                msg = str(e)
                is_404 = "404" in msg or "NOT_FOUND" in msg
                is_429 = "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
                is_503 = "503" in msg or "UNAVAILABLE" in msg or "overloaded" in msg.lower() or "high demand" in msg.lower()
                if is_daily_quota_error(msg):
                    # Daily 20 RPD exhausted — retrying is pointless for ~24h. Fail fast to Groq or abort.
                    print(f"\n  ✗ Daily quota hit (20/day) on {model_name}: {short_error(e)}")
                    print(f"    → Quota resets ~midnight Pacific. Use a different project/key or wait.")
                    log_detail(f"[{image_path.name}] DAILY QUOTA hit on {model_name}: {msg[:800]}")
                    last_error = e
                    break
                if is_404:
                    last_error = e
                    print(f"  ⚠ Model {model_name} not found (404), trying next...")
                    continue
                if is_429 or is_503:
                    status = "429 quota" if is_429 else "503 overloaded"
                    wait = min(8 * (2 ** attempt) + random.uniform(0, 2), 30)
                    print(f"\n  ⚠ Gemini {status} on {model_name} - waiting {wait:.0f}s (attempt {attempt+1}/{max_retries})...")
                    log_detail(f"[{image_path.name}] {status} retry {attempt+1}: {msg[:500]}")
                    time.sleep(wait)
                    throttle_gemini()
                    last_error = e
                    break  # break model loop -> retry outer or fallback to Groq
                if is_network_error(e) or not check_internet():
                    print(f"\n  ⚠ Network issue: {short_error(e)}")
                    wait_for_internet()
                    last_error = e
                    break
                # other error - try next model if 404-ish else retry outer
                last_error = e
                print(f"  ⚠ Gemini error on {model_name}: {short_error(e)} - trying next model...")
                continue

        # if we got here without returning, we either had 429/503/network (break) or all models failed
        if last_error is None:
            continue
        msg = str(last_error)
        is_429 = "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
        is_503 = "503" in msg or "UNAVAILABLE" in msg
        is_network = is_network_error(last_error) or not check_internet()
        if is_429 or is_503 or is_network:
            if attempt < max_retries - 1:
                continue
            else:
                raise last_error  # will trigger Groq fallback in caller
        # non-retryable but we tried all models - retry outer a few times
        if attempt < max_retries - 1:
            wait = 3 * (attempt + 1)
            print(f"  ⚠ Retrying in {wait}s ({attempt+1}/{max_retries})...")
            time.sleep(wait)
            continue
        raise last_error
    raise RuntimeError(f"Failed after {max_retries} retries: {last_error}")


def call_image_with_fallback(image_path: Path) -> str:
    """Try Gemini first, fallback to Groq on 429/503/404 to avoid long waits. Groq will fail fast if daily quota is hit."""
    try:
        return call_gemini_image(image_path)
    except Exception as gem_e:
        msg = str(gem_e)
        if is_daily_quota_error(msg):
            # Don't even try Groq if we know Gemini daily quota is gone — but try Groq anyway as fallback,
            # because Groq has its own separate quota.
            groq_client = get_groq_client()
            if groq_client is not None:
                print(f"  → Gemini daily quota exhausted, trying Groq {GROQ_MODEL}...")
                log_detail(f"[{image_path.name}] Gemini daily quota -> Groq fallback: {msg[:400]}")
                try:
                    text = call_groq_image(image_path)
                    print(f"  ✓ Groq succeeded (bypassing Gemini daily limit)")
                    return text
                except Exception as groq_e:
                    print(f"  ✗ Groq also failed: {short_error(groq_e)}")
                    raise RuntimeError(f"Gemini daily quota hit: {gem_e} | Groq failed: {groq_e}") from gem_e
            raise RuntimeError(f"Gemini daily quota hit (20/day) — add new API key or wait until midnight Pacific. Detail: {gem_e}") from gem_e
        is_fallback_error = any(x in msg for x in ["429", "503", "404", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "quota", "NOT_FOUND", "overloaded"])
        groq_client = get_groq_client()
        if is_fallback_error and groq_client is not None:
            print(f"  → Gemini failed ({short_error(gem_e)}), trying Groq {GROQ_MODEL}...")
            log_detail(f"[{image_path.name}] Gemini failed, Groq fallback: {msg[:400]}")
            try:
                text = call_groq_image(image_path)
                print(f"  ✓ Groq succeeded")
                return text
            except Exception as groq_e:
                # Groq OTPM 429 with max_tokens fix should be rare now; surface clearly
                if "OTPM" in str(groq_e) or "output tokens" in str(groq_e).lower():
                    log_detail(f"[{image_path.name}] Groq OTPM hit: {groq_e}")
                print(f"  ✗ Groq also failed: {short_error(groq_e)}")
                raise RuntimeError(f"Gemini failed: {gem_e} | Groq failed: {groq_e}") from gem_e
        raise


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


def list_images(directory: Path) -> list:
    return (sorted(directory.glob("*.[jJ][pP][gG]")) +
            sorted(directory.glob("*.[jJ][pP][eE][gG]")) +
            sorted(directory.glob("*.[pP][nN][gG]")))


def sidecar_path_for(image_name: str) -> Path:
    return RECORD_DIR / f"{Path(image_name).stem}_data.json"


def save_sidecar(image_name: str, data: dict, layout: dict, source_file: str, output_file: str,
                 template_file: str = TEMPLATE_PATH):
    """Write the editable record that the browser editor (--edit) reads/writes."""
    record = {
        "image_name": image_name,
        "source_file": source_file,
        "output_file": output_file,
        "template_file": template_file,
        "data": data,
        "layout": layout,
    }
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = sidecar_path_for(image_name)
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def fill_template(data: dict, image_name: str, source_image_path: Path, ai_ratios=None, enhance_photo=False,
                  photo_source_path: Path = None):
    """Render the card for the first time and save an editable sidecar next to it."""
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

    crop_source = photo_source_path or source_image_path
    crop_ai_ratios = None if photo_source_path else ai_ratios
    crop_rect = detect_face_crop_rect(crop_source, ai_ratios=crop_ai_ratios)
    if crop_rect:
        rotation = crop_rect.get("rotation") or 0
        layout["photo"]["crop"] = {k: crop_rect[k] for k in ("x", "y", "w", "h")}
        layout["photo"]["rotation"] = rotation
    else:
        layout["photo"]["crop"] = None

    im = render_card(data, layout, template_path=template_path, photo_source_path=str(crop_source), enhance_photo=enhance_photo)
    im.save(output_path)
    print(f"  Filled card saved: {output_path}")
    if crop_rect is None:
        print("  No face detected in source image - photo left blank (adjust the crop in --edit).")

    if photo_source_path:
        source_rel = str(Path("completed") / "student" / Path(photo_source_path).name)
    else:
        source_rel = str(Path("completed") / image_name)
    save_sidecar(image_name, data, layout, source_rel, str(output_path), template_file=template_path)
    return output_path


def process_single_image(image_path: Path, enhance_photo=False, student_photo_path: Path = None):
    print(f"\n--- Processing: {image_path.name} ---")
    groq_ready = get_groq_client() is not None
    provider_info = f"{GEMINI_MODEL} (Gemini {GEMINI_RPM} RPM" + (f" + Groq {GROQ_MODEL} fallback)" if groq_ready else ")")
    print(f"  Sending to {provider_info}...")

    raw_text = call_image_with_fallback(image_path)
    log_detail(f"[{image_path.name}] Gemini raw response:\n{raw_text[:4000]}")

    data = extract_json(raw_text)
    if data is None:
        log_detail(f"[{image_path.name}] Could not parse JSON. Raw text:\n{raw_text}")
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
    fill_template(data, image_path.name, image_path, ai_ratios=None if student_photo_path else ai_ratios,
                  enhance_photo=enhance_photo, photo_source_path=student_photo_path)
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


def _move_to_completed(src_path, form_name, is_student_photo=False, external_student_image=False):
    if external_student_image:
        target_dir = COMPLETED_STUDENT_DIR if is_student_photo else COMPLETED_FORM_DIR
    else:
        target_dir = COMPLETED_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / src_path.name
    if dest.exists():
        p = Path(src_path.name)
        dest = target_dir / f"{p.stem}_photo{p.suffix}"
    shutil.move(str(src_path), str(dest))
    if is_student_photo:
        sidecar = sidecar_path_for(form_name)
        if sidecar.exists():
            try:
                record = json.loads(sidecar.read_text(encoding="utf-8"))
                record["source_file"] = str(dest.relative_to(Path.cwd()))
                sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
    return dest


def _copy_to_retry(form_path, student_photo_path, external_student_image=False):
    RETRY_DIR.mkdir(exist_ok=True)
    if external_student_image:
        RETRY_FORM_DIR.mkdir(parents=True, exist_ok=True)
        RETRY_STUDENT_DIR.mkdir(parents=True, exist_ok=True)
        retry_form = RETRY_FORM_DIR / form_path.name
        retry_student = RETRY_STUDENT_DIR / student_photo_path.name if student_photo_path else None
        if form_path.exists() and form_path != retry_form:
            shutil.move(str(form_path), str(retry_form))
        if student_photo_path and student_photo_path.exists() and student_photo_path != retry_student:
            shutil.move(str(student_photo_path), str(retry_student))
        return retry_form, retry_student
    retry_path = RETRY_DIR / form_path.name
    if form_path.exists():
        shutil.copy2(str(form_path), str(retry_path))
    return retry_path, None


def process_image_pass(jobs, label, pass_start, enhance_photo=False, external_student_image=False):
    """Process a list of jobs. Each job is a tuple (form_path, student_photo_path)"""
    total = len(jobs)
    done = 0
    failed = 0
    retry_jobs = []
    queue = deque(jobs)

    consecutive_bad_json = 0
    bad_json_batch = []

    def flush_bad_json_batch_as_failures():
        nonlocal failed, done
        for form_path, student_path in bad_json_batch:
            failed += 1
            done += 1
            log_detail(f"[{form_path.name}] Bad-JSON streak broke without hitting {BAD_JSON_STREAK_LIMIT} in a row; treating as failed.")
            print(f"\n  ✗ Failed: {form_path.name} — bad/non-JSON response")
            retry_jobs.append(_copy_to_retry(form_path, student_path, external_student_image))
        bad_json_batch.clear()

    while queue:
        # --- UI stop check ---
        if should_stop():
            print(f"\n  ■ Stopped by user — {len(queue)+1} remaining will stay in {IMAGE_DIR}/")
            log_detail(f"[STOP] User stopped batch, {len(queue)+1} remaining")
            # put current + remaining back to image folder (they are already there unless moved)
            # ensure stop flag cleared for next run
            clear_stop()
            try:
                (TEMP_DIR / "stop_requested").unlink(missing_ok=True)
            except Exception:
                pass
            break
        form_path, student_path = queue.popleft()
        img_start = time.time()
        finished = False
        while not finished:
            # also check stop inside retry loop (e.g., during quota wait)
            if should_stop():
                print(f"\n  ■ Stop requested — aborting {form_path.name}")
                queue.appendleft((form_path, student_path))
                clear_stop()
                try:
                    (TEMP_DIR / "stop_requested").unlink(missing_ok=True)
                except Exception:
                    pass
                finished = True
                # break outer while via flag
                queue.clear()
                break
            try:
                process_single_image(form_path, enhance_photo=enhance_photo, student_photo_path=student_path)
                cleanup_temp_screenshots()
                _move_to_completed(form_path, form_path.name, external_student_image=external_student_image)
                if student_path:
                    _move_to_completed(student_path, form_path.name, is_student_photo=True,
                                       external_student_image=external_student_image)
                done += 1
                print(f"  ⏱ Done in {time.time() - img_start:.1f}s")
                finished = True
                if bad_json_batch:
                    consecutive_bad_json = 0
                    flush_bad_json_batch_as_failures()

            except BadJSONResponseError as e:
                consecutive_bad_json += 1
                bad_json_batch.append((form_path, student_path))
                cleanup_temp_screenshots()
                print(f"\n  ⚠ Non-JSON response for {form_path.name} "
                      f"({consecutive_bad_json}/{BAD_JSON_STREAK_LIMIT} in a row): {short_error(e)}")

                if consecutive_bad_json >= BAD_JSON_STREAK_LIMIT:
                    print(f"  {BAD_JSON_STREAK_LIMIT} bad responses in a row — "
                          f"waiting {fmt_time(JSON_RETRY_WAIT)} before trying them again...")
                    time.sleep(JSON_RETRY_WAIT)
                    consecutive_bad_json = 0
                    for job in reversed(bad_json_batch):
                        queue.appendleft(job)
                    bad_json_batch.clear()
                    print("  Resuming...")

                finished = True

            except Exception as e:
                if is_daily_quota_error(str(e)) and get_groq_client() is None:
                    # No Groq to fallback to — abort the whole batch cleanly instead of 86 x 1m42s failures.
                    print(f"\n  ✗ Daily Gemini quota exhausted (20/day) on {form_path.name}. Stopping batch.")
                    print(f"    → Options: 1) Add GROQ_API_KEY for fallback, 2) wait until midnight Pacific, 3) use a second Google Cloud project key.")
                    print(f"    → Remaining {len(queue)+1} image(s) left untouched in {IMAGE_DIR}/ — re-run after fixing quota.")
                    log_detail(f"[{form_path.name}] DAILY QUOTA ABORT: {traceback.format_exc()}")
                    # put current + remaining queue into retry so nothing is lost
                    retry_jobs.append(_copy_to_retry(form_path, student_path, external_student_image))
                    for q_form, q_student in list(queue):
                        retry_jobs.append(_copy_to_retry(q_form, q_student, external_student_image))
                    failed += 1 + len(queue)
                    done += 1 + len(queue)
                    queue.clear()
                    finished = True
                    break

                if is_network_error(e) or not check_internet():
                    print(f"\n  ⚠ Network issue while processing {form_path.name}: {short_error(e)}")
                    log_detail(f"[{form_path.name}] NETWORK ERROR:\n{traceback.format_exc()}")
                    cleanup_temp_screenshots()
                    wait_for_internet()
                    print(f"  Resuming {form_path.name}...")
                    continue

                if bad_json_batch:
                    consecutive_bad_json = 0
                    flush_bad_json_batch_as_failures()

                failed += 1
                log_detail(f"[{form_path.name}] ERROR:\n{traceback.format_exc()}")
                print(f"\n  ✗ Failed: {form_path.name} — {short_error(e)}")
                print(f"    (full details in {LOG_FILE})")
                cleanup_temp_screenshots()
                retry_jobs.append(_copy_to_retry(form_path, student_path, external_student_image))
                done += 1
                finished = True

        if done == 1:
            per_image = time.time() - pass_start
            print(f"  → First image took {fmt_time(per_image)}. Estimated total: ~{fmt_time(per_image * total)}\n")
        show_progress(min(done, total), total, failed, label=label, start_time=pass_start)

    if bad_json_batch:
        flush_bad_json_batch_as_failures()

    print()
    return done, failed, retry_jobs


def scrape_main(enhance_photo=False, external_student_image=False):
    OUTPUT_DIR.mkdir(exist_ok=True)
    CARD_DIR.mkdir(exist_ok=True)
    RECORD_DIR.mkdir(exist_ok=True)
    RETRY_DIR.mkdir(exist_ok=True)
    COMPLETED_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    # sync last used models from premium UI config if present
    try:
        try:
            import config_store as _cs
        except ModuleNotFoundError:
            from main import config_store as _cs
        gm = _cs.get_last_model("gemini")
        gg = _cs.get_last_model("groq")
        global GEMINI_MODEL, GROQ_MODEL
        if gm:
            GEMINI_MODEL = gm
        if gg:
            GROQ_MODEL = gg
    except Exception:
        pass

    all_images = list_images(IMAGE_DIR)
    if not all_images:
        print(f"No images found in {IMAGE_DIR}")
        return

    # Validate Gemini + Groq keys before starting
    try:
        get_gemini_client()
        groq_client = get_groq_client()
        if groq_client:
            print(f"✓ Gemini ready ({GEMINI_MODEL}, ~{GEMINI_RPM} RPM throttled, 20/day cap) + Groq ready ({GROQ_MODEL}, ~{GROQ_RPM} RPM)")
            print(f"  ℹ Free tier daily caps: Gemini 20/day per model, Groq 1000 OTPM + 30 RPM. Batch of 86 will need 4+ days or multiple keys.")
        else:
            print(f"✓ Gemini ready ({GEMINI_MODEL}, ~{GEMINI_RPM} RPM throttled, 20/day cap)")
            print(f"  ℹ For fallback when Gemini daily quota hits, add Groq: export GROQ_API_KEY='gsk_...' from https://console.groq.com/keys (free)")
            print(f"  ℹ 86 images exceeds Gemini 20/day free limit — will stall after ~20 without Groq or extra keys.")
    except Exception as e:
        print(f"\n✗ {e}")
        return

    if external_student_image:
        student_images = list_images(STUDENT_IMAGE_DIR)
        if not student_images:
            print(f"No student images found in {STUDENT_IMAGE_DIR}")
            return
        if len(student_images) < len(all_images):
            print(f"  ⚠ Found {len(all_images)} forms but only {len(student_images)} student images "
                  f"in {STUDENT_IMAGE_DIR}. The sequences must match 1:1 in order.")
            print("  Processing only the first", len(student_images), "forms to keep the pairing intact.")
            all_images = all_images[:len(student_images)]
        elif len(student_images) > len(all_images):
            print(f"  ⚠ {len(student_images)} student images but only {len(all_images)} forms. "
                  f"Extra student images will be ignored.")

        jobs = list(zip(all_images, student_images))
        print(f"\nUsing external student photos (paired in file order with the forms):")
        for i, (form, photo) in enumerate(jobs):
            print(f"  {i+1:>3}. {form.name}  ↔  {photo.name}")
    else:
        jobs = [(img, None) for img in all_images]

    total = len(jobs)
    print(f"\nFound {total} image(s) to process. Estimating time after the first one...\n")
    print(f"  Throttled: ~{fmt_time(60/GEMINI_RPM)} per image + processing (daily cap: 20 Gemini / 1000 Groq tokens)")
    if total > 20 and get_groq_client() is None:
        print(f"  ⚠ WARNING: {total} > 20 daily Gemini free limit — will hit 429. Add GROQ_API_KEY or --use multiple projects.")
    print()

    pass_start = time.time()
    done, failed, retry_jobs = process_image_pass(jobs, "Processing", pass_start,
                                                   enhance_photo=enhance_photo,
                                                   external_student_image=external_student_image)

    if retry_jobs:
        print(f"\n=== Retrying {len(retry_jobs)} image(s) ===")
        retry_pass_start = time.time()
        retry_done, retry_failed, still_failed = process_image_pass(retry_jobs, "Retry", retry_pass_start,
                                                                     enhance_photo=enhance_photo,
                                                                     external_student_image=external_student_image)

        if still_failed:
            print(f"\n=== {len(still_failed)} image(s) still failed after retry ===")
            for form_path, _ in still_failed:
                print(f"  {form_path.name}")
            if external_student_image:
                print("Forms remain in retry/form/ and their student photos in retry/student/.")
            else:
                print("These remain in the retry folder.")
        else:
            print("\n=== All retries succeeded! ===")
    else:
        print("\n=== All images processed successfully! ===")


def archive_current_template(name: str) -> bool:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    target = HISTORY_DIR / f"{name}.zip"
    if target.exists():
        print(f"  {target} already exists. Pick a different name or delete it first.")
        return False

    paths = ["image", "student_images", "output", "completed", "retry", "temp", "templates", "template.png", "pdf"]
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in paths:
            p = Path(rel)
            if not p.exists():
                continue
            if p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file():
                        zf.write(f, arcname=str(f))
                        count += 1
            else:
                zf.write(p, arcname=str(p))
                count += 1
    print(f"  ✓ Archived current batch ({count} files) -> {target}")

    cleaned = []
    for rel in paths:
        p = Path(rel)
        if p.is_dir():
            shutil.rmtree(p)
            cleaned.append(str(p) + "/")
        elif p.exists():
            p.unlink()
            cleaned.append(str(p))
    if cleaned:
        print("  ✓ Removed from workspace:")
        for c in cleaned:
            print(f"    - {c}")
    return True


def restore_archive(name: str) -> bool:
    target = HISTORY_DIR / f"{name}.zip"
    if not target.exists():
        print(f"  ✗ No archived batch named '{name}' (looked for {target}).")
        return False

    with zipfile.ZipFile(target) as zf:
        top_level = sorted({n.split("/", 1)[0] for n in zf.namelist() if n})

    removed = []
    for t in top_level:
        p = Path.cwd() / t
        if p.is_dir():
            shutil.rmtree(p)
            removed.append(str(p) + "/")
        elif p.exists():
            p.unlink()
            removed.append(str(p))

    if removed:
        print("  Restoring will replace the current:")
        for r in removed:
            print(f"    - {r}")
        print("  (If you still need the current batch, archive it first with --mark-old).")

    with zipfile.ZipFile(target) as zf:
        zf.extractall(Path.cwd())

    print(f"  ✓ Restored archived batch '{name}'. You can continue your work now.")
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
    parser.add_argument("--external-student-image", action="store_true", default=False,
                        help="Use a student photo from the student_images/ folder instead of the one cropped out of "
                             "the scanned form. Forms (image/) and photos (student_images/) are paired 1:1 by file "
                             "order, so the i-th form gets the i-th photo. The photo is face-detected & cropped "
                             "automatically (Google Lens bbox is NOT used). Failed pairs go to retry/form/ and "
                             "retry/student/.")
    parser.add_argument("--mark-old", metavar="NAME",
                        help="Archive the ENTIRE current batch (image/, output/, completed/, retry/, temp/, templates/, template.png) "
                             "as a zip in history/ under NAME, then stop.")
    parser.add_argument("--given-name", metavar="NAME",
                        help="Restore the archived batch NAME from history/ back into the project so you can continue it.")
    parser.add_argument("--current", action="store_true", default=False,
                        help="Explicitly keep using the current working state (default behaviour; cancels --given-name).")
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
        scrape_main(enhance_photo=args.image_enhance, external_student_image=args.external_student_image)


if __name__ == "__main__":
    main()
