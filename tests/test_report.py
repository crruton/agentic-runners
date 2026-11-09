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
    assert html.count("Нет данных в снимке") == 6  # queue, canceled, classes, hot, whatif + cgroup (нет логов в фикстуре)
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


SAMPLE_LOG = """\x1b[0;m2026-11-04T09:34:07Z 01O Running on CPU: Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz (avx, avx2)
2026-11-04T09:34:42Z 01O workers: 3 (cpu 4, mem 4096 MiB, per-worker 1100 MiB, heaviest +336 MiB, reserve 256 MiB, ceiling 6)
2026-11-04T10:10:52Z 01O job-resources: cgroup v2 memory.events: oom_kill=1 oom=1 max=1634 high=0
2026-11-04T10:10:52Z 01O job-resources: cgroup v2 memory: peak=4096MiB max=4096MiB current=40MiB swap.peak=0MiB
2026-11-04T10:10:52Z 01O job-resources: host meminfo: MemTotal=15383MiB MemAvailable=9873MiB SwapTotal=4095MiB SwapFree=3802MiB
2026-11-04T10:10:52Z 01O job-resources: host vmstat: oom_kill=48 pswpin=42454822 pswpout=60492662 (за прогон: oom_kill+1 pswpin+113 pswpout+2165)
2026-11-04T10:10:52Z 01O job-resources: host pressure memory: some avg10=0.00 avg60=0.04 avg300=0.03 total=1 | full avg10=0.00 avg60=0.04 avg300=0.02 total=1
2026-11-04T10:10:52Z 01O job-resources: cpu: nproc=4 cpu_count=8 cpu.max=max 100000 effective=4 loadavg=3.87 5.60 5.72 2/497 38001
"""


def test_parse_job_resources():
    from runners.resources import parse
    r = parse(SAMPLE_LOG)
    assert r["cpu_model"] == "Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz"
    assert (r["workers"], r["mem_max_mib"], r["mem_peak_mib"]) == (3, 4096, 4096)
    assert (r["job_oom_kill"], r["job_mem_max_events"]) == (1, 1634)
    assert (r["host_oom_kill_total"], r["host_oom_kill_delta"], r["host_pswpout_delta"]) == (48, 1, 2165)
    assert (r["nproc"], r["cpu_count"], r["cpu_max"], r["loadavg1"]) == (4, 8, "max 100000", 3.87)
    assert r["host_psi_mem_some_avg300"] == 0.03 and r["parsed"] == 1


def test_parse_unknown_log_is_empty():
    from runners.resources import parse
    assert parse("just some build output\n") == {"parsed": 0}


def test_schedule_pipeline_does_not_supersede_push():
    import pandas as pd
    from runners import whatif
    jobs = _jobs([(1, "main", "test:a", 0, 30), (2, "main", "test:a", 10, 5)])
    pipes = pd.DataFrame({"pipeline_id": [1, 2], "source": ["push", "schedule"]})
    g = whatif.prepare(jobs, pipes)
    assert whatif.interruptible_saved(g, "all").saved_s.sum() == 0
    pipes["source"] = ["push", "push"]
    g = whatif.prepare(jobs, pipes)
    assert whatif.interruptible_saved(g, "all").saved_s.sum() == 21 * 60


def test_numbers_and_check(snap_root, tmp_path):
    from runners import docs
    _build(snap_root, tmp_path / "out")
    m = str(tmp_path / "out" / "metrics.json")
    d = tmp_path / "docs"
    d.mkdir()
    out = str(d / "talk" / "numbers.md")
    docs.numbers(m, out)
    first = open(out, encoding="utf-8").read()
    docs.numbers(m, out)
    assert open(out, encoding="utf-8").read() == first
    (d / "ok.md").write_text("p95 — `queue.p95_s`\n", encoding="utf-8")
    assert docs.check(m, str(d), out) == []
    (d / "bad.md").write_text("p97 — `queue.p97_s`\n", encoding="utf-8")
    problems = docs.check(m, str(d), out)
    assert len(problems) == 1 and "bad.md:1" in problems[0] and "queue.p97_s" in problems[0]
