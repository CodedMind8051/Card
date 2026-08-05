"""
designer.py
-----------
Browser-based designer for creating a NEW ID-card template from a blank
template image.

Launched via:  python cardfiller.py --new

It asks for a template image (file or folder containing images), copies it
into templates/, then serves a page where you:
  * drag/resize the student-photo box,
  * drag/resize each text field and set its font size / bold / colour /
    alignment,
  * type dummy student data so you can preview the final look,
  * hit "Save template".

Saving writes templates/active_template.json, which the normal batch run
(`python cardfiller.py`) reads automatically, so every scanned form is filled
using this design from then on (the photo crop itself is still auto-detected
per student at fill time).
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

from card_render import (
    ACTIVE_TEMPLATE_FILE, TEMPLATES_DIR, build_default_layout,
    list_designed_templates, save_active_template,
)

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = Path.cwd()
ALLOWED_MEDIA_ROOTS = [BASE_DIR, TEMPLATES_DIR]

# Which fields every design starts with, in order. `prefix` is prepended to
# the value at render time (used for things like "Sec- ").
FIELD_DEFS = [
    {"key": "school_name",   "label": "School name"},
    {"key": "student_name",  "label": "Student name", "uppercase": True},
    {"key": "father_name",   "label": "Father's name"},
    {"key": "mother_name",   "label": "Mother's name"},
    {"key": "class",         "label": "Class"},
    {"key": "section",       "label": "Section", "prefix": "Sec- "},
    {"key": "roll_number",   "label": "Roll number"},
    {"key": "mobile_number", "label": "Mobile number"},
    {"key": "dob",           "label": "Date of birth"},
    {"key": "address",       "label": "Address", "multiline": True},
]

DEFAULT_DUMMY = {
    "school_name": "SCHOOL NAME",
    "student_name": "STUDENT NAME",
    "father_name": "FATHER NAME",
    "mother_name": "MOTHER NAME",
    "class": "CLASS",
    "section": "SEC",
    "roll_number": "ROLL",
    "mobile_number": "+91XXXXXXXXXX",
    "dob": "XX/XX/XXXX",
    "address": "ADDRESS",
}

app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
)


# --------------------------------------------------------------- helpers ---

def template_image_path(name: str) -> Path:
    return TEMPLATES_DIR / f"{name}_template.png"


def design_json_path(name: str) -> Path:
    return TEMPLATES_DIR / f"{name}_design.json"


def sanitize_layout(layout: dict) -> dict:
    """Normalise numbers to int/float so the JSON sidecar stays clean."""
    def num(v):
        if isinstance(v, bool):
            return v
        try:
            return int(v)
        except (TypeError, ValueError):
            try:
                return float(v)
            except (TypeError, ValueError):
                return v

    out = {"photo": {}, "texts": {}}
    photo = layout.get("photo") or {}
    for key in ("x", "y", "width", "height", "corner_radius"):
        if key in photo:
            out["photo"][key] = num(photo[key])
    out["photo"]["rotation"] = num(photo.get("rotation", 0))
    if photo.get("crop"):
        out["photo"]["crop"] = photo["crop"]

    for key, spec in (layout.get("texts") or {}).items():
        s = dict(spec or {})
        for k in ("x", "y", "width", "height", "font_size", "min_font_size"):
            if k in s:
                s[k] = num(s[k])
        out["texts"][key] = s
    return out


def default_layout_for(w: int, h: int) -> dict:
    """A sensible starting layout: photo box on the left, fields down the
    right. The user drags everything to fit their actual template."""
    text_h = int(h * 0.06)
    y = int(h * 0.05)
    texts = {}
    for fd in FIELD_DEFS:
        spec = {
            "x": int(w * 0.46),
            "y": y,
            "width": int(w * 0.5),
            "font_size": max(20, int(h * 0.028)),
            "min_font_size": 14,
            "color": "#D81616",
            "bold": True,
            "align": "left",
            "multiline": bool(fd.get("multiline")),
            "uppercase": bool(fd.get("uppercase")),
        }
        if fd.get("prefix"):
            spec["prefix"] = fd["prefix"]
        if fd.get("multiline"):
            spec["height"] = int(h * 0.12)
        texts[fd["key"]] = spec
        y += int(text_h * 1.15)

    return {
        "photo": {
            "x": int(w * 0.08),
            "y": int(h * 0.05),
            "width": int(w * 0.26),
            "height": int(h * 0.4),
            "corner_radius": 20,
            "rotation": 0,
        },
        "texts": texts,
    }


def load_starting_state(name: str) -> dict:
    """If this template was designed before, re-open its saved layout + dummy
    data; otherwise start from defaults."""
    cfg_path = design_json_path(name)
    layout = None
    dummy = None
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            layout = cfg.get("layout")
            dummy = cfg.get("dummy_data")
        except Exception:
            pass
    if layout is None:
        from PIL import Image
        with Image.open(template_image_path(name)) as im:
            layout = default_layout_for(im.width, im.height)
    if dummy is None:
        dummy = dict(DEFAULT_DUMMY)
    return layout, dummy


def safe_media_path(rel: str) -> Path:
    p = (BASE_DIR / rel).resolve()
    if not any(str(p).startswith(str(root.resolve())) for root in ALLOWED_MEDIA_ROOTS):
        abort(403)
    if not p.exists():
        abort(404)
    return p


# ----------------------------------------------------------------- routes ---

@app.route("/")
def index():
    return render_template("designer.html")


@app.route("/api/designs")
def api_designs():
    """Every previously designed template, so the page can offer reopening."""
    active_name = None
    if ACTIVE_TEMPLATE_FILE.exists():
        try:
            active_name = json.loads(ACTIVE_TEMPLATE_FILE.read_text(encoding="utf-8")).get("name")
        except Exception:
            pass
    return jsonify([
        {
            "name": t["name"],
            "thumb_url": f"/media/{t['template_file']}",
            "active": t["name"] == active_name,
        }
        for t in list_designed_templates()
    ])


@app.route("/api/start", methods=["POST"])
def api_start():
    """Receive a template image picked through the OS file manager, store it
    under templates/, and hand the page a design name to open."""
    if "template" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["template"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg"):
        return jsonify({"error": f"Unsupported type '{suffix}' - use png/jpg/jpeg"}), 400

    stem = Path(file.filename).stem.replace("_template", "")
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", stem).strip("_") or "template"
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    dest = template_image_path(name)
    file.save(str(dest))
    print(f"  Uploaded new template: {dest}")
    return jsonify({"ok": True, "name": name})


@app.route("/api/state/<name>")
def api_state(name: str):
    if not template_image_path(name).exists():
        abort(404, description=f"No template named '{name}'")
    from PIL import Image
    with Image.open(template_image_path(name)) as im:
        w, h = im.width, im.height
    layout, dummy = load_starting_state(name)
    return jsonify({
        "name": name,
        "template_url": f"/media/{template_image_path(name)}",
        "image_w": w,
        "image_h": h,
        "fields": FIELD_DEFS,
        "layout": layout,
        "dummy": dummy,
        "active": ACTIVE_TEMPLATE_FILE.exists()
        and ACTIVE_TEMPLATE_FILE.read_text(encoding="utf-8") != "",
    })


@app.route("/api/save/<name>", methods=["POST"])
def api_save(name: str):
    if not template_image_path(name).exists():
        abort(404, description=f"No template named '{name}'")
    payload = request.get_json(force=True)
    layout = sanitize_layout(payload.get("layout") or {})
    dummy = payload.get("dummy") or {}
    for fd in FIELD_DEFS:
        layout["texts"].setdefault(fd["key"], {})

    # Sidecar for re-opening the design later.
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    design_json_path(name).write_text(
        json.dumps({"layout": layout, "dummy_data": dummy},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")

    # Mark this as the template every future batch run uses.
    save_active_template(
        name, layout, dummy,
        template_file=str(template_image_path(name)))

    return jsonify({
        "ok": True,
        "active_template_file": str(ACTIVE_TEMPLATE_FILE),
        "preview_url": f"/media/preview/{name}",
    })


@app.route("/media/<path:rel>")
def media(rel: str):
    return send_file(safe_media_path(rel))


@app.route("/media/preview/<name>")
def preview(name: str):
    """Server-side render of the design with the dummy data, using the exact
    same code path as the real batch fill (render_card)."""
    layout, dummy = load_starting_state(name)
    from card_render import render_card
    layout["photo"]["crop"] = {"x": 0, "y": 0, "w": 1, "h": 1}
    im = render_card(
        dummy, layout,
        template_path=str(template_image_path(name)),
        photo_source_path=None,
    )
    from io import BytesIO
    buf = BytesIO()
    im.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# -------------------------------------------------------------- run logic ---

def pick_template(path_str: str) -> Path:
    """Resolve the CLI-given path to a single template image. A folder lists
    its images; a file is used directly."""
    p = Path(path_str).expanduser()
    if not p.exists():
        print(f"  ✗ Path not found: {path_str}")
        raise SystemExit(1)
    if p.is_file():
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            print(f"  ✗ Not an image file: {path_str}")
            raise SystemExit(1)
        return p
    images = sorted(
        [f for f in p.iterdir()
         if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg")]
    )
    if not images:
        print(f"  ✗ No image files found in {path_str}")
        raise SystemExit(1)
    print(f"\n  Found {len(images)} template image(s) in {path_str}:\n")
    for i, img in enumerate(images, 1):
        print(f"    {i:>3}. {img.name}")
    while True:
        try:
            choice = int(input("\n  Choose a number: ").strip())
        except ValueError:
            continue
        if 1 <= choice <= len(images):
            return images[choice - 1]


def prepare_template(src: Path) -> str:
    """Copy the chosen template into templates/ and return its design name."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    name = src.stem
    if name.endswith("_template"):
        name = name[:-9]
    dest = template_image_path(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
        shutil.copy2(str(src), str(dest))
    print(f"  Template ready: {dest}")
    return name


def run_editor(port: int = 5001, open_browser: bool = True, template: str | None = None):
    query = f"?template={template}" if template else ""
    url = f"http://127.0.0.1:{port}{query}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"\n  Designer running at {url}  (Ctrl+C to stop)")
    if template:
        print("  Drag the photo box + text fields, set sizes/colours, type dummy")
        print("  data, then press 'Save template'.\n")
    else:
        print("  In the browser, click 'Choose template image' (opens the file")
        print("  manager) or reopen a previously designed template.\n")
    app.run(host="127.0.0.1", port=port, debug=False)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Design a new ID-card template.")
    parser.add_argument("path", nargs="?", help="Template image or folder to use directly (optional - otherwise pick it in the browser).")
    parser.add_argument("--port", type=int, default=5001, help="Port for the designer (default 5001).")
    args = parser.parse_args()

    template = None
    if args.path:
        src = pick_template(args.path)
        template = prepare_template(src)

    run_editor(port=args.port, template=template)


if __name__ == "__main__":
    main()
