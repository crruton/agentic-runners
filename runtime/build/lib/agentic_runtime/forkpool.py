"""Параллельный прогон форком от прогретого процесса (ADR-0005).

Родитель уже собрал тесты, значит, весь код проекта импортирован. После `gc.collect(); gc.freeze()`
родитель форкает воркеров: страницы импортированного кода остаются общими (copy-on-write),
а `gc.freeze()` не даёт сборщику мусора трогать их и тем самым копировать.

Протокол воркер → родитель: кадры «длина + pickle» по pipe: ("start", nodeid), ("report", data), ("done", None).
Отчёты сериализуются штатным хуком pytest, родитель восстанавливает их и вызывает
`pytest_runtest_logreport`, поэтому терминал, JUnit и код выхода работают как обычно.
"""

import gc
import os
import pickle
import select
import signal
import struct

from _pytest.reports import TestReport

HEADER = struct.Struct("!I")
SILENCED = ("terminalreporter", "LogXML", "LFPlugin", "NFPlugin", "CacheshowPlugin")


def _write(fd, obj):
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    buf = HEADER.pack(len(data)) + data
    while buf:
        n = os.write(fd, buf)
        buf = buf[n:]


class _Sender:
    """Плагин внутри воркера: отправляет каждый отчёт родителю."""

    def __init__(self, config, fd):
        self.config, self.fd = config, fd

    def pytest_runtest_logreport(self, report):
        _write(self.fd, ("report", self.config.hook.pytest_report_to_serializable(config=self.config, report=report)))


def plan(items, workers):
    """Файлы целиком по воркерам (как --dist=loadfile), жадно по числу тестов: крупные файлы первыми."""
    groups = {}
    for item in items:
        groups.setdefault(str(item.path), []).append(item)
    buckets = [[] for _ in range(workers)]
    for group in sorted(groups.values(), key=len, reverse=True):
        min(buckets, key=len).extend(group)
    order = {id(it): i for i, it in enumerate(items)}
    return [sorted(b, key=lambda it: order[id(it)]) for b in buckets if b]


def _child(session, items, wid, count, fd):
    os.environ["PYTEST_XDIST_WORKER"] = f"gw{wid}"
    os.environ["PYTEST_XDIST_WORKER_COUNT"] = str(count)
    config = session.config
    pm = config.pluginmanager
    for plugin in list(pm.get_plugins()):  # печатает и пишет файлы только родитель
        if type(plugin).__name__ in SILENCED or pm.get_name(plugin) in SILENCED:
            pm.unregister(plugin)
    pm.register(_Sender(config, fd), "agentic_runtime_sender")
    try:
        for i, item in enumerate(items):
            _write(fd, ("start", item.nodeid))
            item.config.hook.pytest_runtest_protocol(item=item, nextitem=items[i + 1] if i + 1 < len(items) else None)
            if session.shouldfail or session.shouldstop:
                break
        _write(fd, ("done", None))
    finally:
        os._exit(0)


class _Worker:
    def __init__(self, wid, items, pid, fd):
        self.wid, self.items, self.pid, self.fd = wid, items, pid, fd
        self.buf, self.current, self.finished, self.done = b"", None, set(), False


def _spawn(session, wid, items, count):
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        _child(session, items, wid, count, w)
    os.close(w)
    return _Worker(wid, items, pid, r)


def _died(config, item, wid, status):
    sig = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
    why = f"signal {sig} ({signal.Signals(sig).name})" if sig else f"exit code {os.WEXITSTATUS(status)}"
    hint = " — вероятно, OOM-kill в cgroup задания" if sig == signal.SIGKILL else ""
    rep = TestReport(item.nodeid, item.location, {}, "failed", f"agentic-runtime: воркер gw{wid} умер: {why}{hint}",
                     "call", [], 0.0)
    config.hook.pytest_runtest_logreport(report=rep)
    config.hook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)


def run(session, workers, max_restarts=2):
    """Выполнить session.items в форк-воркерах. Возвращает число перезапусков."""
    config = session.config
    by_id = {it.nodeid: it for it in session.items}
    buckets = plan(session.items, workers)
    count = len(buckets)
    gc.collect()
    gc.freeze()  # объекты прогретого процесса уходят из-под GC: их страницы не копируются при обходе
    pool = {}
    for wid, items in enumerate(buckets):
        w = _spawn(session, wid, items, count)
        pool[w.fd] = w
    restarts = 0
    try:
        while pool:
            ready, _, _ = select.select(list(pool), [], [])
            for fd in ready:
                w = pool[fd]
                chunk = os.read(fd, 1 << 16)
                if chunk:
                    w.buf += chunk
                    while len(w.buf) >= HEADER.size:
                        (n,) = HEADER.unpack_from(w.buf)
                        if len(w.buf) < HEADER.size + n:
                            break
                        kind, data = pickle.loads(w.buf[HEADER.size:HEADER.size + n])
                        w.buf = w.buf[HEADER.size + n:]
                        if kind == "start":
                            w.current = data
                            it = by_id[data]
                            config.hook.pytest_runtest_logstart(nodeid=it.nodeid, location=it.location)
                        elif kind == "report":
                            rep = config.hook.pytest_report_from_serializable(config=config, data=data)
                            config.hook.pytest_runtest_logreport(report=rep)
                            if rep.when == "teardown":
                                w.finished.add(rep.nodeid)
                                config.hook.pytest_runtest_logfinish(nodeid=rep.nodeid, location=by_id[rep.nodeid].location)
                        elif kind == "done":
                            w.done = True
                    if session.shouldfail or session.shouldstop:
                        _stop_all(pool)  # остановка на первой ошибке: остальные воркеры больше не нужны
                        break
                    continue
                # EOF: воркер завершился
                os.close(fd)
                del pool[fd]
                try:
                    _, status = os.waitpid(w.pid, 0)
                except ChildProcessError:  # уже пожат
                    status = 0
                if w.done or session.shouldfail or session.shouldstop:
                    continue
                left = [it for it in w.items if it.nodeid not in w.finished]
                if w.current and w.current not in w.finished:
                    _died(config, by_id[w.current], w.wid, status)
                    left = [it for it in left if it.nodeid != w.current]
                if left and restarts < max_restarts:
                    restarts += 1  # дешёвый перезапуск: форк от того же прогретого родителя
                    nw = _spawn(session, w.wid, left, count)
                    pool[nw.fd] = nw
                elif left:
                    for it in left:
                        _died(config, it, w.wid, status)
    finally:
        for w in pool.values():
            _kill(w.pid)
        gc.unfreeze()
    return restarts


def _stop_all(pool):
    for w in list(pool.values()):
        _kill(w.pid)
        try:
            os.close(w.fd)
        except OSError:
            pass
    pool.clear()


def _kill(pid):
    try:
        os.kill(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass
