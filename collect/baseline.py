"""Базовая линия по CSV из ci_history.py: очереди, длительности, нагрузка на раннеры.

Запуск: python collect/baseline.py data/jobs_30d.csv > docs/experiments/E00-baseline.md
"""

import re
import sys

import pandas as pd

CLASSES = [
    ("lint", r"lint|eslint|ruff|flake|mypy|format|prettier|check|typecheck|sast|secret"),
    ("browser-e2e", r"e2e|playwright|cypress|selenium|browser|ui-test"),
    ("test", r"test|pytest|jest|vitest|spec|coverage"),
    ("build", r"build|compile|image|docker|kaniko|package"),
    ("deploy", r"deploy|release|publish|pages|helm|rollout"),
]


def classify(name):
    n = name.lower()
    for cls, rx in CLASSES:
        if re.search(rx, n):
            return cls
    return "other"


def host(runner):
    # notebook-runner-5-2 -> notebook-runner-5: несколько регистраций на одном хосте
    if not isinstance(runner, str):
        return "-"
    return re.sub(r"-\d+$", "", runner) if re.search(r"runner-\d+-\d+$|-2$", runner) else runner


def q(s, p):
    s = s.dropna()
    return round(float(s.quantile(p)), 1) if len(s) else float("nan")


def md(df):
    return df.to_markdown()


def main(path):
    df = pd.read_csv(path, parse_dates=["created_at", "started_at", "finished_at"])
    df = df[df["status"].isin(["success", "failed", "canceled"])].copy()
    df["cls"] = df["name"].map(classify)
    df["host"] = df["runner"].map(host)
    df["day"] = df["created_at"].dt.tz_convert("Europe/Moscow").dt.date
    df["hour"] = df["created_at"].dt.tz_convert("Europe/Moscow").dt.hour

    span = (df["created_at"].max() - df["created_at"].min()).days or 1
    print(f"# E00. Базовая линия CI\n")
    print(f"- Источник: `{path}`, заданий: {len(df)}, проектов: {df['project'].nunique()}, "
          f"период: {df['created_at'].min():%Y-%m-%d} — {df['created_at'].max():%Y-%m-%d} ({span} дн.)")
    print(f"- Очередь, с: p50={q(df.queued_s, .5)}, p95={q(df.queued_s, .95)}, p99={q(df.queued_s, .99)}")
    print(f"- Длительность, с: p50={q(df.duration_s, .5)}, p95={q(df.duration_s, .95)}")
    print(f"- Доля упавших: {round((df.status == 'failed').mean() * 100, 1)}%\n")

    print("## По хостам раннеров\n")
    g = df.groupby("host").agg(
        jobs=("job_id", "count"),
        busy_h=("duration_s", lambda s: round(s.sum() / 3600, 1)),
        queue_p50=("queued_s", lambda s: q(s, .5)),
        queue_p95=("queued_s", lambda s: q(s, .95)),
        dur_p50=("duration_s", lambda s: q(s, .5)),
        dur_p95=("duration_s", lambda s: q(s, .95)),
    ).sort_values("busy_h", ascending=False)
    print(md(g), "\n")

    print("## По классам заданий\n")
    c = df.groupby("cls").agg(
        jobs=("job_id", "count"),
        busy_h=("duration_s", lambda s: round(s.sum() / 3600, 1)),
        dur_p50=("duration_s", lambda s: q(s, .5)),
        dur_p95=("duration_s", lambda s: q(s, .95)),
        fail_pct=("status", lambda s: round((s == "failed").mean() * 100, 1)),
    ).sort_values("busy_h", ascending=False)
    c["share_pct"] = (c.busy_h / c.busy_h.sum() * 100).round(1)
    print(md(c), "\n")

    print("## Топ проектов по занятому времени\n")
    p = df.groupby("project").agg(
        jobs=("job_id", "count"),
        pipelines=("pipeline_id", "nunique"),
        busy_h=("duration_s", lambda s: round(s.sum() / 3600, 1)),
        queue_p95=("queued_s", lambda s: q(s, .95)),
    ).sort_values("busy_h", ascending=False).head(15)
    print(md(p), "\n")

    print("## По дням\n")
    d = df.groupby("day").agg(
        jobs=("job_id", "count"),
        pipelines=("pipeline_id", "nunique"),
        busy_h=("duration_s", lambda s: round(s.sum() / 3600, 1)),
        queue_p95=("queued_s", lambda s: q(s, .95)),
    )
    print(md(d), "\n")

    print("## Очередь по часам (МСК), p95, с\n")
    h = df.groupby("hour").agg(jobs=("job_id", "count"), queue_p95=("queued_s", lambda s: q(s, .95)))
    print(md(h), "\n")

    print("## Причины падений\n")
    print(md(df[df.status == "failed"].groupby("failure_reason").size().sort_values(ascending=False).to_frame("jobs")), "\n")

    print("## Авторы запусков (для разметки агент/человек)\n")
    print(md(df.groupby("user").agg(jobs=("job_id", "count"), pipelines=("pipeline_id", "nunique"))
             .sort_values("jobs", ascending=False).head(25)))


if __name__ == "__main__":
    main(sys.argv[1])
