# Student ID Card Automation

Automatically extracts handwritten student details from scanned admission forms using **Google Lens AI Mode**, then renders a filled school ID card (photo + all fields) into PNGs and optionally an A4 PDF.

The photo box, every text field, and even the text **colour gradient** are fully configurable in a browser-based designer — no code edits needed.

---

# Features

- Extract handwritten information (Hindi & English)
- Converts Hindi text to English
- Auto-detects, crops, rotates and enhances the student photograph
- Browser template **designer** to lay out the photo box and text fields
- **Multi-colour text** — up to **4 colour stops**, each with a position %, along a **horizontal or vertical** axis (a real gradient inside the letters)
- Auto-fits long names and addresses
- Handles network interruptions, automatic retry, CAPTCHA waiting
- Progress bar, logging
- **Archive / restore** whole batches (`--mark-old` / `--given-name` / `--current`)
- Combine finished cards into an **A4 PDF**

---

# Folder Structure

```
student-id-card/
│
├── main/
│   ├── cardfiller.py        # CLI entry point (scrape / fill / edit / design)
│   ├── card_render.py       # shared rendering (photos, text, gradients)
│   ├── designer.py          # browser template designer (--new)
│   └── editor_app.py        # browser photo/field editor (--edit)
├── image/                   # put scanned admission forms here
├── output/
│   ├── cards/               # rendered ID card PNGs
│   └── records/             # editable *_data.json sidecar files
├── completed/              # originals moved here after successful processing
├── retry/                   # failed images, retried automatically
├── temp/                    # screenshots + run_log.txt
├── templates/               # designed templates (: template_name + *_design.json)
├── history/                 # archived batch zips
├── template.png              # default/fallback template
├── pdf_maker.py             # combine cards into an A4 PDF
├── setup.sh                  # one-command installer
├── requirements.txt
└── README.md
```

---

# Setup (recommended)

The included installer does everything: system packages, Python venv, dependencies, Playwright Firefox, and workspace folders.

```bash
cd student-id-card
bash setup.sh

# Different distro / already have system libs:
bash setup.sh --skip-system-deps
```

`setup.sh` is idempotent — run it again to repair or complete an install.

### Manual (or if you prefer)

```bash
# 1. System packages (Debian/Ubuntu/Kali)
sudo apt update
sudo apt install python3-venv python3-pip fonts-dejavu-core \
     libgl1 libglib2.0-0 libsm6 libxext6 libxrender-dev

# 2. Python virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Python packages
pip install -r requirements.txt

# 4. Playwright browser
playwright install firefox
playwright install-deps firefox
```

> Requirements are deliberately **playwright-free torch/torchvision** — photo enhancement uses only OpenCV, so you avoid the large PyTorch download.

---

# Firefox profile / Google login

The scraper drives a **persistent Firefox profile** so it can reuse your logged-in Google session. Find the path, then point the script at it.

```bash
ls ~/.mozilla/firefox      # e.g.  xxxxxxxx.default-esr
```

Connect `user_data_dir` inside `main/cardfiller.py`:

```python
context = p.firefox.launch_persistent_context(
    user_data_dir="/home/YOURUSER/.mozilla/firefox/xxxxxxxx.default-esr",
    ...
)
```

Open Firefox once, **log in to Google**, then close it. Now run the tool.

---

# Usage

## 1. Put your forms in `image/`

Supported: `jpg`, `jpeg`, `png`.

## 2. Design the card template (optional but recommended)

```bash
python main/cardfiller.py --new [image_or_folder] [--port PORT]
```

A browser opens. You can:

- drag / resize the **photo box** and every **text field**
- set font size, **bold**, alignment, single colour, or a **4-stop gradient**:
  - enable up to **4 colour boxes**, each with a colour + a position %
  - pick **→ (horizontal)** or **↕ (vertical)** for the blend
  - type dummy student data and press **Preview** to see the final card
- press **Save template** — this becomes the active template for every later run

### 3. Fill the batch (scrapes + auto-fills every form)

```bash
python main/cardfiller.py                 # normal run
python main/cardfiller.py --image-enhance # also upscale/denoise/colour-grade each photo
```

Processed cards → `output/cards/`, editable sidecars → `output/records/`, originals moved to `completed/`, failures to `retry/` (auto-retried with a fresh browser).

### 4. Touch up a card in the browser

```bash
python main/cardfiller.py --edit [--port 5000]
```

Opens a per-card editor: fix the crop/rotation, text, or fields; saving regenerates that card and its sidecar.

### 5. Make an A4 PDF of all cards

```bash
python pdf_maker.py               # default grid layout
python pdf_maker.py --enhance-image
```

---

# Batch archive / restore

Keep several batches around and switch between them easily.

```bash
# Zip the ENTIRE current batch (images, outputs, sidecars, template) into history/batch1.zip
python main/cardfiller.py --mark-old batch1

# Restore an archived batch so you can continue exactly where you left off:
python main/cardfiller.py --given-name batch1

# Staying on the current batch (default; the flag cancels a --given-name):
python main/cardfiller.py --current
```

`--mark-old` zips `image/`, `output/`, `completed/`, `retry/`, `temp/`, `templates/` and `template.png`, then cleans them out of the workspace. `--given-name` unzips them back into place.

---

# Command reference

| Command | What it does |
|---|---|
| `python main/cardfiller.py` | Scrape + fill the batch (uses the active template) |
| `python main/cardfiller.py --image-enhance` | Same, but enhance every photo |
| `python main/cardfiller.py --new` | Open the template designer in the browser |
| `python main/cardfiller.py --edit` | Open the per-card editor |
| `python main/cardfiller.py --mark-old NAME` | Archive the current batch → `history/` |
| `python main/cardfiller.py --given-name NAME` | Restore archived batch `NAME` |
| `python main/cardfiller.py --current` | Stay on the current batch |
| `python pdf_maker.py [--enhance-image]` | Combine cards into an A4 PDF |

For every flag:
```bash
python main/cardfiller.py --help
```

---

# How it works (pipeline)

```
Forms in image/  →  Google Lens AI Mode  →  JSON  →  photo crop/rotation
                                              ↓
               render_card (render)  ←  active template (designer)
                                              ↓
              output/cards/*.png  +  output/records/*.json
                                              ↓
                           pdf_maker.py  →  output.pdf
```

The layout (photo box, every field, its font, alignment and **gradient**) is JSON inside the active template / record sidecar, so the on-screen designer, the preview and the final exported PNG are generated by **the same `card_render.py` code path** — no drift.

---

# Customization

| Constant | File | Meaning |
|---|---|---|
| `TEMPLATE_PATH` | `card_render.py` | default `template.png` |
| `FONT_BOLD` / `FONT_REGULAR` | `card_render.py` | DejaVu fonts |
| `_FACE_CASCADE` | `card_render.py` | OpenCV face detector |
| Firefox `user_data_dir` | `cardfiller.py` | persistent profile for scraping |
| Prompt / selectors | `cardfiller.py` | Google Lens AI query + Playwright selectors |

---

# FAQ / Notes

- **Photo blank?** No face detected. Open that card with `--edit` and fix the crop.
- **Google changed the page?** The selectors live in `cardfiller.py` — update them.
- **CAPTCHA?** The script pauses; solve it manually and processing resumes.
- **Card text long?** Fields auto-shrink to fit with no overlap.

---

# License

Process only documents you own, or for which you have explicit permission.