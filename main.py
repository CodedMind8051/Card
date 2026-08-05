import time

from playwright.sync_api import sync_playwright

from constants.paths import IMAGE_DIR, OUTPUT_DIR, RETRY_DIR, COMPLETED_DIR, TEMP_DIR
from workflow.pass_processor import process_image_pass


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    RETRY_DIR.mkdir(exist_ok=True)
    COMPLETED_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    all_images = sorted(IMAGE_DIR.glob("*.[jJ][pP][gG]")) + sorted(IMAGE_DIR.glob("*.[jJ][pP][eE][gG]")) + sorted(IMAGE_DIR.glob("*.[pP][nN][gG]"))
    if not all_images:
        print(f"No images found in {IMAGE_DIR}")
        return

    with sync_playwright() as p:
        context = p.firefox.launch_persistent_context(
            user_data_dir="/home/coded_mind__/.mozilla/firefox/e0uaw6ea.default-esr",
            headless=False,
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        page = context.pages[0] if context.pages else context.new_page()

        total = len(all_images)
        print(f"Found {total} image(s) to process. Estimating time after the first one...\n")
        pass_start = time.time()

        done, failed, retry_images = process_image_pass(page, all_images, "Processing", pass_start)

        if retry_images:
            print(f"\n=== Restarting browser for retry of {len(retry_images)} image(s) ===")
            context.close()
            context = p.firefox.launch_persistent_context(
                user_data_dir="/home/coded_mind__/.mozilla/firefox/e0uaw6ea.default-esr",
                headless=False,
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
            )
            page = context.pages[0] if context.pages else context.new_page()

            retry_pass_start = time.time()
            retry_done, retry_failed, still_failed = process_image_pass(page, retry_images, "Retry", retry_pass_start)

            if still_failed:
                print(f"\n=== {len(still_failed)} image(s) still failed after retry ===")
                for f in still_failed:
                    print(f"  {f.name}")
                print("These remain in the retry folder.")
            else:
                print("\n=== All retries succeeded! ===")
        else:
            print("\n=== All images processed successfully! ===")

        input("\nPress ENTER to close browser...")
        context.close()


if __name__ == "__main__":
    main()