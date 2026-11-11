"""Правила runners/affected.py на маленьком git-репозитории."""

import subprocess

import pytest

from runners import affected as A

RULES = {"invalidate": ["uv.lock"], "suites": {"tests/dbt/": ["dbt/*"]}, "references": True}


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    files = {
        "app/__init__.py": "", "app/a.py": "X = 1\n", "app/b.py": "Y = 2\n",
        "tests/test_a.py": "from app import a\n", "tests/test_b.py": "from app.b import Y\nP = 'docs/spec.md'\n",
        "tests/dbt/test_m.py": "", "dbt/models/m.sql": "select 1\n", "docs/spec.md": "v1\n", "docs/other.md": "v1\n",
        "uv.lock": "a==1\n", "README.md": "v1\n",
    }
    for p, body in files.items():
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return tmp_path


def change(repo, path, body):
    (repo / path).write_text(body, encoding="utf-8")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", f"change {path}")


@pytest.mark.parametrize("path,expected", [
    ("app/a.py", {"tests/test_a.py"}),
    ("docs/spec.md", {"tests/test_b.py"}),
    # каталог docs/ упомянут в test_b (через docs/spec.md): сознательный запас на динамические пути
    ("docs/other.md", {"tests/test_b.py"}),
    ("README.md", set()),
    ("dbt/models/m.sql", {"tests/dbt/test_m.py"}),
    ("uv.lock", {"tests/test_a.py", "tests/test_b.py", "tests/dbt/test_m.py"}),
])
def test_affected_rules(repo, path, expected):
    change(repo, path, "changed\n")
    weights = {"tests/test_a.py": 10.0, "tests/test_b.py": 30.0, "tests/dbt/test_m.py": 60.0}
    df = A.analyze(str(repo), "2000-01-01", weights, RULES)
    row = df.iloc[-1]
    assert row.affected_s == sum(weights[t] for t in expected)
