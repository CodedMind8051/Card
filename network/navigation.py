from network.checks import is_network_error, check_internet, wait_for_internet
from utils.logging import short_error


def goto_with_retry(page, url, **kwargs):
    while True:
        try:
            page.goto(url, **kwargs)
            return
        except Exception as e:
            if is_network_error(e) or not check_internet():
                print(f"\n  ⚠ Couldn't reach {url}: {short_error(e)}")
                wait_for_internet()
                continue
            raise