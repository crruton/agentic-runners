"""Тесты рантайма по сценариям openspec/changes/test-runtime/specs/test-runtime/spec.md."""

import os
import re
import sys

import pytest

RUNTIME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(RUNTIME)
needs_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="fork доступен только в Linux/macOS")

SUITE = {
    "test_a.py": """
import os, helper
def test_ok(): assert helper.value() == 1
def test_fail(): assert False
def test_worker(): assert os.environ.get("PYTEST_XDIST_WORKER", "").startswith("gw")
""",
    "test_b.py": """
import pytest
@pytest.mark.skip
def test_skipped(): pass
def test_ok1(): pass
def test_ok2(): pass
""",
    "test_c.py": "def test_c1(): pass\ndef test_c2(): pass\n",
    "helper.py": "def value():\n    return 1\n",
}


def _env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", RUNTIME + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")  # кириллица в выводе дочернего pytest на Windows


@pytest.fixture
def suite(pytester, monkeypatch):
    _env(monkeypatch)
    pytester.makepyfile(**{k[:-3]: v for k, v in SUITE.items()})
    return pytester


def run(pytester, *args):
    return pytester.runpytest_subprocess("-p", "agentic_runtime", "-p", "no:cacheprovider", *args)


def counts(result):
    return result.parseoutcomes()


@needs_fork
def test_fork_same_outcomes_as_serial(suite):
    serial = run(suite, "-k", "not worker")
    forked = run(suite, "--rt-workers", "3")
    s, f = counts(serial), counts(forked)
    assert (f["passed"], f["failed"], f["skipped"]) == (s["passed"] + 1, s["failed"], s["skipped"])
    assert forked.ret == serial.ret == 1
    forked.stdout.fnmatch_lines(["*режим fork, воркеров 3*"])


@needs_fork
def test_fork_junit_has_all_cases(suite):
    run(suite, "--rt-workers", "2", "--junitxml=out.xml")
    xml = (suite.path / "out.xml").read_text(encoding="utf-8")
    assert len(re.findall(r"<testcase ", xml)) == 8  # 3 + 3 + 2 теста в SUITE


@needs_fork
def test_killed_worker_is_restarted(pytester, monkeypatch):
    _env(monkeypatch)
    pytester.makepyfile(test_kill="""
import os, signal
def test_1(): pass
def test_dies(): os.kill(os.getpid(), signal.SIGKILL)
def test_3(): pass
def test_4(): pass
""")
    r = run(pytester, "--rt-workers", "2")
    o = counts(r)
    assert o["passed"] == 3 and o["failed"] == 1
    r.stdout.fnmatch_lines(["*воркер gw* умер: signal 9 (SIGKILL)*OOM*", "*перезапусков 1*"])


def test_cache_skips_unchanged_and_reruns_changed_import(suite):
    cache = str(suite.path / ".rt")
    first = run(suite, f"--rt-cache={cache}", "-k", "not worker and not fail")
    assert counts(first)["passed"] == 5
    # -k отобрал часть test_a — файл не кэшируется; test_b и test_c — кэшируются
    second = run(suite, f"--rt-cache={cache}", "-k", "not worker and not fail")
    second.stdout.fnmatch_lines(["*по кэшу пропущено 5 тестов в 2 файлах*"])
    # меняем модуль, который импортирует только test_a: test_b/test_c по-прежнему из кэша
    (suite.path / "helper.py").write_text("def value():\n    return 1  # changed\n", encoding="utf-8")
    third = run(suite, f"--rt-cache={cache}", "-k", "not worker and not fail")
    assert counts(third)["passed"] == 1
    third.stdout.fnmatch_lines(["*по кэшу пропущено 5 тестов в 2 файлах*"])


def test_failed_file_is_not_cached(suite):
    cache = str(suite.path / ".rt")
    run(suite, f"--rt-cache={cache}", "-k", "not worker")
    again = run(suite, f"--rt-cache={cache}", "-k", "not worker")
    o = counts(again)
    assert o["failed"] == 1  # test_a.py выполняется снова целиком
    again.stdout.fnmatch_lines(["*по кэшу пропущено 5 тестов в 2 файлах*"])


def test_cache_key_changes_with_lockfile(tmp_path):
    sys.path.insert(0, RUNTIME)
    from agentic_runtime import cache
    (tmp_path / "test_x.py").write_text("def test(): pass\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("a==1\n", encoding="utf-8")
    g = cache.ImportGraph(tmp_path, roots=[])
    k1 = cache.file_key(g, tmp_path, tmp_path / "test_x.py", cache.common_key(tmp_path))
    (tmp_path / "uv.lock").write_text("a==2\n", encoding="utf-8")
    k2 = cache.file_key(g, tmp_path, tmp_path / "test_x.py", cache.common_key(tmp_path))
    assert k1 != k2


def test_relative_imports_in_closure(tmp_path):
    sys.path.insert(0, RUNTIME)
    from agentic_runtime import cache
    pkg = tmp_path / "app"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("from ..core import x\n", encoding="utf-8")
    (pkg / "core.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "test_r.py").write_text("from app.sub import x\n", encoding="utf-8")
    names = {p.name for p in cache.ImportGraph(tmp_path, roots=[]).closure(tmp_path / "test_r.py")}
    assert {"test_r.py", "__init__.py", "core.py"} <= names


@pytest.mark.skipif(not os.path.exists("/proc/vmstat"), reason="нужен Linux /proc")
def test_telemetry_lines_are_parsed_by_snapshot(suite):
    r = run(suite, "--rt-telemetry", "-k", "not worker")
    sys.path.insert(0, REPO)
    from runners.resources import parse
    assert parse(r.stdout.str())["parsed"] == 1


def test_cache_dir_outside_rootdir_hits_on_second_run(suite, tmp_path_factory):
    cache = str(tmp_path_factory.mktemp("outside") / "rt")  # вне rootdir, как /cache/rt на раннере
    run(suite, f"--rt-cache={cache}", "-k", "not worker and not fail")
    again = run(suite, f"--rt-cache={cache}", "-k", "not worker and not fail")
    again.stdout.fnmatch_lines(["*по кэшу пропущено 5 тестов в 2 файлах*"])


FAIL_LAST = {
    "test_1.py": "def test_a(): pass\ndef test_b(): pass\n",
    "test_2.py": "def test_c(): pass\ndef test_d(): pass\n",
    "test_9.py": "def test_ok(): pass\ndef test_broken(): assert False\n",
}


def test_fail_fast_stops_at_first_failure(pytester, monkeypatch):
    _env(monkeypatch)
    pytester.makepyfile(**{k[:-3]: v for k, v in FAIL_LAST.items()})
    r = run(pytester, "--rt-fail-fast")
    o = counts(r)
    assert o["failed"] == 1 and r.ret == 1
    r.stdout.fnmatch_lines(["*stopping after 1 failures*"])


def test_fail_fast_runs_previous_failures_first(pytester, monkeypatch):
    _env(monkeypatch)
    pytester.makepyfile(**{k[:-3]: v for k, v in FAIL_LAST.items()})
    cache = str(pytester.path / ".rt")
    first = run(pytester, "--rt-fail-fast", f"--rt-cache={cache}")
    assert counts(first)["passed"] == 5  # падение в последнем файле: до него прошли 5 тестов
    second = run(pytester, "--rt-fail-fast", f"--rt-cache={cache}", "-v")
    o = counts(second)
    assert o["failed"] == 1 and o.get("passed", 0) == 0  # прошлое падение выполнено первым, сессия остановлена
    second.stdout.fnmatch_lines(["*test_9.py::test_broken FAILED*"])
    third = run(pytester, "--rt-fail-fast", f"--rt-cache={cache}")
    assert counts(third)["failed"] == 1 and counts(third).get("passed", 0) == 0  # падение не забыто


@needs_fork
def test_fail_fast_in_fork_mode_stops_all_workers(pytester, monkeypatch):
    _env(monkeypatch)
    pytester.makepyfile(**{k[:-3]: v for k, v in FAIL_LAST.items()})
    r = run(pytester, "--rt-fail-fast", "--rt-workers=2")
    assert counts(r)["failed"] == 1 and r.ret == 1
