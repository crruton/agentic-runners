"""Что если: оценка политик CI повторным прогоном истории, без изменений в репозиториях.

Политика interruptible: когда на той же ветке проекта создан новый пайплайн, задания старого
пайплайна останавливаются, в том числе уже запущенные. GitLab по умолчанию отменяет только
ожидающие задания (auto-cancel redundant); запущенные без `interruptible: true` доезжают до конца.

Два уровня оценки:
1. Прямой replay: сколько часов раннеров ушло на задания, которые продолжали работать после
   появления более нового пайплайна на той же ветке. Не зависит от модели очереди.
2. Симуляция очереди: FIFO на K одинаковых слотах. K калибруется так, чтобы симуляция без
   изменений давала фактический p95 очереди. Затем та же симуляция с политикой.
"""

import heapq

import numpy as np
import pandas as pd

from runners.metrics import DONE, Metric, Section, classify, gitlab_period, mask_path, q, r1

PROTECTED = ("main", "master")  # на этих ветках остановка сборок может сорвать выкатку

# Варианты политики: какие задания можно останавливать. interruptible задаётся на задании, а не на ветке.
MODES = {
    "all": "все задания, верхняя граница",
    "no_deploy": "все, кроме заданий выкатки",
    "no_main": "все задания, кроме веток main/master",
}


def cuttable(g, mode):
    if mode == "all":
        return pd.Series(True, index=g.index)
    if mode == "no_deploy":
        return g.name.map(classify) != "deploy"
    if mode == "no_main":
        return ~g.ref.isin(PROTECTED)
    raise ValueError(mode)


def _with_next_pipeline(g):
    """Каждому заданию — время создания следующего пайплайна на той же ветке проекта."""
    pc = (g.groupby(["project", "ref", "pipeline_id"]).created_at.min().reset_index()
          .sort_values(["project", "ref", "created_at", "pipeline_id"]))
    pc["next_created"] = pc.groupby(["project", "ref"]).created_at.shift(-1)
    return g.merge(pc[["pipeline_id", "next_created"]], on="pipeline_id", how="left")


def prepare(gitlab):
    """Задания для replay: готовность к запуску, длительность, срок отмены."""
    g = gitlab[gitlab.status.isin(DONE)].copy()
    g = _with_next_pipeline(g)
    started = g.started_at.notna() & g.duration_s.notna()
    q_s = pd.to_timedelta(g.queued_s.fillna(0), unit="s")
    # момент, когда задание стало pending: старт минус ожидание; у отменённых до старта — отмена минус ожидание
    g["ready"] = np.where(started, g.started_at - q_s, g.finished_at - q_s)
    g["ready"] = pd.to_datetime(g["ready"], utc=True).fillna(g.created_at)
    med = g[started].groupby(["project", "name"]).duration_s.median()
    est = g.set_index(["project", "name"]).index.map(med).to_numpy(dtype=float)
    g["dur_s"] = np.where(started, g.duration_s, np.nan_to_num(est, nan=float(g[started].duration_s.median())))
    g["started"] = started
    # срок, после которого задание в реальности уже не стартует (его отменили в очереди)
    g["deadline"] = g.finished_at.where(~started)
    return g.sort_values(["ready", "job_id"]).reset_index(drop=True)


def interruptible_saved(g, mode="no_deploy"):
    """Прямой replay: часы, которые задания проработали после появления нового пайплайна на ветке."""
    run = g[g.started].copy()
    end = run.started_at + pd.to_timedelta(run.duration_s, unit="s")
    cut = run.next_created.notna() & (run.next_created < end) & cuttable(run, mode)
    saved = (end - run[["next_created", "started_at"]].max(axis=1)).dt.total_seconds().where(cut, 0.0)
    return run.assign(saved_s=saved)


def simulate(g, slots, mode=None):
    """FIFO-очередь на `slots` одинаковых слотах. Возвращает ожидание (с) и занятые часы по запущенным."""
    t0 = g.ready.min()
    ready = (g.ready - t0).dt.total_seconds().to_numpy()
    dur = g.dur_s.to_numpy(dtype=float)
    deadline = (g.deadline - t0).dt.total_seconds().to_numpy(dtype=float)  # NaN — срока нет
    cancel = np.full(len(g), np.nan)
    if mode:
        nxt = (g.next_created - t0).dt.total_seconds().to_numpy(dtype=float)
        ok = cuttable(g, mode).to_numpy()
        cancel = np.where(ok, nxt, np.nan)
    free = [0.0] * slots
    heapq.heapify(free)
    waits, busy = [], 0.0
    for i in range(len(g)):
        start = max(ready[i], free[0])
        if not np.isnan(deadline[i]) and start > deadline[i]:
            continue  # отменили в очереди, как и в реальности
        if not np.isnan(cancel[i]) and cancel[i] <= start:
            continue  # пайплайн уже устарел к моменту старта
        run = dur[i] if np.isnan(cancel[i]) else min(dur[i], cancel[i] - start)
        heapq.heapreplace(free, start + run)
        waits.append(start - ready[i])
        busy += run
    return np.array(waits), busy / 3600


def calibrate(g, observed_p95, lo=2, hi=120):
    """Наименьшее K, при котором p95 симулированной очереди не выше фактического."""
    while lo < hi:
        mid = (lo + hi) // 2
        w, _ = simulate(g, mid)
        if np.quantile(w, 0.95) <= observed_p95:
            hi = mid
        else:
            lo = mid + 1
    return lo


def whatif(snap):
    s = Section("whatif", "Что если: interruptible (replay истории, репозитории не меняются)", "gitlab", "")
    if snap.gitlab is None:
        s.missing = True
        return s
    s.period = gitlab_period(snap.gitlab)
    g = prepare(snap.gitlab)
    total_h = g[g.started].duration_s.sum() / 3600
    saved = {m: interruptible_saved(g, m) for m in MODES}
    main = saved["no_deploy"]
    by_proj = (main.groupby("project").saved_s.sum() / 3600).sort_values(ascending=False)
    t = by_proj[by_proj > 0].head(8).to_frame("saved_h").round(1)
    t["share_of_saved_pct"] = (100 * t.saved_h / (main.saved_s.sum() / 3600)).round(1)
    t.index = pd.Index([mask_path(p) for p in t.index], name="project")
    s.tables["by_project"] = t
    s.tables["modes"] = pd.DataFrame({
        "saved_h": [r1(saved[m].saved_s.sum() / 3600) for m in MODES],
        "saved_pct": [r1(100 * saved[m].saved_s.sum() / 3600 / total_h) for m in MODES],
        "jobs_cut": [int((saved[m].saved_s > 0).sum()) for m in MODES],
    }, index=pd.Index(list(MODES.values()), name="что прерываем"))

    obs = g[g.started].queued_s.dropna()
    k = calibrate(g, float(obs.quantile(0.95)))
    w0, b0 = simulate(g, k)
    w1, b1 = simulate(g, k, "no_deploy")
    s.tables["queue"] = pd.DataFrame({
        "p50_s": [q(obs, .5), q(w0, .5), q(w1, .5)],
        "p95_s": [q(obs, .95), q(w0, .95), q(w1, .95)],
        "p99_s": [q(obs, .99), q(w0, .99), q(w1, .99)],
        "busy_h": [r1(total_h), r1(b0), r1(b1)],
    }, index=pd.Index(["факт", f"модель, K={k}, как есть", f"модель, K={k}, interruptible кроме выкатки"], name="вариант"))

    src, p = "gitlab", s.period
    pct = lambda m: r1(100 * saved[m].saved_s.sum() / 3600 / total_h)  # noqa: E731
    s.metrics = [
        Metric("whatif.interruptible_saved_h", "Освободилось бы часов раннеров (interruptible кроме выкатки)", r1(main.saved_s.sum() / 3600), "ч", src, p),
        Metric("whatif.interruptible_saved_pct", "Освободилось бы времени раннеров (interruptible кроме выкатки)", pct("no_deploy"), "%", src, p),
        Metric("whatif.interruptible_all_pct", "То же, все задания (верхняя граница)", pct("all"), "%", src, p),
        Metric("whatif.interruptible_no_main_pct", "То же, кроме веток main/master (осторожный вариант)", pct("no_main"), "%", src, p),
        Metric("whatif.jobs_cut", "Заданий было бы остановлено (кроме выкатки)", int((main.saved_s > 0).sum()), "заданий", src, p),
        Metric("whatif.main_share_of_saved_pct", "Доля экономии на ветках main/master", r1(100 * main[main.ref.isin(PROTECTED)].saved_s.sum() / max(main.saved_s.sum(), 1)), "%", src, p),
        Metric("whatif.sim_slots", "Слотов в откалиброванной модели", int(k), "шт.", src, p),
        Metric("whatif.sim_p95_before_s", "Модель: очередь p95 как есть", q(w0, .95), "с", src, p),
        Metric("whatif.sim_p95_after_s", "Модель: очередь p95 с interruptible кроме выкатки", q(w1, .95), "с", src, p),
        Metric("whatif.sim_p99_before_s", "Модель: очередь p99 как есть", q(w0, .99), "с", src, p),
        Metric("whatif.sim_p99_after_s", "Модель: очередь p99 с interruptible кроме выкатки", q(w1, .99), "с", src, p),
    ]
    s.notes += [
        "Прямой replay: часы, которые задания проработали после появления более нового пайплайна на той же ветке. "
        "Он не зависит от модели очереди, это основная оценка. Выкатка — класс deploy по имени задания.",
        "Модель очереди грубая: один пул одинаковых слотов, без тегов; стадии не сдвигаются при ускорении предыдущих; "
        "проекты вне охвата GitLab-выгрузки учтены только через калибровку K (наименьшее K, при котором p95 модели "
        "не выше фактического). Сравнивать варианты модели между собой, а не с фактом.",
    ]
    return s
