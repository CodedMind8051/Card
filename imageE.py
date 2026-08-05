import cv2
import numpy as np

def enhance_old_photo(path_in, path_out):
    img = cv2.imread(path_in)

    # 1) Upscale 2x
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)

    # 2) Remove grain/noise
    img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)

    # 3) Fix faded colors (per-channel contrast stretch)
    out = np.zeros_like(img, np.float32)
    for c in range(3):
        ch = img[:, :, c].astype(np.float32)
        lo, hi = np.percentile(ch, 1), np.percentile(ch, 99)
        out[:, :, c] = np.clip((ch - lo) * 255.0 / max(hi - lo, 1), 0, 255)
    img = out.astype(np.uint8)

    # 4) Local contrast (CLAHE on lightness)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(l)
    img = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    # 5) Smooth skin but keep edges (bilateral filter)
    img = cv2.bilateralFilter(img, 9, 60, 60)

    # 6) Gentle sharpen (unsharp mask)
    blur = cv2.GaussianBlur(img, (0, 0), 2.0)
    img = cv2.addWeighted(img, 1.4, blur, -0.4, 0)

    cv2.imwrite(path_out, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print("Saved ->", path_out)

enhance_old_photo("student_photo_debug.png", "enhanced.jpg")