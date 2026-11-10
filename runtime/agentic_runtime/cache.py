"""Кэш результатов по хэшу входов файла теста (ADR-0005).

Ключ файла — sha256 от: содержимого файла; локального замыкания его импортов (AST, только файлы
внутри rootdir); всех conftest.py от rootdir до каталога файла; lock- и конфиг-файлов проекта;
версии Python и плагина; файлов из дополнительных glob. Файл пропускается, только если в прошлый
раз все его тесты выполнились и ни один не упал.

Динамические импорты и внешнее состояние (схема БД, данные) ключ не видит — для них
`--rt-cache-extra` и отключение кэша. Кэш для проверки перед слиянием наполняет только CI.
"""

import ast
import glob
import hashlib
import os
import sys
from pathlib import Path

VERSION = "1"
PROJECT_FILES = ("uv.lock", "poetry.lock", "Pipfile.lock", "pyproject.toml", "setup.cfg", "setup.py",
                 "tox.ini", "pytest.ini")
PROJECT_GLOBS = ("requirements*.txt",)


class ImportGraph:
    """Разрешение импортов в файлы внутри rootdir. Кэширует разбор файлов."""

    def __init__(self, root, roots=None):
        self.root = Path(root).resolve()
        # где искать модули верхнего уровня: rootdir, src/ и пути sys.path внутри rootdir
        cands = [self.root, self.root / "src"] + [Path(p).resolve() for p in (roots or sys.path) if p]
        self.roots = []
        for c in cands:
            try:
                if c.is_dir() and (c == self.root or self.root in c.parents) and c not in self.roots:
                    self.roots.append(c)
            except OSError:
                pass
        self._deps = {}

    def _module_file(self, dotted):
        parts = dotted.split(".")
        for base in self.roots:
            p = base.joinpath(*parts)
            for f in (p.with_suffix(".py"), p / "__init__.py"):
                if f.is_file():
                    return f.resolve()
        return None

    def _package_of(self, path):
        """Точечное имя пакета файла относительно ближайшего корня поиска."""
        for base in self.roots:
            if base in path.parents:
                rel = path.relative_to(base).with_suffix("")
                parts = list(rel.parts)
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                return parts[:-1] if path.name != "__init__.py" else parts
        return []

    def deps(self, path):
        path = Path(path).resolve()
        if path in self._deps:
            return self._deps[path]
        out = set()
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (SyntaxError, ValueError, OSError):
            self._deps[path] = out
            return out
        pkg = self._package_of(path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg[: len(pkg) - node.level + 1] if node.level > 1 else pkg
                    mod = ".".join(base + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                names = [mod] + [f"{mod}.{a.name}" if mod else a.name for a in node.names]
            for name in names:
                parts = name.split(".")
                for i in range(1, len(parts) + 1):  # пакет и все его родители (__init__.py)
                    f = self._module_file(".".join(parts[:i]))
                    if f:
                        out.add(f)
        self._deps[path] = out
        return out

    def closure(self, path):
        seen, stack = set(), [Path(path).resolve()]
        while stack:
            p = stack.pop()
            if p in seen:
                continue
            seen.add(p)
            stack.extend(self.deps(p) - seen)
        return seen


def _conftests(root, path):
    root, d = Path(root).resolve(), Path(path).resolve().parent
    out = []
    while True:
        c = d / "conftest.py"
        if c.is_file():
            out.append(c)
        if d == root or root not in d.parents:
            break
        d = d.parent
    return out


def project_files(root, extra=()):
    root = Path(root)
    files = [root / f for f in PROJECT_FILES if (root / f).is_file()]
    for pattern in PROJECT_GLOBS + tuple(extra):
        files += [Path(p) for p in glob.glob(str(root / pattern), recursive=True) if os.path.isfile(p)]
    return sorted({f.resolve() for f in files})


def file_key(graph, root, test_file, common):
    """common — хэш проектных файлов и окружения, общий для всех файлов тестов."""
    files = set(graph.closure(test_file))
    for c in _conftests(root, test_file):
        files |= graph.closure(c)
    h = hashlib.sha256(common.encode())
    for f in sorted(files):
        h.update(str(f.relative_to(graph.root) if graph.root in f.parents else f).encode())
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def common_key(root, extra=()):
    h = hashlib.sha256(f"agentic-runtime-cache:{VERSION}:{sys.version}".encode())
    for f in project_files(root, extra):
        h.update(str(f).encode())
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


class Store:
    """Локальный каталог: файл-метка <key>.ok на каждый полностью успешный файл тестов."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def hit(self, key):
        return (self.path / f"{key}.ok").exists()

    def failed(self):
        """Тесты, упавшие в прошлых прогонах (для --rt-fail-fast: они идут первыми)."""
        f = self.path / "failed.txt"
        return set(f.read_text(encoding="utf-8").split("\n")) - {""} if f.exists() else set()

    def put_failed(self, nodeids):
        tmp = self.path / "failed.txt.tmp"
        tmp.write_text("\n".join(sorted(nodeids)), encoding="utf-8")
        os.replace(tmp, self.path / "failed.txt")

    def put(self, key, nodeids):
        tmp = self.path / f"{key}.ok.tmp"
        tmp.write_text("\n".join(nodeids), encoding="utf-8")
        os.replace(tmp, self.path / f"{key}.ok")
