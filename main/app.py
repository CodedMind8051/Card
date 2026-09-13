"""
app.py — Premium Unified UI
Serves dashboard with: API key management, model selection, processing, templates, editor, history
Replaces CLI with browser UI. Keep existing editor/designer mounted.

Run: python main/app.py  or python -m main.app
"""
from __future__ import annotations
import json
import time
import threading
import traceback
from pathlib import Path
from collections import deque

from flask import Flask, jsonify, request, send_file, render_template, abort

try:
    import config_store
except ModuleNotFoundError:
    from main import config_store

BASE_DIR = Path.cwd()
CODE_DIR = Path(__file__).resolve().parent
IMAGE_DIR = BASE_DIR / "image"
STUDENT_IMAGE_DIR = BASE_DIR / "student_images"
OUTPUT_DIR = BASE_DIR / "output"
CARD_DIR = OUTPUT_DIR / "cards"
RECORD_DIR = OUTPUT_DIR / "records"
COMPLETED_DIR = BASE_DIR / "completed"
RETRY_DIR = BASE_DIR / "retry"
TEMP_DIR = BASE_DIR / "temp"
CONFIG_DIR = BASE_DIR / "config"
TEMPLATES_DIR = BASE_DIR / "templates"
HISTORY_DIR = BASE_DIR / "history"

ALLOWED_MEDIA_ROOTS = [BASE_DIR, OUTPUT_DIR, CARD_DIR, RECORD_DIR, COMPLETED_DIR, IMAGE_DIR, TEMP_DIR, TEMPLATES_DIR]

# card_render helpers for editor/designer routes
try:
    from card_render import (
        TEMPLATE_PATH, build_default_layout, merge_layout, render_card, hex_to_rgb,
        load_active_template, ACTIVE_TEMPLATE_FILE, TEMPLATES_DIR as CR_TEMPLATES_DIR,
        list_designed_templates, save_active_template,
    )
except Exception:
    TEMPLATE_PATH = "template.png"
    build_default_layout = merge_layout = render_card = hex_to_rgb = load_active_template = lambda *a, **k: None
    ACTIVE_TEMPLATE_FILE = TEMPLATES_DIR / "active_template.json"
    list_designed_templates = lambda: []
    save_active_template = lambda *a, **k: None

app = Flask(__name__, template_folder=str(CODE_DIR / "templates"), static_folder=str(CODE_DIR / "static"))

# Mount existing editor/designer blueprints if available (lazy import)
try:
    from editor_app import app as editor_flask
    from designer import app as designer_flask
    # we don't mount as blueprint; we replicate their routes into this app later via separate prefixes
    # For simplicity, editor/designer remain accessible via /editor and /designer rendering same templates
except Exception:
    editor_flask = None
    designer_flask = None

# --------------------------- helpers ---------------------------
def safe_media_path(rel: str) -> Path:
    p = (BASE_DIR / rel).resolve()
    if not any(str(p).startswith(str(r.resolve())) for r in ALLOWED_MEDIA_ROOTS if r.exists()):
        # allow if inside BASE_DIR generally
        if not str(p).startswith(str(BASE_DIR.resolve())):
            abort(403)
    if not p.exists():
        abort(404)
    return p

# --------------------------- config / keys API ---------------------------
@app.route("/api/config")
def api_config():
    cfg = config_store.load_config()
    # mask keys
    masked = []
    for k in cfg.get("keys", []):
        masked.append({**k, "masked": config_store.mask_key(k.get("key","")), "key": "***"})
    cfg_masked = {**cfg, "keys": masked}
    return jsonify(cfg_masked)

@app.route("/api/keys", methods=["GET"])
def api_keys_list():
    provider = request.args.get("provider")
    keys = config_store.list_keys(provider)
    active = {}
    for prov in ("gemini", "groq"):
        active[prov] = config_store.get_active_id(prov)
    return jsonify({"keys": keys, "active": active})

@app.route("/api/keys", methods=["POST"])
def api_keys_add():
    data = request.get_json(force=True)
    provider = (data.get("provider") or "gemini").lower()
    key = (data.get("key") or "").strip()
    label = (data.get("label") or "").strip()
    if not key:
        return jsonify({"error": "Key is required"}), 400
    try:
        entry = config_store.add_key(provider, key, label)
        return jsonify({"ok": True, "entry": {**entry, "masked": config_store.mask_key(entry["key"]), "key":"***"}})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/keys/<key_id>", methods=["DELETE"])
def api_keys_delete(key_id):
    ok = config_store.delete_key(key_id)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})

@app.route("/api/keys/<key_id>/activate", methods=["POST"])
def api_keys_activate(key_id):
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    # infer provider if not given
    if not provider:
        cfg = config_store.load_config()
        for k in cfg.get("keys", []):
            if k.get("id")==key_id:
                provider = k.get("provider")
                break
    if not provider:
        return jsonify({"error": "Provider unknown"}), 400
    try:
        config_store.set_active(provider, key_id)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/keys/test", methods=["POST"])
def api_keys_test():
    data = request.get_json(force=True)
    provider = (data.get("provider") or "gemini").lower()
    key = (data.get("key") or "").strip()
    if not key:
        # try active
        key = config_store.get_active_key_value(provider)
        if not key:
            return jsonify({"ok": False, "error": "No key provided and no active key"}), 400
    try:
        if provider == "gemini":
            from google import genai
            client = genai.Client(api_key=key)
            # try list models
            models = list(client.models.list())
            return jsonify({"ok": True, "message": f"Connected — {len(models)} models available", "count": len(models)})
        elif provider == "groq":
            from groq import Groq
            client = Groq(api_key=key)
            models = client.models.list()
            cnt = len(models.data) if hasattr(models, "data") else 0
            return jsonify({"ok": True, "message": f"Connected — {cnt} models", "count": cnt})
        else:
            return jsonify({"ok": True, "message": "Key format looks valid (custom provider)"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:400]}), 400

# --------------------------- models API ---------------------------
@app.route("/api/models", methods=["GET"])
def api_models():
    provider = request.args.get("provider", "gemini")
    last = config_store.get_last_model(provider)
    custom = config_store.get_custom_models(provider)
    known = config_store.get_all_known_models(provider)
    return jsonify({"provider": provider, "last_used": last, "custom": custom, "known": known})

@app.route("/api/models/live", methods=["GET"])
def api_models_live():
    provider = request.args.get("provider", "gemini")
    key = config_store.get_active_key_value(provider)
    if not key:
        # try fallback env/file like cardfiller does
        import os
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not key:
        return jsonify({"error": "No active API key for provider"}), 400
    try:
        if provider == "gemini":
            from google import genai
            client = genai.Client(api_key=key)
            mods = list(client.models.list())
            out = []
            for m in mods:
                name = getattr(m, "name", str(m))
                # name is like models/gemini-2.0-flash
                short = name.split("/")[-1] if "/" in name else name
                out.append({"id": short, "name": short, "raw": name})
            return jsonify({"provider": provider, "models": out[:50]})
        elif provider == "groq":
            from groq import Groq
            client = Groq(api_key=key)
            mods = client.models.list()
            out = [{"id": m.id, "name": m.id} for m in mods.data]
            return jsonify({"provider": provider, "models": out})
        else:
            return jsonify({"models": []})
    except Exception as e:
        return jsonify({"error": str(e)[:500]}), 500

@app.route("/api/models/select", methods=["POST"])
def api_models_select():
    data = request.get_json(force=True)
    provider = (data.get("provider") or "gemini").lower()
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"error": "Model required"}), 400
    config_store.set_last_model(provider, model)
    # also update cardfiller globals if imported
    try:
        import cardfiller
        if provider == "gemini":
            cardfiller.GEMINI_MODEL = model
        elif provider == "groq":
            cardfiller.GROQ_MODEL = model
    except Exception:
        pass
    return jsonify({"ok": True, "last_used": model})

@app.route("/api/models/custom", methods=["POST"])
def api_models_custom_add():
    data = request.get_json(force=True)
    provider = (data.get("provider") or "gemini").lower()
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"error": "Model name required"}), 400
    try:
        config_store.add_custom_model(provider, model)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/models/custom", methods=["DELETE"])
def api_models_custom_delete():
    provider = request.args.get("provider", "gemini")
    model = request.args.get("model", "")
    if not model:
        return jsonify({"error": "Model required"}), 400
    ok = config_store.delete_custom_model(provider, model)
    return jsonify({"ok": ok})

# --------------------------- stats / files ---------------------------
@app.route("/api/stats")
def api_stats():
    def count(p: Path):
        return len([f for f in p.iterdir() if f.is_file()]) if p.exists() else 0
    img_cnt = len([f for f in IMAGE_DIR.glob("*") if f.is_file()]) if IMAGE_DIR.exists() else 0
    stu_cnt = len([f for f in STUDENT_IMAGE_DIR.glob("*") if f.is_file()]) if STUDENT_IMAGE_DIR.exists() else 0
    completed_cnt = len([f for f in COMPLETED_DIR.glob("*") if f.is_file()]) if COMPLETED_DIR.exists() else 0
    # also count nested retry/form
    retry_cnt = 0
    if RETRY_DIR.exists():
        retry_cnt = len([f for f in RETRY_DIR.rglob("*") if f.is_file()])
    cards_cnt = len([f for f in CARD_DIR.glob("*") if f.is_file()]) if CARD_DIR.exists() else 0
    pdf_cnt = 0
    for d in [BASE_DIR / "pdf", BASE_DIR]:
        if d.exists():
            pdf_cnt += len([f for f in d.glob("*.pdf") if f.is_file()])
    cfg = config_store.load_config()
    return jsonify({
        "images_pending": img_cnt,
        "student_pending": stu_cnt,
        "completed": completed_cnt,
        "cards": cards_cnt,
        "retry": retry_cnt,
        "pdfs": pdf_cnt,
        "config_keys": len(cfg.get("keys", [])),
        "templates": len(list(TEMPLATES_DIR.glob("*_template.png"))) if TEMPLATES_DIR.exists() else 0,
        "history": len(list(HISTORY_DIR.glob("*.zip"))) if HISTORY_DIR.exists() else 0,
        "last_models": cfg.get("models", {}),
        "active": cfg.get("active", {}),
    })

@app.route("/api/work")
def api_work():
    """Detailed work state: pending, student_pending, completed, retry, cards"""
    def list_files(p: Path, limit=100):
        if not p.exists():
            return []
        files = sorted([f for f in p.iterdir() if f.is_file()], key=lambda x: x.name)
        out=[]
        for f in files[:limit]:
            try:
                sz = f.stat().st_size
            except Exception:
                sz = 0
            out.append({"name": f.name, "size": sz, "url": f"/media/{f.relative_to(BASE_DIR)}" if f.exists() else None})
        return out
    pending = list_files(IMAGE_DIR)
    student_pending = list_files(STUDENT_IMAGE_DIR)
    completed = list_files(COMPLETED_DIR)
    # also include completed/form and completed/student if they exist
    for sub in [COMPLETED_DIR / "form", COMPLETED_DIR / "student"]:
        completed.extend(list_files(sub, limit=50))
    retry = []
    if RETRY_DIR.exists():
        for f in sorted(RETRY_DIR.rglob("*"), key=lambda x: x.name):
            if f.is_file():
                retry.append({"name": f.name, "path": str(f.relative_to(BASE_DIR)), "url": f"/media/{f.relative_to(BASE_DIR)}"})
                if len(retry)>=50:
                    break
    cards = []
    if CARD_DIR.exists():
        for f in sorted(CARD_DIR.glob("*"), key=lambda x: x.name)[:50]:
            cards.append({"name": f.name, "url": f"/media/{f.relative_to(BASE_DIR)}"})
    records=[]
    if RECORD_DIR.exists():
        for sidecar in sorted(RECORD_DIR.glob("*_data.json"))[:50]:
            try:
                rec=json.loads(sidecar.read_text(encoding="utf-8"))
                records.append({"name": sidecar.stem[:-5], "student_name": (rec.get("data") or {}).get("student_name",""), "output_url": f"/media/{rec.get('output_file','')}"})
            except Exception:
                continue
    return jsonify({"pending": pending, "student_pending": student_pending, "completed": completed, "retry": retry, "cards": cards, "records": records})

@app.route("/api/templates")
def api_templates():
    from card_render import list_designed_templates, ACTIVE_TEMPLATE_FILE
    active = None
    if ACTIVE_TEMPLATE_FILE.exists():
        try:
            active = json.loads(ACTIVE_TEMPLATE_FILE.read_text(encoding="utf-8")).get("name")
        except Exception:
            pass
    return jsonify({
        "templates": list_designed_templates(),
        "active": active,
    })

@app.route("/api/templates/activate", methods=["POST"])
def api_templates_activate():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    from card_render import ACTIVE_TEMPLATE_FILE, TEMPLATES_DIR
    import json as js
    design_path = TEMPLATES_DIR / f"{name}_design.json"
    template_path = TEMPLATES_DIR / f"{name}_template.png"
    if not template_path.exists():
        return jsonify({"error": "template not found"}), 404
    layout = {}
    dummy={}
    if design_path.exists():
        try:
            cfg = js.loads(design_path.read_text(encoding="utf-8"))
            layout = cfg.get("layout", {})
            dummy = cfg.get("dummy_data", {})
        except Exception:
            pass
    from card_render import save_active_template
    save_active_template(name, layout, dummy, template_file=str(template_path))
    return jsonify({"ok": True, "active": name})

@app.route("/api/history")
def api_history():
    if not HISTORY_DIR.exists():
        return jsonify([])
    items=[]
    for p in sorted(HISTORY_DIR.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            items.append({"name": p.stem, "file": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime})
        except Exception:
            items.append({"name": p.stem, "file": p.name})
    return jsonify(items)

@app.route("/api/history/archive", methods=["POST"])
def api_history_archive():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    import re
    name = re.sub(r"[^A-Za-z0-9_\-]+","_", name).strip("_") or "archive"
    try:
        import cardfiller
        ok = cardfiller.archive_current_template(name)
        if not ok:
            return jsonify({"error": "archive already exists — pick another name"}), 400
        # recreate empty workspace dirs so UI can immediately upload next batch
        for p in [IMAGE_DIR, STUDENT_IMAGE_DIR, OUTPUT_DIR, CARD_DIR, RECORD_DIR, COMPLETED_DIR, RETRY_DIR, TEMP_DIR, TEMPLATES_DIR, BASE_DIR / "pdf"]:
            p.mkdir(parents=True, exist_ok=True)
        return jsonify({"ok": True, "name": name})
    except Exception as e:
        return jsonify({"error": str(e)[:400]}), 500

@app.route("/api/history/restore", methods=["POST"])
def api_history_restore():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    save_current = bool(data.get("save_current"))
    save_name = (data.get("save_name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        import cardfiller
        if save_current:
            if not save_name:
                return jsonify({"error": "save_name required when save_current"}), 400
            import re
            save_name = re.sub(r"[^A-Za-z0-9_\-]+","_", save_name).strip("_")
            ok = cardfiller.archive_current_template(save_name)
            if not ok:
                return jsonify({"error": "save_name already exists"}), 400
        ok = cardfiller.restore_archive(name)
        if not ok:
            return jsonify({"error": "restore failed"}), 400
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)[:500]}), 500

@app.route("/api/history/delete", methods=["DELETE"])
def api_history_delete():
    name = request.args.get("name","").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    p = HISTORY_DIR / f"{name}.zip"
    if not p.exists():
        p = HISTORY_DIR / name
        if not p.exists():
            return jsonify({"error": "not found"}), 404
    try:
        p.unlink()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/records")
def api_records():
    if not RECORD_DIR.exists():
        return jsonify([])
    out = []
    for sidecar in sorted(RECORD_DIR.glob("*_data.json")):
        try:
            rec = json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            continue
        stem = sidecar.stem[:-5]
        try:
            st = sidecar.stat()
            created_at = st.st_ctime
            updated_at = st.st_mtime
        except OSError:
            created_at = updated_at = 0.0
        out.append({
            "name": stem,
            "student_name": (rec.get("data") or {}).get("student_name", ""),
            "output_url": f"/media/{rec.get('output_file','')}",
            "data": rec.get("data", {}),
            "created_at": created_at,
            "updated_at": updated_at,
        })
    return jsonify(out)

# ---- Editor compatible routes (fix for dashboard → editor) ----
def _sidecar_path(name: str) -> Path:
    return RECORD_DIR / f"{name}_data.json"

def _load_record(name: str) -> dict:
    path = _sidecar_path(name)
    if not path.exists():
        abort(404, description=f"No record named '{name}'")
    rec = json.loads(path.read_text(encoding="utf-8"))
    # merge with default layout to ensure all fields present
    try:
        rec["layout"] = merge_layout(build_default_layout(), rec.get("layout"))
    except Exception:
        pass
    return rec

def _record_template_path(record: dict) -> str:
    tpl = record.get("template_file")
    if tpl:
        p = (BASE_DIR / tpl).resolve()
        if p.exists():
            try:
                return str(p.relative_to(BASE_DIR.resolve()))
            except ValueError:
                pass
    try:
        active = load_active_template()
        if active and active.get("template_file"):
            p = (BASE_DIR / str(active["template_file"])).resolve()
            if p.exists():
                try:
                    return str(p.relative_to(BASE_DIR.resolve()))
                except ValueError:
                    pass
    except Exception:
        pass
    return TEMPLATE_PATH

def _save_record(name: str, record: dict):
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    _sidecar_path(name).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

def _resolve_source_image(record: dict) -> Path | None:
    candidates=[]
    src=record.get("source_file")
    if src:
        candidates.append(BASE_DIR / src)
    name=record["image_name"]
    candidates.extend([COMPLETED_DIR / name, COMPLETED_DIR / "form" / name, COMPLETED_DIR / "student" / name, IMAGE_DIR / name])
    for c in candidates:
        if c.exists():
            return c
    return None

def _image_size(path: Path):
    from PIL import Image
    try:
        with Image.open(path) as im:
            return {"width": im.width, "height": im.height}
    except Exception:
        return None

@app.route("/api/record/<name>")
def api_record(name):
    record = _load_record(name)
    source = _resolve_source_image(record)
    return jsonify({
        "name": name,
        "data": record["data"],
        "layout": record["layout"],
        "template_url": f"/media/{_record_template_path(record)}",
        "output_url": f"/media/{record.get('output_file','')}",
        "source_url": f"/media/{source.relative_to(BASE_DIR)}" if source else None,
        "source_size": _image_size(source) if source else None,
    })

@app.route("/api/save/<name>", methods=["POST"])
def api_save(name):
    # Branch: if this is a designer template save (has dummy or template file exists and payload has layout without data)
    payload = request.get_json(force=True, silent=True) or {}
    # Designer payload contains 'dummy' and no 'data' with student fields, or template file exists
    is_designer = False
    tpl_path = TEMPLATES_DIR / f"{name}_template.png"
    if tpl_path.exists() and ("dummy" in payload or "layout" in payload and "data" not in payload):
        # treat as designer save
        layout=_sanitize_layout(payload.get("layout") or {})
        dummy=payload.get("dummy") or {}
        for k in ["school_name","student_name","father_name","mother_name","class","section","roll_number","mobile_number","dob","address"]:
            layout["texts"].setdefault(k,{})
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        (TEMPLATES_DIR / f"{name}_design.json").write_text(json.dumps({"layout":layout,"dummy_data":dummy}, ensure_ascii=False, indent=2), encoding="utf-8")
        save_active_template(name, layout, dummy, template_file=str(tpl_path))
        return jsonify({"ok":True,"active_template_file":str(ACTIVE_TEMPLATE_FILE),"preview_url":f"/media/preview/{name}"})
    # Otherwise editor save
    try:
        record = _load_record(name)
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 404
    record["data"] = payload.get("data", record["data"])
    try:
        record["layout"] = merge_layout(record["layout"], payload.get("layout"))
    except Exception:
        record["layout"] = payload.get("layout", record["layout"])
    enhance_photo = bool(payload.get("enhance_photo", False))
    source = _resolve_source_image(record)
    try:
        im = render_card(
            record["data"], record["layout"],
            template_path=str(Path.cwd() / _record_template_path(record)),
            photo_source_path=str(source) if source else None,
            enhance_photo=enhance_photo,
        )
    except Exception as e:
        return jsonify({"error": str(e)[:400]}), 500
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CARD_DIR / f"{name}_filled.png"
    im.save(output_path)
    record["output_file"] = str(output_path.relative_to(BASE_DIR))
    _save_record(name, record)
    return jsonify({"ok": True, "output_url": f"/media/{output_path.relative_to(BASE_DIR)}"})

@app.route("/api/upload_photo/<name>", methods=["POST"])
def api_upload_photo(name):
    record = _load_record(name)
    f = request.files.get("photo")
    if not f or not f.filename:
        abort(400, description="No photo file was uploaded")
    from PIL import Image
    from werkzeug.utils import secure_filename
    image_dir = TEMP_DIR / "uploads"
    image_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(secure_filename(f.filename)).suffix.lower() or ".jpg"
    target = image_dir / f"{name}_photo{ext}"
    f.save(str(target))
    try:
        with Image.open(target) as im:
            width, height = im.size
    except Exception:
        abort(400, description="Uploaded file is not a readable image")
    rel = str(target.relative_to(BASE_DIR))
    record["source_file"] = rel
    record["layout"]["photo"]["crop"] = {"x": 0, "y": 0, "w": width, "h": height}
    record["layout"]["photo"]["rotation"] = 0
    _save_record(name, record)
    return jsonify({"ok": True, "url": f"/media/{rel}", "width": width, "height": height})

@app.route("/api/delete/<name>", methods=["POST"])
def api_delete(name):
    import shutil
    deleted=[]
    for p in (_sidecar_path(name), CARD_DIR / f"{name}_filled.png"):
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
    import shutil
    record = _load_record(name)
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
        p = (BASE_DIR / record.get("output_file","")).resolve()
        if p.exists() and p.parent != dest_dir:
            src = p
        else:
            abort(404, description=f"No rendered image found for '{name}'")
    dest = dest_dir / src.name
    shutil.move(str(src), str(dest))
    try:
        output_file = str(dest.relative_to(BASE_DIR))
    except ValueError:
        output_file = str(dest)
    record["output_file"] = output_file
    _save_record(name, record)
    return jsonify({"ok": True, "output_url": f"/media/{output_file}"})

@app.route("/api/dirs")
def api_dirs():
    raw = (request.args.get("path") or "").strip()
    p = (Path(raw).expanduser() if raw else BASE_DIR).resolve()
    if not p.is_dir():
        abort(400, description="Not a directory")
    try:
        dirs=[]
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

# ---- Designer compatible routes ----
@app.route("/api/designs")
def api_designs():
    try:
        active_name=None
        if ACTIVE_TEMPLATE_FILE.exists():
            try:
                active_name=json.loads(ACTIVE_TEMPLATE_FILE.read_text(encoding="utf-8")).get("name")
            except Exception:
                pass
        return jsonify([
            {"name": t["name"], "thumb_url": f"/media/{t['template_file']}", "active": t["name"]==active_name}
            for t in list_designed_templates()
        ])
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 500

@app.route("/api/start", methods=["POST"])
def api_designer_start():
    if "template" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["template"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".png",".jpg",".jpeg"):
        return jsonify({"error": f"Unsupported type '{suffix}'"}), 400
    import re
    stem = Path(file.filename).stem.replace("_template","")
    name = re.sub(r"[^A-Za-z0-9_\-]+","_", stem).strip("_") or "template"
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    dest = TEMPLATES_DIR / f"{name}_template.png"
    file.save(str(dest))
    return jsonify({"ok": True, "name": name})

def _sanitize_layout(layout: dict) -> dict:
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
    out={"photo":{}, "texts":{}}
    photo=layout.get("photo") or {}
    for key in ("x","y","width","height","corner_radius"):
        if key in photo:
            out["photo"][key]=num(photo[key])
    out["photo"]["rotation"]=num(photo.get("rotation",0))
    if photo.get("crop"):
        out["photo"]["crop"]=photo["crop"]
    for key, spec in (layout.get("texts") or {}).items():
        s=dict(spec or {})
        for k in ("x","y","width","height","font_size","min_font_size"):
            if k in s:
                s[k]=num(s[k])
        out["texts"][key]=s
    return out

def _default_layout_for(w:int,h:int)->dict:
    FIELD_DEFS=[
        {"key":"school_name","label":"School name"},
        {"key":"student_name","label":"Student name","uppercase":True},
        {"key":"father_name","label":"Father's name"},
        {"key":"mother_name","label":"Mother's name"},
        {"key":"class","label":"Class"},
        {"key":"section","label":"Section","prefix":"Sec- "},
        {"key":"roll_number","label":"Roll number"},
        {"key":"mobile_number","label":"Mobile number"},
        {"key":"dob","label":"Date of birth"},
        {"key":"address","label":"Address","multiline":True},
    ]
    text_h=int(h*0.06); y=int(h*0.05); texts={}
    for fd in FIELD_DEFS:
        spec={"x":int(w*0.46),"y":y,"width":int(w*0.5),"font_size":max(20,int(h*0.028)),"min_font_size":14,"color":"#D81616","bold":True,"align":"left","multiline":bool(fd.get("multiline")),"uppercase":bool(fd.get("uppercase"))}
        if fd.get("prefix"):
            spec["prefix"]=fd["prefix"]
        if fd.get("multiline"):
            spec["height"]=int(h*0.12)
        texts[fd["key"]]=spec
        y+=int(text_h*1.15)
    return {"photo":{"x":int(w*0.08),"y":int(h*0.05),"width":int(w*0.26),"height":int(h*0.4),"corner_radius":20,"rotation":0},"texts":texts}

def _load_starting_state(name:str):
    cfg_path=TEMPLATES_DIR / f"{name}_design.json"
    layout=None; dummy=None
    if cfg_path.exists():
        try:
            cfg=json.loads(cfg_path.read_text(encoding="utf-8"))
            layout=cfg.get("layout"); dummy=cfg.get("dummy_data")
        except Exception:
            pass
    if layout is None:
        from PIL import Image
        with Image.open(TEMPLATES_DIR / f"{name}_template.png") as im:
            layout=_default_layout_for(im.width, im.height)
    if dummy is None:
        dummy={"school_name":"SCHOOL NAME","student_name":"STUDENT NAME","father_name":"FATHER NAME","mother_name":"MOTHER NAME","class":"CLASS","section":"SEC","roll_number":"ROLL","mobile_number":"+91XXXXXXXXXX","dob":"XX/XX/XXXX","address":"ADDRESS"}
    return layout, dummy

@app.route("/api/state/<name>")
def api_designer_state(name:str):
    tpl_path=TEMPLATES_DIR / f"{name}_template.png"
    if not tpl_path.exists():
        abort(404, description=f"No template named '{name}'")
    from PIL import Image
    with Image.open(tpl_path) as im:
        w,h=im.width, im.height
    layout, dummy=_load_starting_state(name)
    return jsonify({"name":name,"template_url":f"/media/{tpl_path.relative_to(BASE_DIR)}","image_w":w,"image_h":h,"layout":layout,"dummy":dummy,"active": ACTIVE_TEMPLATE_FILE.exists() and ACTIVE_TEMPLATE_FILE.read_text(encoding="utf-8")!="","fields":[
        {"key":"school_name","label":"School name"},
        {"key":"student_name","label":"Student name","uppercase":True},
        {"key":"father_name","label":"Father's name"},
        {"key":"mother_name","label":"Mother's name"},
        {"key":"class","label":"Class"},
        {"key":"section","label":"Section","prefix":"Sec- "},
        {"key":"roll_number","label":"Roll number"},
        {"key":"mobile_number","label":"Mobile number"},
        {"key":"dob","label":"Date of birth"},
        {"key":"address","label":"Address","multiline":True},
    ]})

@app.route("/api/save_design/<name>", methods=["POST"])
def api_designer_save(name:str):
    # use distinct endpoint to avoid conflict with editor's /api/save/<name>
    tpl_path=TEMPLATES_DIR / f"{name}_template.png"
    if not tpl_path.exists():
        abort(404, description=f"No template named '{name}'")
    payload=request.get_json(force=True)
    layout=_sanitize_layout(payload.get("layout") or {})
    dummy=payload.get("dummy") or {}
    FIELD_DEFS_KEYS=["school_name","student_name","father_name","mother_name","class","section","roll_number","mobile_number","dob","address"]
    for k in FIELD_DEFS_KEYS:
        layout["texts"].setdefault(k,{})
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    (TEMPLATES_DIR / f"{name}_design.json").write_text(json.dumps({"layout":layout,"dummy_data":dummy}, ensure_ascii=False, indent=2), encoding="utf-8")
    save_active_template(name, layout, dummy, template_file=str(tpl_path))
    return jsonify({"ok":True,"active_template_file":str(ACTIVE_TEMPLATE_FILE),"preview_url":f"/media/preview/{name}"})

@app.route("/media/preview/<name>")
def media_preview(name:str):
    layout, dummy=_load_starting_state(name)
    layout["photo"]["crop"]={"x":0,"y":0,"w":1,"h":1}
    im=render_card(dummy, layout, template_path=str(TEMPLATES_DIR / f"{name}_template.png"), photo_source_path=None)
    from io import BytesIO
    buf=BytesIO(); im.save(buf,"PNG"); buf.seek(0)
    return send_file(buf, mimetype="image/png")

@app.route("/api/pdf/list")
def api_pdf_list():
    pdfs=[]
    for folder in [BASE_DIR / "pdf", BASE_DIR, OUTPUT_DIR]:
        if not folder.exists():
            continue
        for f in sorted(folder.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True):
            pdfs.append({"name": f.name, "path": str(f.relative_to(BASE_DIR)), "url": f"/media/{f.relative_to(BASE_DIR)}", "size": f.stat().st_size, "mtime": f.stat().st_mtime})
    # also check history/pdfs?
    return jsonify(pdfs)

@app.route("/api/pdf/make", methods=["POST"])
def api_pdf_make():
    data = request.get_json(silent=True) or {}
    enhance = bool(data.get("enhance"))
    cols = int(data.get("cols") or 5)
    rows = int(data.get("rows") or 2)
    output = (data.get("output") or "output.pdf").strip()
    # sanitize output
    if "/" in output or "\\" in output:
        output = Path(output).name
    if not output.lower().endswith(".pdf"):
        output += ".pdf"
    try:
        from PIL import Image, ImageEnhance, ImageFilter
        import os
        INPUT_FOLDER = str(CARD_DIR)
        OUTPUT_PDF = str(BASE_DIR / output) if not output.startswith("pdf/") else str(BASE_DIR / output)
        # ensure pdf folder
        if "/" in output:
            Path(OUTPUT_PDF).parent.mkdir(parents=True, exist_ok=True)
        PAGE_W, PAGE_H = 3508, 2480
        PAGE_MARGIN_X, PAGE_MARGIN_Y = 54, 120
        IMAGE_SPACING_X, IMAGE_SPACING_Y = 40, 160
        IMAGES_PER_PAGE = cols * rows
        img_w = (PAGE_W - 2*PAGE_MARGIN_X - (cols-1)*IMAGE_SPACING_X)//cols
        img_h = (PAGE_H - 2*PAGE_MARGIN_Y - (rows-1)*IMAGE_SPACING_Y)//rows
        if hasattr(Image, "Resampling"):
            RESAMPLE = Image.Resampling.LANCZOS
        else:
            RESAMPLE = Image.LANCZOS
        files = sorted([os.path.join(INPUT_FOLDER, f) for f in os.listdir(INPUT_FOLDER) if f.lower().endswith((".png",".jpg",".jpeg"))], reverse=True) if os.path.isdir(INPUT_FOLDER) else []
        if not files:
            return jsonify({"error": f"No images in {INPUT_FOLDER}"}), 400
        pages=[]
        for start in range(0, len(files), IMAGES_PER_PAGE):
            canvas = Image.new("RGB", (PAGE_W, PAGE_H), "white")
            batch = files[start:start+IMAGES_PER_PAGE]
            for i, path in enumerate(batch):
                img = Image.open(path).convert("RGB").resize((img_w, img_h), RESAMPLE)
                if enhance:
                    img = ImageEnhance.Contrast(img).enhance(1.08)
                    img = ImageEnhance.Color(img).enhance(1.03)
                    img = ImageEnhance.Sharpness(img).enhance(2.8)
                    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=220, threshold=3))
                row, col = divmod(i, cols)
                x = PAGE_MARGIN_X + col*(img_w+IMAGE_SPACING_X)
                y = PAGE_MARGIN_Y + row*(img_h+IMAGE_SPACING_Y)
                canvas.paste(img, (x,y))
            pages.append(canvas)
        if pages:
            pages[0].save(OUTPUT_PDF, save_all=True, append_images=pages[1:], resolution=300)
        return jsonify({"ok": True, "output": output, "url": f"/media/{output}", "images": len(files), "pages": len(pages)})
    except Exception as e:
        return jsonify({"error": str(e)[:600]}), 500

@app.route("/api/pdf/delete", methods=["DELETE"])
def api_pdf_delete():
    name = request.args.get("name","").strip()
    if not name:
        try:
            name = (request.get_json(force=True) or {}).get("name","").strip()
        except Exception:
            pass
    if not name:
        return jsonify({"error": "name required"}), 400
    # sanitize: only allow pdf files inside allowed roots
    # try multiple locations
    candidates = [BASE_DIR / name, BASE_DIR / "pdf" / name, BASE_DIR / "pdf" / Path(name).name, OUTPUT_DIR / name]
    # also handle if name is a path like pdf/xxx.pdf
    if "/" in name or "\\" in name:
        candidates.append(BASE_DIR / Path(name))
    deleted=None
    for p in candidates:
        try:
            rp = p.resolve()
            if not str(rp).startswith(str(BASE_DIR.resolve())):
                continue
            if rp.exists() and rp.is_file() and rp.suffix.lower()==".pdf":
                rp.unlink()
                deleted=str(rp.relative_to(BASE_DIR))
                break
        except Exception:
            continue
    if not deleted:
        return jsonify({"error": "pdf not found"}), 404
    return jsonify({"ok": True, "deleted": deleted})

# --------------------------- upload images ---------------------------
@app.route("/api/upload", methods=["POST"])
def api_upload():
    files = request.files.getlist("images") or request.files.getlist("file")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400
    target = request.args.get("target","").strip()
    dest_dir = STUDENT_IMAGE_DIR if target in ("student","student_images") else IMAGE_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        if not f.filename:
            continue
        from werkzeug.utils import secure_filename
        name = secure_filename(f.filename)
        if not name:
            continue
        dest = dest_dir / name
        if dest.exists():
            stem = dest.stem
            suf = dest.suffix
            i = 1
            while (dest_dir / f"{stem}_{i}{suf}").exists():
                i += 1
            dest = dest_dir / f"{stem}_{i}{suf}"
        f.save(str(dest))
        saved.append(name)
    return jsonify({"ok": True, "saved": saved, "count": len(saved), "target": str(dest_dir.relative_to(BASE_DIR))})

@app.route("/api/upload/template", methods=["POST"])
def api_upload_template():
    if "template" not in request.files:
        return jsonify({"error": "No template file"}), 400
    file = request.files["template"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    import re, shutil
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".png",".jpg",".jpeg"):
        return jsonify({"error": f"Unsupported {suffix}"}), 400
    stem = Path(file.filename).stem.replace("_template","")
    name = re.sub(r"[^A-Za-z0-9_\-]+","_",stem).strip("_") or "template"
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    dest = TEMPLATES_DIR / f"{name}_template.png"
    file.save(str(dest))
    return jsonify({"ok": True, "name": name})

# --------------------------- process manager ---------------------------
process_state = {
    "running": False,
    "thread": None,
    "log": deque(maxlen=500),
    "progress": {"done":0,"total":0,"failed":0,"current":""},
    "error": None,
    "start_time": None,
    "stop_requested": False,
}

def _log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    process_state["log"].append(line)
    # also to run_log.txt
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        with open(TEMP_DIR / "run_log.txt", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def _process_worker(enhance_photo=False, external_student_image=False):
    process_state["running"] = True
    process_state["stop_requested"] = False
    process_state["error"] = None
    process_state["start_time"] = time.time()
    # clear any previous stop signal
    try:
        (TEMP_DIR / "stop_requested").unlink(missing_ok=True)
    except Exception:
        pass
    try:
        import cardfiller
        cardfiller.clear_stop()
    except Exception:
        pass
    try:
        # patch cardfiller to use config_store keys/models
        # (cardfiller already imported above, reuse it)
        # override model from config
        gem_last = config_store.get_last_model("gemini")
        groq_last = config_store.get_last_model("groq")
        if gem_last:
            cardfiller.GEMINI_MODEL = gem_last
        if groq_last:
            cardfiller.GROQ_MODEL = groq_last
        # monkey patch get_gemini_client / get_groq_client to use config_store active keys
        orig_gem = cardfiller.get_gemini_client
        orig_groq = cardfiller.get_groq_client
        def patched_gem():
            k = config_store.get_active_key_value("gemini")
            if k:
                import os
                os.environ["GEMINI_API_KEY"] = k
                # need to reset client if exists? clear cached
                cardfiller._gemini_client = None
            return orig_gem()
        def patched_groq():
            k = config_store.get_active_key_value("groq")
            if k:
                import os
                os.environ["GROQ_API_KEY"] = k
                cardfiller._groq_client = None
            return orig_groq()
        cardfiller.get_gemini_client = patched_gem
        cardfiller.get_groq_client = patched_groq

        # hook progress: wrap process_image_pass to emit logs
        # Just call scrape_main but capture prints via _log
        _log(f"Starting batch: enhance={enhance_photo} external={external_student_image} model={cardfiller.GEMINI_MODEL}")
        # call scrape_main but in a way we can intercept progress
        # We'll monkey patch print to log
        import builtins
        orig_print = builtins.print
        def log_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            _log(msg)
            orig_print(*args, **kwargs)
        builtins.print = log_print
        try:
            cardfiller.scrape_main(enhance_photo=enhance_photo, external_student_image=external_student_image)
        finally:
            builtins.print = orig_print
        if process_state.get("stop_requested"):
            _log("■ Batch stopped by user")
        else:
            _log("Batch finished")
    except Exception as e:
        _log(f"ERROR: {e}")
        _log(traceback.format_exc())
        process_state["error"] = str(e)
    finally:
        process_state["running"] = False
        process_state["thread"] = None
        # clear stop signal for next run
        try:
            import cardfiller as _cfc
            _cfc.clear_stop()
        except Exception:
            pass
        try:
            (TEMP_DIR / "stop_requested").unlink(missing_ok=True)
        except Exception:
            pass
        process_state["stop_requested"] = False

@app.route("/api/process/start", methods=["POST"])
def api_process_start():
    if process_state["running"]:
        return jsonify({"error": "Already running"}), 400
    data = request.get_json(silent=True) or {}
    enhance = bool(data.get("enhance_photo"))
    external = bool(data.get("external_student_image"))
    # clear log
    process_state["log"].clear()
    process_state["progress"] = {"done":0,"total":0,"failed":0,"current":""}
    th = threading.Thread(target=_process_worker, args=(enhance, external), daemon=True)
    process_state["thread"] = th
    th.start()
    return jsonify({"ok": True})

@app.route("/api/process/status")
def api_process_status():
    out = {
        "running": process_state["running"],
        "error": process_state["error"],
        "start_time": process_state["start_time"],
        "log_tail": list(process_state["log"])[-80:],
    }
    # stats for progress
    try:
        from pathlib import Path as P
        total_pending = len(list(IMAGE_DIR.glob("*"))) if IMAGE_DIR.exists() else 0
        completed = len(list(COMPLETED_DIR.glob("*"))) if COMPLETED_DIR.exists() else 0
        cards = len(list(CARD_DIR.glob("*"))) if CARD_DIR.exists() else 0
        out["stats"] = {"pending": total_pending, "completed": completed, "cards": cards}
    except Exception:
        pass
    return jsonify(out)

@app.route("/api/process/log")
def api_process_log():
    return jsonify({"log": list(process_state["log"])})

@app.route("/api/process/stop", methods=["POST"])
def api_process_stop():
    if not process_state["running"]:
        return jsonify({"error": "Not running"}), 400
    process_state["stop_requested"] = True
    # signal cardfiller loop to stop between images
    try:
        import cardfiller
        cardfiller.request_stop()
    except Exception:
        pass
    try:
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        (TEMP_DIR / "stop_requested").write_text(str(time.time()))
    except Exception:
        pass
    _log("■ Stop requested by user — will stop after current image")
    return jsonify({"ok": True, "message": "Stop requested — will stop after current image (max ~30s)"})

# --------------------------- media ---------------------------
@app.route("/media/<path:rel_path>")
def media(rel_path):
    return send_file(safe_media_path(rel_path))

# --------------------------- pages ---------------------------
@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/editor")
def editor_page():
    return render_template("editor.html")

@app.route("/designer")
def designer_page():
    return render_template("designer.html")

def run_app(port=5000, open_browser=True):
    import webbrowser, threading
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Premium UI running at {url}  (Ctrl+C to stop)")
    app.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    run_app()
