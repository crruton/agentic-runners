import gzip
import os
import re

import pytest

from runners import cli, metrics, snapshot
from runners.load import load
from runners.report import build
from tests.conftest import HOT, LOGIN, make_snapshot


def _build(root, out):
    paths = build(load(root), out)
    html = open(paths[0], encoding="utf-8").read()
    js = open(paths[1], encoding="utf-8").read()
    return html, js


def test_full_weeks_drop_partial_edges(snap_root):
    w = metrics.full_weeks(load(snap_root).daily)
    assert [f"{d:%d.%m}" for d in w.index] == ["05.10", "12.10", "19.10", "26.10"]


def test_canceled_before_start(snap_root):
    sec = metrics.canceled(load(snap_root))
    by = {m.id: m for m in sec.metrics}
    assert by["canceled.before_start"].value == 10
    assert by["canceled.before_start_pct"].value == 25.0


def test_mask_personal_namespace():
    assert metrics.mask_path("bob.smith/side") == "<личный>/side"
    assert metrics.mask_path("team/hot") == "team/hot"


def test_report_offline_sections_and_no_pii(snap_root, tmp_path):
    html, js = _build(snap_root, tmp_path / "out")
    assert not re.search(r"(src|href)=[\"']?https?://", html)
    assert "<script" not in html and "<link" not in html
    for sid in ("trend", "queue", "canceled", "classes", "projects", "hot", "runners", "whatif", "quality"):
        assert f'<section id="{sid}">' in html
    assert 'class="kpis"' in html
    for leak in (LOGIN, "bob.smith", "secret commit title"):
        assert leak not in html and leak not in js
    assert HOT in html


def test_metrics_json_is_deterministic(snap_root, tmp_path):
    _, a = _build(snap_root, tmp_path / "a")
    _, b = _build(snap_root, tmp_path / "b")
    assert a == b


def test_every_metric_has_source_and_period(snap_root, tmp_path):
    import json
    _, js = _build(snap_root, tmp_path / "out")
    for m in json.loads(js)["metrics"].values():
        assert m["source"] and m["period"]
        assert set(m["source"].split(" + ")) <= {"gitlab", "ci_analytics", "владелец"}


def test_missing_gitlab_marks_sections(tmp_path):
    root = tmp_path / "snapshots"
    make_snapshot(str(root), with_gitlab=False)
    html, _ = _build(str(root), tmp_path / "out")
    assert html.count("Нет данных в снимке") == 5  # queue, canceled, classes, hot, whatif
    assert '<section id="trend">' in html and "Нет данных" not in html.split('<section id="queue">')[0]


def test_report_without_snapshot_fails(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["report", "--root", str(tmp_path / "none"), "--out", str(tmp_path / "out")])
    assert e.value.code not in (0, None)


def test_gitlab_csv_has_no_pii(tmp_path):
    snapshot.write_gitlab_csv([{"project": "p", "job_id": 1, "user": LOGIN, "commit_title": "t"}],
                              str(tmp_path), snapshot.author_hasher())
    text = gzip.open(tmp_path / "gitlab_jobs.csv.gz", "rt", encoding="utf-8").read()
    assert LOGIN not in text and "commit_title" not in text and "author_hash" in text


class _BrokenCursor:
    """Серверный курсор, который рвёт соединение посреди таблицы."""
    description = [("id",)]
    itersize = 0

    def execute(self, sql):
        pass

    def __iter__(self):
        yield (1,)
        raise ConnectionError("vpn dropped")

    def close(self):
        pass


class _Conn:
    def set_session(self, readonly):
        assert readonly is True

    def cursor(self, name=None):
        if name:
            return _BrokenCursor()

        class C:
            def execute(self, sql):
                pass

            def fetchall(self):
                return [("job_details",)]
        return C()


def test_network_drop_leaves_no_final_file(tmp_path, monkeypatch):
    import psycopg2
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: _Conn())
    monkeypatch.setenv("CI_ANALYTICS_DSN", "postgresql://u:supersecret@h/db")
    monkeypatch.chdir(tmp_path)
    rc = snapshot.take("snapshots", 30, only="ci_analytics")
    assert rc == 1
    day = os.listdir("snapshots")[0]
    files = os.listdir(os.path.join("snapshots", day, "ci_analytics"))
    assert "job_details.jsonl.gz" not in files
    manifest = open(os.path.join("snapshots", day, "manifest.json"), encoding="utf-8").read()
    assert '"failed"' in manifest and "supersecret" not in manifest


def _jobs(rows):
    import pandas as pd
    base = pd.Timestamp("2026-11-01T10:00:00Z")
    recs = []
    for i, (pipe, ref, name, start_min, dur_min) in enumerate(rows):
        recs.append({"project": "p", "job_id": i, "pipeline_id": pipe, "ref": ref, "name": name, "status": "success",
                     "created_at": base + pd.Timedelta(minutes=start_min - 1),
                     "started_at": base + pd.Timedelta(minutes=start_min),
                     "finished_at": base + pd.Timedelta(minutes=start_min + dur_min),
                     "queued_s": 60.0, "duration_s": dur_min * 60.0})
    return pd.DataFrame(recs)


def test_interruptible_cuts_superseded_but_not_deploy():
    from runners import whatif
    # пайплайн 1: тест 0–30 мин и выкатка 0–30 мин; пайплайн 2 на той же ветке создан на 9-й минуте
    g = whatif.prepare(_jobs([(1, "main", "test:a", 0, 30), (1, "main", "deploy:prod", 0, 30), (2, "main", "test:a", 10, 5)]))
    s = whatif.interruptible_saved(g, "no_deploy").set_index("name").saved_s
    assert s["deploy:prod"] == 0
    assert s.loc["test:a"].max() == 21 * 60  # проработал бы до 9-й минуты вместо 30-й
    assert whatif.interruptible_saved(g, "no_main").saved_s.sum() == 0


def test_simulation_fifo_and_interruptible():
    from runners import whatif
    g = whatif.prepare(_jobs([(1, "dev", "test:a", 0, 30), (2, "dev", "test:b", 10, 5)]))
    w, busy = whatif.simulate(g, 1)
    assert list(w) == [0.0, 20 * 60.0]          # второе ждёт, пока первое доработает
    w, busy = whatif.simulate(g, 1, "all")
    # первое стартует в модели сразу по готовности (−1 мин) и прерывается на 9-й: 10 мин + 5 мин второго
    assert list(w) == [0.0, 0.0] and round(busy * 60) == 15
