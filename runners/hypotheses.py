"""Офлайн-проверка гипотез из docs/research/agentic-ci-hypotheses.md повторным прогоном истории (E06).

Каждая функция возвращает строки таблицы: гипотеза, оценка экономии (часы и доля), риск и правило.
Оценки — верхние границы по истории, без обратной связи (агент мог бы вести себя иначе).
Источники: БД ci_analytics (все проекты, 15 дней, есть allow_failure), GitLab (охват токена, 30 дней, есть ветки).
"""

import pandas as pd

from runners.metrics import DONE, Metric, Section, classify, period, r1

HEAVY = ("test", "browser-e2e")  # классы, которые остаются тяжёлыми после линта


def _gitlab(snap):
    """Задания GitLab с источником пайплайна и allow_failure из БД, где он известен."""
    g = snap.gitlab[snap.gitlab.status.isin(DONE)].copy()
    g["cls"] = g.name.map(classify)
    g["h"] = g.duration_s.fillna(0) / 3600
    if snap.pipelines is not None:
        g = g.merge(snap.pipelines[["pipeline_id", "source"]], on="pipeline_id", how="left")
    if snap.jobs is not None:
        g = g.merge(snap.jobs[["job_id", "allow_failure"]], on="job_id", how="left")
    g["allow_failure"] = g.get("allow_failure", False)
    g["allow_failure"] = g["allow_failure"].fillna(False).astype(bool)
    g["hard_fail"] = (g.status == "failed") & ~g.allow_failure
    return g


def h1_fail_fast(snap):
    """H1: пайплайн останавливается на первом упавшем задании (allow_failure не считается).

    Сэкономлено: задания, стартовавшие после первого падения, целиком; шедшие в момент падения — остаток.
    Риск: агент не видит остальных ошибок пайплайна в этом прогоне (увидит в следующем).
    """
    j = snap.jobs[snap.jobs.status.isin(DONE)].copy()
    total = j.duration_s.fillna(0).sum() / 3600
    first = j[(j.status == "failed") & ~j.allow_failure].groupby("pipeline_id").finished_at.min().rename("ff")
    k = j.merge(first, left_on="pipeline_id", right_index=True)
    k = k[k.started_at.notna()]
    after = k[k.started_at >= k.ff].duration_s.fillna(0).sum()
    over = k[(k.started_at < k.ff) & (k.finished_at > k.ff)]
    rest = (over.finished_at - over.ff).dt.total_seconds().clip(lower=0).sum()
    saved = (after + rest) / 3600
    return {"id": "h1", "hyp": "H1 Пайплайн умирает на первой ошибке", "src": "ci_analytics",
            "saved_h": saved, "total_h": total, "risk": "остальные ошибки пайплайна агент увидит в следующем прогоне"}


def _streaks(g):
    pp = (g.groupby(["project", "ref", "pipeline_id"])
          .agg(c=("created_at", "min"), red=("hard_fail", "any"), source=("source", "first")).reset_index())
    pp = pp[pp.source.isin(["push", "merge_request_event"])].sort_values(["project", "ref", "c", "pipeline_id"])
    pos = []
    for _, grp in pp.groupby(["project", "ref"], sort=False):
        c = 0
        for red in grp.red:
            c = c + 1 if red else 0
            pos.append(c)
    pp["streak"] = pos
    pp["prev"] = pp.groupby(["project", "ref"]).pipeline_id.shift(1)
    return pp


def h3_breaker(snap, after=3):
    """H3: после `after` красных подряд на ветке пайплайн гоняет только линт и то, что упало в прошлый раз.

    Считаются только красные пайплайны на позиции > after в серии. Сэкономлено: часы остальных заданий.
    Риск: доля таких пайплайнов, где упало задание, которого не было в прошлом падении (ошибка найдётся позже).
    """
    g = _gitlab(snap)
    total = g.h.sum()
    pp = _streaks(g)
    tail = pp[(pp.streak > after)]
    failed_names = g[g.hard_fail].groupby("pipeline_id").name.apply(set)
    saved, missed = 0.0, 0
    for _, row in tail.iterrows():
        jobs = g[g.pipeline_id == row.pipeline_id]
        keep = failed_names.get(row.prev, set())
        run = jobs.name.isin(keep) | (jobs.cls == "lint")
        saved += jobs[~run].h.sum()
        if not set(jobs[jobs.hard_fail].name) <= (keep | set(jobs[jobs.cls == "lint"].name)):
            missed += 1
    return {"id": "h3", "hyp": f"H3 Предохранитель после {after} красных подряд", "src": "gitlab",
            "saved_h": saved, "total_h": total,
            "risk": f"в {missed} из {len(tail)} пайплайнов упало новое задание — найдётся при полном прогоне"}


def h2_preflight(snap):
    """H2: агент не пушит, пока у себя не прошли линт и затронутые тесты.

    Красный пайплайн считается предотвращённым, если все его жёсткие падения — script_failure в классах
    lint/test/other (воспроизводятся локально). Сэкономлено: все часы таких пайплайнов.
    Нижняя граница — пайплайны, упавшие только на линте (линт локально — секунды).
    Риск: тесты, зависящие от окружения CI, локально могут пройти.
    """
    g = _gitlab(snap)
    total = g.h.sum()
    fails = g[g.hard_fail]
    by = fails.groupby("pipeline_id").agg(
        local=("failure_reason", lambda s: (s.fillna("script_failure") == "script_failure").all()),
        cls=("cls", lambda s: set(s)))
    local = by[by.local & by.cls.map(lambda c: c <= {"lint", "test", "other"})].index
    lint_only = by[by.local & by.cls.map(lambda c: c == {"lint"})].index
    ph = g.groupby("pipeline_id").h.sum()
    return [
        {"id": "h2", "hyp": "H2 Предполётная проверка у агента (линт + тесты)", "src": "gitlab",
         "saved_h": ph.reindex(local).sum(), "total_h": total, "risk": "тесты, зависящие от окружения CI"},
        {"id": "h2lint", "hyp": "H2 нижняя граница: пайплайны, упавшие только на линте", "src": "gitlab",
         "saved_h": ph.reindex(lint_only).sum(), "total_h": total, "risk": "почти нет: линт детерминирован"},
    ]


def h10_train_only(snap):
    """H10: полный набор тестов только один раз на ветку MR (последний пайплайн — как поезд слияния).

    Ветки: MR-пайплайны и push в не-основные ветки. Сэкономлено: часы классов test/browser-e2e во всех
    пайплайнах ветки, кроме последнего. Верхняя граница: затронутые тесты на MR тоже чего-то стоят.
    """
    g = _gitlab(snap)
    total = g.h.sum()
    br = g[(g.source.isin(["merge_request_event", "push"])) & ~g.ref.isin(["main", "master"])]
    last = br.groupby(["project", "ref"]).pipeline_id.max()
    keep = br.pipeline_id.isin(set(last))
    saved = br[~keep & br.cls.isin(HEAVY)].h.sum()
    runs = br[br.cls.isin(HEAVY)].groupby(["project", "ref"]).pipeline_id.nunique()
    return {"id": "h10", "hyp": "H10 Полный набор тестов — один раз на ветку", "src": "gitlab",
            "saved_h": saved, "total_h": total,
            "risk": f"верхняя граница; сейчас в среднем {r1(runs.mean())} прогонов тяжёлых тестов на ветку"}


def h12_batching(snap, window_min=30):
    """H12: пайплайны основной ветки от push проверяются пачками за окно `window_min` минут.

    Пачка стоит как её самый дорогой пайплайн; пачка с падением — плюс бисекция (log2(k) прогонов).
    Сэкономлено: сумма часов минус стоимость пачек.
    """
    g = _gitlab(snap)
    total = g.h.sum()
    main = g[(g.source == "push") & g.ref.isin(["main", "master"])]
    pp = (main.groupby(["project", "pipeline_id"])
          .agg(c=("created_at", "min"), h=("h", "sum"), red=("hard_fail", "any")).reset_index()
          .sort_values(["project", "c"]))
    cost, batches = 0.0, 0
    import math
    for _, grp in pp.groupby("project"):
        start, batch = None, []
        for row in list(grp.itertuples()) + [None]:
            if row is not None and start is not None and (row.c - start).total_seconds() <= window_min * 60:
                batch.append(row)
                continue
            if batch:
                k = len(batch)
                one = max(b.h for b in batch)
                cost += one * (1 + (math.ceil(math.log2(k)) if any(b.red for b in batch) and k > 1 else 0))
                batches += 1
            if row is not None:
                start, batch = row.c, [row]
    saved = pp.h.sum() - cost  # отрицательное значение: пачки с бисекцией дороже, чем по одному
    return {"id": "h12", "hyp": f"H12 Пачки пайплайнов основной ветки, окно {window_min} мин", "src": "gitlab",
            "saved_h": saved, "total_h": total,
            "risk": f"{len(pp)} пайплайнов → {batches} пачек; красных {r1(100 * pp.red.mean())}% — бисекция съедает выигрыш"}


def h13_builds(snap):
    """H13: сборки образов. Доля часов класса build и повторы одноимённых сборок в проекте за сутки."""
    g = _gitlab(snap)
    total = g.h.sum()
    b = g[g.cls == "build"].copy()
    b["day"] = b.created_at.dt.date
    per = b.groupby(["project", "name", "day"]).size()
    return {"id": "h13", "hyp": "H13 Сборки образов (верхняя граница общего кэша)", "src": "gitlab",
            "saved_h": b.h.sum(), "total_h": total,
            "risk": f"в среднем {r1(per.mean())} одноимённых сборок в проекте за сутки; реальная доля попаданий в кэш неизвестна"}


def evaluate(snap):
    s = Section("hyp", "Гипотезы: офлайн-оценка по истории", "gitlab + ci_analytics", "")
    if snap.gitlab is None or snap.jobs is None or snap.pipelines is None:
        s.missing = True
        return s
    rows = [h1_fail_fast(snap), h3_breaker(snap), *h2_preflight(snap), h10_train_only(snap),
            h12_batching(snap), h13_builds(snap)]
    t = pd.DataFrame(rows).set_index("id")
    t["saved_pct"] = (100 * t.saved_h / t.total_h).round(1)
    t["saved_h"] = t.saved_h.round(0)
    t["total_h"] = t.total_h.round(0)
    s.tables["hypotheses"] = t[["hyp", "src", "saved_h", "total_h", "saved_pct", "risk"]]
    s.period = (f"GitLab {snap.gitlab.created_at.min():%Y-%m-%d} — {snap.gitlab.created_at.max():%Y-%m-%d}; "
                f"БД {snap.jobs.finished_at.min():%Y-%m-%d} — {snap.jobs.finished_at.max():%Y-%m-%d}")
    for hid, row in t.iterrows():
        src = row.src
        p = s.period
        s.metrics += [
            Metric(f"hyp.{hid}_saved_h", f"{row.hyp}: часов", r1(row.saved_h), "ч", src, p),
            Metric(f"hyp.{hid}_saved_pct", f"{row.hyp}: доля часов", r1(row.saved_pct), "%", src, p),
        ]
    s.notes += ["Оценки — верхние границы по истории, без учёта того, как агент изменил бы поведение; эффекты пересекаются, "
                "складывать их нельзя. Правила — в docstring функций runners/hypotheses.py."]
    return s


__all__ = ["evaluate", "period"]
