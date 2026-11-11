"""Синтетический снимок: маленький, но со всеми случаями (неполные недели, отмены до старта, личный неймспейс)."""

import datetime as dt
import gzip
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runners.snapshot import author_hasher, write_gitlab_csv  # noqa: E402

LOGIN = "alice.agent"
HOT = "team/hot"
PERSONAL = "bob.smith/side"


def _jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def make_snapshot(root, with_gitlab=True):
    out = os.path.join(root, "20261109")
    db = os.path.join(out, "ci_analytics")
    # 01.10 (ср) … 04.11 (ср): полные недели 06.10–02.11, неполные края отбрасываются
    daily = []
    day = dt.date(2026, 10, 1)
    while day <= dt.date(2026, 11, 4):
        k = (day - dt.date(2026, 10, 1)).days + 1
        for path, scale in ((HOT, 3), (PERSONAL, 1)):
            daily.append({"day": day.isoformat(), "path": path, "outcome": "success", "attempts": k * scale,
                          "runner_hours": k * scale / 10, "queue_sum": 10.0 * k, "queue_count": k,
                          "duration_sum": 60.0 * k, "duration_count": k})
        day += dt.timedelta(days=1)
    _jsonl(os.path.join(db, "project_daily_report.jsonl.gz"), daily)
    _jsonl(os.path.join(db, "runners.jsonl.gz"), [
        {"id": 1, "data": {"online": True, "tag_list": ["cpu-4", "ram-4g"]}},
        {"id": 2, "data": {"online": False, "tag_list": ["cpu-2", "ram-3g"]}},
    ])
    base = dt.datetime(2026, 11, 1, 10, tzinfo=dt.timezone.utc)
    jd = []
    for i in range(10):
        t = base + dt.timedelta(hours=i)
        jd.append({"job_id": i, "data": {"name": "test:a", "stage": "test", "status": "success",
                                         "runner": {"id": 1 + i % 2}, "project": {"path_with_namespace": HOT},
                                         "queued_duration": float(i), "duration": 600.0,
                                         "created_at": _iso(t), "finished_at": _iso(t + dt.timedelta(minutes=10))}})
    jd.append({"job_id": 99, "data": {"name": "deploy", "status": "manual", "runner": None,
                                      "project": {"path_with_namespace": HOT}, "created_at": _iso(base),
                                      "finished_at": None}})
    _jsonl(os.path.join(db, "job_details.jsonl.gz"), jd)
    manifest = {"taken_at": "2026-11-09T12:00:00+00:00", "sources": {"ci_analytics": {"status": "ok"}}}

    if with_gitlab:
        rows = []
        for i in range(40):
            t = base + dt.timedelta(hours=i)
            cancel = i % 4 == 0
            rows.append({
                "project": HOT if i % 5 else PERSONAL, "job_id": i, "pipeline_id": i // 4,
                "name": ["test:pytest", "lint", "build"][i % 3], "stage": "test",
                "status": "canceled" if cancel else "success",
                "created_at": _iso(t), "started_at": "" if cancel else _iso(t + dt.timedelta(seconds=i)),
                "finished_at": _iso(t + dt.timedelta(minutes=5)), "queued_s": float(i),
                "duration_s": "" if cancel else 120.0, "runner_id": "" if cancel else 1,
                "runner": "" if cancel else "host-1", "user": LOGIN, "ref": f"feat/{i % 3}",
                "failure_reason": "", "commit_title": "secret commit title",
            })
        write_gitlab_csv(rows, out, author_hasher())
        manifest["sources"]["gitlab"] = {"status": "ok"}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return out


@pytest.fixture
def snap_root(tmp_path):
    root = tmp_path / "snapshots"
    make_snapshot(str(root))
    return str(root)
