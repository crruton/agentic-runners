"""Показатели проекта для пилота ci-preflight: «до» и «после» по снимкам БД ci_analytics (все проекты)."""

import pandas as pd

from runners.metrics import DONE, r1


def kpis(snap, project):
    """Часы раннеров в сутки, доля красных пайплайнов, самая длинная красная серия, медиана задания."""
    j = snap.jobs[(snap.jobs.project == project) & snap.jobs.status.isin(DONE)].copy()
    if j.empty:
        return None
    days = max((j.finished_at.max() - j.finished_at.min()).days, 1)
    pp = (j.groupby("pipeline_id")
          .agg(c=("created_at", "min"), red=("status", lambda s: False), h=("duration_s", lambda s: s.sum() / 3600))
          .sort_values("c"))
    hard = j[(j.status == "failed") & ~j.allow_failure].pipeline_id.unique()
    pp["red"] = pp.index.isin(hard)
    streak = best = 0
    for red in pp.red:
        streak = streak + 1 if red else 0
        best = max(best, streak)
    return {
        "project": project,
        "period": f"{j.finished_at.min():%Y-%m-%d} — {j.finished_at.max():%Y-%m-%d}",
        "runner_h_per_day": r1(j.duration_s.sum() / 3600 / days),
        "pipelines_per_day": r1(len(pp) / days),
        "red_pipelines_pct": r1(100 * pp.red.mean()),
        "red_hours_pct": r1(100 * pp[pp.red].h.sum() / pp.h.sum()) if pp.h.sum() else None,
        "max_red_streak": int(best),
        "job_p50_min": r1(j.duration_s.median() / 60),
    }


def table(snap, projects):
    return pd.DataFrame([k for p in projects if (k := kpis(snap, p))]).set_index("project")
