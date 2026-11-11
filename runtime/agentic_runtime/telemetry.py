"""Телеметрия задания: строки `job-resources:` в формате santinel (ADR-0003).

Их разбирает `runners/resources.py`, поэтому снимок `job_resources` работает для любого проекта,
где подключён рантайм. Читаются только файлы cgroup v2 своего процесса и /proc хоста.
"""

import os

MiB = 1024 * 1024


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _cgroup_dir():
    line = _read("/proc/self/cgroup").splitlines()
    rel = next((ln.split("::", 1)[1] for ln in line if ln.startswith("0::")), "/")
    return "/sys/fs/cgroup" + (rel if rel != "/" else "")


def _kv(text):
    out = {}
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) >= 2:
            out[parts[0].rstrip(":")] = parts[1]
    return out


def vmstat():
    v = _kv(_read("/proc/vmstat"))
    return {k: int(v.get(k, 0)) for k in ("oom_kill", "pswpin", "pswpout")}


def lines(start, workers=None, mode="fork"):
    """Строки для вывода в конце сессии. start — vmstat() в начале сессии."""
    cg = _cgroup_dir()
    ev = _kv(_read(f"{cg}/memory.events"))
    mx = _read(f"{cg}/memory.max")
    peak = _read(f"{cg}/memory.peak")
    cur = _read(f"{cg}/memory.current")
    mem = _kv(_read("/proc/meminfo"))
    now = vmstat()
    psi = _read("/proc/pressure/memory").splitlines()
    cpu_max = _read(f"{cg}/cpu.max") or "max 100000"
    nproc = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 0)
    load = _read("/proc/loadavg")
    out = []
    if workers:
        mem_max = int(mx) // MiB if mx.isdigit() else 0
        out.append(f"workers: {workers} (cpu {nproc}, mem {mem_max} MiB, mode {mode})")
    if ev:
        out.append(f"job-resources: cgroup v2 memory.events: oom_kill={ev.get('oom_kill', 0)} oom={ev.get('oom', 0)} "
                   f"max={ev.get('max', 0)} high={ev.get('high', 0)}")
    if peak or cur:
        mx_s = f"{int(mx) // MiB}MiB" if mx.isdigit() else "max"
        out.append(f"job-resources: cgroup v2 memory: peak={int(peak or 0) // MiB}MiB max={mx_s} "
                   f"current={int(cur or 0) // MiB}MiB")
    if mem:
        out.append(f"job-resources: host meminfo: MemTotal={int(mem.get('MemTotal', 0)) // 1024}MiB "
                   f"MemAvailable={int(mem.get('MemAvailable', 0)) // 1024}MiB")
    if start is not None and now:
        d = {k: now[k] - start.get(k, 0) for k in now}
        out.append(f"job-resources: host vmstat: oom_kill={now['oom_kill']} pswpin={now['pswpin']} pswpout={now['pswpout']} "
                   f"(за прогон: oom_kill+{d['oom_kill']} pswpin+{d['pswpin']} pswpout+{d['pswpout']})")
    if psi:
        out.append(f"job-resources: host pressure memory: {psi[0]}" + (f" | {psi[1]}" if len(psi) > 1 else ""))
    if load:
        out.append(f"job-resources: cpu: nproc={nproc} cpu_count={os.cpu_count()} cpu.max={cpu_max} "
                   f"effective={nproc} loadavg={load}")
    return out
