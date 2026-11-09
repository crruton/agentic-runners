"""Показатели отчёта: чистые функции Snapshot → числа и таблицы, без отрисовки.

Каждый показатель — Metric с источником и периодом. Таблицы разделов — DataFrame.
"""

import re
from dataclasses import dataclass, field

import pandas as pd

TZ = "Europe/Moscow"
DONE = ("success", "failed", "canceled")

# Классы заданий по имени, первое совпадение (как в collect/baseline.py)
CLASSES = [
    ("lint", r"lint|eslint|ruff|flake|mypy|format|prettier|check|typecheck|sast|secret"),
    ("browser-e2e", r"e2e|playwright|cypress|selenium|browser|ui-test"),
    ("test", r"test|pytest|jest|vitest|spec|coverage"),
    ("build", r"build|compile|image|docker|kaniko|package"),
    ("deploy", r"deploy|release|publish|pages|helm|rollout"),
]


@dataclass
class Metric:
    id: str
    label: str
    value: float | int | None
    unit: str
    source: str
    period: str


@dataclass
class Section:
    id: str
    title: str
    source: str
    period: str
    tables: dict = field(default_factory=dict)   # имя → DataFrame
    metrics: list = field(default_factory=list)  # Metric
    notes: list = field(default_factory=list)    # str
    missing: bool = False


def classify(name):
    n = str(name).lower()
    for cls, rx in CLASSES:
        if re.search(rx, n):
            return cls
    return "other"


def mask_path(path):
    """Личный неймспейс (имя.фамилия/проект) — это логин, маскируем."""
    ns, _, rest = str(path).partition("/")
    return f"<личный>/{rest}" if "." in ns and rest else path


def r1(x):
    return None if x is None or pd.isna(x) else round(float(x), 1)


def q(s, p):
    s = pd.Series(s).dropna()
    return r1(s.quantile(p)) if len(s) else None


def period(a, b):
    return f"{a:%Y-%m-%d} — {b:%Y-%m-%d}"


def _gitlab_done(g):
    return g[g["status"].isin(DONE)].copy()


def gitlab_period(g):
    return period(g["created_at"].min(), g["created_at"].max())


def daily_period(d):
    return period(d["day"].min(), d["day"].max())


def jobs_period(j):
    return period(j["finished_at"].min(), j["finished_at"].max())


# --- тренд по неделям (ci_analytics) -------------------------------------------------

def full_weeks(daily):
    """Недели пн–вс, целиком покрытые данными: неполные первая и последняя искажают рост."""
    d = daily.copy()
    d["week"] = d["day"] - pd.to_timedelta(d["day"].dt.weekday, unit="D")
    lo, hi = d["day"].min(), d["day"].max()
    d = d[(d["week"] >= lo) & (d["week"] + pd.Timedelta(days=6) <= hi)]
    w = d.groupby("week").agg(jobs=("attempts", "sum"), runner_h=("runner_hours", "sum"),
                              queue_sum=("queue_sum", "sum"), queue_count=("queue_count", "sum"),
                              dur_sum=("duration_sum", "sum"), dur_count=("duration_count", "sum"))
    w["avg_queue_s"] = w["queue_sum"] / w["queue_count"].where(w["queue_count"] > 0)
    w["avg_dur_s"] = w["dur_sum"] / w["dur_count"].where(w["dur_count"] > 0)
    w = w[["jobs", "runner_h", "avg_queue_s", "avg_dur_s"]].round(1)
    w["jobs"] = w["jobs"].astype(int)
    return w


GROWTH_DAYS = 61   # окно роста: последние 2 месяца, основной рост парка и нагрузки пришёлся на них
EDGE_WEEKS = 2     # начало и конец окна — среднее за 2 полные недели, чтобы одна провальная неделя не решала


def trend(snap):
    s = Section("trend", "Тренд по неделям", "ci_analytics", "")
    if snap.daily is None:
        s.missing = True
        return s
    w = full_weeks(snap.daily)
    s.period = period(w.index.min(), w.index.max() + pd.Timedelta(days=6))
    s.tables["weeks"] = w
    win = w[w.index >= snap.daily["day"].max() - pd.Timedelta(days=GROWTH_DAYS)]
    head, tail = win.head(EDGE_WEEKS), win.tail(EDGE_WEEKS)
    wp = period(win.index.min(), win.index.max() + pd.Timedelta(days=6))
    span = (f"среднее {head.index[0]:%d.%m}–{head.index[-1] + pd.Timedelta(days=6):%d.%m} → "
            f"{tail.index[0]:%d.%m}–{tail.index[-1] + pd.Timedelta(days=6):%d.%m}")
    first, last = w.iloc[0], w.iloc[-1]
    s.metrics = [
        Metric("trend.jobs_growth", f"Рост заданий в неделю за 2 месяца ({span})", r1(tail.jobs.mean() / head.jobs.mean()), "×", "ci_analytics", wp),
        Metric("trend.runner_h_growth", f"Рост часов раннеров в неделю за 2 месяца ({span})", r1(tail.runner_h.mean() / head.runner_h.mean()), "×", "ci_analytics", wp),
        Metric("trend.avg_dur_growth", f"Рост средней длительности задания за 2 месяца ({span})", r1(tail.avg_dur_s.mean() / head.avg_dur_s.mean()), "×", "ci_analytics", wp),
        Metric("trend.jobs_week_last", f"Заданий за неделю {w.index[-1]:%d.%m}", int(last.jobs), "заданий", "ci_analytics", s.period),
        Metric("trend.runner_h_week_last", f"Часов раннеров за неделю {w.index[-1]:%d.%m}", r1(last.runner_h), "ч", "ci_analytics", s.period),
        Metric("trend.jobs_growth_all", f"Рост заданий в неделю, {w.index[0]:%d.%m} → {w.index[-1]:%d.%m} (весь период, по одной неделе)", r1(last.jobs / first.jobs), "×", "ci_analytics", s.period),
    ]
    s.notes.append(f"Рост за 2 месяца: окно с {win.index.min():%d.%m}, края — среднее за {EDGE_WEEKS} полные недели. "
                   "Считаются только полные недели пн–вс.")
    return s


# --- очередь (gitlab) -------------------------------------------------------------------

def queue(snap):
    s = Section("queue", "Очередь: хвост по часам и дням", "gitlab", "")
    if snap.gitlab is None:
        s.missing = True
        return s
    g = _gitlab_done(snap.gitlab)
    s.period = gitlab_period(snap.gitlab)
    local = g["created_at"].dt.tz_convert(TZ)
    g["hour"], g["day"] = local.dt.hour, local.dt.date
    s.tables["by_hour"] = g.groupby("hour").agg(jobs=("job_id", "count"), queue_p95_s=("queued_s", lambda x: q(x, .95)))
    s.tables["by_day"] = g.groupby("day").agg(jobs=("job_id", "count"), queue_p95_s=("queued_s", lambda x: q(x, .95)))
    bad = (s.tables["by_day"]["queue_p95_s"] > 1800).sum()
    s.metrics = [
        Metric("queue.p50_s", "Очередь p50", q(g.queued_s, .5), "с", "gitlab", s.period),
        Metric("queue.p95_s", "Очередь p95", q(g.queued_s, .95), "с", "gitlab", s.period),
        Metric("queue.p99_s", "Очередь p99", q(g.queued_s, .99), "с", "gitlab", s.period),
        Metric("queue.days_p95_over_30min", "Дней с p95 очереди больше 30 мин", int(bad), "дней", "gitlab", s.period),
    ]
    s.notes.append("Задания со статусом success, failed, canceled. Время по Москве, по моменту создания задания.")
    return s


# --- отменённые до старта (gitlab) -----------------------------------------------------

def canceled(snap):
    s = Section("canceled", "Отменённые до старта", "gitlab", "")
    if snap.gitlab is None:
        s.missing = True
        return s
    g = snap.gitlab
    s.period = gitlab_period(g)
    done = _gitlab_done(g)
    c = done[(done.status == "canceled") & done.started_at.isna()].copy()
    wait = (c.finished_at - c.created_at).dt.total_seconds()
    c["day"] = c["created_at"].dt.tz_convert(TZ).dt.date
    s.tables["by_day"] = c.groupby("day").size().to_frame("jobs")
    by_proj = c.groupby("project").size().sort_values(ascending=False)
    top = by_proj.index[0] if len(by_proj) else None
    s.metrics = [
        Metric("canceled.before_start", "Отменено до старта", int(len(c)), "заданий", "gitlab", s.period),
        Metric("canceled.before_start_pct", "Доля отменённых до старта среди завершённых", r1(100 * len(c) / len(done)) if len(done) else None, "%", "gitlab", s.period),
        Metric("canceled.wait_p50_s", "Ожидание до отмены, p50", q(wait, .5), "с", "gitlab", s.period),
        Metric("canceled.wait_p95_s", "Ожидание до отмены, p95", q(wait, .95), "с", "gitlab", s.period),
        Metric("canceled.top_project_pct", f"Доля проекта {mask_path(top)} в отменах до старта", r1(100 * by_proj.iloc[0] / len(c)) if top else None, "%", "gitlab", s.period),
    ]
    s.notes.append("Отмена до старта: статус canceled, задание не получило раннер и не стартовало.")
    return s


# --- классы заданий (gitlab) -----------------------------------------------------------

def classes(snap):
    s = Section("classes", "Классы заданий", "gitlab", "")
    if snap.gitlab is None:
        s.missing = True
        return s
    g = _gitlab_done(snap.gitlab)
    s.period = gitlab_period(snap.gitlab)
    g["cls"] = g["name"].map(classify)
    t = g.groupby("cls").agg(jobs=("job_id", "count"), busy_h=("duration_s", lambda x: x.sum() / 3600),
                             dur_p50_s=("duration_s", lambda x: q(x, .5)), dur_p95_s=("duration_s", lambda x: q(x, .95)),
                             fail_pct=("status", lambda x: 100 * (x == "failed").mean()))
    t["share_pct"] = 100 * t.busy_h / t.busy_h.sum()
    t = t.sort_values("busy_h", ascending=False).round(1)
    s.tables["classes"] = t
    s.metrics = [Metric(f"classes.{k}_share_pct", f"Доля класса {k} в занятом времени", r1(v), "%", "gitlab", s.period)
                 for k, v in t["share_pct"].items()]
    return s


# --- топ проектов (ci_analytics) -------------------------------------------------------

def top_projects(snap, days=30):
    s = Section("projects", f"Топ проектов по часам раннеров, {days} дней", "ci_analytics", "")
    if snap.daily is None:
        s.missing = True
        return s
    d = snap.daily
    d = d[d["day"] > d["day"].max() - pd.Timedelta(days=days)]
    s.period = daily_period(d)
    t = d.groupby("path").agg(jobs=("attempts", "sum"), runner_h=("runner_hours", "sum"),
                              qs=("queue_sum", "sum"), qc=("queue_count", "sum"))
    t["share_pct"] = 100 * t.runner_h / t.runner_h.sum()
    t["avg_queue_s"] = t.qs / t.qc.where(t.qc > 0)
    t = t.drop(columns=["qs", "qc"]).sort_values("runner_h", ascending=False).round(1)
    t.index = [mask_path(p) for p in t.index]
    t.index.name = "project"
    s.tables["projects"] = t.head(10)
    s.metrics = [
        Metric("projects.total_runner_h", "Часов раннеров всего", r1(t.runner_h.sum()), "ч", "ci_analytics", s.period),
        Metric("projects.top_share_pct", f"Доля {t.index[0]} в часах раннеров", r1(t.share_pct.iloc[0]), "%", "ci_analytics", s.period),
        Metric("projects.top3_share_pct", "Доля трёх крупнейших проектов", r1(t.share_pct.head(3).sum()), "%", "ci_analytics", s.period),
    ]
    return s


# --- главный проект (gitlab) -----------------------------------------------------------

def hot_project(snap):
    s = Section("hot", "Главный проект", "gitlab", "")
    if snap.gitlab is None:
        s.missing = True
        return s
    g = _gitlab_done(snap.gitlab)
    s.period = gitlab_period(snap.gitlab)
    busy = g.groupby("project")["duration_s"].sum()
    name = busy.idxmax()
    p = g[g.project == name]
    days = max((snap.gitlab.created_at.max() - snap.gitlab.created_at.min()).days, 1)
    pipelines = p.pipeline_id.nunique()
    refs = p.ref.nunique()
    s.title = f"Главный проект: {mask_path(name)}"
    t = (p.groupby("name").agg(jobs=("job_id", "count"), busy_h=("duration_s", lambda x: x.sum() / 3600),
                               dur_p50_s=("duration_s", lambda x: q(x, .5)))
         .sort_values("busy_h", ascending=False).head(10).round(1))
    t["share_pct"] = (100 * t.busy_h / (p.duration_s.sum() / 3600)).round(1)
    s.tables["jobs"] = t
    author = p.author_hash.value_counts(normalize=True) if "author_hash" in p else pd.Series(dtype=float)
    s.metrics = [
        Metric("hot.busy_share_pct", "Доля в занятом времени (охват GitLab)", r1(100 * busy.max() / busy.sum()), "%", "gitlab", s.period),
        Metric("hot.pipelines_per_day", "Пайплайнов в сутки", r1(pipelines / days), "шт.", "gitlab", s.period),
        Metric("hot.jobs_per_pipeline", "Заданий на пайплайн", r1(len(p) / pipelines), "шт.", "gitlab", s.period),
        Metric("hot.pipelines_per_ref", "Пайплайнов на ветку", r1(pipelines / refs), "шт.", "gitlab", s.period),
        Metric("hot.top10_jobs_share_pct", "Доля 10 самых тяжёлых заданий в его времени", r1(t.share_pct.sum()), "%", "gitlab", s.period),
        Metric("hot.queue_p95_s", "Очередь p95", q(p.queued_s, .95), "с", "gitlab", s.period),
        Metric("hot.top_author_pct", "Доля заданий одного автора (агента)", r1(100 * author.iloc[0]) if len(author) else None, "%", "gitlab", s.period),
    ]
    return s


# --- раннеры (ci_analytics) ------------------------------------------------------------

def _tag(tags, prefix):
    hit = [t for t in tags if re.fullmatch(prefix + r"\d+g?", t)]
    return ",".join(sorted(hit)) if hit else "—"


def runners(snap):
    s = Section("runners", "Раннеры", "ci_analytics", "")
    if snap.jobs is None or snap.runners is None:
        s.missing = True
        return s
    j = snap.jobs[snap.jobs.runner_id.notna()].copy()
    s.period = jobs_period(snap.jobs)
    j["runner_id"] = j.runner_id.astype(int)
    t = j.groupby("runner_id").agg(jobs=("job_id", "count"), busy_h=("duration_s", lambda x: x.sum() / 3600),
                                   avg_queue_s=("queued_s", "mean"), p95_queue_s=("queued_s", lambda x: q(x, .95)))
    r = snap.runners.set_index("runner_id")
    r["cpu"] = r.tags.map(lambda x: _tag(x, "cpu-"))
    r["ram"] = r.tags.map(lambda x: _tag(x, "(ram-)?"))
    t = r[["online", "cpu", "ram"]].join(t, how="outer").sort_values("busy_h", ascending=False).round(1)
    s.tables["runners"] = t
    s.metrics = [
        Metric("runners.registered", "Раннеров зарегистрировано", int(len(r)), "шт.", "ci_analytics", s.period),
        Metric("runners.online", "Раннеров онлайн", int(r.online.fillna(False).astype(bool).sum()), "шт.", "ci_analytics", s.period),
        Metric("runners.busy_h_median", "Занятость раннера, медиана", r1(t.busy_h.median()), "ч", "ci_analytics", s.period),
    ]
    f = snap.fleet
    if f is not None and len(f) >= 2:
        a, b = f.iloc[0], f.iloc[-1]
        src = " + ".join(sorted({a.source, b.source}))
        fp = period(a.date, b.date)
        s.metrics += [
            Metric("runners.fleet_before", f"Раннеров на {a.date:%d.%m}", int(a.runners), "шт.", a.source, fp),
            Metric("runners.fleet_growth", f"Рост парка раннеров, {a.date:%d.%m} → {b.date:%d.%m}", r1(b.runners / a.runners), "×", src, fp),
        ]
        s.notes.append("Хосты — базовые ноутбуки на 8–16 ядер (со слов владельца). Теги cpu-N/ram-Ng — вероятно, "
                       "лимиты на задание в Docker executor, а не размер машины (docs/research/inventory.md).")
    return s


# --- качество данных -------------------------------------------------------------------

def quality(snap, sections):
    s = Section("quality", "Качество данных", "gitlab + ci_analytics", "")
    rows = []
    if snap.gitlab is not None:
        g = snap.gitlab
        rows.append(("gitlab", "задания", len(g), g.project.nunique(), gitlab_period(g)))
    if snap.daily is not None:
        rows.append(("ci_analytics", "дневные агрегаты", len(snap.daily), snap.daily.path.nunique(), daily_period(snap.daily)))
    if snap.jobs is not None:
        rows.append(("ci_analytics", "задания поштучно", len(snap.jobs), snap.jobs.project.nunique(), jobs_period(snap.jobs)))
    s.tables["sources"] = pd.DataFrame(rows, columns=["source", "what", "rows", "projects", "period"]).set_index("source")
    by = {m.id: m for sec in sections for m in sec.metrics}
    if snap.gitlab is not None:
        cls = by.get("classes.other_share_pct")
        if cls:
            s.notes.append(f"Класс other (имя задания не распознано): {cls.value}% занятого времени.")
        s.notes.append("Охват GitLab — только проекты, где у владельца токена есть членство. Доли «от всего парка» берутся из ci_analytics.")
    if snap.jobs is not None:
        st = snap.jobs.status.value_counts()
        s.metrics.append(Metric("quality.ci_analytics_canceled", "Отмен в ci_analytics (поштучно)", int(st.get("canceled", 0)), "заданий", "ci_analytics", jobs_period(snap.jobs)))
        s.metrics.append(Metric("quality.ci_analytics_manual", "Ручных незапущенных в ci_analytics", int(st.get("manual", 0)), "заданий", "ci_analytics", jobs_period(snap.jobs)))
        s.notes.append("В ci_analytics почти не видны отмены до старта; задания без раннера там — в основном manual. Отмены считаем по GitLab.")
    return s


def compute(snap):
    from runners.whatif import whatif  # whatif использует примитивы этого модуля

    sections = [trend(snap), queue(snap), canceled(snap), classes(snap), top_projects(snap), hot_project(snap),
                runners(snap), job_resources(snap), whatif(snap)]
    sections.append(quality(snap, sections))
    return sections


KPI = ["trend.jobs_growth", "trend.runner_h_growth", "runners.fleet_growth", "queue.p95_s", "queue.p99_s",
       "canceled.before_start_pct", "projects.top_share_pct", "whatif.interruptible_saved_pct"]


# --- ресурсы задания из логов (cgroup v2) ----------------------------------------------

def job_resources(snap):
    s = Section("cgroup", "Ресурсы задания: cgroup v2 по логам шардов", "gitlab-логи", "")
    r = snap.resources
    if r is None or not len(r) or not r.parsed.sum():
        s.missing = True
        return s
    r = r[r.parsed == 1].copy()
    s.period = period(r.created_at.min(), r.created_at.max())
    oom = r.job_oom_kill.fillna(0) > 0
    if snap.gitlab is not None:
        r = r.merge(snap.gitlab[["job_id", "duration_s"]], on="job_id", how="left")
    else:
        r["duration_s"] = float("nan")
    at_max = r.mem_max_mib.notna() & (r.mem_peak_mib >= r.mem_max_mib)
    t = (r.groupby("runner").agg(jobs=("job_id", "count"), cpu_count=("cpu_count", "max"), nproc=("nproc", "max"),
                                 mem_max_mib=("mem_max_mib", "max"),
                                 oom_jobs=("job_oom_kill", lambda x: int((x.fillna(0) > 0).sum())),
                                 host_oom_total=("host_oom_kill_total", "max"),
                                 dur_p50_s=("duration_s", lambda x: q(x, .5)))
         .sort_values("jobs", ascending=False))
    s.tables["by_runner"] = t
    w = (r.groupby("mem_max_mib").agg(jobs=("job_id", "count"), workers_p50=("workers", "median"),
                                      oom_pct=("job_oom_kill", lambda x: r1(100 * (x.fillna(0) > 0).mean())),
                                      dur_p50_s=("duration_s", lambda x: q(x, .5))))
    w["at_max_pct"] = r.assign(a=at_max).groupby("mem_max_mib").a.mean().mul(100).round(1)
    s.tables["by_mem_max"] = w
    src, p = "gitlab-логи", s.period
    s.metrics = [
        Metric("cgroup.jobs", "Заданий с телеметрией cgroup", int(len(r)), "заданий", src, p),
        Metric("cgroup.host_cpu_p50", "Ядер у хоста (cpu_count), медиана", r1(r.cpu_count.median()), "шт.", src, p),
        Metric("cgroup.job_cpu_p50", "CPU, видимых заданию (cpuset nproc), медиана", r1(r.nproc.median()), "шт.", src, p),
        Metric("cgroup.at_mem_max_pct", "Заданий, упёршихся в memory.max (peak = max)", r1(100 * at_max.mean()), "%", src, p),
        Metric("cgroup.oom_kill_pct", "Заданий с OOM-kill внутри cgroup", r1(100 * oom.mean()), "%", src, p),
        Metric("cgroup.oom_kill_jobs", "Заданий с OOM-kill внутри cgroup", int(oom.sum()), "заданий", src, p),
        Metric("cgroup.hosts_with_oom", "Регистраций на хостах, где ядро уже убивало процессы (oom_kill > 0)", int((t.host_oom_total.fillna(0) > 0).sum()), "шт.", src, p),
    ]
    # Естественный эксперимент: те же шарды на хостах разного класса (по числу ядер хоста)
    for cores, h in r.groupby("cpu_count"):
        c = int(cores)
        s.metrics += [
            Metric(f"cgroup.cpu{c}_jobs", f"Хосты {c} ядер: заданий", int(len(h)), "заданий", src, p),
            Metric(f"cgroup.cpu{c}_dur_p50_s", f"Хосты {c} ядер: медиана длительности шарда", q(h.duration_s, .5), "с", src, p),
            Metric(f"cgroup.cpu{c}_oom_pct", f"Хосты {c} ядер: заданий с OOM-kill в cgroup", r1(100 * (h.job_oom_kill.fillna(0) > 0).mean()), "%", src, p),
            Metric(f"cgroup.cpu{c}_host_oom_max", f"Хосты {c} ядер: OOM-kill ядра хоста с загрузки, максимум", r1(h.host_oom_kill_total.max()), "шт.", src, p),
        ]
    s.notes += [
        "Источник — строки job-resources: в логах шардов santinel (scripts/ci_job_resources.py проекта). Только этот проект, "
        "только шарды pytest/dbt/tia с 26.10.",
        "peak включает page cache: упор в memory.max сам по себе не доказывает OOM; доказательство — oom_kill > 0 у cgroup задания.",
        "Число xdist-воркеров шард выбирает сам из memory.max и cpuset: меньше памяти — меньше воркеров — дольше задание.",
    ]
    return s
