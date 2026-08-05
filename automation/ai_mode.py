def find_ai_mode_button(page):
    ai_mode_selectors = [
        'div[role="tab"]:has-text("AI Mode")',
        'a[role="tab"]:has-text("AI Mode")',
        'button:has-text("AI Mode")',
        'a:has-text("AI Mode")',
        '[aria-label="AI Mode"]',
        '[aria-label*="AI Mode"]',
        'text=AI Mode',
    ]
    frames_to_check = [page.main_frame] + page.frames
    for frame in frames_to_check:
        for sel in ai_mode_selectors:
            try:
                loc = frame.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return loc.first
            except Exception:
                pass
    return None