"""
config_store.py — persistent storage for API keys, active selections, last used models
Single JSON file: config/app_config.json (fallback temp/app_config.json)
Used by both the UI and cardfiller fallback path.
"""
from __future__ import annotations
import json
import uuid
import time
from pathlib import Path
from datetime import datetime, timezone

def _base_dir() -> Path:
    # When run as main.app, cwd is project root; when imported, use project root two levels up if needed
    cwd = Path.cwd()
    # if cwd is .../main, go up one
    if cwd.name == "main" and (cwd.parent / "config").exists():
        return cwd.parent
    if (cwd / "main" / "app.py").exists():
        return cwd
    # fallback to file location
    try:
        return Path(__file__).resolve().parents[1]
    except Exception:
        return cwd

BASE_DIR = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "app_config.json"
FALLBACK_CONFIG = BASE_DIR / "temp" / "app_config.json"

DEFAULT_CONFIG = {
    "keys": [],  # list of {id, provider, label, key, created_at, last_used}
    "active": {},  # provider -> id
    "models": {
        "gemini": {"last_used": "gemini-flash-latest", "custom": []},
        "groq": {"last_used": "qwen/qwen3.6-27b", "custom": []},
    },
    "settings": {"theme": "dark"},
}

def _config_path() -> Path:
    if CONFIG_FILE.exists():
        return CONFIG_FILE
    if FALLBACK_CONFIG.exists():
        return FALLBACK_CONFIG
    return CONFIG_FILE

def load_config() -> dict:
    p = _config_path()
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # migrate defaults
        for k, v in DEFAULT_CONFIG.items():
            if k not in data:
                data[k] = v
        if "models" not in data:
            data["models"] = DEFAULT_CONFIG["models"]
        for prov in ("gemini", "groq"):
            if prov not in data["models"]:
                data["models"][prov] = {"last_used": DEFAULT_CONFIG["models"][prov]["last_used"], "custom": []}
        return data
    except Exception:
        return json.loads(json.dumps(DEFAULT_CONFIG))

def save_config(cfg: dict):
    p = CONFIG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    # also mirror to temp for cardfiller fallback that may look there
    try:
        FALLBACK_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        FALLBACK_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "•" * (len(key) - 8) + key[-4:]

def list_keys(provider: str | None = None) -> list[dict]:
    cfg = load_config()
    keys = cfg.get("keys", [])
    if provider:
        keys = [k for k in keys if k.get("provider") == provider]
    # add masked view
    out = []
    for k in keys:
        out.append({**k, "masked": mask_key(k.get("key", "")), "key": "***" })  # never leak raw
    return out

def list_keys_raw(provider: str | None = None) -> list[dict]:
    cfg = load_config()
    keys = cfg.get("keys", [])
    if provider:
        keys = [k for k in keys if k.get("provider") == provider]
    return keys

def get_active_id(provider: str) -> str | None:
    cfg = load_config()
    return cfg.get("active", {}).get(provider)

def get_active_key(provider: str) -> dict | None:
    cfg = load_config()
    aid = cfg.get("active", {}).get(provider)
    if not aid:
        # fallback to first key of that provider
        for k in cfg.get("keys", []):
            if k.get("provider") == provider:
                return k
        return None
    for k in cfg.get("keys", []):
        if k.get("id") == aid:
            return k
    return None

def get_active_key_value(provider: str) -> str | None:
    k = get_active_key(provider)
    return k.get("key") if k else None

def add_key(provider: str, key: str, label: str = "") -> dict:
    provider = provider.lower().strip()
    if provider not in ("gemini", "groq", "openai", "custom"):
        provider = "custom"
    if not key or len(key.strip()) < 6:
        raise ValueError("API key too short")
    cfg = load_config()
    # dedupe by key value
    for existing in cfg["keys"]:
        if existing.get("key") == key.strip():
            raise ValueError("This key already exists")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "provider": provider,
        "label": label.strip() or f"{provider} {len([k for k in cfg['keys'] if k['provider']==provider])+1}",
        "key": key.strip(),
        "created_at": _now_iso(),
        "last_used": None,
    }
    cfg["keys"].append(entry)
    # auto-activate if none active for this provider
    if provider not in cfg.get("active", {}) or not cfg["active"][provider]:
        cfg.setdefault("active", {})[provider] = entry["id"]
    save_config(cfg)
    return entry

def delete_key(key_id: str) -> bool:
    cfg = load_config()
    before = len(cfg["keys"])
    cfg["keys"] = [k for k in cfg["keys"] if k.get("id") != key_id]
    # clean active refs
    for prov, aid in list(cfg.get("active", {}).items()):
        if aid == key_id:
            del cfg["active"][prov]
            # auto-select next of same provider if exists
            remain = [k for k in cfg["keys"] if k.get("provider") == prov]
            if remain:
                cfg["active"][prov] = remain[0]["id"]
    save_config(cfg)
    return len(cfg["keys"]) != before

def set_active(provider: str, key_id: str):
    cfg = load_config()
    if not any(k.get("id") == key_id and k.get("provider")==provider for k in cfg["keys"]):
        # allow provider mismatch if custom provider
        if not any(k.get("id")==key_id for k in cfg["keys"]):
            raise ValueError("Key not found")
    cfg.setdefault("active", {})[provider] = key_id
    # update last_used
    for k in cfg["keys"]:
        if k.get("id")==key_id:
            k["last_used"] = _now_iso()
    save_config(cfg)

def touch_key(key_id: str):
    cfg = load_config()
    for k in cfg["keys"]:
        if k.get("id")==key_id:
            k["last_used"] = _now_iso()
    save_config(cfg)

# ---- models ----
def get_last_model(provider: str) -> str | None:
    cfg = load_config()
    return cfg.get("models", {}).get(provider, {}).get("last_used")

def set_last_model(provider: str, model: str):
    cfg = load_config()
    cfg.setdefault("models", {}).setdefault(provider, {"last_used": model, "custom": []})
    cfg["models"][provider]["last_used"] = model
    save_config(cfg)

def get_custom_models(provider: str) -> list[str]:
    cfg = load_config()
    return cfg.get("models", {}).get(provider, {}).get("custom", [])

def add_custom_model(provider: str, model: str):
    model = model.strip()
    if not model:
        raise ValueError("Model name empty")
    cfg = load_config()
    cfg.setdefault("models", {}).setdefault(provider, {"last_used": model, "custom": []})
    if model not in cfg["models"][provider]["custom"]:
        cfg["models"][provider]["custom"].append(model)
    cfg["models"][provider]["last_used"] = model
    save_config(cfg)

def delete_custom_model(provider: str, model: str) -> bool:
    cfg = load_config()
    lst = cfg.get("models", {}).get(provider, {}).get("custom", [])
    if model in lst:
        lst.remove(model)
        save_config(cfg)
        return True
    return False

def get_all_known_models(provider: str) -> list[str]:
    """Combine built-ins + custom + last_used deduped"""
    builtin = {
        "gemini": ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"],
        "groq": ["qwen/qwen3-32b", "qwen/qwen3-27b", "qwen/qwen3.6-27b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it"],
    }.get(provider, [])
    cfg = load_config()
    custom = cfg.get("models", {}).get(provider, {}).get("custom", [])
    last = cfg.get("models", {}).get(provider, {}).get("last_used")
    combined = []
    seen = set()
    for m in ([last] if last else []) + custom + builtin:
        if m and m not in seen:
            seen.add(m)
            combined.append(m)
    return combined
