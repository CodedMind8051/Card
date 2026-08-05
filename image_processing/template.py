from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from constants.paths import OUTPUT_DIR, TEMPLATE_PATH, TEMP_DIR
from constants.screenshots import TEMP_SCREENSHOTS
from constants.fonts import FONT_BOLD
from constants.sizes import (
    FIELD_FONT_SIZE,
    NAME_FONT_SIZE,
    SECTION_FONT_SIZE,
    FIELD_MIN_FONT_SIZE,
    NAME_MIN_FONT_SIZE,
)
from constants.colors import VALUE_COLOR, NAME_COLOR, SECTION_COLOR
from constants.positions import FIELD_POSITIONS, NAME_CENTER, SECTION_POSITION
from constants.photo import PHOTO_BOX, PHOTO_CORNER_RADIUS
from image_processing.enhancement import enhance_photo, fit_cover, add_rounded_corners
from image_processing.text import draw_fitted_text, draw_autofit_text


def fill_template(data: dict, image_name: str, student_photo: Image.Image = None):
    output_path = OUTPUT_DIR / f"{Path(image_name).stem}_filled.png"
    if not Path(TEMPLATE_PATH).exists():
        print(f"  Template not found at {TEMPLATE_PATH}, skipping fill step.")
        return None
    im = Image.open(TEMPLATE_PATH).convert("RGB")
    draw = ImageDraw.Draw(im)

    if student_photo is not None:
        px, py, pw, ph = PHOTO_BOX
        enhanced_photo = enhance_photo(student_photo)
        fitted_photo = fit_cover(enhanced_photo, pw, ph)
        rounded_photo = add_rounded_corners(fitted_photo, radius=PHOTO_CORNER_RADIUS)
        im.paste(rounded_photo, (px, py), rounded_photo)
        print(f"  Student photo enhanced and pasted at {PHOTO_BOX} (rounded corners r={PHOTO_CORNER_RADIUS})")
    else:
        print("  No student photo detected - skipping photo paste.")

    # ---- Student name: auto-shrink to fit within the banner width, so a
    # long name can never overflow past the card edges (which would visually
    # overlap the decorative border/wave graphics on either side). ----
    student_name = str(data.get("student_name", "") or "").upper()
    if student_name:
        name_max_width = im.width - 60  # keep a margin from both edges
        draw_autofit_text(
            draw, student_name, NAME_CENTER[0], NAME_CENTER[1],
            name_max_width, FONT_BOLD, NAME_FONT_SIZE, NAME_COLOR,
            min_font_size=NAME_MIN_FONT_SIZE, anchor="mm",
        )

    # ---- Section (e.g. "Sec- A") — short by nature, but auto-fit anyway
    # for consistency and safety against unexpected long values. ----
    section = str(data.get("section", "") or "").strip()
    if section:
        section_text = f"Sec- {section}"
        section_max_width = im.width - SECTION_POSITION[0] - 30
        draw_autofit_text(
            draw, section_text, SECTION_POSITION[0], SECTION_POSITION[1],
            section_max_width, FONT_BOLD, SECTION_FONT_SIZE, SECTION_COLOR,
            min_font_size=20, anchor="lm",
        )

    # ---- Regular fields ----
    # Each field's available width stops well before the card's right edge,
    # and address's available height stops well before SECTION_POSITION's
    # row, so a long address wrapping onto extra lines can never grow down
    # into the "Sec- A" text or the signature/footer graphics below it.
    for key, (x, y) in FIELD_POSITIONS.items():
        value = str(data.get(key, "") or "")
        if not value:
            continue

        avail_width = im.width - x - 30

        if key == "address":
            avail_height = SECTION_POSITION[1] - y - 20
            draw_fitted_text(
                draw, value, x, y, avail_width, avail_height,
                FONT_BOLD, FIELD_FONT_SIZE, VALUE_COLOR,
                min_font_size=FIELD_MIN_FONT_SIZE,
            )
        else:
            draw_autofit_text(
                draw, value, x, y, avail_width, FONT_BOLD, FIELD_FONT_SIZE, VALUE_COLOR,
                min_font_size=FIELD_MIN_FONT_SIZE,
            )

    im.save(output_path)
    print(f"  Filled card saved: {output_path}")
    return output_path