import time


def fmt_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def show_progress(done, total, failed, label="Processing", start_time=None):
    pct = (done / total * 100) if total else 0
    bar_len = 30
    filled = int(bar_len * done / total) if total else 0
    bar = "█" * filled + "░" * (bar_len - filled)
    remaining = total - done - failed
    elapsed = time.time() - start_time if start_time else 0
    eta = (elapsed / done * (total - done)) if done > 0 else 0
    print(f"\r  {label}: |{bar}| {pct:.1f}%  {done}/{total}  {fmt_time(elapsed)} elapsed  ~{fmt_time(eta)} left  {failed} retry", end="", flush=True)