"""Эмулятор шарда тестов santinel внутри контейнера задания.

Повторяет то, что важно для ядра: главный процесс pytest и N воркеров xdist (fork), у каждого RSS растёт
до своего пика, работа — CPU плюс запросы к своему Postgres-сервису. Если ядро убивает воркера
(OOM-kill внутри cgroup задания), шард падает целиком, как при `--max-worker-restart=0`.

Параметры калибровки — из infra/ci/README.md santinel (§ «Смерть xdist-воркера», замеры 26.10–06.11):
пик воркера pytest 844–1473 MiB (все замеры «largest reaped» в README), dbt — около 672 MiB; pytest-шард больше ждёт Postgres, чем считает.

Запуск (внутри контейнера): python shard_emu.py --kind pytest --workers 3 --seed 1 --db-host db
Вывод: одна строка JSON с итогом и счётчиками cgroup задания.
"""

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import random
import signal
import time

MiB = 1024 * 1024
PROFILES = {
    # пик RSS воркера (MiB), базовый RSS, задач в шарде, CPU на задачу (мс), строк в запросе к БД
    "pytest": {"peak": (844, 1473), "base": 250, "tasks": 600, "cpu_ms": 35, "rows": 4000, "main_mib": 300},
    "dbt": {"peak": (560, 720), "base": 200, "tasks": 400, "cpu_ms": 60, "rows": 9000, "main_mib": 250},
}


def burn(ms):
    end = time.perf_counter() + ms / 1000
    h = b"x"
    while time.perf_counter() < end:
        h = hashlib.sha256(h).digest()


def touch(n_mib):
    """Выделить и реально занять страницы (RSS, а не виртуальная память)."""
    b = bytearray(n_mib * MiB)
    b[::4096] = b"\x01" * len(b[::4096])
    return b


def worker(idx, tasks, prof, peak, db_host, out):
    import psycopg2

    conn = psycopg2.connect(host=db_host, user="postgres", password="bench", dbname="postgres", connect_timeout=30)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"create table if not exists w{idx} (id serial, v text)")
    held = [touch(prof["base"])]
    # RSS набирается в первые 40% прогона (импорты, фикстуры, кэш SQL) и дальше держится на пике
    ramp = max(int(len(tasks) * 0.4), 1)
    step = max((peak - prof["base"]) // ramp, 1)
    done = 0
    for i, t in enumerate(tasks):
        if i < ramp:
            held.append(touch(step))
        burn(t["cpu_ms"])
        cur.execute(f"insert into w{idx} (v) select md5(g::text) from generate_series(1, %s) g", (t["rows"],))
        cur.execute(f"select count(*), max(v) from w{idx}")
        cur.fetchone()
        if i % 50 == 49:  # граница модуля: тестовые данные чистятся, БД задания не разрастается
            cur.execute(f"truncate w{idx}")
        done += 1
    out.put((idx, done))


def cgroup(name):
    try:
        with open(f"/sys/fs/cgroup/{name}") as f:
            return f.read().strip()
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=PROFILES, required=True)
    ap.add_argument("--workers", type=int, required=True)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--db-host", default="db")
    a = ap.parse_args()
    prof = PROFILES[a.kind]
    rnd = random.Random(a.seed)  # одинаковая нагрузка шарда во всех сценариях
    tasks = [{"cpu_ms": prof["cpu_ms"] * rnd.uniform(0.5, 1.5), "rows": int(prof["rows"] * rnd.uniform(0.5, 1.5)),
              } for _ in range(prof["tasks"])]
    peaks = [rnd.randint(*prof["peak"]) for _ in range(8)]
    main_mem = touch(prof["main_mib"])  # главный процесс pytest  # noqa: F841

    t0 = time.time()
    out = mp.Queue()
    chunks = [tasks[i::a.workers] for i in range(a.workers)]  # --dist=loadfile: файлы раскладываются по воркерам
    procs = [mp.Process(target=worker, args=(i, chunks[i], prof, peaks[i], a.db_host, out)) for i in range(a.workers)]
    for p in procs:
        p.start()
    node_down = None
    while any(p.is_alive() for p in procs):
        for i, p in enumerate(procs):
            if p.exitcode not in (None, 0) and node_down is None:
                node_down = {"worker": i, "exitcode": p.exitcode}
                for q in procs:  # --max-worker-restart=0: первая смерть останавливает сессию
                    if q.is_alive():
                        os.kill(q.pid, signal.SIGTERM)
        time.sleep(0.2)
    for p in procs:
        p.join()
    if node_down is None:
        bad = [(i, p.exitcode) for i, p in enumerate(procs) if p.exitcode != 0]
        if bad:
            node_down = {"worker": bad[0][0], "exitcode": bad[0][1]}
    ev = dict(line.split() for line in cgroup("memory.events").splitlines() if line)
    print(json.dumps({
        "kind": a.kind, "workers": a.workers, "seed": a.seed, "wall_s": round(time.time() - t0, 2),
        "ok": node_down is None, "node_down": node_down,
        "mem_max": cgroup("memory.max"), "mem_peak_mib": int(cgroup("memory.peak") or 0) // MiB,
        "oom_kill": int(ev.get("oom_kill", 0)), "mem_max_events": int(ev.get("max", 0)),
        "nproc": os.cpu_count() and len(os.sched_getaffinity(0)),
    }))


if __name__ == "__main__":
    main()
