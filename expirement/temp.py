import canvas_fix  
from invisible_playwright import InvisiblePlaywright
import time

with InvisiblePlaywright() as browser:
    page = browser.new_page()
    page.goto("https://images.google.com")
    time.sleep(10000)