"""Ресурсы задания из логов GitLab: строки `job-resources:` и `workers:`, которые печатают шарды santinel.

Читаются только начало и конец лога (HTTP Range), сохраняются только разобранные числа — сырой лог
в снимок не попадает (в нём могут быть секреты окружения). Только чтение, репозиторий не меняется.

Источник формата: scripts/ci_job_resources.py и scripts/ci_shards.py проекта santinel
(копии для справки — data/evidence/santinel/).
"""

import csv
import gzip
import os
import re
import sys
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FIELDS = ["job_id", "project", "name", "runner", "created_at", "cpu_model", "cpu_count", "nproc", "cpu_max",
          "workers", "workers_mem_mib", "mem_max_mib", "mem_peak_mib", "job_oom_kill", "job_mem_max_events",
          "host_mem_total_mib", "host_mem_avail_mib", "host_oom_kill_total", "host_oom_kill_delta",
          "host_pswpout_delta", "host_psi_mem_some_avg300", "loadavg1", "parsed"]

PATTERNS = {
    "cpu_model": r"Running on CPU: (.+?)(?: \(|$)",
    "workers": r"workers: (\d+) \(cpu \d+, mem (\d+) MiB",
    "job_events": r"memory\.events: oom_kill=(\d+) oom=\d+ max=(\d+)",
    "job_mem": r"cgroup v2 memory: peak=(\d+)MiB max=(\d+|max)",
    "host_mem": r"host meminfo: MemTotal=(\d+)MiB MemAvailable=(\d+)MiB",
    "host_vmstat": r"host vmstat: oom_kill=(\d+) .*?oom_kill\+(\d+) pswpin\+\d+ pswpout\+(\d+)",
    "host_psi": r"host pressure memory: some avg10=[\d.]+ avg60=[\d.]+ avg300=([\d.]+)",
    "cpu": r"cpu: nproc=(\d+) cpu_count=(\d+) cpu\.max=(\S+ \S+) effective=\S+ loadavg=([\d.]+)",
}


def parse(text):
    """Разобрать хвост и начало лога в словарь показателей. Неизвестный формат — пустые поля."""
    t = ANSI.sub("", text)
    r = {}
    m = {k: re.search(p, t, re.M) for k, p in PATTERNS.items()}
    if m["cpu_model"]:
        r["cpu_model"] = m["cpu_model"].group(1).strip()
    if m["workers"]:
        r["workers"], r["workers_mem_mib"] = int(m["workers"].group(1)), int(m["workers"].group(2))
    if m["job_events"]:
        r["job_oom_kill"], r["job_mem_max_events"] = int(m["job_events"].group(1)), int(m["job_events"].group(2))
    if m["job_mem"]:
        r["mem_peak_mib"] = int(m["job_mem"].group(1))
        r["mem_max_mib"] = None if m["job_mem"].group(2) == "max" else int(m["job_mem"].group(2))
    if m["host_mem"]:
        r["host_mem_total_mib"], r["host_mem_avail_mib"] = int(m["host_mem"].group(1)), int(m["host_mem"].group(2))
    if m["host_vmstat"]:
        r["host_oom_kill_total"] = int(m["host_vmstat"].group(1))
        r["host_oom_kill_delta"] = int(m["host_vmstat"].group(2))
        r["host_pswpout_delta"] = int(m["host_vmstat"].group(3))
    if m["host_psi"]:
        r["host_psi_mem_some_avg300"] = float(m["host_psi"].group(1))
    if m["cpu"]:
        r["nproc"], r["cpu_count"] = int(m["cpu"].group(1)), int(m["cpu"].group(2))
        r["cpu_max"], r["loadavg1"] = m["cpu"].group(3), float(m["cpu"].group(4))
    r["parsed"] = int(bool(m["job_mem"] or m["cpu"]))
    return r


def _fetch(s, base, job, head=24000, tail=24000):
    pid = urllib.parse.quote(job["project"], safe="")
    url = f"{base}/api/v4/projects/{pid}/jobs/{job['job_id']}/trace"
    parts = []
    for rng in (f"bytes=0-{head - 1}", f"bytes=-{tail}"):
        r = s.get(url, headers={"Range": rng}, timeout=60)
        if r.status_code in (200, 206):
            parts.append(r.content.decode("utf-8", "replace"))
        if r.status_code == 200:  # сервер отдал целиком — второй запрос не нужен
            break
    return "\n".join(parts)


def collect(token, base, jobs, out, workers=8):
    """jobs: DataFrame с job_id, project, name, runner, created_at. Пишет job_resources.csv.gz."""
    from collect.ci_history import session

    s = session(token)
    rows = jobs[["job_id", "project", "name", "runner", "created_at"]].to_dict("records")

    def one(j):
        try:
            return {**j, **parse(_fetch(s, base, j))}
        except Exception as e:  # обрыв по одному заданию не роняет выгрузку
            print(f"job {j['job_id']}: {type(e).__name__}", file=sys.stderr)
            return {**j, "parsed": 0}

    with ThreadPoolExecutor(workers) as pool:
        res = list(pool.map(one, rows))
    final = os.path.join(out, "job_resources.csv.gz")
    part = final + ".part"
    with gzip.open(part, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(res)
    os.replace(part, final)
    n = sum(r.get("parsed", 0) for r in res)
    print(f"job_resources: {len(res)} заданий, разобрано {n}", file=sys.stderr)
    return {"rows": len(res), "parsed": n}
