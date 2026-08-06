"""
editor_app.py
-------------
Browser-based editor for the student ID cards produced by card_filler.py.

Launched via:  python card_filler.py --edit

Serves a page listing every finished card (from output/*_data.json) on the
left. Clicking one loads an editable canvas of that card, plus the original
uploaded photo in the corner for reference, plus a form with all the text
fields. You can drag/resize text and the photo, rotate + crop the photo,
change font size/colour, and edit any field's text - then hit Save, which
re-renders the final PNG server-side with PIL so the exported card always
matches what a normal (non-browser) render would produce.
"""

from __future__ import annotations

import json
import shutil
import webbrowser
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_file, render_template, abort

from card_render import (
    TEMPLATE_PATH, build_default_layout, merge_layout, render_card, hex_to_rgb,
    load_active_template,
)

# cardfiller.py resolves its data directories (image/, output/, completed/,
# temp/, template.png) relative to the CWD, so the editor must do the same —
# otherwise the two disagree and the editor can't find the template or the
# uploaded photos. Only the HTML templates and static assets live next to
# this file.
CODE_DIR = Path(__file__).resolve().parent
BASE_DIR = Path.cwd()
OUTPUT_DIR = BASE_DIR / "output"
CARD_DIR = OUTPUT_DIR / "cards"       # rendered card PNGs
RECORD_DIR = OUTPUT_DIR / "records"   # editable *_data.json sidecars
COMPLETED_DIR = BASE_DIR / "completed"
IMAGE_DIR = BASE_DIR / "image"
TEMP_DIR = BASE_DIR / "temp"

# Directories the /media/ route is allowed to serve files from.
ALLOWED_MEDIA_ROOTS = [BASE_DIR, OUTPUT_DIR, CARD_DIR, RECORD_DIR, COMPLETED_DIR, IMAGE_DIR, TEMP_DIR]

app = Flask(__name__, template_folder=str(CODE_DIR / "templates"), static_folder=str(CODE_DIR / "static"))


# --------------------------------------------------------------- helpers ---

def sidecar_path(name: str) -> Path:
    return RECORD_DIR / f"{name}_data.json"


def load_record(name: str) -> dict:
    path = sidecar_path(name)
    if not path.exists():
        abort(404, description=f"No record named '{name}'")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["layout"] = merge_layout(build_default_layout(), record.get("layout"))
    return record


def record_template_path(record: dict) -> str:
    """Which template image this card should be shown/rendered on, as a path
    relative to the project root (for both the /media/ route and PIL).

    Resolution order:
      1. the per-card template recorded in the sidecar (new batch runs write it);
      2. the active designed template (templates/active_template.json) — used by
         the scraper, and also covers old sidecars that predate the field;
      3. the legacy hard-coded template.png.
    """
    tpl = record.get("template_file")
    if tpl:
        p = (BASE_DIR / tpl).resolve()
        if p.exists():
            try:
                return str(p.relative_to(BASE_DIR.resolve()))
            except ValueError:
                pass
    active = load_active_template()
    if active and active.get("template_file"):
        p = (BASE_DIR / str(active["template_file"])).resolve()
        if p.exists():
            try:
                return str(p.relative_to(BASE_DIR.resolve()))
            except ValueError:
                pass
    return TEMPLATE_PATH


def save_record(name: str, record: dict):
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    sidecar_path(name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_source_image(record: dict) -> Path | None:
    """Find the original uploaded image, whether it's still in image/, has
    moved to completed/, or was recorded with an explicit relative path."""
    candidates = []
    src = record.get("source_file")
    if src:
        candidates.append(BASE_DIR / src)
    name = record["image_name"]
    candidates.append(COMPLETED_DIR / name)
    candidates.append(IMAGE_DIR / name)
    for c in candidates:
        if c.exists():
            return c
    print(f"[editor] WARNING: could not find original photo for '{name}'. Looked in: "
          f"{', '.join(str(c) for c in candidates)}")
    return None


def safe_media_path(rel_path: str) -> Path:
    p = (BASE_DIR / rel_path).resolve()
    if not any(str(p).startswith(str(root.resolve())) for root in ALLOWED_MEDIA_ROOTS):
        abort(403)
    if not p.exists():
        abort(404)
    return p


# ------------------------------------------------------------------ API ---

@app.route("/")
def index():
    return render_template("editor.html")


@app.route("/api/records")
def api_records():
    records = []
    for sidecar in sorted(RECORD_DIR.glob("*_data.json")):
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        stem = sidecar.stem[:-5]  # strip "_data"
        try:
            st = sidecar.stat()
            created_at = st.st_ctime
            updated_at = st.st_mtime
        except OSError:
            created_at = updated_at = 0.0
        records.append({
            "name": stem,
            "student_name": (record.get("data") or {}).get("student_name", ""),
            "output_url": f"/media/{record.get('output_file', '')}",
            "created_at": created_at,
            "updated_at": updated_at,
        })
    return jsonify(records)


@app.route("/api/record/<name>")
def api_record(name):
    record = load_record(name)
    source = resolve_source_image(record)
    return jsonify({
        "name": name,
        "data": record["data"],
        "layout": record["layout"],
        "template_url": f"/media/{record_template_path(record)}",
        "output_url": f"/media/{record.get('output_file', '')}",
        "source_url": f"/media/{source.relative_to(BASE_DIR)}" if source else None,
        "source_size": _image_size(source) if source else None,
    })


def _image_size(path: Path):
    from PIL import Image
    try:
        with Image.open(path) as im:
            return {"width": im.width, "height": im.height}
    except Exception:
        return None


@app.route("/api/save/<name>", methods=["POST"])
def api_save(name):
    record = load_record(name)
    payload = request.get_json(force=True)

    record["data"] = payload.get("data", record["data"])
    record["layout"] = merge_layout(record["layout"], payload.get("layout"))

    source = resolve_source_image(record)
    im = render_card(
        record["data"], record["layout"],
        template_path=str(Path.cwd() / record_template_path(record)),
        photo_source_path=str(source) if source else None,
    )
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CARD_DIR / f"{name}_filled.png"
    im.save(output_path)
    record["output_file"] = str(output_path.relative_to(BASE_DIR))

    save_record(name, record)
    return jsonify({"ok": True, "output_url": f"/media/{output_path.relative_to(BASE_DIR)}"})


@app.route("/api/delete/<name>", methods=["POST"])
def api_delete(name):
    deleted = []
    for p in (sidecar_path(name), CARD_DIR / f"{name}_filled.png"):
        if p.exists():
            try:
                p.unlink()
                deleted.append(str(p))
            except OSError:
                pass
    if not deleted:
        abort(404, description=f"No record named '{name}'")
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/move/<name>", methods=["POST"])
def api_move(name):
    """Move only the rendered output image (card PNG) into a folder of the
    user's choice (relative to the project root or an absolute path). The
    editable record stays."""
    record = load_record(name)
    payload = request.get_json(silent=True) or {}
    folder = (payload.get("folder") or "").strip().strip("/")
    path = (payload.get("path") or "").strip()
    if not folder and not path:
        abort(400, description="A target folder is required")
    if path:
        dest_dir = Path(path).expanduser().resolve()
    else:
        dest_dir = (BASE_DIR / folder).resolve()
        if not str(dest_dir).startswith(str(BASE_DIR.resolve()) + "/"):
            abort(403, description="Folder must be inside the project directory")
    if not dest_dir.is_dir():
        abort(400, description=f"Destination is not a folder: {dest_dir}")

    src = CARD_DIR / f"{name}_filled.png"
    if not src.exists():
        p = (BASE_DIR / record.get("output_file", "")).resolve()
        if p.exists() and p.parent != dest_dir:
            src = p
        else:
            abort(404, description=f"No rendered image found for '{name}'")

    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))

    # record a path the editor can serve back if possible (inside the project),
    # otherwise keep the absolute location.
    try:
        output_file = str(dest.relative_to(BASE_DIR))
    except ValueError:
        output_file = str(dest)
    record["output_file"] = output_file
    save_record(name, record)
    return jsonify({"ok": True, "output_url": f"/media/{output_file}"})


@app.route("/api/dirs")
def api_dirs():
    """List the folders under a given path (used by the file-manager modal).
    No path = project root. Converts to absolute; returns path + parent."""
    raw = (request.args.get("path") or "").strip()
    p = (Path(raw).expanduser() if raw else BASE_DIR).resolve()
    if not p.is_dir():
        abort(400, description="Not a directory")
    try:
        dirs = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if child.is_dir() and not child.name.startswith("."):
                dirs.append(child.name)
    except PermissionError:
        abort(403, description="Cannot read this folder (permissions)")
    return jsonify({"path": str(p), "parent": str(p.parent), "dirs": dirs})


@app.route("/api/mkdir", methods=["POST"])
def api_mkdir():
    payload = request.get_json(silent=True) or {}
    parent = (payload.get("parent") or "").strip()
    name = (payload.get("name") or "").strip().strip("/")
    if not name or "/" in name:
        abort(400, description="Enter a simple folder name")
    target = (Path(parent).expanduser() / name) if parent else (BASE_DIR / name)
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    return jsonify({"path": str(target)})


@app.route("/media/<path:rel_path>")
def media(rel_path):
    return send_file(safe_media_path(rel_path))


def run_editor(port=5000, open_browser=True):
    OUTPUT_DIR.mkdir(exist_ok=True)
    CARD_DIR.mkdir(exist_ok=True)
    RECORD_DIR.mkdir(exist_ok=True)
    if not list(RECORD_DIR.glob("*_data.json")):
        print(f"No cards found in {RECORD_DIR} yet. Run the scraper first, then come back with --edit.")
        return
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Editor running at {url}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    run_editor()
    