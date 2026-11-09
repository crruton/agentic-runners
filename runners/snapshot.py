"""Разовый снимок источников CI в data/snapshots/<YYYYMMDD>/.

Структура снимка:
  ci_analytics/<таблица>.jsonl.gz  — все таблицы и представления схемы public, строка = запись
  gitlab_jobs.csv.gz               — задания GitLab без логинов и заголовков коммитов, автор как хэш
  manifest.json                    — время, число строк, параметры, статус источников

Окружение: CI_ANALYTICS_DSN, GITLAB_TOKEN, GITLAB_URL (по умолчанию https://gitlab.raftds.com).
"""

import csv
import datetime as dt
import decimal
import gzip
import hashlib
import json
import os
import secrets
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

# Колонки, которые не попадают в снимок: персональные данные (ПДн)
PII = ("user", "commit_title")


def _default(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    return str(v)


def _publish(part, final):
    os.replace(part, final)


def _manifest_path(out):
    return os.path.join(out, "manifest.json")


def _read_manifest(out):
    try:
        with open(_manifest_path(out), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"sources": {}}


def _write_manifest(out, m):
    m["taken_at"] = m.get("taken_at") or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(_manifest_path(out), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2, sort_keys=True)


def author_hasher():
    """Хэш автора с солью, которая нигде не сохраняется: стабилен внутри снимка, не обратим перебором."""
    salt = secrets.token_bytes(16)
    return lambda name: hashlib.sha256(salt + str(name).encode()).hexdigest()[:12] if name else ""


def snapshot_db(dsn, out):
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=20)
    conn.set_session(readonly=True)
    cur = conn.cursor()
    cur.execute("select table_name from information_schema.tables where table_schema = 'public' order by 1")
    tables = [r[0] for r in cur.fetchall()]
    d = os.path.join(out, "ci_analytics")
    os.makedirs(d, exist_ok=True)
    counts = {}
    for t in tables:
        named = conn.cursor(name=f"snap_{t}")  # серверный курсор: таблица не грузится в память целиком
        named.itersize = 5000
        named.execute(f'select * from "{t}"')
        final = os.path.join(d, f"{t}.jsonl.gz")
        part = final + ".part"
        n = 0
        with gzip.open(part, "wt", encoding="utf-8") as f:
            for row in named:
                cols = [c[0] for c in named.description]
                f.write(json.dumps(dict(zip(cols, row)), default=_default, ensure_ascii=False, sort_keys=True) + "\n")
                n += 1
        named.close()
        _publish(part, final)
        counts[t] = n
        print(f"ci_analytics.{t}: {n}", file=sys.stderr)
    conn.close()
    return counts


def write_gitlab_csv(rows, out, hasher):
    from collect.ci_history import FIELDS

    fields = [f for f in FIELDS if f not in PII] + ["author_hash"]
    final = os.path.join(out, "gitlab_jobs.csv.gz")
    part = final + ".part"
    with gzip.open(part, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "author_hash": hasher(r.get("user"))})
    _publish(part, final)
    return len(rows)


def snapshot_gitlab(token, base, days, out):
    from collect.ci_history import paged, project_jobs, session

    s = session(token)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    projects = list(paged(s, f"{base}/api/v4/projects", {
        "membership": "true", "simple": "true", "last_activity_after": since.isoformat(),
    }))
    rows = []
    with ThreadPoolExecutor(8) as pool:
        for part in pool.map(lambda p: project_jobs(s, base, p, since), projects):
            rows.extend(part)
    n = write_gitlab_csv(rows, out, author_hasher())
    print(f"gitlab: {len(projects)} проектов, {n} заданий", file=sys.stderr)
    return {"days": days, "projects": len(projects), "rows": n}


def take(root, days, only=None):
    out = os.path.join(root, dt.date.today().strftime("%Y%m%d"))
    os.makedirs(out, exist_ok=True)
    m = _read_manifest(out)
    m["taken_at"] = None
    rc = 0
    jobs = {
        "ci_analytics": lambda: {"tables": snapshot_db(_env("CI_ANALYTICS_DSN"), out)},
        "gitlab": lambda: snapshot_gitlab(_env("GITLAB_TOKEN"),
                                          os.environ.get("GITLAB_URL", "https://gitlab.raftds.com").rstrip("/"),
                                          days, out),
    }
    for name, fn in jobs.items():
        if only and name != only:
            continue
        try:
            m["sources"][name] = {"status": "ok", **fn()}
        except Exception as e:  # обрыв сети, нет доступа: фиксируем и идём дальше
            # str(e) у psycopg2 может содержать хост, но не пароль; токен в исключения requests не попадает
            m["sources"][name] = {"status": "failed", "error": type(e).__name__}
            print(f"{name}: failed ({type(e).__name__}: {str(e).strip().splitlines()[0] if str(e).strip() else ''})",
                  file=sys.stderr)
            rc = 1
    _write_manifest(out, m)
    print(out)
    return rc


def _env(name):
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"{name} не задан")
    return v


def import_legacy(db_dir, csv_path, root):
    """Разложить снимок БД (collect/snapshot.py) и CSV (collect/ci_history.py) по новой структуре."""
    with open(os.path.join(db_dir, "manifest.json"), encoding="utf-8") as f:
        old = json.load(f)
    taken = old["taken_at"]
    out = os.path.join(root, dt.datetime.fromisoformat(taken).strftime("%Y%m%d"))
    d = os.path.join(out, "ci_analytics")
    os.makedirs(d, exist_ok=True)
    for t in old["tables"]:
        shutil.copyfile(os.path.join(db_dir, f"{t}.jsonl.gz"), os.path.join(d, f"{t}.jsonl.gz"))
    m = {"taken_at": taken, "sources": {"ci_analytics": {"status": "ok", "tables": old["tables"]}}}

    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    n = write_gitlab_csv(rows, out, author_hasher())
    m["sources"]["gitlab"] = {"status": "ok", "rows": n, "projects": len({r["project"] for r in rows}),
                              "days": 30, "imported_from": os.path.basename(csv_path)}
    _write_manifest(out, m)
    return out
