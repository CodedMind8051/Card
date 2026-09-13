from PIL import Image, ImageEnhance, ImageFilter
import os
import argparse

parser = argparse.ArgumentParser(description="Combine rendered cards into an A4 PDF.")
parser.add_argument("--enhance-image", action="store_true", default=False,
                    help="Enhance each card image (contrast/colour/sharpness) before placing it in the PDF. "
                         "Off by default — images are used as-is.")
args = parser.parse_args()

enhance = args.enhance_image

# ------------------------
# CONFIGURATION
# ------------------------
INPUT_FOLDER = "output/cards"
OUTPUT_PDF = "pdf/output.pdf"

# A4 Landscape at 300 DPI
PAGE_W = 3508
PAGE_H = 2480

COLS = 5
ROWS = 2
IMAGES_PER_PAGE = COLS * ROWS

# Margins
PAGE_MARGIN_X = 54
PAGE_MARGIN_Y = 120

# Space between cards
IMAGE_SPACING_X = 40
IMAGE_SPACING_Y = 160

# ------------------------

# Calculate card size
img_w = (
    PAGE_W
    - (2 * PAGE_MARGIN_X)
    - ((COLS - 1) * IMAGE_SPACING_X)
) // COLS

img_h = (
    PAGE_H
    - (2 * PAGE_MARGIN_Y)
    - ((ROWS - 1) * IMAGE_SPACING_Y)
) // ROWS

# Pillow compatibility
if hasattr(Image, "Resampling"):
    RESAMPLE = Image.Resampling.LANCZOS
else:
    RESAMPLE = Image.LANCZOS

# ------------------------
# Load images from LAST
# ------------------------

files = sorted(
    [
        os.path.join(INPUT_FOLDER, f)
        for f in os.listdir(INPUT_FOLDER)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ],
    reverse=True,      # <-- Last image first
)

if not files:
    raise Exception(f"No images found in '{INPUT_FOLDER}'.")

pages = []

# ------------------------
# Create PDF Pages
# ------------------------

for start in range(0, len(files), IMAGES_PER_PAGE):

    canvas = Image.new("RGB", (PAGE_W, PAGE_H), "white")

    batch = files[start:start + IMAGES_PER_PAGE]

    for i, path in enumerate(batch):

        img = Image.open(path).convert("RGB")

        # Resize
        img = img.resize((img_w, img_h), RESAMPLE)

        # ------------------------
        # Image Enhancement (only with --enhance-image)
        # ------------------------

        if enhance:
            # Slight contrast boost
            img = ImageEnhance.Contrast(img).enhance(1.08)

            # Slight color boost
            img = ImageEnhance.Color(img).enhance(1.03)

            # Increase sharpness
            img = ImageEnhance.Sharpness(img).enhance(2.8)

            # Unsharp Mask
            img = img.filter(
                ImageFilter.UnsharpMask(
                    radius=2,
                    percent=220,
                    threshold=3
                )
            )

        # ------------------------

        row = i // COLS
        col = i % COLS

        x = PAGE_MARGIN_X + col * (img_w + IMAGE_SPACING_X)
        y = PAGE_MARGIN_Y + row * (img_h + IMAGE_SPACING_Y)

        canvas.paste(img, (x, y))

    pages.append(canvas)

# ------------------------
# Save PDF
# ------------------------

if pages:

    pages[0].save(
        OUTPUT_PDF,
        save_all=True,
        append_images=pages[1:],
        resolution=300,
    )

    print(f"\n✅ PDF saved successfully!")
    print(f"📄 File: {OUTPUT_PDF}")
    print(f"🖼️ Images processed: {len(files)}")
    print(f"📑 Pages created: {len(pages)}")

else:
    print("No pages generated.")