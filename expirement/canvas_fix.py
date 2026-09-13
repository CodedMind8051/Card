"""
Drop this ONE file into your project root (next to temp.py).

Then add ONE line at the top of your main file - no other code changes.

It fixes Google Lens static noise on STOCK `pip install invisible_playwright` too.
"""
import invisible_core.prefs as _prefs
import invisible_playwright._session as _sess

# patch core defaults
for k in ["zoom.stealth.canvas.substitute_pixels", "zoom.stealth.webgl.substitute_pixels"]:
    if hasattr(_prefs, "DEFAULT_PREFS") and k in _prefs.DEFAULT_PREFS:
        _prefs.DEFAULT_PREFS[k] = False

# patch wrapper so old core=True is still overridden
_orig = _sess.build_prefs
def _fixed(*a, **kw):
    extra = kw.get("extra_prefs") or {}
    kw["extra_prefs"] = {
        "zoom.stealth.canvas.substitute_pixels": False,
        "zoom.stealth.webgl.substitute_pixels": False,
        **extra
    }
    return _orig(*a, **kw)
_sess.build_prefs = _fixed
