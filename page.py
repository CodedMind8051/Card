from PIL import Image
import os
import math

# ------------------------
# CONFIGURATION
# ------------------------
INPUT_FOLDER = "output"
OUTPUT_PDF = "output.pdf"

IMAGES_PER_PAGE = 10
COLS = 2
ROWS = 5

PAGE_MARGIN = 45     # Margin around page (pixels)
IMAGE_SPACING = 30    # Space between images (pixels)

# ------------------------

files = sorted(
    [
        os.path.join(INPUT_FOLDER, f)
        for f in os.listdir(INPUT_FOLDER)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ]
)

if not files:
    raise Exception("No images found.")

# Assume all images are same size
sample = Image.open(files[0]).convert("RGB")
img_w, img_h = sample.size

page_w = PAGE_MARGIN * 2 + COLS * img_w + (COLS - 1) * IMAGE_SPACING
page_h = PAGE_MARGIN * 2 + ROWS * img_h + (ROWS - 1) * IMAGE_SPACING

pages = []

for start in range(0, len(files), IMAGES_PER_PAGE):

    canvas = Image.new("RGB", (page_w, page_h), "white")

    batch = files[start:start + IMAGES_PER_PAGE]

    for i, path in enumerate(batch):
        img = Image.open(path).convert("RGB")

        x = PAGE_MARGIN + (i % COLS) * (img_w + IMAGE_SPACING)
        y = PAGE_MARGIN + (i // COLS) * (img_h + IMAGE_SPACING)

        canvas.paste(img, (x, y))

    pages.append(canvas)

pages[0].save(
    OUTPUT_PDF,
    save_all=True,
    append_images=pages[1:],
    resolution=300
)

print(f"Saved {OUTPUT_PDF}")