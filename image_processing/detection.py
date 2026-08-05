import cv2
from pathlib import Path
from PIL import Image

from constants.photo import (
    PHOTO_PAD_SIDE,
    PHOTO_PAD_TOP,
    PHOTO_PAD_BOTTOM,
    ROTATION_CONFIDENCE_MARGIN,
)

_FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
_ROTATIONS = [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE]


def extract_student_photo_smart(image_path: Path, target_aspect: float, ai_ratios=None):
    img = cv2.imread(str(image_path))
    if img is None:
        return None

    h_img, w_img = img.shape[:2]

    if ai_ratios:
        x = int(ai_ratios[0] * w_img)
        y = int(ai_ratios[1] * h_img)
        w = int(ai_ratios[2] * w_img)
        h = int(ai_ratios[3] * h_img)

        x, y = max(0, x), max(0, y)
        w, h = max(1, min(w, w_img - x)), max(1, min(h, h_img - y))
        roi = img[y:y+h, x:x+w]
    else:
        roi = img

    results_by_code = {}
    for code in _ROTATIONS:
        rot = cv2.rotate(roi, code) if code is not None else roi
        gray = cv2.cvtColor(rot, cv2.COLOR_BGR2GRAY)
        faces = _FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        if len(faces) > 0:
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            results_by_code[code] = (fw * fh, (fx, fy, fw, fh), rot)

    if not results_by_code:
        return None

    baseline_area = results_by_code[None][0] if None in results_by_code else 0
    chosen_code = None if None in results_by_code else max(results_by_code, key=lambda c: results_by_code[c][0])

    for code, (area, box, rot) in results_by_code.items():
        if code is None:
            continue
        if area > baseline_area * ROTATION_CONFIDENCE_MARGIN and area > results_by_code[chosen_code][0]:
            chosen_code = code

    best_area, best_box, best_rot_roi = results_by_code[chosen_code]
    fx, fy, fw, fh = best_box

    pad_side = fw * PHOTO_PAD_SIDE
    pad_top = fh * PHOTO_PAD_TOP
    pad_bottom = fh * PHOTO_PAD_BOTTOM

    crop_h = fh + pad_top + pad_bottom
    crop_w = crop_h * target_aspect if target_aspect else fw + 2 * pad_side

    face_cx = fx + fw / 2.0
    x0 = face_cx - crop_w / 2.0
    y0 = fy - pad_top
    x1 = x0 + crop_w
    y1 = y0 + crop_h

    x0 = max(0, int(x0))
    y0 = max(0, int(y0))
    x1 = min(best_rot_roi.shape[1], int(x1))
    y1 = min(best_rot_roi.shape[0], int(y1))

    if x1 - x0 < crop_w: x0 = max(0, x1 - int(crop_w))
    if y1 - y0 < crop_h: y0 = max(0, y1 - int(crop_h))

    crop = best_rot_roi[y0:y1, x0:x1]
    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    pil_crop = Image.fromarray(crop_rgb)

    return pil_crop