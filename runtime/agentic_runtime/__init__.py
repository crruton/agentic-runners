"""agentic_runtime — тестовый рантайм платформы для CI под нагрузкой агентов (ADR-0005).

Подключается снаружи проекта, без правок в его репозитории:
    PYTEST_ADDOPTS="-p agentic_runtime --rt-workers=auto --rt-cache=/cache/rt --rt-telemetry"

Значения опций — только через «=»: pytest принимает существующий путь, записанный отдельным
аргументом (`--rt-cache /cache/rt`), за путь к тестам и сдвигает rootdir к общему предку.

- --rt-workers N|auto  прогон форком от прогретого процесса (Linux), общие страницы кода у воркеров
- --rt-cache DIR       пропуск файлов тестов, чьи входы не менялись с последнего успешного прогона
- --rt-telemetry       строки job-resources: (cgroup v2, vmstat, PSI) в конце сессии
- --rt-fail-fast       остановка на первом упавшем тесте; с --rt-cache тесты, падавшие раньше, идут первыми
- --rt-select=coverage ключ кэша по реально выполненному коду, а не по импортам через conftest (H4, E09)
"""

import os
import sys
import threading

import pytest

from agentic_runtime import cache as _cache
from agentic_runtime import telemetry as _telemetry

__version__ = "0.2.0"


def pytest_addoption(parser):
    g = parser.getgroup("agentic-runtime")
    g.addoption("--rt-workers", default=None,
                help="число воркеров-форков или auto (по cpuset задания); без опции — обычный прогон")
    g.addoption("--rt-max-restarts", type=int, default=2, help="сколько раз перезапускать упавшего воркера")
    g.addoption("--rt-cache", default=None, help="каталог кэша результатов по хэшу входов файла теста")
    g.addoption("--rt-cache-extra", action="append", default=[],
                help="дополнительные glob-входы ключа кэша (миграции, фикстуры-данные), относительно rootdir")
    g.addoption("--rt-telemetry", action="store_true", help="печатать строки job-resources: в конце сессии")
    g.addoption("--rt-select", choices=["static", "coverage"], default="static",
                help="ключ кэша: static — замыкание импортов с conftest; coverage — выполненный код (нужен --rt-cache)")
    g.addoption("--rt-fail-fast", action="store_true",
                help="остановиться на первом упавшем тесте; с --rt-cache прошлые падения выполняются первыми")


class Runtime:
    def __init__(self, config):
        self.config = config
        o = config.option
        self.workers = _workers(o.rt_workers)
        self.telemetry = o.rt_telemetry
        self.vm_start = _telemetry.vmstat() if self.telemetry else None
        self.store = _cache.Store(o.rt_cache) if o.rt_cache else None
        self.extra = o.rt_cache_extra
        self.keys, self.skipped, self.skipped_files = {}, 0, 0
        self.partial, self.failed_files, self.ran = set(), set(), {}
        self.restarts = 0
        self.fail_fast = o.rt_fail_fast
        if self.fail_fast and not o.maxfail:
            o.maxfail = 1  # агенту для исправления нужна первая ошибка (H1, E06)
        self.prev_failed = self.store.failed() if self.store else set()
        self.failed_ids, self.ran_ids, self.promoted = set(), set(), 0
        self.coverage = o.rt_select == "coverage" and self.store is not None
        self.root = str(config.rootpath)
        self.cov, self.cov_seen, self.cov_bad, self.per_file = {}, {}, set(), {}
        self.setup_prev = self.store.setup_get() if self.coverage else set()
        self.setup_now, self.target = set(), [None]  # target[0] — куда пишет профайлер
        self.mode = "fork" if self.workers > 1 and hasattr(os, "fork") else "serial"

    # --- кэш ----------------------------------------------------------------------
    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session, config, items):
        self._select_by_cache(config, items)
        self._failed_first(items)

    def _select_by_cache(self, config, items):
        if not self.store:
            return
        root = str(config.rootpath)
        graph = _cache.ImportGraph(root)
        common = _cache.common_key(root, self.extra)
        by_file = {}
        for it in items:
            by_file.setdefault(str(it.path), []).append(it)
        keep, drop = [], []
        for path, its in by_file.items():
            executed = self.store.cov_get(_rel(root, path)) if self.coverage else None
            if executed is not None:
                executed = set(executed) | self.setup_prev  # код фикстур setup — для всех файлов
            key = (_cache.file_key_coverage(graph, root, path, common, executed) if executed is not None
                   else _cache.file_key(graph, root, path, common))
            self.keys[path] = (key, [i.nodeid for i in its])
            hit = self.store.hit(key)
            (drop if hit else keep).extend(its)
            self.skipped_files += hit
        if drop:
            self.skipped = len(drop)
            config.hook.pytest_deselected(items=drop)
            kept = {id(it) for it in keep}
            items[:] = [it for it in items if id(it) in kept]

    def pytest_deselected(self, items):
        # файл, из которого что-то отобрано (-k, -m), не кэшируем: запуск был неполным
        for it in items:
            path = str(it.path)
            if path not in self.keys:  # отобрано не нами
                self.partial.add(path)

    def _failed_first(self, items):
        if not (self.fail_fast and self.prev_failed):
            return
        first = [it for it in items if it.nodeid in self.prev_failed]
        if first:
            files = {str(it.path) for it in first}
            same = [it for it in items if it.nodeid not in self.prev_failed and str(it.path) in files]
            rest = [it for it in items if it.nodeid not in self.prev_failed and str(it.path) not in files]
            items[:] = first + same + rest
            self.promoted = len(first)

    # --- запись выполненного кода (coverage) ----------------------------------------
    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        if not self.coverage:
            yield
            return
        path = str(item.path)
        if not self.per_file:
            for it in item.session.items:
                self.per_file[str(it.path)] = self.per_file.get(str(it.path), 0) + 1
        self.target[0] = self.cov.setdefault(path, set())
        prof = _profiler(self.root, self.target)
        sys.setprofile(prof)
        threading.setprofile(prof)
        try:
            yield
        finally:
            sys.setprofile(None)
            threading.setprofile(None)
        self.cov_seen[path] = self.cov_seen.get(path, 0) + 1
        if self.cov_seen[path] == self.per_file.get(path) and path not in self.cov_bad:
            rel = _rel(self.root, path)
            self.store.cov_put(rel, {_rel(self.root, f) for f in self.cov[path] if f != path})
        self.store.setup_put(os.environ.get("PYTEST_XDIST_WORKER", "main"),
                             {_rel(self.root, f) for f in self.setup_now})

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_setup(self, item):
        if not self.coverage:
            yield
            return
        back, self.target[0] = self.target[0], self.setup_now  # setup фикстур — в общую карту
        try:
            yield
        finally:
            self.target[0] = back

    def pytest_runtest_logreport(self, report):
        if self.coverage and report.failed:
            self.cov_bad.add(str(self.config.rootpath / report.nodeid.split("::")[0]))
        if report.failed:
            self.failed_ids.add(report.nodeid)
        if report.when == "teardown":
            self.ran_ids.add(report.nodeid)
        path = str(report.fspath) if hasattr(report, "fspath") else ""
        full = os.path.join(str(self.config.rootpath), path) if path and not os.path.isabs(path) else path
        if report.failed:
            self.failed_files.add(os.path.normcase(os.path.abspath(full)))
        if report.when == "teardown":
            self.ran.setdefault(os.path.normcase(os.path.abspath(full)), set()).add(report.nodeid)

    # --- прогон -------------------------------------------------------------------
    @pytest.hookimpl(tryfirst=True)
    def pytest_runtestloop(self, session):
        if self.mode != "fork" or not session.items:
            return None
        if session.testsfailed and not session.config.option.continue_on_collection_errors:
            raise session.Interrupted(f"{session.testsfailed} error(s) during collection")
        if session.config.option.collectonly:
            return True
        from agentic_runtime import forkpool
        self.restarts = forkpool.run(session, self.workers, self.config.option.rt_max_restarts)
        return True

    def pytest_sessionfinish(self, session):
        if not self.store:
            return
        # прошлые падения, которые в этот раз не выполнялись (остановка на первой ошибке), остаются в списке
        self.store.put_failed(self.failed_ids | (self.prev_failed - self.ran_ids))
        for path, (key, nodeids) in self.keys.items():
            norm = os.path.normcase(os.path.abspath(path))
            if path in self.partial or norm in self.failed_files or self.store.hit(key):
                continue
            if set(nodeids) <= self.ran.get(norm, set()):  # все тесты файла реально выполнились
                if self.coverage:  # ключ — по только что записанной карте выполненного кода
                    executed = self.store.cov_get(_rel(self.root, path))
                    if executed is not None:
                        executed = set(executed) | self.store.setup_get()
                        root = self.root
                        key = _cache.file_key_coverage(_cache.ImportGraph(root), root, path,
                                                       _cache.common_key(root, self.extra), executed)
                self.store.put(key, nodeids)

    def pytest_terminal_summary(self, terminalreporter):
        tr = terminalreporter
        if self.store:
            tr.write_line(f"agentic-runtime: по кэшу пропущено {self.skipped} тестов в {self.skipped_files} файлах")
        if self.workers > 1:
            note = "" if self.mode == "fork" else " (fork недоступен на этой ОС — прогон последовательный)"
            tr.write_line(f"agentic-runtime: режим {self.mode}, воркеров {self.workers}, перезапусков {self.restarts}{note}")
        if self.telemetry:
            for line in _telemetry.lines(self.vm_start, self.workers if self.workers > 1 else None, self.mode):
                tr.write_line(line)


def _rel(root, path):
    try:
        return os.path.relpath(path, root).replace(os.sep, "/")
    except ValueError:  # другой диск на Windows
        return str(path)


def _profiler(root, target):
    """Профайлер вызовов: запоминает файлы проекта, чей код выполнялся (только события call — дёшево).
    target[0] — текущее множество (файл тестов или setup фикстур)."""
    root = os.path.normcase(os.path.abspath(root))

    def prof(frame, event, arg):
        if event == "call":
            f = frame.f_code.co_filename
            sink = target[0]
            if f not in sink and os.path.normcase(f).startswith(root):
                sink.add(f)
    return prof


def _workers(value):
    if not value:
        return 1
    if value == "auto":
        n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
        return max(1, n)
    return max(1, int(value))


def pytest_configure(config):
    config.pluginmanager.register(Runtime(config), "agentic_runtime_core")
    cache_dir = config.option.rt_cache
    if cache_dir and os.path.isdir(cache_dir) and any(a == cache_dir for a in config.invocation_params.args):
        sys.stderr.write("agentic-runtime: путь кэша передан отдельным аргументом и мог сдвинуть rootdir; "
                         "используйте --rt-cache=DIR\n")
    if _workers(config.option.rt_workers) > 1 and not hasattr(os, "fork"):
        sys.stderr.write("agentic-runtime: fork недоступен на этой ОС, прогон будет последовательным\n")
