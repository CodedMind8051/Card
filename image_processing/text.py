from PIL import ImageFont

from constants.fonts import FONT_BOLD
from constants.sizes import FIELD_MIN_FONT_SIZE


def draw_fitted_text(draw, text, x, y, max_width, max_height, font_path, font_size, color, min_font_size=14):
    """
    Wraps text across multiple lines AND shrinks the font if needed, so the
    whole block fits inside max_width x max_height starting at (x, y).
    Used for 'address', which has vertical room below it to grow into.
    Stops shrinking at min_font_size to keep text readable.
    """
    lines = text.split("\n")
    while font_size > min_font_size:
        font = ImageFont.truetype(font_path, font_size)
        bbox = font.getbbox("Ay")
        line_height = bbox[3] - bbox[1] + 4
        wrapped = []
        for line in lines:
            words = line.split()
            current = ""
            for word in words:
                test = (current + " " + word).strip()
                test_bbox = font.getbbox(test)
                if test_bbox[2] - test_bbox[0] <= max_width:
                    current = test
                else:
                    if current:
                        wrapped.append(current)
                    current = word
            if current:
                wrapped.append(current)
        total_height = len(wrapped) * line_height
        if total_height <= max_height:
            for i, wline in enumerate(wrapped):
                draw.text((x, y + i * line_height), wline, font=font, fill=color, anchor="la")
            return
        font_size -= 2
    # Reached min_font_size and it still doesn't fit — draw at min size anyway
    # rather than silently dropping the text; it may slightly overflow but
    # stays visible and readable, which is better than disappearing.
    font = ImageFont.truetype(font_path, font_size)
    bbox = font.getbbox("Ay")
    line_height = bbox[3] - bbox[1] + 4
    wrapped = []
    for line in lines:
        words = line.split()
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            test_bbox = font.getbbox(test)
            if test_bbox[2] - test_bbox[0] <= max_width:
                current = test
            else:
                if current:
                    wrapped.append(current)
                current = word
        if current:
            wrapped.append(current)
    for i, wline in enumerate(wrapped):
        draw.text((x, y + i * line_height), wline, font=font, fill=color, anchor="la")


def draw_autofit_text(draw, text, x, y, max_width, font_path, base_font_size, color,
                      min_font_size=FIELD_MIN_FONT_SIZE, anchor="lm"):
    """
    Draws a SINGLE line of text, shrinking the font size until it fits within
    max_width. Used for fields that must stay on one line (name, DOB, class,
    roll number, mobile number) — unlike draw_fitted_text, this never wraps,
    since wrapping these would collide with the next field's fixed row
    position below it. Stops at min_font_size so text stays readable even
    for unusually long values.
    """
    font_size = base_font_size
    font = ImageFont.truetype(font_path, font_size)
    while font_size > min_font_size:
        bbox = font.getbbox(text)
        text_width = bbox[2] - bbox[0]
        if text_width <= max_width:
            break
        font_size -= 1
        font = ImageFont.truetype(font_path, font_size)
    draw.text((x, y), text, font=font, fill=color, anchor=anchor)