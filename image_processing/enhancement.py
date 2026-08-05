import cv2
import numpy as np
from PIL import Image, ImageDraw


def enhance_photo(img: Image.Image) -> Image.Image:
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    h, w = arr.shape[:2]
    if max(h, w) < 700:
        scale = 700 / max(h, w)
        arr = cv2.resize(arr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    arr = cv2.fastNlMeansDenoisingColored(arr, None, h=7, hColor=7, templateWindowSize=7, searchWindowSize=21)
    lab = cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    arr = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    blurred = cv2.GaussianBlur(arr, (0, 0), sigmaX=2)
    arr = cv2.addWeighted(arr, 1.5, blurred, -0.5, 0)
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def fit_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = int(src_w * scale + 0.5), int(src_h * scale + 0.5)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def add_rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(
        [0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255
    )
    img.putalpha(mask)
    return img