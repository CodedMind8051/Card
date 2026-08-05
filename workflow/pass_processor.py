import shutil
import time
import traceback
from collections import deque

from constants.paths import COMPLETED_DIR, RETRY_DIR, LOG_FILE
from constants.resilience import BAD_JSON_STREAK_LIMIT, JSON_RETRY_WAIT
from exceptions import BadJSONResponseError
from utils.logging import log_detail, short_error, cleanup_temp_screenshots
from utils.progress import fmt_time, show_progress
from network.checks import is_network_error, check_internet, wait_for_internet
from processing.pipeline import process_single_image


def process_image_pass(page, images, label, pass_start):
    total = len(images)
    done = 0
    failed = 0
    retry_images = []
    queue = deque(images)

    consecutive_bad_json = 0
    bad_json_batch = []

    def flush_bad_json_batch_as_failures():
        nonlocal failed, done
        for p in bad_json_batch:
            failed += 1
            done += 1
            log_detail(f"[{p.name}] Bad-JSON streak broke without hitting {BAD_JSON_STREAK_LIMIT} in a row; treating as failed.")
            print(f"\n  ✗ Failed: {p.name} — bad/non-JSON response")
            retry_path = RETRY_DIR / p.name
            if p.exists():
                shutil.copy2(str(p), str(retry_path))
                retry_images.append(retry_path)
        bad_json_batch.clear()

    while queue:
        img_path = queue.popleft()
        img_start = time.time()
        finished = False
        while not finished:
            try:
                process_single_image(page, img_path)
                cleanup_temp_screenshots()
                shutil.move(str(img_path), str(COMPLETED_DIR / img_path.name))
                done += 1
                print(f"  ⏱ Done in {time.time() - img_start:.1f}s")
                finished = True
                if bad_json_batch:
                    consecutive_bad_json = 0
                    flush_bad_json_batch_as_failures()

            except BadJSONResponseError as e:
                consecutive_bad_json += 1
                bad_json_batch.append(img_path)
                cleanup_temp_screenshots()
                print(f"\n  ⚠ Non-JSON response for {img_path.name} "
                      f"({consecutive_bad_json}/{BAD_JSON_STREAK_LIMIT} in a row): {short_error(e)}")

                if consecutive_bad_json >= BAD_JSON_STREAK_LIMIT:
                    print(f"  {BAD_JSON_STREAK_LIMIT} bad responses in a row — "
                          f"waiting {fmt_time(JSON_RETRY_WAIT)} before trying them again...")
                    time.sleep(JSON_RETRY_WAIT)
                    consecutive_bad_json = 0
                    for p in reversed(bad_json_batch):
                        queue.appendleft(p)
                    bad_json_batch.clear()
                    print("  Resuming...")

                finished = True

            except Exception as e:
                if is_network_error(e) or not check_internet():
                    print(f"\n  ⚠ Network issue while processing {img_path.name}: {short_error(e)}")
                    log_detail(f"[{img_path.name}] NETWORK ERROR:\n{traceback.format_exc()}")
                    cleanup_temp_screenshots()
                    wait_for_internet()
                    print(f"  Resuming {img_path.name}...")
                    continue

                if bad_json_batch:
                    consecutive_bad_json = 0
                    flush_bad_json_batch_as_failures()

                failed += 1
                log_detail(f"[{img_path.name}] ERROR:\n{traceback.format_exc()}")
                print(f"\n  ✗ Failed: {img_path.name} — {short_error(e)}")
                print(f"    (full details in {LOG_FILE})")
                cleanup_temp_screenshots()
                retry_path = RETRY_DIR / img_path.name
                shutil.copy2(str(img_path), str(retry_path))
                retry_images.append(retry_path)
                done += 1
                finished = True

        if done == 1:
            per_image = time.time() - pass_start
            print(f"  → First image took {fmt_time(per_image)}. Estimated total: ~{fmt_time(per_image * total)}\n")
        show_progress(min(done, total), total, failed, label=label, start_time=pass_start)

    if bad_json_batch:
        flush_bad_json_batch_as_failures()

    print()
    return done, failed, retry_images