"""A/B конфигурации раннера на эмулированном ноутбуке (E03). Репозитории и боевые раннеры не меняются.

Эмуляция:
- «Хост» — родительская cgroup `/emuhost` в VM Docker Desktop с лимитами ноутбука-раннера
  (8 CPU, 15 GiB, своп 4 GiB — по `cpu_count`, `MemTotal`, `SwapTotal` из логов шардов, E02).
- «Регистрация раннера» — поток, который по одному берёт шарды из общей очереди (concurrent = 1).
- «Задание» — контейнер с cpuset и `memory` как в Docker executor, внутри эмулятор шарда (bench/shard_emu.py);
  рядом свой Postgres-сервис без лимита задания, но внутри лимита хоста.
- Нагрузка во всех сценариях одна и та же: два пайплайна по шесть шардов, одинаковые seed.

Запуск: python -m runners bench [--only A-now,B-one-6g] → data/bench/<YYYYMMDD-HHMM>/results.json
"""

import datetime as dt
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
import uuid

IMAGE = "runners-bench:1"
PG_IMAGE = "postgres:16-alpine"
HELPER = "alpine:3.20"
NET = "runners-bench"
CG = "emuhost"
HOST = {"cpus": "0-7", "mem": "15G", "swap": "4G"}  # ноутбук 8 ядер / ~15 GiB / своп 4 GiB (E02)
LABEL = "runners-bench=1"   # метка всего, что создаёт бенч: убирается после каждого сценария
MIN_FREE_GB = 10            # не начинать сценарий, если на диске хоста меньше

# Правило числа воркеров santinel (scripts/ci_shards.py, infra/ci/README.md): наименьшее из потолка,
# CPU × воркеров на CPU и (memory.max − 256 − 336) ÷ память воркера.
RULES = {
    "budget1600": {"pytest": (6, 1.5, 1600), "dbt": (4, 1.0, 672)},  # с 06.11.2026
    "budget1100": {"pytest": (6, 1.5, 1100), "dbt": (4, 1.0, 672)},  # до 06.11.2026 — давал OOM на 4 GiB
}

SCENARIOS = {
    "A-now": {"title": "Как сейчас: 2 регистрации × (4 CPU, 4 GiB)", "rule": "budget1600",
              "regs": [("0-3", 4096), ("4-7", 4096)]},
    "D-before-0611": {"title": "До 06.11: 2 × (4 CPU, 4 GiB), бюджет 1100 MiB на воркер", "rule": "budget1100",
                      "regs": [("0-3", 4096), ("4-7", 4096)]},
    "B-one-6g": {"title": "1 регистрация × (8 CPU, 6 GiB)", "rule": "budget1600",
                 "regs": [("0-7", 6144)]},
    "C-two-6g": {"title": "2 регистрации × (4 CPU, 6 GiB)", "rule": "budget1600",
                 "regs": [("0-3", 6144), ("4-7", 6144)]},
}

PIPELINES = 2
SHARDS = [("pytest", k) for k in "abc"] + [("dbt", k) for k in "abc"]


def workers_for(rule, kind, cpus, mem_mib):
    ceiling, per_cpu, per_worker = RULES[rule][kind]
    by_mem = (mem_mib - 256 - 336) // per_worker
    return max(1, int(min(ceiling, cpus * per_cpu, by_mem)))


def ncpus(cpuset):
    n = 0
    for part in cpuset.split(","):
        a, _, b = part.partition("-")
        n += int(b or a) - int(a) + 1
    return n


def sh(*args, check=True, capture=True):
    r = subprocess.run(list(args), capture_output=capture, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])}…: {r.stderr.strip()[:300]}")
    return r.stdout


def cg_exec(script):
    """Команда в корне cgroup v2 VM Docker Desktop (привилегированный вспомогательный контейнер)."""
    return sh("docker", "run", "--rm", "--privileged", "--cgroupns=host", "-v", "/sys/fs/cgroup:/cg", HELPER,
              "sh", "-c", script)


def cg_reset():
    cg_exec(f"rmdir /cg/{CG} 2>/dev/null; mkdir -p /cg/{CG} && "
            f"echo '+cpu +cpuset +memory +io +pids' > /cg/{CG}/cgroup.subtree_control && "
            f"echo {HOST['cpus']} > /cg/{CG}/cpuset.cpus && echo {HOST['mem']} > /cg/{CG}/memory.max && "
            f"(echo {HOST['swap']} > /cg/{CG}/memory.swap.max || true)")


def cg_read():
    # memory.events иерархический (включает OOM в cgroup заданий), memory.events.local — только сам «хост»
    out = cg_exec(f"cd /cg/{CG} && for f in memory.events memory.events.local memory.peak memory.pressure cpu.pressure io.pressure; "
                  f"do echo \"## $f\"; cat $f; done")
    res, cur = {}, None
    for line in out.splitlines():
        if line.startswith("## "):
            cur = line[3:]
        elif cur in ("memory.events", "memory.events.local"):
            k, v = line.split()
            res[f"host_{'local_' if cur.endswith('local') else ''}{k}"] = int(v)
        elif cur == "memory.peak":
            res["host_mem_peak_mib"] = int(line) // (1024 * 1024)
        elif cur.endswith(".pressure"):
            kind = line.split()[0]
            total = int(line.rsplit("total=", 1)[1])
            res[f"{cur.split('.')[0]}_pressure_{kind}_us"] = total
    return res


def run_job(reg, kind, seed, rule):
    cpuset, mem = reg
    jid = uuid.uuid4().hex[:8]
    db = f"bench-db-{jid}"
    w = workers_for(rule, kind, ncpus(cpuset), mem)
    sh("docker", "run", "-d", "--name", db, "--label", LABEL, "--network", NET, f"--cgroup-parent=/{CG}",
       "-e", "POSTGRES_PASSWORD=bench", PG_IMAGE, "-c", "shared_buffers=128MB")
    try:
        for _ in range(120):
            if subprocess.run(["docker", "exec", db, "pg_isready", "-U", "postgres"], capture_output=True).returncode == 0:
                break
            time.sleep(0.5)
        t0 = time.time()
        r = subprocess.run(["docker", "run", "--rm", "--label", LABEL, "--network", NET, f"--cgroup-parent=/{CG}",
                            f"--cpuset-cpus={cpuset}", f"--memory={mem}m", f"--memory-swap={mem}m", IMAGE,
                            "--kind", kind, "--workers", str(w), "--seed", str(seed), "--db-host", db],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        wall = time.time() - t0
        line = (r.stdout.strip().splitlines() or ["{}"])[-1]
        try:
            res = json.loads(line)
        except json.JSONDecodeError:  # главный процесс убит целиком
            res = {"ok": False, "node_down": {"main": r.returncode}}
        res.update({"cpuset": cpuset, "mem_mib": mem, "job_wall_s": round(wall, 2), "exit": r.returncode})
        if r.returncode == 137 and res.get("ok"):
            res["ok"] = False
        return res
    finally:
        sh("docker", "rm", "-f", "-v", db, check=False)  # -v: анонимный том данных Postgres удаляется вместе с ним


def cleanup():
    """Удалить всё, что создал бенч (по метке), и его сеть. Чужие контейнеры и тома не трогаются."""
    ids = sh("docker", "ps", "-aq", "--filter", f"label={LABEL}", check=False).split()
    if ids:
        sh("docker", "rm", "-f", "-v", *ids, check=False)
    vols = sh("docker", "volume", "ls", "-q", "--filter", f"label={LABEL}", check=False).split()
    if vols:
        sh("docker", "volume", "rm", *vols, check=False)
    sh("docker", "network", "rm", NET, check=False)


def free_gb(path=None):
    import shutil
    return shutil.disk_usage(path or os.path.expanduser("~")).free / 2**30


def run_scenario(name, retry_failed=True):
    sc = SCENARIOS[name]
    if free_gb() < MIN_FREE_GB:
        raise RuntimeError(f"на диске меньше {MIN_FREE_GB} GB свободно — сценарий не запускается")
    sh("docker", "network", "create", "--label", LABEL, NET, check=False)
    cg_reset()
    jobs = queue.Queue()
    for p in range(PIPELINES):
        for i, (kind, k) in enumerate(SHARDS):
            jobs.put({"kind": kind, "shard": f"{kind}:{k}", "seed": 100 + p * len(SHARDS) + i, "pipeline": p, "attempt": 1})
    results, lock = [], threading.Lock()

    def registration(reg):
        while True:
            try:
                j = jobs.get_nowait()
            except queue.Empty:
                return
            res = {**j, **run_job(reg, j["kind"], j["seed"], sc["rule"])}
            with lock:
                results.append(res)
            if not res.get("ok") and retry_failed and j["attempt"] == 1:
                jobs.put({**j, "attempt": 2})  # агент пушит снова: шард перезапускается

    t0 = time.time()
    threads = [threading.Thread(target=registration, args=(r,)) for r in sc["regs"]]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    makespan = time.time() - t0
    host = cg_read()
    cleanup()
    return {"name": name, "title": sc["title"], "rule": sc["rule"], "regs": sc["regs"],
            "makespan_s": round(makespan, 1), "shards": results, **host}


def machine():
    info = {"python_platform": platform.platform()}
    try:
        info["docker"] = json.loads(sh("docker", "info", "--format",
                                       '{"ncpu":{{.NCPU}},"mem":{{.MemTotal}},"kernel":"{{.KernelVersion}}","cgroup":"{{.CgroupVersion}}"}'))
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            info["cpu"] = sh("powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name").strip()
            info["ram_gib"] = round(int(sh("powershell", "-NoProfile", "-Command",
                                           "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory").strip()) / 2**30, 1)
        except Exception:
            pass
    return info


def run(names=None, root="data/bench", resume=None):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # образы, которых не было до прогона, бенч удалит за собой; чужие (уже скачанные владельцем) не трогает
    pulled = [img for img in (IMAGE, PG_IMAGE, HELPER)
              if subprocess.run(["docker", "image", "inspect", img], capture_output=True).returncode != 0]
    cache_was_empty = "0B" in sh("docker", "system", "df", "--format", "{{.Type}}={{.Size}}", check=False).split("Build Cache=")[-1][:3]
    sh("docker", "build", "-q", "-t", IMAGE, os.path.join(here, "bench"))
    for img in (PG_IMAGE, HELPER):
        sh("docker", "pull", "-q", img)
    cleanup()  # остатки прерванного прогона
    out = os.path.join(root, resume or dt.datetime.now().strftime("%Y%m%d-%H%M"))
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "results.json")
    if resume and os.path.exists(path):  # дозапись: досчитать сценарии, которые не успели
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        doc.setdefault("resumed_at", []).append(dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    else:
        doc = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "machine": machine(),
               "host": HOST, "pipelines": PIPELINES, "shards": SHARDS, "rules": RULES, "scenarios": []}
    done = {s["name"] for s in doc["scenarios"]}
    try:
        for name in [n for n in (names or list(SCENARIOS)) if n not in done]:
            print(f"сценарий {name}…", file=sys.stderr)
            doc["scenarios"].append(run_scenario(name))
            with open(path, "w", encoding="utf-8") as f:  # сохраняем после каждого сценария
                json.dump(doc, f, ensure_ascii=False, indent=2)
    finally:
        cleanup()
        cg_exec(f"rmdir /cg/{CG} 2>/dev/null || true")
        if pulled:
            sh("docker", "rmi", *pulled, check=False)
        if cache_was_empty:  # кэш сборки до прогона был пуст — весь он от бенча
            sh("docker", "builder", "prune", "-af", check=False)
    print(out)
    return out
