def find_ask_box_all_frames(page):
    ask_selectors = [
        'textarea[placeholder*="Ask"]',
        'input[placeholder*="Ask"]',
        'textarea[aria-label*="Ask"]',
        'div[contenteditable="true"][aria-label*="Ask"]',
        'div[contenteditable="true"]',
    ]
    frames_to_check = [page.main_frame] + page.frames
    for frame in frames_to_check:
        for sel in ask_selectors:
            try:
                loc = frame.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first, frame
            except Exception:
                pass
    return None, None