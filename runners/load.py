"""Загрузка снимка из data/snapshots/<YYYYMMDD>/ в DataFrame."""

import gzip
import json
import os
import re
from dataclasses import dataclass

import pandas as pd


class SnapshotMissing(Exception):
    pass


@dataclass
class Snapshot:
    path: str
    manifest: dict
    gitlab: pd.DataFrame | None       # задания GitLab, одна строка = задание
    daily: pd.DataFrame | None        # project_daily_report: агрегаты по проекту и дню
    jobs: pd.DataFrame | None         # job_details, развёрнутый JSON
    runners: pd.DataFrame | None
    fleet: pd.DataFrame | None = None  # docs/research/fleet.csv: размер парка во времени, с источником


def latest(root):
    days = sorted(d for d in os.listdir(root) if re.fullmatch(r"\d{8}", d)) if os.path.isdir(root) else []
    if not days:
        raise SnapshotMissing(f"В {root} нет снимков")
    return days[-1]


def _jsonl(path):
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _daily(rows):
    if rows is None:
        return None
    df = pd.DataFrame(rows)
    df["day"] = pd.to_datetime(df["day"])
    return df


def _jobs(rows):
    if rows is None:
        return None
    recs = []
    for r in rows:
        d = r["data"]
        runner = d.get("runner") or {}
        recs.append({
            "job_id": r["job_id"],
            "project": (d.get("project") or {}).get("path_with_namespace"),
            "name": d.get("name"),
            "stage": d.get("stage"),
            "status": d.get("status"),
            "runner_id": runner.get("id"),
            "queued_s": d.get("queued_duration"),
            "duration_s": d.get("duration"),
            "created_at": d.get("created_at"),
            "finished_at": d.get("finished_at"),
        })
    df = pd.DataFrame(recs)
    for c in ("created_at", "finished_at"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    return df


def _runners(rows):
    if rows is None:
        return None
    return pd.DataFrame([{"runner_id": r["id"], "online": r["data"].get("online"),
                          "tags": r["data"].get("tag_list") or []} for r in rows])


def _gitlab(path):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, compression="gzip")
    for c in ("created_at", "started_at", "finished_at"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    return df


def _fleet(path):
    if not path or not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    return df.sort_values("date")


def load(root="data/snapshots", day=None, fleet="docs/research/fleet.csv"):
    day = day or latest(root)
    path = os.path.join(root, day)
    if not os.path.isdir(path):
        raise SnapshotMissing(f"Снимка {path} нет")
    with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    db = os.path.join(path, "ci_analytics")
    return Snapshot(
        path=path,
        manifest=manifest,
        gitlab=_gitlab(os.path.join(path, "gitlab_jobs.csv.gz")),
        daily=_daily(_jsonl(os.path.join(db, "project_daily_report.jsonl.gz"))),
        jobs=_jobs(_jsonl(os.path.join(db, "job_details.jsonl.gz"))),
        runners=_runners(_jsonl(os.path.join(db, "runners.jsonl.gz"))),
        fleet=_fleet(fleet),
    )
