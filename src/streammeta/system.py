"""Self-process metrics that remain correct across procfs/PID namespaces."""
import os
from pathlib import Path
import psutil

def self_rss_bytes():
    # /proc/self is resolved by the kernel; /proc/<os.getpid()> can name a
    # different process when the mounted procfs belongs to an outer namespace.
    if Path('/proc/self/statm').exists():
        resident_pages=int(Path('/proc/self/statm').read_text().split()[1])
        return resident_pages*os.sysconf('SC_PAGE_SIZE')
    return psutil.Process().memory_info().rss

def self_affinity():
    if hasattr(os,'sched_getaffinity'):return sorted(os.sched_getaffinity(0))
    proc=psutil.Process()
    return proc.cpu_affinity() if hasattr(proc,'cpu_affinity') else None

def procfs_pid():
    try:return int(os.readlink('/proc/self'))
    except (OSError,ValueError):return None
