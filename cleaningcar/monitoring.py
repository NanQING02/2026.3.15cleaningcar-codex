import os
import time

try:
    import psutil
except ImportError:
    psutil = None

def monitor_loop(interval, stop_event):
    if interval <= 0:
        return
    if psutil is None:
        print('[monitor] psutil not installed, monitoring disabled.')
        return
    proc = None
    try:
        proc = psutil.Process(os.getpid())
    except Exception:
        proc = None
    started = time.time()
    count = 0
    sum_cpu = 0.0
    sum_mem = 0.0
    max_cpu = 0.0
    max_mem = 0.0
    while not stop_event.is_set():
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        rss_mb = 0.0
        if proc is not None:
            try:
                rss_mb = proc.memory_info().rss / (1024 * 1024)
            except Exception:
                rss_mb = 0.0
        count += 1
        sum_cpu += cpu
        sum_mem += mem
        if cpu > max_cpu:
            max_cpu = cpu
        if mem > max_mem:
            max_mem = mem
        avg_cpu = sum_cpu / max(1, count)
        avg_mem = sum_mem / max(1, count)
        uptime = time.time() - started
        msg = (
            f'[monitor] cpu={cpu:.1f}% mem={mem:.1f}% rss={rss_mb:.1f}MB '
            f'avg_cpu={avg_cpu:.1f}% max_cpu={max_cpu:.1f}% '
            f'avg_mem={avg_mem:.1f}% max_mem={max_mem:.1f}% '
            f'uptime={uptime:.0f}s'
        )
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                first = next(iter(temps.values()))
                if first:
                    msg += f' temp={first[0].current:.1f}C'
        except Exception:
            pass
        print(msg, flush=True)
        stop_event.wait(interval)
