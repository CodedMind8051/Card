from PIL import Image
import os

# ------------------------
# CONFIGURATION
# ------------------------
INPUT_FOLDER = "output2"
OUTPUT_PDF = "output.pdf"

# A4 Landscape dimensions in pixels at 300 DPI (297mm x 210mm)
PAGE_W = 3508  
PAGE_H = 2480  

IMAGES_PER_PAGE = 10
COLS = 5
ROWS = 2

# Margins and Spacing (in pixels)
PAGE_MARGIN_X = 54     # Left/Right page margin
PAGE_MARGIN_Y = 120    # Top/Bottom page margin
IMAGE_SPACING_X = 40   # Horizontal gap between images
IMAGE_SPACING_Y = 160  # Vertical gap between images

# Calculate exact image dimensions to fit the grid perfectly
img_w = (PAGE_W - 2 * PAGE_MARGIN_X - (COLS - 1) * IMAGE_SPACING_X) // COLS
img_h = (PAGE_H - 2 * PAGE_MARGIN_Y - (ROWS - 1) * IMAGE_SPACING_Y) // ROWS

# Handle different Pillow versions for high-quality resampling
resampling_filter = Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS

# ------------------------

# 1. Get all image files
files = sorted(
    [
        os.path.join(INPUT_FOLDER, f)
        for f in os.listdir(INPUT_FOLDER)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ]
)

if not files:
    raise Exception(f"No images found in {INPUT_FOLDER}.")

pages = []

# 2. Process images in batches of 10
for start in range(0, len(files), IMAGES_PER_PAGE):
    # Create a blank A4 Landscape white canvas
    canvas = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    batch = files[start:start + IMAGES_PER_PAGE]
    
    for i, path in enumerate(batch):
        img = Image.open(path).convert("RGB")
        
        # Force exact dimensions/ratio matching the PDF cards
        img = img.resize((img_w, img_h), resampling_filter)
        
        # Calculate X/Y positions
        col = i % COLS
        row = i // COLS
        
        x = PAGE_MARGIN_X + col * (img_w + IMAGE_SPACING_X)
        y = PAGE_MARGIN_Y + row * (img_h + IMAGE_SPACING_Y)
        
        # Paste onto canvas
        canvas.paste(img, (x, y))
        
    pages.append(canvas)

# 3. Save as PDF
if pages:
    pages[0].save(
        OUTPUT_PDF,
        save_all=True,
        append_images=pages[1:],
        resolution=300  # Crucial for defining physical print size (A4)
    )
    print(f"Successfully saved '{OUTPUT_PDF}' (A4 Landscape, 5x2 Grid)")
else:
    print("No pages were generated.")