"""Выгрузка истории CI-заданий из GitLab для базовой линии (шаг 1 плана).

Берёт проекты, где у владельца токена есть членство, и задания за N дней.
Пишет CSV: одна строка на задание.

Окружение: GITLAB_TOKEN, GITLAB_URL (по умолчанию https://gitlab.raftds.com).
Запуск: python collect/ci_history.py --days 30 --out data/jobs.csv
"""

import argparse
import csv
import datetime as dt
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import requests

FIELDS = [
    "project", "job_id", "pipeline_id", "name", "stage", "status",
    "created_at", "started_at", "finished_at", "queued_s", "duration_s",
    "runner_id", "runner", "user", "ref", "failure_reason", "commit_title",
]


def session(token):
    s = requests.Session()
    s.headers["PRIVATE-TOKEN"] = token
    return s


def paged(s, url, params):
    page = 1
    while True:
        r = s.get(url, params={**params, "per_page": 100, "page": page}, timeout=60)
        r.raise_for_status()
        items = r.json()
        if not items:
            return
        yield from items
        if not r.headers.get("X-Next-Page"):
            return
        page = int(r.headers["X-Next-Page"])


def project_jobs(s, base, project, since):
    rows = []
    for j in paged(s, f"{base}/api/v4/projects/{project['id']}/jobs", {}):
        created = dt.datetime.fromisoformat(j["created_at"].replace("Z", "+00:00"))
        if created < since:
            break
        runner = j.get("runner") or {}
        commit = j.get("commit") or {}
        rows.append({
            "project": project["path_with_namespace"],
            "job_id": j["id"],
            "pipeline_id": (j.get("pipeline") or {}).get("id"),
            "name": j["name"],
            "stage": j["stage"],
            "status": j["status"],
            "created_at": j["created_at"],
            "started_at": j.get("started_at"),
            "finished_at": j.get("finished_at"),
            "queued_s": j.get("queued_duration"),
            "duration_s": j.get("duration"),
            "runner_id": runner.get("id"),
            "runner": runner.get("description"),
            "user": (j.get("user") or {}).get("username"),
            "ref": j.get("ref"),
            "failure_reason": j.get("failure_reason"),
            "commit_title": (commit.get("title") or "")[:200],
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="data/jobs.csv")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    base = os.environ.get("GITLAB_URL", "https://gitlab.raftds.com").rstrip("/")
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        sys.exit("GITLAB_TOKEN не задан")

    s = session(token)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    projects = list(paged(s, f"{base}/api/v4/projects", {
        "membership": "true", "simple": "true",
        "last_activity_after": since.isoformat(),
    }))
    print(f"проектов с активностью: {len(projects)}", file=sys.stderr)

    rows = []
    with ThreadPoolExecutor(args.workers) as pool:
        for part in pool.map(lambda p: project_jobs(s, base, p, since), projects):
            rows.extend(part)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"заданий: {len(rows)} -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
