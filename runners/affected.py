"""Сколько тестового времени нужно на каждое изменение (H4, H10): анализ истории git без запуска тестов.

Для каждого коммита (против его первого родителя) определяются затронутые файлы тестов по тем же
правилам, что у кэша рантайма (ADR-0005), плюс консервативные правила для не-Python входов:
- изменён файл из `invalidate` (lock-файлы, конфиг, миграции) — затронуты все тесты;
- изменён файл под путём из `suites` (например dbt/** для tests/dbt/) — затронуты все тесты этого набора;
- изменён не-.py файл внутри tests/ — затронуты тесты того же каталога и ниже;
- изменён не-.py файл вне tests/ и вне правил выше — затронуты все тесты (неизвестный вход);
- изменён .py — затронуты тесты, в замыкании импортов или conftest-цепочке которых он есть.
Доля времени взвешивается по замеренным секундам файлов тестов (JSON проекта).

Репозиторий только читается: git ls-tree, git diff, git cat-file.
"""

import ast
import fnmatch
import json
import subprocess
from collections import defaultdict

import numpy as np


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


class Blobs:
    """Чтение блобов через один процесс git cat-file --batch, с кэшем разбора импортов по sha."""

    def __init__(self, repo):
        self.p = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)
        self.imports = {}
        self._text = {}

    def read(self, sha):
        self.p.stdin.write((sha + "\n").encode())
        self.p.stdin.flush()
        header = self.p.stdout.readline().split()
        size = int(header[2])
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)
        return data

    def text(self, sha):
        if sha not in self._text:
            self._text[sha] = self.read(sha).decode("utf-8", "replace")
        return self._text[sha]

    def import_names(self, path, sha):
        """Точечные имена импортов файла; относительные — с уровнем: (level, module, names)."""
        if sha in self.imports:
            return self.imports[sha]
        out = []
        try:
            tree = ast.parse(self.read(sha))
        except (SyntaxError, ValueError):
            tree = None
        for node in ast.walk(tree) if tree else []:
            if isinstance(node, ast.Import):
                out += [(0, a.name, ()) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                out.append((node.level, node.module or "", tuple(a.name for a in node.names)))
        self.imports[sha] = out
        return out


def _resolve(paths, base):
    """base — путь модуля без расширения (app/models/user)."""
    for cand in (base + ".py", base + "/__init__.py"):
        if cand in paths:
            return cand
    return None


def forward_deps(blobs, files, roots=("", "src/")):
    """files: path -> sha на коммите. Возвращает path -> множество путей, которые он импортирует."""
    pyfiles = {p for p in files if p.endswith(".py")}
    deps = {}
    for path in pyfiles:
        pkg = path.rsplit("/", 1)[0].split("/") if "/" in path else []  # пакет файла (для __init__ — он сам)
        out = set()
        for level, mod, names in blobs.import_names(path, files[path]):
            if level:
                base = pkg[: len(pkg) - level + 1] if level > 1 else pkg
                mod = ".".join(base + ([mod] if mod else []))
            cands = [mod] + [f"{mod}.{n}" if mod else n for n in names]
            for name in cands:
                parts = name.split(".")
                for i in range(1, len(parts) + 1):  # пакет и все его родители (__init__.py)
                    for root in roots:
                        hit = _resolve(pyfiles, root + "/".join(parts[:i]))
                        if hit:
                            out.add(hit)
        deps[path] = out
    return deps


def _conftests(path, files):
    out, parts = [], path.split("/")[:-1]
    for i in range(len(parts), -1, -1):
        c = "/".join(parts[:i] + ["conftest.py"]) if i else "conftest.py"
        if c in files:
            out.append(c)
    return out


def _referencing(blobs, files, path):
    """Python-файлы, в тексте которых упомянут путь, имя или каталог не-Python файла."""
    tokens = {path, path.rsplit("/", 1)[-1]}
    if "/" in path:
        tokens.add(path.rsplit("/", 1)[0] + "/")
        tokens.add(path.rsplit("/", 1)[0] + '"')
    return {f for f, sha in files.items() if f.endswith(".py") and any(tok in blobs.text(sha) for tok in tokens)}


def affected_tests(files, deps, changed, tests, rules, blobs=None):
    """Множество затронутых файлов тестов на коммите.

    rules["references"] = True: не-Python файл вне tests/ затрагивает Python-модули, в тексте которых
    упомянуты его путь, имя или каталог (а через них — тесты по обратному замыканию); False — все тесты.
    """
    if any(any(fnmatch.fnmatch(c, g) for g in rules["invalidate"]) for c in changed):
        return set(tests), "all"
    hit = set()
    for prefix, globs in rules.get("suites", {}).items():
        if any(any(fnmatch.fnmatch(c, g) for g in globs) for c in changed):
            hit |= {t for t in tests if t.startswith(prefix)}
    py_changed = set()
    for c in changed:
        if c.endswith(".py"):
            py_changed.add(c)
        elif c.startswith("tests/"):
            d = c.rsplit("/", 1)[0] + "/"
            hit |= {t for t in tests if t.startswith(d)}
        elif any(any(fnmatch.fnmatch(c, g) for g in globs) for globs in rules.get("suites", {}).values()):
            pass
        elif any(fnmatch.fnmatch(c, g) for g in rules.get("ignore", [])):
            pass
        elif rules.get("references") and blobs is not None:
            py_changed |= _referencing(blobs, files, c)  # упомянувшие файл модули считаются изменёнными
        else:
            return set(tests), "unknown-input"
    if py_changed:
        rev = defaultdict(set)
        for a, ds in deps.items():
            for b in ds:
                rev[b].add(a)
        seen, stack = set(py_changed), list(py_changed)
        while stack:
            for a in rev.get(stack.pop(), ()):
                if a not in seen:
                    seen.add(a)
                    stack.append(a)
        for t in tests:
            if t in seen or any(c in seen for c in _conftests(t, files)):
                hit.add(t)
    return hit, "closure"


def analyze(repo, since, weights, rules, first_parent=True):
    """weights: path -> секунды. Возвращает DataFrame-подобный список по коммитам."""
    import pandas as pd

    fmt = ["log", "--format=%H %P", f"--since={since}", "--reverse"] + (["--first-parent"] if first_parent else ["--no-merges"])
    commits = [ln.split() for ln in _git(repo, *fmt).splitlines() if ln.strip()]
    blobs = Blobs(repo)
    total_w = sum(weights.values())
    rows = []
    for c, *parents in commits:
        if not parents:
            continue
        files = {}
        for ln in _git(repo, "ls-tree", "-r", c).splitlines():
            meta, path = ln.split("\t", 1)
            files[path] = meta.split()[2]
        changed = [p for p in _git(repo, "diff", "--name-only", parents[0], c).splitlines() if p]
        if not changed:
            continue
        tests = [t for t in weights if t in files]
        deps = forward_deps(blobs, files)
        hit, why = affected_tests(files, deps, changed, tests, rules, blobs)
        w = sum(weights[t] for t in hit)
        rows.append({"commit": c[:12], "changed": len(changed), "affected_files": len(hit), "tests_total": len(tests),
                     "affected_s": round(w, 1), "affected_pct": round(100 * w / total_w, 1), "reason": why})
    return pd.DataFrame(rows)


def load_weights(paths):
    w = {}
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        ds = d["datasets"][d["selected_dataset"]] if "datasets" in d else d
        w.update(ds.get("weights", ds))
    return w


def summary(df):
    if df.empty:
        return {}
    a = df.affected_pct.to_numpy()
    return {"commits": int(len(df)), "p50_pct": float(np.percentile(a, 50)), "p75_pct": float(np.percentile(a, 75)),
            "p90_pct": float(np.percentile(a, 90)), "mean_pct": round(float(a.mean()), 1),
            "all_pct": round(100 * float((df.reason != "closure").mean()), 1),
            "zero_pct": round(100 * float((df.affected_pct == 0).mean()), 1)}
