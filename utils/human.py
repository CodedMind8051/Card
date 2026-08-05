import random
import time


def human_move(page, x, y, steps=25):
    box = page.viewport_size
    sx = random.randint(100, box["width"] - 100)
    sy = random.randint(100, box["height"] - 100)
    for i in range(steps):
        t = i / steps
        cx = sx + (x - sx) * t + random.uniform(-15, 15)
        cy = sy + (y - sy) * t + random.uniform(-15, 15)
        page.mouse.move(cx, cy)
        time.sleep(random.uniform(0.005, 0.02))
    page.mouse.move(x, y)


def human_click(page, locator):
    box = locator.bounding_box()
    if not box:
        locator.click()
        return
    x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
    y = box["y"] + box["height"] / 2 + random.uniform(-5, 5)
    human_move(page, x, y)
    time.sleep(random.uniform(0.2, 0.6))
    page.mouse.click(x, y)


def human_wait(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))